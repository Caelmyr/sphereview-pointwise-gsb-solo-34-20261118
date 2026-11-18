import math

import cv2
import numpy as np

ASPECTS = {'3:2': (3, 2), '16:9': (16, 9), '4:5': (4, 5), '1:1': (1, 1)}
FOV_MIN = 24
FOV_MAX = 140
# Keeping the current heading is preferred unless it costs more than this many
# extra degrees of horizontal fov compared with the best heading.
FIT_KEEP_TOLERANCE = 12
FACES = {
    'front': (0, 0),
    'right': (90, 0),
    'back': (180, 0),
    'left': (-90, 0),
    'top': (0, 90),
    'bottom': (0, -90),
}


def basis(yaw, pitch, roll):
    """Columns are world-space right, up and forward on an oriented view."""
    yaw, pitch, roll = map(math.radians, (yaw, pitch, roll))
    sy, cy = math.sin(yaw), math.cos(yaw)
    sp, cp = math.sin(pitch), math.cos(pitch)
    sr, cr = math.sin(roll), math.cos(roll)
    right = np.array([cy, 0, -sy], dtype=np.float32)
    up = np.array([-sy * sp, cp, -cy * sp], dtype=np.float32)
    forward = np.array([sy * cp, sp, cy * cp], dtype=np.float32)
    return np.column_stack((right * cr + up * sr, up * cr - right * sr, forward))


def rotation(pose):
    return basis(*(pose[key] for key in ('yaw', 'pitch', 'roll')))


def pixel_axis(count):
    return (np.arange(count, dtype=np.float32) + 0.5) / count


def view_size(aspect, long_edge):
    w, h = ASPECTS[aspect]
    return max(1, round(long_edge * w / max(w, h))), max(1, round(long_edge * h / max(w, h)))


def view_rays(width, height, fov):
    """Rays at pixel centers in the perspective camera's own frame."""
    limit = math.tan(math.radians(fov / 2))
    x = (2 * pixel_axis(width) - 1) * limit
    y = (1 - 2 * pixel_axis(height)) * limit * height / width
    mx, my = np.meshgrid(x, y)
    return np.stack((mx, my, np.ones_like(mx)), axis=-1)


def panorama_rays(width, height):
    longitude = (pixel_axis(width) - 0.5) * (2 * np.pi)
    latitude = (0.5 - pixel_axis(height)) * np.pi
    clat = np.cos(latitude)[:, None]
    y = np.broadcast_to(np.sin(latitude)[:, None], (height, width))
    return np.stack((np.sin(longitude)[None, :] * clat, y,
                     np.cos(longitude)[None, :] * clat), axis=-1)


def source_coordinates(rays, width, height):
    x, y, z = np.moveaxis(rays, -1, 0)
    lon = np.arctan2(x, z)
    lat = np.arctan2(y, np.hypot(x, z))
    columns = np.mod(lon / (2 * np.pi) + 0.5, 1) * width - 0.5
    rows = (0.5 - lat / np.pi) * height - 0.5
    return columns.astype(np.float32), rows.astype(np.float32)


