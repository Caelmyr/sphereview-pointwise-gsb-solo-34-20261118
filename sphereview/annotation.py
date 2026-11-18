"""Entrance marker projection, collision-free placement and PNG drawing.

The same engine drives the downloaded view PNG and the browser overlays, so
the page and the exported file always agree. Placement works in pixel space
on any canvas; the direction map uses a virtual canvas and normalizes the
result to fractions.
"""
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .projection import basis, rotation

ACCENT = (201, 239, 149, 255)
PRIORITY_TEXT = (24, 40, 24, 255)
LABEL_FILL = (255, 255, 255, 238)
LABEL_TEXT = (26, 34, 30, 255)
BADGE_BORDER = (16, 22, 19, 255)
LINE_COLOR = (241, 241, 233, 255)
DOT_CORE = (255, 255, 255, 255)

MAP_CANVAS = (1000, 500)


def metrics(width, height):
    """Annotation sizes scale with the rendered image's long edge."""
    long_edge = max(width, height)
    badge_h = max(20, round(long_edge * 0.042))
    return {
        'badge_h': badge_h,
        'pad_x': max(6, round(long_edge * 0.014)),
        'font_size': max(13, round(long_edge * 0.027)),
        'dot_r': max(3, round(long_edge * 0.0065)),
        'line_w': max(2, round(long_edge * 0.0035)),
        'max_offset': min(width, height) * 0.28,
    }


def load_font(size):
    return ImageFont.load_default(size=size)


def box_size(font, label, config):
    bbox = font.getbbox(label)
    text_w = bbox[2] - bbox[0]
    return text_w + 2 * config['pad_x'], config['badge_h']


def project_markers(markers, view, pose, width, height):
    """Project markers onto the perspective camera; keep points inside frame."""
    camera = (rotation(pose) @ rotation(view)).T
    limit = math.tan(math.radians(view['fov'] / 2))
    half_height = limit * height / width
    visible = []
    for marker in markers:
        # Markers are stored in the pose-adjusted working frame, so the world
        # ray is built directly from the marker's own yaw/pitch, matching the
        # longitude/latitude convention used by the panorama sampler.
        ray = basis(marker['yaw'], marker['pitch'], 0)[:, 2]
        x, y, z = camera @ ray
        if z <= 1e-9:
            continue
        nx, ny = x / z, y / z
        if -limit <= nx <= limit and -half_height <= ny <= half_height:
            visible.append({
                'id': marker['id'], 'label': marker['label'],
                'priority': bool(marker.get('priority')),
                'x': (nx / (2 * limit) + 0.5) * width,
                'y': (0.5 - ny / (2 * half_height)) * height,
            })
    return visible, limit, half_height


def rects_overlap(a, b, pad=0.0):
    return not (a[2] + pad <= b[0] or b[2] + pad <= a[0]
                or a[3] + pad <= b[1] or b[3] + pad <= a[1])


def point_in_rect(point, rect):
    return rect[0] <= point[0] <= rect[2] and rect[1] <= point[1] <= rect[3]


def closest_point(rect, point):
    return (min(max(point[0], rect[0]), rect[2]),
            min(max(point[1], rect[1]), rect[3]))


def segment_intersects_rect(start, end, rect):
    """Liang-Barsky segment/AABB intersection."""
    t0, t1 = 0.0, 1.0
    x0, y0 = start
    dx, dy = end[0] - x0, end[1] - y0
    for origin, delta, low, high in ((x0, dx, rect[0], rect[2]),
                                     (y0, dy, rect[1], rect[3])):
        if abs(delta) < 1e-12:
            if origin < low or origin > high:
                return False
            continue
        near, far = (low - origin) / delta, (high - origin) / delta
        if near > far:
            near, far = far, near
        t0, t1 = max(t0, near), min(t1, far)
        if t0 > t1:
            return False
    return True


def point_segment_distance(point, start, end):
    vx, vy = end[0] - start[0], end[1] - start[1]
    length_sq = vx * vx + vy * vy
    if length_sq < 1e-12:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    t = min(1, max(0, ((point[0] - start[0]) * vx + (point[1] - start[1]) * vy) / length_sq))
    return math.hypot(point[0] - (start[0] + t * vx), point[1] - (start[1] + t * vy))


def candidate_offsets(unit, cap):
    """Concentric rings of label offsets; the centered position comes first."""
    yield 0.0, 0.0
    seen = {(0.0, 0.0)}
    for ring in range(1, 9):
        radius = ring * unit * 0.95
        for index in range(16):
            angle = 2 * math.pi * index / 16 + (ring % 2) * math.pi / 16
            dx, dy = radius * math.cos(angle), -radius * math.sin(angle)
            if math.hypot(dx, dy) > cap:
                continue
            key = (round(dx, 1), round(dy, 1))
            if key not in seen:
                seen.add(key)
                yield dx, dy


def clamp_rect(rect, width, height):
    x0, y0, x1, y1 = rect
    dx = min(0.0, x0)
    if x1 > width:
        dx = width - x1
    dy = min(0.0, y0)
    if y1 > height:
        dy = height - y1
    return (x0 + dx, y0 + dy, x1 + dx, y1 + dy)


