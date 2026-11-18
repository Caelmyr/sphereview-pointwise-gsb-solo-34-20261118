"""窄带接缝修正。

思路是在左右接缝两侧各取一条窄带，只做*乘性曝光/色偏*修正：
逐行估计每个通道的增益，使两侧边缘列向同一个几何均值目标靠拢，
再用边缘保持的双边滤波沿行向平滑。逐行估计意味着细栏杆等局部
结构只按本行增益轻微变化，不会被相邻背景的曝光差“抹”掉；不复制
对侧像素，因此也不会把一侧的纹理涂到另一侧。窄带之外的像素、
透明度和原图尺寸一律保留。
"""

import cv2
import numpy as np

from image_lab import validation as v

DEFAULT_BAND = 6
MAX_BAND = 24
# 增益限制在温和区间，暗部不放大噪声，亮部不强行压暗。
GAIN_MIN = 0.5
GAIN_MAX = 2.0
# 接近黑位时不放大，避免把暗部噪声拉出色差。
NOISE_FLOOR = 6.0
# 修正后允许到的最高色值，留一点余量，避免细亮线被压成整片纯白。
HIGHLIGHT_HEADROOM = 254.5
# 行向边缘保持平滑参数。
BILATERAL_D = 15
BILATERAL_SIGMA_COLOR = 0.12
BILATERAL_SIGMA_SPACE = 9.0
MEDIAN_KERNEL = 5
# 无效段（对侧无有效像素）边界的羽化长度（行）。
FEATHER_ROWS = 32


def band_width(value):
    return v.integer(value, '修正窄带宽度(列)', 1, MAX_BAND)


def _vertical_bilateral(gains, valid):
    """边缘保持的行向平滑：曝光差跨栏杆等突变时不互相污染。"""
    work = gains.copy()
    work[~valid] = 1.0
    work = cv2.medianBlur(work, MEDIAN_KERNEL)
    smoothed = np.empty_like(work)
    # 沿行向（高度轴）逐通道双边滤波，通道之间互不串色。
    for channel in range(3):
        column = work[:, channel:channel + 1]
        smoothed[:, channel:channel + 1] = cv2.bilateralFilter(
            column, BILATERAL_D, BILATERAL_SIGMA_COLOR, BILATERAL_SIGMA_SPACE,
            borderType=cv2.BORDER_REPLICATE)
    smoothed[~valid] = 1.0
    return np.clip(smoothed, GAIN_MIN, GAIN_MAX)


def edge_gains(source):
    """返回 (左增益, 右增益, 有效行掩码, 置信度)，形状为 (H,3) 或 (H,)。"""
    left = source[:, 0, :3].astype(np.float32)
    right = source[:, -1, :3].astype(np.float32)
    valid = (source[:, 0, 3] > 0) & (source[:, -1, 3] > 0)
    valid3 = np.repeat(valid[:, None], 3, axis=1)

    # 逐行目标取几何均值，纯乘性、对两侧对称。
    target = np.sqrt(np.maximum(left, 1e-6) * np.maximum(right, 1e-6))
    gain_left = target / np.maximum(left, 1e-6)
    gain_right = target / np.maximum(right, 1e-6)
    for gain in (gain_left, gain_right):
        np.clip(gain, GAIN_MIN, GAIN_MAX, out=gain)
        # 黑位附近的增益向 1 收拢，防止暗噪点被放大。
        confidence = np.clip(left / NOISE_FLOOR, 0, 1)
        confidence *= np.clip(right / NOISE_FLOOR, 0, 1)
        gain[:] = 1 + (gain - 1) * confidence
        gain[~valid3] = 1.0

    gain_left = _vertical_bilateral(gain_left, valid3)
    gain_right = _vertical_bilateral(gain_right, valid3)

    # 与无效段相邻的有效行向 1 平滑羽化，让增益在触及“保留现状”的无效
    # 行之前已平缓回到 1，避免无效段边界出现新的行向接缝。
    if valid.any() and (~valid).any():
        distance = np.full(len(source), len(source), dtype=np.int32)
        last_invalid = -len(source)
        for index, ok in enumerate(valid):
            if not ok:
                last_invalid = index
            distance[index] = min(distance[index], index - last_invalid)
        next_invalid = 2 * len(source)
        for index in range(len(source) - 1, -1, -1):
            if not valid[index]:
                next_invalid = index
            distance[index] = min(distance[index], next_invalid - index)
        feather_linear = np.clip(distance / FEATHER_ROWS, 0, 1).astype(np.float32)
        # smoothstep：两端导数为零，过渡最平缓，避免逐行台阶形成可见接缝。
        feather = feather_linear * feather_linear * (3 - 2 * feather_linear)
        feather3 = np.repeat(feather[:, None], 3, axis=1)
        gain_left[:] = 1 + (gain_left - 1) * feather3
        gain_right[:] = 1 + (gain_right - 1) * feather3
    return gain_left, gain_right, valid, confidence


