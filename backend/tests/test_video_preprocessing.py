import subprocess
import uuid

import imageio_ffmpeg
import pytest

from app.modules.video_model.evidence.preprocessing import prepare_video


def test_full_video_compression_preserves_timeline_audio_and_source(client, tmp_path, monkeypatch):
    source = tmp_path / 'source.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=size=1080x1920:rate=24:duration=1', '-f', 'lavfi', '-i',
                    'sine=frequency=440:duration=1', '-c:v', 'libx264', '-preset', 'ultrafast',
                    '-c:a', 'aac', '-shortest', str(source)], check=True, timeout=30)
    original = source.read_bytes()
    response = client.post('/api/v1/files', files={'file': ('source.mp4', original, 'video/mp4')},
                           data={'purpose':'video'}, headers={'Idempotency-Key': uuid.uuid4().hex})
    identity = response.json()['data']['id']
    prepared = client.post('/api/v1/video-preparations', json={'fileId':identity,'preset':'balanced'})
    assert prepared.status_code == 200, prepared.text
    data = prepared.json()['data']
    assert max(data['prepared']['width'], data['prepared']['height']) <= 1280
    assert data['prepared']['fps'] == data['original']['fps'] == 24
    assert data['prepared']['audioTracks'] == data['original']['audioTracks'] == 1
    assert abs(data['prepared']['durationMs'] - data['original']['durationMs']) <= 250
    assert data['canSend'] and data['sourceSha256'] != data['preparedSha256']
    assert source.read_bytes() == original
    assert client.get('/api/v1/files/' + identity + '/content').content == original
    again = client.post('/api/v1/video-preparations', json={'fileId':identity,'preset':'balanced'})
    assert again.json()['data'] == data
    # Cached bytes remain reusable; legacy size settings no longer impose a local send cap.
    from app.modules.video_model.evidence.console_schemas import ModelSettingsInput
    from app.modules.video_model.evidence.console_service import save_settings
    save_settings(client.app.state.settings, ModelSettingsInput(endpoint='https://models.example.com/v1', revision=0, max_inline_bytes=20_000_000))
    updated = client.post('/api/v1/video-preparations', json={'fileId':identity,'preset':'balanced'}).json()['data']
    assert updated['maxInlineBytes'] is None
    assert updated['preparedSha256'] == data['preparedSha256']
    assert updated['canSend'] is True
    compact = client.post('/api/v1/video-preparations', json={'fileId':identity,'preset':'compact'}).json()['data']
    assert max(compact['prepared']['width'],compact['prepared']['height']) <= 960
    unmodified = client.post('/api/v1/video-preparations', json={'fileId':identity,'preset':'original'}).json()['data']
    assert unmodified['sourceSha256'] == unmodified['preparedSha256']
    assert client.get(f'/api/v1/video-preparations/{identity}/balanced/content').status_code == 200
    assert client.get('/api/v1/evidence-runs').json()['data'] == []
    # Simulate a large asset size without allocating multi-GB media; no local byte cap.
    from app.modules.video_model.evidence import preprocessing
    original_get_asset = preprocessing.get_asset
    def large_asset(settings, identity):
        asset, path = original_get_asset(settings, identity)
        return asset.model_copy(update={'byte_size': 3_000_000_000}), path
    monkeypatch.setattr(preprocessing, 'get_asset', large_asset)
    (client.app.state.settings.data_dir / 'prepared-videos' / identity / 'original' / 'metadata.json').unlink()
    large, _ = prepare_video(client.app.state.settings, identity, 'original', max_inline_bytes=1)
    assert large['originalBytes'] == 3_000_000_000
    assert large['canSend'] is False  # The model-send limit remains independent.



def test_invalid_preset_rejected_without_model(client):
    r = client.post('/api/v1/video-preparations', json={'fileId':'missing','preset':'execute-shell'})
    assert r.status_code == 400
