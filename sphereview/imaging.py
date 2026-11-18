import io

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from image_lab.validation import Invalid

MAX_UPLOAD = 16 * 1024 * 1024
MAX_WIDTH = 2048


def decode(raw):
    if len(raw) > MAX_UPLOAD:
        raise Invalid('图片超过16MB')
    try:
        with Image.open(io.BytesIO(raw)) as opened:
            if opened.format not in ('PNG', 'JPEG'):
                raise Invalid('请选择PNG或JPEG全景图片')
            orientation = opened.getexif().get(274)
            width, height = opened.size
            if orientation in (5, 6, 7, 8):
                width, height = height, width
            if not 128 <= width <= MAX_WIDTH or width != height * 2:
                raise Invalid('需要宽高比2:1且宽度为128至2048像素的经纬全景图')
            return np.asarray(ImageOps.exif_transpose(opened).convert('RGBA'))
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise Invalid('无法读取全景照片') from exc


def png(image):
    stream = io.BytesIO()
    Image.fromarray(image).save(stream, 'PNG', compress_level=3)
    return stream.getvalue()


def seam_report(source):
    height = len(source)
    valid = (source[:, 0, 3] > 0) & (source[:, -1, 3] > 0)
    left = source[:, 0, :3].astype(np.float32)
    right = source[:, -1, :3].astype(np.float32)
    differences = np.mean(np.abs(left - right), axis=1)
    bands = []
    for band in range(8):
        y0, y1 = band * height // 8, (band + 1) * height // 8
        values = differences[y0:y1][valid[y0:y1]]
        bands.append({'row_start': y0, 'row_stop': y1, 'compared': len(values),
                      'mean_rgb_difference': round(float(values.mean()), 2) if len(values) else None})
    return {
        'rows': height, 'valid_rows': int(valid.sum()),
        'mean_rgb_difference': round(float(differences[valid].mean()), 2) if valid.any() else None,
        'bands': bands,
    }


def demo():
    """A deliberately synthetic, longitude-periodic location with known headings."""
    width, height = 1536, 768
    x = (np.arange(width) + 0.5) / width * 2 * np.pi
    y = (np.arange(height) + 0.5)[:, None] / height
    top = np.array([43, 87, 131])
    bottom = np.array([236, 185, 139])
    sky_factor = np.clip(y / 0.52, 0, 1) ** 1.12
    sky = np.broadcast_to(top + (bottom - top) * sky_factor[..., None], (height, width, 3)).copy()
    cloud = (np.sin(x * 4 + y * 53) + 0.6 * np.cos(x * 7 - y * 75) +
             0.4 * np.sin(x * 17 + y * 95))
    sky += (np.maximum(0, cloud - 0.65) * np.exp(-((y - 0.38) / 0.085) ** 2))[..., None] * 19
    pixels = sky
    for height_offset, amplitude, phase, colors in [
        (0.47, 0.040, 0.4, [123, 117, 127]),
        (0.50, 0.064, 1.2, [78, 93, 110]),
        (0.57, 0.045, 2.1, [49, 82, 92]),
        (0.67, 0.025, 2.2, [56, 92, 78]),
        (0.79, 0.020, 3.7, [49, 74, 59]),
    ]:
        profile = (0.62 * np.sin(3 * x + phase) + 0.27 * np.cos(7 * x - phase)
                   + 0.11 * np.sin(13 * x))
        relief = (y >= height_offset - amplitude * profile)
        detail = 3 * np.sin(x * 60 + y * 110) + 2 * np.cos(x * 93 - y * 155)
        pixels[relief] = (np.array(colors) + detail[..., None])[relief]
    image = Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=18)
    detail_font = ImageFont.load_default(size=12)
    marks = [
        (0, 'WEST RAIL', (215, 112, 85)),
        (width // 4, 'PINE RIDGE', (202, 154, 79)),
        (width // 2, 'BLUE BAY', (64, 145, 205)),
        (3 * width // 4, 'EAST WATCH', (126, 182, 153)),
    ]
    for anchor, label, color in marks:
        for center in (anchor - width, anchor, anchor + width):
            if not -80 <= center <= width + 80:
                continue
            draw.rectangle((center - 4, 381, center + 4, 522), fill=(47, 47, 45))
            draw.polygon([(center - 57, 446), (center + 57, 446), (center + 49, 402),
                          (center - 49, 402)], fill=color)
            draw.text((center, 424), label, font=font, fill=(243, 241, 223), anchor='mm')
            draw.line([(center - 78, 522), (center - 55, 497), (center + 55, 497),
                       (center + 78, 522)], fill=(54, 49, 48), width=5)
    draw.text((width // 2, 551), 'SYNTHETIC PANORAMA', font=detail_font,
              fill=(231, 217, 184), anchor='mm')
    return np.asarray(image.convert('RGBA'))