def _column_weight(distance, band):
    """窄带内的水平权重：贴缝为 1，到窄带外缘平滑降到 0。"""
    ratio = np.clip(distance / band, 0, 1)
    return 1 - ratio * ratio * (3 - 2 * ratio)


def corrected(source, band=DEFAULT_BAND):
    """返回修正后的整幅 RGBA 图。窄带外像素逐字节保留。"""
    band = band_width(band)
    result = source.copy()
    height, width = result.shape[:2]
    gain_left, gain_right, _, _ = edge_gains(source)
    rgb = result[:, :, :3].astype(np.float32)

    for side, gains in ((0, gain_left), (1, gain_right)):
        for col in range(band):
            # col=0 即接缝边缘列，权重为 1（完整修正）；向内逐列平滑淡出。
            weight = _column_weight(col, band)
            if side == 0:  # 左侧窄带，从第 0 列向内
                x = col
            else:          # 右侧窄带，从最后一列向内
                x = width - 1 - col
            applied = 1 + (gains - 1) * weight
            adjusted = rgb[:, x] * applied
            # 亮部保护：只允许单向触顶，且不顶到 255，保住细线层次。
            adjusted = np.minimum(adjusted, HIGHLIGHT_HEADROOM)
            mask = result[:, x, 3] > 0
            rgb[:, x] = np.where(mask[:, None], adjusted, rgb[:, x])

    result[:, :, :3] = np.clip(np.rint(rgb), 0, 255).astype(np.uint8)
    return result


def seam_strip(image, band, half=40):
    """把接缝两侧的邻域按周期展开，让接缝位于画面正中。"""
    band = band_width(band)
    width = image.shape[1]
    span = max(half, band + 4)
    columns = np.arange(width - span, width + span) % width
    return image[:, columns].copy()


def _edge_rgb(image):
    valid = (image[:, 0, 3] > 0) & (image[:, -1, 3] > 0)
    left = image[:, 0, :3].astype(np.float32)
    right = image[:, -1, :3].astype(np.float32)
    diff = np.mean(np.abs(left - right), axis=1)
    return left, right, diff, valid


def compare(source, band=DEFAULT_BAND):
    """同位置修前/修后读数，用于界面对照。"""
    band = band_width(band)
    fixed = corrected(source, band)
    left0, right0, diff0, valid = _edge_rgb(source)
    left1, right1, diff1, _ = _edge_rgb(fixed)
    height = len(source)

    bands = []
    for part in range(8):
        y0, y1 = part * height // 8, (part + 1) * height // 8
        mask = valid[y0:y1]
        before = diff0[y0:y1][mask]
        after = diff1[y0:y1][mask]
        bands.append({
            'row_start': y0, 'row_stop': y1, 'compared': int(mask.sum()),
            'before': round(float(before.mean()), 2) if len(before) else None,
            'after': round(float(after.mean()), 2) if len(after) else None,
        })

    samples = []
    for row in np.linspace(0, height - 1, 6).round().astype(int):
        if not valid[row]:
            continue
        samples.append({
            'row': int(row),
            'left_before': [int(v) for v in left0[row]],
            'left_after': [int(v) for v in left1[row]],
            'right_before': [int(v) for v in right0[row]],
            'right_after': [int(v) for v in right1[row]],
        })
    return {
        'band': band,
        'valid_rows': int(valid.sum()),
        'rows': height,
        'mean_before': round(float(diff0[valid].mean()), 2) if valid.any() else None,
        'mean_after': round(float(diff1[valid].mean()), 2) if valid.any() else None,
        'bands': bands,
        'samples': samples,
    }