def feasible(rect, anchor, placed, anchors, config, strict=True):
    """A label must stay in frame, avoid other labels/entrances and, in strict
    mode, keep its leader line clear of other labels and entrance points."""
    for item in placed:
        if rects_overlap(rect, item['rect'], 2):
            return False
    for other in anchors:
        if other != anchor and point_in_rect(other, rect):
            return False
    if not strict or point_in_rect(anchor, rect):
        return True
    target = closest_point(rect, anchor)
    for item in placed:
        inflated = (item['rect'][0] - 1, item['rect'][1] - 1,
                    item['rect'][2] + 1, item['rect'][3] + 1)
        if segment_intersects_rect(anchor, target, inflated):
            return False
    # Only entrance points lying between the segment endpoints can be crossed.
    low_x, high_x = sorted((anchor[0], target[0]))
    low_y, high_y = sorted((anchor[1], target[1]))
    clearance = config['dot_r'] * 2.4
    for other in anchors:
        if other == anchor or not (low_x <= other[0] <= high_x
                                   and low_y <= other[1] <= high_y):
            continue
        if point_segment_distance(other, anchor, target) < clearance:
            return False
    return True


def place_rect(anchor, size, placed, anchors, width, height, config):
    bw, bh = size
    for strict in (True, False):
        tried = set()
        for dx, dy in candidate_offsets(config['badge_h'], config['max_offset']):
            rect = clamp_rect((anchor[0] + dx - bw / 2, anchor[1] + dy - bh / 2,
                               anchor[0] + dx + bw / 2, anchor[1] + dy + bh / 2),
                              width, height)
            key = (tuple(round(value) for value in rect), strict)
            if key in tried:
                continue
            tried.add(key)
            if feasible(rect, anchor, placed, anchors, config, strict):
                return rect
    return None


def build_plan(entries, width, height, label_sizes):
    """Place labels for projected anchor points.

    Priority markers are placed first, so non-priority labels always yield to
    them; markers that cannot fit anywhere are reported instead of drawn.
    """
    config = metrics(width, height)
    anchors = [(item['x'], item['y']) for item in entries]
    ordered = sorted(enumerate(entries),
                     key=lambda pair: (not pair[1]['priority'], pair[0]))
    placed, dropped = [], []
    for _, item in ordered:
        anchor = (item['x'], item['y'])
        rect = place_rect(anchor, label_sizes[item['label']], placed, anchors,
                          width, height, config)
        if rect is None:
            dropped.append({**{key: item[key] for key in ('id', 'label', 'priority')},
                            'x': anchor[0], 'y': anchor[1], 'reason': 'no_space'})
        else:
            placed.append({**{key: item[key] for key in ('id', 'label', 'priority')},
                           'anchor': anchor, 'rect': rect})
    return placed, dropped, config


def view_plan(markers, view, pose, width, height):
    entries, _, _ = project_markers(markers, view, pose, width, height)
    config = metrics(width, height)
    font = load_font(config['font_size'])
    sizes = {marker['label']: box_size(font, marker['label'], config) for marker in markers}
    placed, dropped, _ = build_plan(entries, width, height, sizes)
    inside = {item['id'] for item in entries}
    hidden = [{key: marker[key] for key in ('id', 'label', 'priority')}
              for marker in markers if marker['id'] not in inside]
    return {'width': width, 'height': height, 'labels': placed,
            'dropped': dropped, 'hidden': hidden}


def map_plan(markers):
    """Equirectangular direction-map placement on a virtual 2:1 canvas."""
    width, height = MAP_CANVAS
    entries = [{
        'id': marker['id'], 'label': marker['label'],
        'priority': bool(marker.get('priority')),
        'x': (marker['yaw'] + 180) / 360 * width,
        'y': (90 - marker['pitch']) / 180 * height,
    } for marker in markers]
    config = metrics(width, height)
    font = load_font(config['font_size'])
    sizes = {marker['label']: box_size(font, marker['label'], config) for marker in markers}
    placed, dropped, _ = build_plan(entries, width, height, sizes)
    return normalize({'width': width, 'height': height, 'labels': placed,
                      'dropped': dropped, 'hidden': []})


def normalize(plan):
    width, height = plan['width'], plan['height']

    def xy(value):
        return (value['x'], value['y']) if isinstance(value, dict) else (value[0], value[1])

    def point(value):
        x, y = xy(value)
        return {'x': float(x) / width, 'y': float(y) / height}

    def rect(value):
        return [float(value[0]) / width, float(value[1]) / height,
                float(value[2]) / width, float(value[3]) / height]

    labels = [{**{key: label[key] for key in ('id', 'label', 'priority')},
               'anchor': point(label['anchor']), 'rect': rect(label['rect'])}
              for label in plan['labels']]
    dropped = [{**{key: label[key] for key in ('id', 'label', 'priority', 'reason')},
                **point(label)} for label in plan['dropped']]
    return {'width': plan['width'], 'height': plan['height'], 'labels': labels,
            'dropped': dropped,
            'hidden': plan['hidden']}


def annotate_view(image, markers, view, pose):
    """Draw the planned labels onto an RGBA perspective image."""
    height, width = image.shape[:2]
    plan = view_plan(markers, view, pose, width, height)
    if not plan['labels']:
        return image, plan
    config = metrics(width, height)
    font = load_font(config['font_size'])
    overlay = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for label in plan['labels']:
        anchor = label['anchor']
        rect = label['rect']
        ax, ay = anchor
        if not point_in_rect(anchor, rect):
            target = closest_point(rect, anchor)
            draw.line([ax, ay, target[0], target[1]], fill=LINE_COLOR, width=config['line_w'])
        radius = config['dot_r']
        draw.ellipse((ax - radius, ay - radius, ax + radius, ay + radius),
                     fill=DOT_CORE, outline=BADGE_BORDER, width=2)
        draw.rounded_rectangle(rect, radius=config['badge_h'] / 2,
                               fill=ACCENT if label['priority'] else LABEL_FILL,
                               outline=BADGE_BORDER, width=2)
        draw.text(((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2), label['label'],
                  font=font, fill=PRIORITY_TEXT if label['priority'] else LABEL_TEXT,
                  anchor='mm')
    combined = Image.alpha_composite(Image.fromarray(image), overlay)
    return np.asarray(combined), plan
