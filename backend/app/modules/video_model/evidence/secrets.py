"""Fernet 加密本地 Key。主密钥与数据库分离，服务端不返回已存明文。"""
import os
from pathlib import Path


def cipher(data_dir: Path):
    from cryptography.fernet import Fernet
    configured = os.environ.get("REVIEW_SECRET_KEY")
    if configured:
        return Fernet(configured.encode("ascii"))
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / ".secrets.key"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "wb") as stream:
            stream.write(Fernet.generate_key())
    return Fernet(path.read_bytes())
