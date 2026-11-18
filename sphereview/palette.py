import cv2
import numpy as np

from image_lab import validation as v
from .projection import panorama_rays, rotation

MAX_COLORS = 4
SEED_CLUSTERS = 8
MAX_CLUSTERS = 12
RESCUE_ROUNDS = 2
RESCUE_SEEDS = 4
MIN_RESCUE_SHARE = 0.002
MERGE_DISTANCE = 24.0
SAMPLE_LIMIT = 24000
KMEANS_ITERATIONS = 30


def clean_region(spec):
    """A yaw/pitch rectangle on the leveled map; yaw0 > yaw1 crosses the ±180° seam."""
    data = v.object_value(spec, '选区')
    region = {
        'yaw0': v.number(data.get('yaw0'), '选区起始水平角', -180, 180),
        'yaw1': v.number(data.get('yaw1'), '选区结束水平角', -180, 180),
        'pitch0': v.number(data.get('pitch0'), '选区下边缘仰角', -90, 90),
        'pitch1': v.number(data.get('pitch1'), '选区上边缘仰角', -90, 90),
    }
    if region['yaw0'] == region['yaw1']:
        raise v.Invalid('选区水平范围为空')
    if region['pitch0'] >= region['pitch1']:
        raise v.Invalid('选区俯仰范围为空')
    return region


def selection_weights(source, pose, region):
    """Visible solid angle of each source pixel: sphere area times alpha, zero outside."""
    height, width = source.shape[:2]
    rays = panorama_rays(width, height)
    work = rays @ rotation(pose)
    lon = np.degrees(np.arctan2(work[..., 0], work[..., 2]))
    lat = np.degrees(np.arctan2(work[..., 1], np.hypot(work[..., 0], work[..., 2])))
    if region['yaw0'] < region['yaw1']:
        inside = (lon >= region['yaw0']) & (lon <= region['yaw1'])
    else:  # the rectangle wraps the longitude cut and stays one piece
        inside = (lon >= region['yaw0']) | (lon <= region['yaw1'])
    inside &= (lat >= region['pitch0']) & (lat <= region['pitch1'])
    latitude = (0.5 - (np.arange(height, dtype=np.float32) + 0.5) / height) * np.pi
    area = np.cos(latitude)[:, None]
    alpha = source[..., 3].astype(np.float32) / 255
    return np.where(inside, area * alpha, np.float32(0))


def nearest(points, centers):
    """Cluster labels and squared distances to the assigned center."""
    labels = np.zeros(len(points), dtype=np.int32)
    best = np.full(len(points), np.inf, dtype=np.float32)
    for slot, center in enumerate(centers):
        distance = np.sum((points - center) ** 2, axis=1)
        better = distance < best
        labels[better] = slot
        best[better] = distance[better]
    return labels, best


def weighted_kmeans(points, weights, count, init=None):
    if init is None:
        rng = np.random.default_rng(7)
        probability = weights.astype(np.float64)
        probability /= probability.sum()
        centers = np.empty((count, points.shape[1]), dtype=np.float32)
        centers[0] = points[rng.choice(len(points), p=probability)]
        closest = np.sum((points - centers[0]) ** 2, axis=1)
        for slot in range(1, count):
            probs = weights * closest
            total = probs.sum()
            pick = rng.choice(len(points), p=probs / total) if total > 0 else rng.integers(len(points))
            centers[slot] = points[pick]
            closest = np.minimum(closest, np.sum((points - centers[slot]) ** 2, axis=1))
    else:
        centers = init.astype(np.float32)
    for _ in range(KMEANS_ITERATIONS):
        labels, _ = nearest(points, centers)
        updated = centers.copy()
        for slot in range(len(centers)):
            mask = labels == slot
            if mask.any():
                w = weights[mask]
                updated[slot] = (points[mask] * w[:, None]).sum(axis=0) / w.sum()
        shift = float(np.abs(updated - centers).max())
        centers = updated
        if shift < 1e-3:
            break
    return centers


def farthest_seeds(points, distance, count):
    seeds = []
    for _ in range(min(count, len(points))):
        index = int(np.argmax(distance))
        seeds.append(points[index])
        distance = np.minimum(distance, np.sum((points - points[index]) ** 2, axis=1))
    return np.array(seeds, dtype=np.float32)


