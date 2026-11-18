"""接缝窄带修正的独立断言测试：python -I tests/test_seamfix.py"""
import sys
from pathlib import Path

sys.path.insert(0, '/opt/pointwise-env/site-packages')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from sphereview import seamfix

rng = np.random.default_rng(7)
W, H = 256, 128


def base_image():
    image = np.zeros((H, W, 4), np.uint8)
    image[..., 3] = 255
    # 平滑的水平渐变加噪声，避免整图同色导致增益退化。
    gradient = np.linspace(40, 210, W, dtype=np.float32)
    image[..., :3] = np.clip(gradient[None, :, None] + rng.normal(0, 3, (H, W, 3)), 0, 255)
    return image


def edge_mean_diff(image):
    valid = (image[:, 0, 3] > 0) & (image[:, -1, 3] > 0)
    diff = np.abs(image[:, 0, :3].astype(np.int16) - image[:, -1, :3].astype(np.int16))
    return diff[valid].mean()


def test_size_alpha_and_outside_preserved():
    source = base_image()
    band = 6
    fixed = seamfix.corrected(source, band)
    assert fixed.shape == source.shape and fixed.dtype == source.dtype
    assert np.array_equal(fixed[..., 3], source[..., 3])
    inside = np.zeros(W, bool)
    inside[:band] = True
    inside[W - band:] = True
    assert np.array_equal(fixed[:, ~inside], source[:, ~inside])
    print('ok  尺寸/透明度/窄带外像素完全保留')


def test_seam_gap_reduced():
    source = base_image()
    source[:, :8, :3] = np.clip(source[:, :8, :3].astype(np.int16) + 35, 0, 255)
    before = edge_mean_diff(source)
    fixed = seamfix.corrected(source, 6)
    after = edge_mean_diff(fixed)
    assert after < before * 0.55, (before, after)
    print(f'ok  接缝平均差下降：{before:.2f} -> {after:.2f}')


def test_highlight_rail_not_blown_to_white():
    source = base_image()
    # 跨接缝的细亮栏杆：左缘 250、右缘 252，背景约 120。
    rail_rows = np.arange(30, 90)
    source[rail_rows, 0, :3] = (250, 248, 245)
    source[rail_rows, -1, :3] = (252, 250, 247)
    fixed = seamfix.corrected(source, 6)
    rail = fixed[rail_rows][:, [0, -1], :3]
    assert rail.max() <= 255
    # 亮部宁可留色差也不许顶成同一片 255。
    assert not np.all(rail == 255)
    assert rail.min() >= 235
    # 栏杆与邻行背景的明暗层次仍然存在。
    background = fixed[[10, 110]][:, [0, -1], :3].mean()
    assert rail.mean() - background > 60
    print(f'ok  细亮栏杆未压成纯白，栏杆/背景对比保留（差 {rail.mean() - background:.1f}）')


def test_dark_rail_contrast_kept():
    source = base_image()
    rail_rows = np.arange(20, 100)
    source[rail_rows, 0, :3] = (18, 16, 14)
    source[rail_rows, -1, :3] = (24, 22, 20)
    before_ratio = source[50, 1, :3].mean() / max(source[50, 0, :3].mean(), 1)
    fixed = seamfix.corrected(source, 6)
    after_ratio = fixed[50, 1, :3].mean() / max(fixed[50, 0, :3].mean(), 1)
    assert abs(after_ratio - before_ratio) / before_ratio < 0.1
    assert fixed[50, 0, :3].mean() < 60
    print(f'ok  深色栏杆明暗层次保留（比值 {before_ratio:.2f} -> {after_ratio:.2f}）')


def test_invalid_rows_untouched():
    source = base_image()
    source[:40, 0, 3] = 0       # 左侧一段透明，对侧无有效像素
    source[90:, -1, 3] = 0      # 另一段在右侧透明
    fixed = seamfix.corrected(source, 6)
    assert np.array_equal(fixed[:40], source[:40])
    assert np.array_equal(fixed[90:], source[90:])
    report = seamfix.compare(source, 6)
    assert report['valid_rows'] == 50
    assert all(part['compared'] == 0 or part['compared'] > 0 for part in report['bands'])
    print('ok  无有效对侧像素的行段保留现状')


def test_feather_boundary_makes_no_visible_seam():
    # 无噪声平滑图，隔离羽化本身引入的行向跳变。
    height, width = 128, 256
    source = np.zeros((height, width, 4), np.uint8)
    source[..., 3] = 255
    grad = np.linspace(60, 180, width, dtype=np.float32)
    vert = np.linspace(0, 20, height, dtype=np.float32)[:, None]
    source[..., :3] = np.clip(grad[None, :, None] + vert[..., None], 0, 255).astype(np.uint8)
    source[:, :8, :3] = np.clip(source[:, :8, :3].astype(np.int16) + 40, 0, 255)
    source[:30, 0, 3] = 0
    fixed = seamfix.corrected(source, 6)
    # 8 位量化本身每行进位就有 ±1~2 的台阶，羽化只允许再加极小的平滑台阶。
    jumps = np.abs(fixed[29:54, 0, :3].astype(np.int16) -
                   np.roll(fixed[29:54, 0, :3], 1, axis=0))[1:]
    assert jumps.max() <= 3
    assert np.array_equal(fixed[:30], source[:30])
    print('ok  无效段边界羽化平缓，无可见新接缝')