def render(source, rays, source_from_world, exposure):
    hx, hy = source_coordinates(rays @ source_from_world.T, source.shape[1], source.shape[0])
    # Padding the horizontal axis makes bilinear lookup periodic at the longitude
    # cut. Latitude is clamped because the image has one pole on each side.
    rgba = source.astype(np.float32) / 255
    rgba[:, :, :3] *= rgba[:, :, 3:]
    periodic = np.concatenate((rgba[:, -1:], rgba, rgba[:, :1]), axis=1)
    output = cv2.remap(periodic, hx + 1, hy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    output[:, :, :3] /= np.maximum(output[:, :, 3:], 1e-8)
    output[:, :, :3] *= 2 ** exposure
    return np.clip(np.rint(255 * output), 0, 255).astype(np.uint8)


def view_image(source, view, pose, exposure, long_edge=768):
    width, height = view_size(view['aspect'], long_edge)
    transform = rotation(pose) @ rotation(view)
    return render(source, view_rays(width, height, view['fov']), transform, exposure)


def frame_corners(view):
    """Rays through the four frame corners, in the shared working coordinates."""
    wide, tall = ASPECTS[view['aspect']]
    limit = math.tan(math.radians(view['fov'] / 2))
    x, y = limit, limit * tall / wide
    corners = np.array([(-x, y, 1), (x, y, 1), (x, -y, 1), (-x, -y, 1)])
    return np.asarray(rotation(view), dtype=np.float64) @ corners.T


def covering_fov(corners, yaws, pitch, roll, ratio):
    """Smallest horizontal fov covering every corner ray, for each candidate yaw.

    A yaw is impossible (inf) while any corner sits on or behind the view plane.
    """
    yaw = np.radians(np.asarray(yaws, dtype=np.float64))
    sy, cy = np.sin(yaw), np.cos(yaw)
    sp, cp = math.sin(math.radians(pitch)), math.cos(math.radians(pitch))
    sr, cr = math.sin(math.radians(roll)), math.cos(math.radians(roll))
    right = np.stack((cy, np.zeros_like(cy), -sy), axis=-1)
    up = np.stack((-sy * sp, np.full_like(cy, cp), -cy * sp), axis=-1)
    forward = np.stack((sy * cp, np.full_like(cy, sp), cy * cp), axis=-1)
    axes = (right * cr + up * sr, up * cr - right * sr, forward)
    x, y, z = (axis @ corners for axis in axes)
    with np.errstate(divide='ignore', invalid='ignore'):
        need = np.maximum(np.abs(x), np.abs(y) * ratio) / z
    need = np.where(z > 1e-6, need, np.inf)
    worst = need.max(axis=1)
    return np.where(np.isfinite(worst), 2 * np.degrees(np.arctan(worst)), np.inf)


def fit_view(views, base):
    """Frame every corner of the given bookmark views in one new view.

    Pitch, roll and aspect follow ``base`` (the current view); only the heading
    and horizontal fov are solved. The current heading is kept unless widening
    it costs more than FIT_KEEP_TOLERANCE degrees over the best heading. The
    fov is rounded up so the corners stay inside the returned frame.
    """
    corners = np.concatenate([frame_corners(view) for view in views], axis=1)
    wide, tall = ASPECTS[base['aspect']]
    ratio = wide / tall

    def fov_at(yaws):
        return covering_fov(corners, yaws, base['pitch'], base['roll'], ratio)

    grid = np.linspace(-180.0, 180.0, 18001)
    best = int(np.argmin(fov_at(grid)))
    fine = np.linspace(grid[max(best - 1, 0)], grid[min(best + 1, len(grid) - 1)], 81)
    yaw_best = float(fine[int(np.argmin(fov_at(fine)))])

    fov_kept = float(fov_at([base['yaw']])[0])
    fov_best = float(fov_at([yaw_best])[0])

    def settled(yaw, kept):
        # Round the heading for display, then recompute and round the fov up so
        # the corners still fit with the rounded numbers.
        yaw = float(base['yaw'] if kept else round(yaw, 2))
        raw = float(fov_at([yaw])[0])
        if not math.isfinite(raw):
            return yaw, math.inf
        return yaw, max(FOV_MIN, math.ceil(raw * 100) / 100)

    chosen = None
    if math.isfinite(fov_kept) and fov_kept <= fov_best + FIT_KEEP_TOLERANCE:
        yaw, fov = settled(base['yaw'], True)
        if fov <= FOV_MAX:
            chosen = (yaw, fov, True)
    if chosen is None:
        yaw, fov = settled(yaw_best, False)
        if fov <= FOV_MAX:
            chosen = (yaw, fov, False)
    report = {'fov_limit': FOV_MAX,
              'heading_fov': round(fov_kept, 1) if math.isfinite(fov_kept) else None}
    if chosen is None:
        return {'fits': False, **report,
                'required_fov': round(fov_best, 1) if math.isfinite(fov_best) else None}
    yaw, fov, kept = chosen
    view = {'yaw': yaw, 'pitch': base['pitch'], 'roll': base['roll'],
            'fov': fov, 'aspect': base['aspect']}
    return {'fits': True, 'view': view, 'kept_heading': kept, **report}


def leveled_image(source, pose, exposure, long_edge):
    width = min(source.shape[1], long_edge)
    return render(source, panorama_rays(width, width // 2), rotation(pose), exposure)


def cube_face(source, pose, exposure, name, size):
    yaw, pitch = FACES[name]
    transform = rotation(pose) @ basis(yaw, pitch, 0)
    return render(source, view_rays(size, size, 90), transform, exposure)


def planet_image(source, pose, exposure, planet, size):
    axis = 2 * pixel_axis(size) - 1
    x, y = np.meshgrid(axis, -axis)
    radial = np.hypot(x, y)
    longitude = np.arctan2(x, y) + math.radians(planet['spin'])
    latitude = 2 * np.arctan(radial * planet['radius']) - np.pi / 2
    horizontal = np.cos(latitude)
    rays = np.stack((np.sin(longitude) * horizontal, np.sin(latitude),
                     np.cos(longitude) * horizontal), axis=-1)
    return render(source, rays, rotation(pose), exposure)


def probe(source, pose, yaw, pitch):
    ray = (rotation(pose) @ basis(yaw, pitch, 0))[:, 2]
    x, y = source_coordinates(ray, source.shape[1], source.shape[0])
    col = int(round(float(x))) % source.shape[1]
    row = min(source.shape[0] - 1, max(0, int(round(float(y)))))
    return {'yaw': yaw, 'pitch': pitch, 'column': col, 'row': row,
            'rgba': [int(value) for value in source[row, col]]}