def fit_centers(points, weights):
    """k-means, then rescue pixel groups that no center represents (small salient signs)."""
    centers = weighted_kmeans(points, weights, min(SEED_CLUSTERS, len(points)))
    total = float(weights.sum())
    for _ in range(RESCUE_ROUNDS):
        room = MAX_CLUSTERS - len(centers)
        if room <= 0:
            break
        _, distance = nearest(points, centers)
        far = distance > MERGE_DISTANCE ** 2
        if float(weights[far].sum()) / total < MIN_RESCUE_SHARE:
            break
        seeds = farthest_seeds(points[far], distance[far], min(RESCUE_SEEDS, room))
        centers = weighted_kmeans(points, weights, len(centers) + len(seeds),
                                  init=np.vstack([centers, seeds]))
    return centers


def adjacency_pairs(label_image):
    """Cluster pairs whose pixels touch, treating the ±180° seam as adjacent."""
    pairs = []
    edges = ((label_image[:, :-1], label_image[:, 1:]),
             (label_image[:-1, :], label_image[1:, :]),
             (label_image[:, -1:], label_image[:, :1]))
    for a, b in edges:
        touch = (a >= 0) & (b >= 0) & (a != b)
        if touch.any():
            pairs.append(np.stack([a[touch], b[touch]], axis=1))
    if not pairs:
        return set()
    unique = np.unique(np.concatenate(pairs), axis=0)
    return {tuple(map(int, sorted(row))) for row in unique}


def merge_groups(centers, pairs):
    """Merge near-color clusters only when they also touch, so shading bands and
    gradients on one surface collapse while a compact sign stays its own color."""
    parent = list(range(len(centers)))

    def find(root):
        while parent[root] != root:
            parent[root] = parent[parent[root]]
            root = parent[root]
        return root

    for first in range(len(centers)):
        for second in range(first + 1, len(centers)):
            if (first, second) not in pairs:
                continue
            if np.linalg.norm(centers[first] - centers[second]) < MERGE_DISTANCE:
                a, b = find(first), find(second)
                if a != b:
                    parent[a] = b
    groups = {}
    for slot in range(len(centers)):
        groups.setdefault(find(slot), []).append(slot)
    return list(groups.values())


def region_palette(source, pose, spec, max_colors=MAX_COLORS):
    region = clean_region(spec)
    weights = selection_weights(source, pose, region)
    total = float(weights.sum(dtype=np.float64))
    if total <= 1e-12:
        return {'region': region, 'pixels': 0, 'colors': [], 'remainder': 0}
    flat = weights.ravel()
    visible = flat > 0
    weight = flat[visible]
    rgb = source[..., :3].reshape(-1, 3)[visible].astype(np.float32)
    pixels = int(visible.sum())
    lab = cv2.cvtColor((rgb / 255)[None, ...], cv2.COLOR_RGB2Lab)[0].astype(np.float32)
    if pixels > SAMPLE_LIMIT:
        stride = int(np.ceil(pixels / SAMPLE_LIMIT))
        fit_points, fit_weight = lab[::stride], weight[::stride]
    else:
        fit_points, fit_weight = lab, weight
    centers = fit_centers(fit_points, fit_weight)
    labels, _ = nearest(lab, centers)
    label_image = np.full(source.shape[0] * source.shape[1], -1, dtype=np.int32)
    label_image[visible] = labels
    pairs = adjacency_pairs(label_image.reshape(source.shape[:2]))
    groups = merge_groups(centers, pairs)
    group_of = np.zeros(len(centers), dtype=np.int32)
    for index, group in enumerate(groups):
        for slot in group:
            group_of[slot] = index
    assigned = group_of[labels]
    shares = np.bincount(assigned, weights=weight, minlength=len(groups))
    sums = [np.bincount(assigned, weights=weight * rgb[:, channel], minlength=len(groups))
            for channel in range(3)]
    occupied = [index for index in np.argsort(-shares) if shares[index] > 0]
    colors = []
    for index in occupied[:max_colors]:
        mean = [sums[channel][index] / shares[index] for channel in range(3)]
        colors.append({'rgb': [int(round(float(value))) for value in mean],
                       'share': round(float(shares[index] / total), 4)})
    remainder = round(1 - sum(row['share'] for row in colors), 4) if len(occupied) > max_colors else 0
    return {'region': region, 'pixels': pixels, 'colors': colors, 'remainder': max(remainder, 0)}
