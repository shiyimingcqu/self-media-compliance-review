"""Local Office conversion. Immutable originals are never rewritten or sent to a service."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading

from app.core.errors import ApiError

_conversion_slots = threading.BoundedSemaphore(2)
VERSION = 'office-preview-v1'


def office_binary():
    configured = os.getenv('REVIEW_LIBREOFFICE_PATH')
    candidates = [configured] if configured else []
    candidates += [shutil.which('soffice'), shutil.which('libreoffice')]
    if os.name == 'nt':
        candidates += [str(Path(os.getenv('PROGRAMFILES', 'C:/Program Files')) / 'LibreOffice/program/soffice.com')]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise ApiError(503, 'OFFICE_PREVIEW_UNAVAILABLE', 'Word / 旧版 Excel 原布局转换器未就绪，请安装 LibreOffice 后重试；仍可查看解析文字或下载原文件。')


def convert_office(payload: bytes, extension: str, target: str, cache_dir: Path) -> Path:
    if extension not in {'doc', 'docx', 'xls', 'xlsx'} or target not in {'pdf', 'docx', 'xlsx'}:
        raise ApiError(415, 'OFFICE_FORMAT_UNSUPPORTED', '此格式不支持 Office 转换')
    digest = hashlib.sha256(payload).hexdigest()
    cache_dir = Path(cache_dir).resolve() / VERSION
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f'{digest}-{extension}.{target}'
    if cached.is_file():
        return cached
    with _conversion_slots, tempfile.TemporaryDirectory(prefix='conversion-', dir=cache_dir) as work:
        if cached.is_file():
            return cached
        work = Path(work)
        source = work / f'source.{extension}'
        source.write_bytes(payload)
        profile = work / 'profile'
        user = profile / 'user'
        user.mkdir(parents=True)
        (user / 'registrymodifications.xcu').write_text(
            '<?xml version="1.0"?><oor:items xmlns:oor="http://openoffice.org/2001/registry">'
            '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
            '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item></oor:items>', encoding='utf-8')
        result = work / f'source.{target}'
        commands = []
        binary = office_binary()
        commands.append([binary, f'-env:UserInstallation={profile.as_uri()}', '--headless', '--nologo',
                         '--nodefault', '--norestore', '--convert-to', target, '--outdir', str(work), str(source)])
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        for command in commands:
            try:
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
                try:
                    process.communicate(timeout=90)
                except subprocess.TimeoutExpired:
                    if os.name == 'nt':
                        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, creationflags=flags)
                    process.kill()
                    process.communicate()
                    raise ApiError(504, 'OFFICE_CONVERSION_TIMEOUT', '原布局转换超时，请重试或下载原文件核对。') from None
            except OSError:
                continue
            if process.returncode == 0 and result.is_file() and result.stat().st_size:
                break
            result.unlink(missing_ok=True)
        else:
            raise ApiError(422, 'OFFICE_CONVERSION_FAILED', '原布局转换失败，文件可能损坏或加密；请下载原文件核对。')
        if target == 'pdf' and not result.read_bytes().startswith(b'%PDF-'):
            raise ApiError(422, 'OFFICE_CONVERSION_FAILED', '转换器未返回有效的 PDF 预览。')
        os.replace(result, cached)
    return cached
