import math

import cv2
import numpy as np

ASPECTS = {'3:2': (3, 2), '16:9': (16, 9), '4:5': (4, 5), '1:1': (1, 1)}
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


def render(source, rays, source_from_world, exposure, mask=None):
    hx, hy = source_coordinates(rays @ source_from_world.T, source.shape[1], source.shape[0])
    # Padding the horizontal axis makes bilinear lookup periodic at the longitude
    # cut. Latitude is clamped because the image has one pole on each side.
    rgba = source.astype(np.float32) / 255
    rgba[:, :, :3] *= rgba[:, :, 3:]
    periodic = np.concatenate((rgba[:, -1:], rgba, rgba[:, :1]), axis=1)
    output = cv2.remap(periodic, hx + 1, hy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    output[:, :, :3] /= np.maximum(output[:, :, 3:], 1e-8)
    output[:, :, :3] *= 2 ** exposure
    output = np.clip(np.rint(255 * output), 0, 255).astype(np.uint8)
    if mask is not None:
        output = apply_mask(output, hx, hy, source, mask)
    return output


def view_image(source, view, pose, exposure, long_edge=768, mask=None):
    width, height = view_size(view['aspect'], long_edge)
    transform = rotation(pose) @ rotation(view)
    return render(source, view_rays(width, height, view['fov']), transform, exposure, mask)


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


def mask_spec(view, pose, box, color):
    """Pin a rectangle drawn on the current perspective view onto the scene.

    ``box`` holds left/right/top/bottom edges as fractions of the displayed
    picture (0-1, origin at the top-left, Y grows downward). The result keeps
    the drawing camera's orientation and the rectangle's tangent-angle limits,
    so the covered region stays on the same part of the panorama for any later
    view.
    """
    limit = math.tan(math.radians(view['fov'] / 2))
    x0, x1 = sorted((box['left'], box['right']))
    y0, y1 = sorted((box['top'], box['bottom']))
    return {
        'rotation': (rotation(pose) @ rotation(view)).tolist(),
        'x': [limit * (2 * x0 - 1), limit * (2 * x1 - 1)],
        'y': [limit * (1 - 2 * y1), limit * (1 - 2 * y0)],
        'color': color,
    }


def blocked_texels(source, mask):
    """Source texel centers whose rays pass through the solid mask volume."""
    height, width = source.shape[:2]
    local = panorama_rays(width, height) @ np.asarray(mask['rotation'], dtype=np.float32).T
    lx, ly, lz = local[..., 0], local[..., 1], local[..., 2]
    x0, x1 = mask['x']
    y0, y1 = mask['y']
    inside = (lz > 1e-6) & (lx >= x0 * lz) & (lx <= x1 * lz) & \
             (ly >= y0 * lz) & (ly <= y1 * lz)
    return inside


def apply_mask(output, hx, hy, source, mask):
    """Fill every output pixel whose bilinear footprint touches a masked texel.

    A perspective pixel interpolates the four texels around its source
    coordinate. If any of them is inside the masked scene region the whole
    pixel becomes solid color, so oblique edges can never leak original
    content; pixels with an untouched footprint are left exactly as rendered.
    """
    height, width = source.shape[:2]
    blocked = blocked_texels(source, mask)
    col0 = np.floor(hx + 0.5).astype(np.int32) % width
    col1 = (col0 + 1) % width
    row0 = np.clip(np.floor(hy + 0.5).astype(np.int32), 0, height - 1)
    row1 = np.clip(row0 + 1, 0, height - 1)
    covered = blocked[row0, col0] | blocked[row0, col1] | \
        blocked[row1, col0] | blocked[row1, col1]
    solid = np.array([int(mask['color'][i:i + 2], 16) for i in (1, 3, 5)] + [255],
                     dtype=np.uint8)
    output[covered] = solid
    return output
