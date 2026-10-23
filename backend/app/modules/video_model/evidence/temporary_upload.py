"""Provider-documented temporary media upload; no credentials or URLs persisted."""
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
import httpx

POLICY_URL = 'https://maas.qianwenaiapi.com/api/v1/uploads'
SUPPORTED_HOSTS = {'maas.qianwenaiapi.com', 'token-plan.maas.qianwenaiapi.com'}

class VideoUploadFailure(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def transport_mode(endpoint):
    return 'temporary_upload' if urlsplit(endpoint).hostname in SUPPORTED_HOSTS else 'inline'


def upload_video(client: httpx.Client, api_key: str, model: str, video: Path) -> str:
    try:
        response = client.get(POLICY_URL, headers={'Authorization': 'Bearer ' + api_key}, params={'action': 'getPolicy', 'model': model})
        if response.status_code != 200:
            raise VideoUploadFailure('VIDEO_UPLOAD_POLICY_FAILED', f'视频上传凭证接口返回 HTTP {response.status_code}；尚未调用审核模型，未自动回退内联或重试')
        try:
            policy = response.json()['data']
            if not isinstance(policy, dict) or not isinstance(policy.get('upload_host'), str) or not isinstance(policy.get('upload_dir'), str):
                raise ValueError('invalid policy')
            host = urlsplit(policy['upload_host'])
            if (host.scheme != 'https' or not host.hostname or not host.hostname.endswith('.aliyuncs.com')
                    or host.username or host.password or host.query or host.fragment or host.port not in (None, 443)
                    or policy['x_oss_object_acl'] != 'private'):
                raise ValueError('invalid upload host or ACL')
            directory = policy['upload_dir'].strip('/')
            if not directory or any(c.isspace() for c in directory) or any(part in ('.', '..') for part in directory.split('/')) or any(c in directory for c in ('?', '#', '\\')):
                raise ValueError('invalid directory')
            key = directory + '/' + uuid4().hex + '.mp4'
            fields = {'OSSAccessKeyId': policy['oss_access_key_id'], 'Signature': policy['signature'], 'policy': policy['policy'],
                      'x-oss-object-acl': policy['x_oss_object_acl'], 'x-oss-forbid-overwrite': policy['x_oss_forbid_overwrite'],
                      'key': key, 'success_action_status': '200'}
            if not all(isinstance(value, str) and value for value in fields.values()):
                raise ValueError('invalid fields')
        except (KeyError, TypeError, ValueError):
            raise VideoUploadFailure('VIDEO_UPLOAD_POLICY_INVALID', '视频上传凭证格式或目标地址校验失败；尚未调用审核模型') from None
        # Multipart streams the file. The model API Key is never sent to the storage host.
        with video.open('rb') as stream:
            response = client.post(policy['upload_host'], files={**{name: (None, value) for name, value in fields.items()}, 'file': (key.rsplit('/', 1)[1], stream, 'video/mp4')}, timeout=httpx.Timeout(300, connect=15))
        if response.status_code != 200:
            raise VideoUploadFailure('VIDEO_UPLOAD_REJECTED', f'视频临时存储上传返回 HTTP {response.status_code}；尚未调用审核模型，未自动重试')
        return 'oss://' + key
    except httpx.HTTPError:
        raise VideoUploadFailure('VIDEO_UPLOAD_INTERRUPTED', '视频临时上传连接中断或超时；未调用审核模型，未自动重试') from None