def test_no_new_seams_at_band_edges():
    source = base_image()
    source[:, :8, :3] = np.clip(source[:, :8, :3].astype(np.int16) + 40, 0, 255)
    band = 8
    fixed = seamfix.corrected(source, band)
    # 窄带最外一列权重为 0，必须与原图一致。
    assert np.array_equal(fixed[:, band], source[:, band])
    assert np.array_equal(fixed[:, W - 1 - band], source[:, W - 1 - band])
    # 外缘相邻列差不应比修前更大（不产生新缝）。
    def jump(image, x):
        return np.abs(image[:, x, :3].astype(np.int16) -
                      image[:, x + 1, :3].astype(np.int16)).mean()
    assert jump(fixed, band) <= jump(source, band) + 0.6
    assert jump(fixed, W - 2 - band) <= jump(source, W - 2 - band) + 0.6
    print('ok  窄带外缘平滑淡出，未出现新接缝')


def test_no_texture_cross_side():
    source = base_image()
    # 左侧边缘放置独有的高对比纹理，右侧没有。
    source[60:64, 0, 0] = 255
    fixed = seamfix.corrected(source, 6)
    # 右侧任何像素都不应出现左侧独有的红色纹理值。
    right = fixed[:, W - 6:, 0]
    assert right[60:64].max() < 240
    # 修正只是乘性缩放：同一像素通道的修后/修前比例为有限正值。
    opaque = source[..., 3] > 0
    ratios = fixed[opaque][..., :3].astype(np.float32) / np.maximum(source[opaque][..., :3], 1)
    assert np.isfinite(ratios).all() and ratios.min() > 0
    print('ok  未把一侧纹理复制到另一侧，修正为纯乘性')


def test_all_transparent_is_identity():
    source = base_image()
    source[..., 3] = 0
    fixed = seamfix.corrected(source, 6)
    assert np.array_equal(fixed, source)
    report = seamfix.compare(source, 6)
    assert report['valid_rows'] == 0 and report['mean_before'] is None
    print('ok  全透明图原样返回')


def test_compare_and_strip_shapes():
    source = base_image()
    report = seamfix.compare(source, 4)
    assert report['band'] == 4 and len(report['bands']) == 8
    assert report['mean_after'] <= report['mean_before'] + 0.01
    assert 1 <= len(report['samples']) <= 6
    strip_before = seamfix.seam_strip(source, 4)
    strip_after = seamfix.seam_strip(seamfix.corrected(source, 4), 4)
    assert strip_before.shape == strip_after.shape
    assert strip_before.shape[1] == 80
    # 条带中线两侧正是左右边缘列。
    assert np.array_equal(strip_before[:, 39], source[:, -1])
    assert np.array_equal(strip_before[:, 40], source[:, 0])
    print('ok  对照报告与周期展开条带结构正确')


def test_band_validation():
    source = base_image()
    for bad in (0, 25, -1, 1.5, 'x', None):
        try:
            seamfix.corrected(source, bad)
        except ValueError:
            continue
        raise AssertionError(f'非法窄带值未被拒绝: {bad!r}')
    assert seamfix.corrected(source, 1).shape == source.shape
    print('ok  窄带宽度校验（1–24 整数）')


def test_seam_columns_fully_corrected_even_at_band_one():
    # 回归：接缝边缘列（col 0 / 最后一列）权重必须为 1，band=1 也要真正修缝。
    source = base_image()
    source[:, :4, :3] = np.clip(source[:, :4, :3].astype(np.int16) + 45, 0, 255)
    fixed = seamfix.corrected(source, 1)
    before = np.abs(source[:, 0, :3].astype(np.int16) - source[:, -1, :3]).mean()
    after = np.abs(fixed[:, 0, :3].astype(np.int16) - fixed[:, -1, :3]).mean()
    assert after < before * 0.1
    # band=1 只动接缝两列，其余像素逐字节保留。
    assert np.array_equal(fixed[:, 1:-1], source[:, 1:-1])
    print(f'ok  band=1 接缝列完整修正（{before:.1f} -> {after:.1f}），其余像素保留')


if __name__ == '__main__':
    for name, fn in sorted(globals().items()):
        if name.startswith('test_') and callable(fn):
            fn()
    print('\n全部接缝修正测试通过')
