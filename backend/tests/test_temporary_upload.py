import json
import subprocess
from uuid import uuid4
import httpx
import imageio_ffmpeg
import pytest
from app.modules.video_model.evidence.temporary_upload import upload_video, transport_mode, VideoUploadFailure
from app.modules.video_model.evidence import console_service as service
from app.modules.video_model.evidence.console_schemas import EvidenceRunInput
from test_evidence_console import save, KEY


def policy(**overrides):
    return {'upload_host': 'https://temporary.oss-cn-beijing.aliyuncs.com', 'upload_dir': 'private/test', 'oss_access_key_id': 'temporary-id', 'signature': 'private-signature', 'policy': 'private-policy', 'x_oss_object_acl': 'private', 'x_oss_forbid_overwrite': 'true', **overrides}


def test_stream_upload_no_api_key_to_storage(tmp_path):
    video = tmp_path / 'video.mp4'; video.write_bytes(b'complete-video-bytes')
    requests = []
    def fixed(request):
        requests.append(request)
        if request.method == 'GET':
            assert request.url.params['model'] == 'qwen3.8-omni-flash'
            assert request.headers['Authorization'] == 'Bearer secret-key'
            return httpx.Response(200, json={'data': policy()})
        assert 'Authorization' not in request.headers
        assert b'complete-video-bytes' in request.content
        assert b'private-signature' in request.content and b'secret-key' not in request.content
        return httpx.Response(200)
    with httpx.Client(transport=httpx.MockTransport(fixed)) as client:
        url = upload_video(client, 'secret-key', 'qwen3.8-omni-flash', video)
    assert url.startswith('oss://private/test/') and len(requests) == 2
    assert transport_mode('https://maas.qianwenaiapi.com/compatible-mode/v1') == 'temporary_upload'
    assert transport_mode('https://unrelated.example/v1') == 'inline'


@pytest.mark.parametrize('bad', [dict(upload_host='https://localhost/upload'), dict(upload_host='https://host.aliyuncs.com.evil.example/upload'), dict(upload_host='http://host.aliyuncs.com/upload'), dict(x_oss_object_acl='public-read')])
def test_upload_rejects_invalid_storage_policy_without_file_transfer(tmp_path, bad):
    calls = []
    def fixed(request):
        calls.append(request)
        return httpx.Response(200, json={'data': policy(**bad)})
    with httpx.Client(transport=httpx.MockTransport(fixed)) as client:
        with pytest.raises(VideoUploadFailure) as error:
            upload_video(client, 'key', 'model', tmp_path / 'unused.mp4')
    assert error.value.code == 'VIDEO_UPLOAD_POLICY_INVALID' and len(calls) == 1


@pytest.mark.parametrize('stage', ['policy', 'upload'])
def test_upload_failure_does_not_retry_or_fallback(tmp_path, stage):
    video = tmp_path / 'video.mp4'; video.write_bytes(b'video')
    calls = []
    def fixed(request):
        calls.append(request)
        if request.method == 'GET' and stage != 'policy':
            return httpx.Response(200, json={'data': policy()})
        return httpx.Response(413)
    with httpx.Client(transport=httpx.MockTransport(fixed)) as client:
        with pytest.raises(VideoUploadFailure): upload_video(client, 'key', 'model', video)
    assert len(calls) == (1 if stage == 'policy' else 2)


def test_run_uploads_once_and_model_receives_url_not_base64(client, tmp_path):
    video = tmp_path / 'input.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-f', 'lavfi', '-i', 'color=s=160x120:d=1', '-c:v', 'libx264', str(video)], check=True, timeout=20)
    assert save(client, endpoint='https://maas.qianwenaiapi.com/compatible-mode/v1').status_code == 200
    asset = client.post('/api/v1/files', files={'file': ('input.mp4', video.read_bytes(), 'video/mp4')}, data={'purpose': 'video'}, headers={'Idempotency-Key': uuid4().hex}).json()['data']
    identity, trusted = service.prepare_run(client.app.state.settings, EvidenceRunInput(kind='video', file_id=asset['id'], config_revision=1, allow_real_call=True), uuid4().hex)
    calls = []
    model_urls = []
    def fixed(request):
        calls.append(str(request.url))
        if request.method == 'GET': return httpx.Response(200, json={'data': policy()})
        if request.url.host.endswith('.aliyuncs.com'):
            assert 'Authorization' not in request.headers
            return httpx.Response(200)
        body = json.loads(request.content)
        assert request.headers['X-DashScope-OssResourceResolve'] == 'enable'
        assert body['messages'][1]['content'][0]['video_url']['url'].startswith('oss://private/test/')
        assert 'data:video/mp4;base64' not in request.content.decode()
        assert len(request.content) < 100_000
        model_urls.append(body['messages'][1]['content'][0]['video_url']['url'])
        from test_localized_video import reply, row
        return httpx.Response(200, json=reply([row('capability-test-rule')]))
    service.execute_run(client.app.state.settings, identity, trusted, transport=httpx.MockTransport(fixed))
    run = service.get_run(client.app.state.settings, identity)
    assert run.status == 'succeeded', run.error_message
    assert len(calls) == 3 and len(model_urls) == 1
    assert run.result['transport']['usesInlineBase64'] is False
    serialized = json.dumps({'snapshot': run.snapshot, 'result': run.result})
    assert 'private-signature' not in serialized and 'oss://private/test' not in serialized and KEY not in serialized
