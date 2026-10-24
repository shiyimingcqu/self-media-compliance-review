"""DOCX 内嵌图片纳入多模态审核（script_media.prepare_media）。

背景：prepare_media 原先只认 xlsx，docx 一律记"此格式中的图片尚未纳入多模态审核"并把
整次审核标成材料不完整。这里锁定 docx 与 xlsx 共用同一套解码/预算/失败如实上报的口径。
"""

import base64
import io
import uuid
import zipfile

import pytest
from PIL import Image

from app.modules.brief_script.parsing.docx import docx_images, parse_docx
from test_real_format_parsing import _docx_bytes
from test_docx_strict_ooxml import to_strict


_SEQ = [0]


def _png():
    """每次给出不同字节：python-docx 会按内容复用同一 media 成员，重复图会测不出多张。"""
    _SEQ[0] += 1
    n = _SEQ[0]
    buf = io.BytesIO()
    Image.new('RGB', (40 + n, 30 + n), (n * 7 % 255, n * 13 % 255, n * 29 % 255)).save(buf, format='PNG')
    return buf.getvalue()


def _docx_with_images(cells, extra_media=(), broken=()):
    """cells: {(行,列): 图片数量}；extra_media: 只放进包里但无人引用的成员；broken: 损坏成员名。

    每张图片字节都不同，避免 python-docx 按内容去重后合并成同一个 media 成员。
    """
    from docx import Document
    from docx.shared import Inches
    d = Document()
    rows = max(r for r, _ in cells) + 1
    cols = max(c for _, c in cells) + 1
    table = d.add_table(rows=rows, cols=cols)
    for (r, c), count in cells.items():
        paragraph = table.cell(r, c).paragraphs[0]
        paragraph.add_run(f'第{r}行第{c}列')      # 同格既有文字又有图，才能验证坐标一致
        for _ in range(count):
            run = paragraph.add_run()
            image = io.BytesIO(_png()); image.seek(0)
            run.add_picture(image, width=Inches(0.5))
    buf = io.BytesIO()
    d.save(buf)
    if not (extra_media or broken):
        return buf.getvalue()
    src = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as dst:
        for name in src.namelist():
            data = src.read(name)
            if name in broken:
                data = b'\x89PNG\r\n\x1a\n' + b'garbage-not-an-image'
            dst.writestr(name, data)
        for name in extra_media:
            dst.writestr(name, _png())
    return out.getvalue()


def test_docx_images_report_member_locations_and_bytes():
    data = _docx_with_images({(0, 1): 1, (1, 1): 2})
    images = docx_images(data)
    assert len(images) == 3
    for member, locations, blob in images:
        assert member.startswith('word/media/')
        assert blob and len(blob) > 0
        assert locations and all(item.startswith('table:0:r') for item in locations)
    # 同一格两张图 → 两个成员都指向 r1c1
    assert sorted(loc for _, locs, _ in images for loc in locs).count('table:0:r1c1') == 2


def test_docx_image_location_matches_parsed_text_cell():
    """图片定位与文本块共用同一坐标语言：同一格的图和文字带同一个 location。"""
    data = _docx_with_images({(0, 0): 1})
    _, locations, _ = docx_images(data)[0]
    assert locations == ['table:0:r0c0']
    assert any(b.location == 'table:0:r0c0' for b in parse_docx(data).blocks)


def test_unreferenced_media_is_still_returned_as_unknown_location():
    """包里有图但正文没引用：不能静默丢，定位记"位置未知"，让上层如实标未覆盖。"""
    data = _docx_with_images({(0, 0): 1}, extra_media=('word/media/orphan.png',))
    images = {name: (locs, blob) for name, locs, blob in docx_images(data)}
    assert 'word/media/orphan.png' in images
    assert images['word/media/orphan.png'][0] == ['位置未知']
    assert images['word/media/orphan.png'][1]


def test_broken_docx_member_is_returned_with_none_payload():
    """成员存在但内容损坏：字节照原样交给上层，由解码环节记未覆盖，不在提图阶段吞掉。"""
    data = _docx_with_images({(0, 0): 1}, broken=('word/media/image1.png',))
    images = docx_images(data)
    assert [name for name, _, _ in images] == ['word/media/image1.png']
    assert images[0][2].startswith(b'\x89PNG')          # 原始（损坏）字节仍交出去
    with pytest.raises(Exception):
        Image.open(io.BytesIO(images[0][2])).convert('RGB')


def test_strict_docx_images_keep_locations():
    """OOXML Strict 变体的 r:embed 命名空间不同，必须同样提到图与定位。"""
    data = to_strict(_docx_with_images({(0, 1): 1}))
    images = docx_images(data)
    assert len(images) == 1
    assert images[0][1] == ['table:0:r0c1']
    assert images[0][2]


def _upload(client, name, data):
    response = client.post('/api/v1/files', files={'file': (name, io.BytesIO(data), 'application/octet-stream')},
                           data={'purpose': 'script'}, headers={'Idempotency-Key': uuid.uuid4().hex})
    assert response.status_code == 201, response.text
    return response.json()['data']['id']


def test_prepare_media_sends_docx_images_through_shared_pipeline(client):
    """端到端：docx 脚本的内嵌图进入多模态清单，不再是"此格式中的图片尚未纳入"。"""
    from app.modules.workflow.script_media import prepare_media

    settings = client.app.state.settings
    file_id = _upload(client, 'script.docx', _docx_with_images({(0, 0): 1, (1, 0): 1}))
    manifest, inputs = prepare_media(settings, {'fileId': file_id,
                                                'parseMeta': {'uncoveredMedia': ['media:word/media/image1.png']}})
    assert manifest['incomplete'] is False, manifest['uncovered']
    assert len(manifest['images']) == 2 and len(inputs) == 2
    assert {img['mimeType'] for img in manifest['images']} == {'image/png'}
    assert all(img['locations'][0].startswith('table:0:r') for img in manifest['images'])
    # 送审图片带稳定 ID 与 base64 data URL（模型侧契约）
    for image, entry in zip(manifest['images'], inputs):
        assert image['id'] == entry['id']
        assert entry['dataUrl'].startswith('data:image/png;base64,')
        assert base64.b64decode(entry['dataUrl'].split(',', 1)[1])
    # 图片已进清单后，parseMeta 里的"未纳入"不再额外记未覆盖
    assert manifest['uncovered'] == []


def test_prepare_media_txt_still_marks_uncovered_media(client):
    """未实现提图的格式保持原行为：如实标未覆盖，不假装审过。"""
    from app.modules.workflow.script_media import prepare_media

    settings = client.app.state.settings
    file_id = _upload(client, 'script.txt', '脚本正文'.encode('utf-8'))
    manifest, inputs = prepare_media(settings, {'fileId': file_id, 'parseMeta': {'uncoveredMedia': ['media:x.png']}})
    assert inputs == [] and manifest['incomplete'] is True
    assert manifest['uncovered'] == ['此格式中的图片尚未纳入多模态审核']
