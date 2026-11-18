"""HTTP 端到端测试：python -I tests/test_seamfix_api.py"""
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, '/opt/pointwise-env/site-packages')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from PIL import Image

from image_lab import create_app
from sphereview.server import register
from sphereview import seamfix


def make_png(width=256):
    rng = np.random.default_rng(3)
    image = np.zeros((width // 2, width, 4), np.uint8)
    image[..., 3] = 255
    gradient = np.linspace(50, 200, width, dtype=np.float32)
    image[..., :3] = np.clip(gradient[None, :, None] + rng.normal(0, 2, image.shape[:2] + (3,)), 0, 255)
    image[:, :8, :3] = np.clip(image[:, :8, :3].astype(np.int16) + 30, 0, 255)
    stream = io.BytesIO()
    Image.fromarray(image).save(stream, 'PNG')
    return stream.getvalue(), image


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    app = create_app(root / 'preparation')
    register(app, root / 'panoramas')
    client = app.test_client()

    raw, source = make_png()
    response = client.post('/sphere/projects', data={'file': (io.BytesIO(raw), 'pano.png')},
                           content_type='multipart/form-data')
    assert response.status_code == 201, response.data
    key = response.get_json()['id']

    # 对照报告
    report = client.get(f'/sphere/projects/{key}/seam-compare?band=6').get_json()
    assert report['band'] == 6 and report['valid_rows'] == source.shape[0]
    assert report['mean_after'] < report['mean_before']
    assert len(report['bands']) == 8 and report['samples']

    # 修前/修后条带：同尺寸，且中线两侧对应左右边缘列
    before = np.asarray(Image.open(io.BytesIO(client.get(
        f'/sphere/projects/{key}/seam-strip?band=6').data)))
    after = np.asarray(Image.open(io.BytesIO(client.get(
        f'/sphere/projects/{key}/seam-strip?band=6&after=1').data)))
    assert before.shape == after.shape
    assert np.array_equal(before[:, 39], source[:, -1])
    assert np.array_equal(before[:, 40], source[:, 0])
    assert not np.array_equal(after[:, 40], before[:, 40])

    # 下载修正全图：尺寸/透明度不变，窄带外字节一致
    corrected = np.asarray(Image.open(io.BytesIO(client.get(
        f'/sphere/projects/{key}/seam-corrected.png?band=6&download=1').data)))
    assert corrected.shape == source.shape
    assert np.array_equal(corrected[..., 3], source[..., 3])
    assert np.array_equal(corrected[:, 6:-6], source[:, 6:-6])
    disposition = client.get(f'/sphere/projects/{key}/seam-corrected.png?band=6&download=1')\
        .headers['Content-Disposition']
    assert 'attachment' in disposition and 'seam-corrected.png' in disposition

    # 与本地算法结果一致
    assert np.array_equal(corrected, seamfix.corrected(source, 6))

    # 非法窄带宽度返回 400
    for bad in (0, 25, 'abc'):
        bad_response = client.get(f'/sphere/projects/{key}/seam-compare?band={bad}')
        assert bad_response.status_code == 400, bad_response.data

    # 原工程仍可导出并恢复，原图接口不受影响
    project_zip = client.get(f'/sphere/projects/{key}/project.zip')
    assert project_zip.status_code == 200
    restored = client.post('/sphere/restore', data={'file': (io.BytesIO(project_zip.data), 'p.zip')},
                           content_type='multipart/form-data')
    assert restored.status_code == 201, restored.data
    source_png = client.get(f'/sphere/projects/{key}/source.png')
    assert source_png.status_code == 200
    assert np.array_equal(np.asarray(Image.open(io.BytesIO(source_png.data))), source)

    # 修正结果本身是合法 2:1 经纬图，可以重新导入为新项目
    reimport = client.post('/sphere/projects',
                           data={'file': (io.BytesIO(client.get(
                               f'/sphere/projects/{key}/seam-corrected.png?band=6').data),
                               'fixed.png')},
                           content_type='multipart/form-data')
    assert reimport.status_code == 201, reimport.data

    # 项目列表/原有视图接口照常工作
    assert client.get('/sphere/projects').status_code == 200
    assert client.get(f'/sphere/projects/{key}/view.png').status_code == 200
    assert client.get('/sphere/health').status_code == 200

print('全部 HTTP 端到端测试通过')
