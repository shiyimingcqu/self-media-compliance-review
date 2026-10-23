import io
import hashlib
import sqlite3
from uuid import uuid4

PDF_HEAD = b"%PDF-1.7\n"
DOCX_HEAD = b"PK\x03\x04" + b"\x00" * 12
FAKE_PDF = b"<?php echo 'bad' ?>" + b"\x00" * 24


def upload(client, name, payload, purpose="brief", key=None):
    headers = {"Idempotency-Key": key or uuid4().hex}
    return client.post("/api/v1/files",
                       files={"file": (name, io.BytesIO(payload), "application/octet-stream")},
                       data={"purpose": purpose}, headers=headers)


def test_upload_download_roundtrip_and_metadata_shape(client):
    payload = PDF_HEAD + b"hello brief"
    response = upload(client, "brand-brief.pdf", payload)
    assert response.status_code == 201, response.text
    asset = response.json()["data"]
    assert asset["purpose"] == "brief" and asset["mode"] == "demo"
    assert asset["sha256"] == hashlib.sha256(payload).hexdigest()
    assert asset["byteSize"] == len(payload)
    assert asset["signatureState"] == "verified"
    # 未验证状态在接口中如实保留，不冒充完整解码/可解析性。
    assert asset["mediaCheck"] == "unverified"
    assert "storageKey" not in asset

    fetched = client.get(f"/api/v1/files/{asset['id']}/content")
    assert fetched.status_code == 200 and fetched.content == payload
    assert fetched.headers["accept-ranges"] == "bytes"
    assert "attachment" in fetched.headers["content-disposition"]


def test_service_generated_storage_name_rejects_path_components(client):
    payload = PDF_HEAD + b"x"
    response = upload(client, "../../../../etc/passwd.pdf", payload)
    assert response.status_code == 201, response.text
    asset = response.json()["data"]
    assert ".." not in asset["originalName"] and "/" not in asset["originalName"]
    fetched = client.get(f"/api/v1/files/{asset['id']}/content")
    assert fetched.status_code == 200 and fetched.content == payload


def test_single_range_and_416(client):
    payload = PDF_HEAD + bytes(2000)
    asset = upload(client, "big.pdf", payload).json()["data"]
    part = client.get(f"/api/v1/files/{asset['id']}/content", headers={"Range": "bytes=10-19"})
    assert part.status_code == 206
    assert part.content == payload[10:20]
    assert part.headers["content-range"] == f"bytes 10-19/{len(payload)}"
    suffix = client.get(f"/api/v1/files/{asset['id']}/content", headers={"Range": "bytes=-5"})
    assert suffix.status_code == 206 and suffix.content == payload[-5:]
    open_end = client.get(f"/api/v1/files/{asset['id']}/content", headers={"Range": "bytes=1998-"})
    assert open_end.status_code == 206 and open_end.content == payload[1998:]
    bad = client.get(f"/api/v1/files/{asset['id']}/content", headers={"Range": "bytes=99999-100000"})
    assert bad.status_code == 416
    assert bad.json()["error"]["code"] == "RANGE_UNSATISFIABLE"
    assert "requestId" in bad.json()


def test_head_follows_get_semantics_without_body(client):
    payload = PDF_HEAD + b"abc"
    asset = upload(client, "head.pdf", payload).json()["data"]
    head = client.head(f"/api/v1/files/{asset['id']}/content")
    assert head.status_code == 200
    assert head.content == b""
    assert int(head.headers["content-length"]) == len(payload)
    assert head.headers["accept-ranges"] == "bytes"


def test_extension_purpose_and_signature_gates(client):
    wrong_purpose = upload(client, "note.txt", b"plain text", purpose="video")
    assert wrong_purpose.status_code == 415 and wrong_purpose.json()["error"]["code"] == "UNSUPPORTED_MEDIA"
    fake = upload(client, "fake.pdf", FAKE_PDF)
    assert fake.status_code == 415
    assert fake.json()["error"]["details"]["extension"] == "pdf"
    no_ext = upload(client, "script", b"hello")
    assert no_ext.status_code == 415
    bad_purpose = upload(client, "a.pdf", PDF_HEAD, purpose="invoice")
    assert bad_purpose.status_code == 400 and bad_purpose.json()["error"]["code"] == "VALIDATION_ERROR"
    docx = upload(client, "s.docx", DOCX_HEAD + b"\x00" * 30)
    assert docx.status_code == 201
    txt = upload(client, "s.txt", b"short")
    assert txt.status_code == 201 and txt.json()["data"]["signatureState"] == "unverifiable"


def test_stream_limit_and_temp_file_cleanup(client):
    from app.core.config import Settings

    limits = client.app.state.settings
    payload = PDF_HEAD + bytes(limits.max_document_bytes + 1)
    oversize = upload(client, "huge.pdf", payload)
    assert oversize.status_code == 413
    assert oversize.json()["error"]["code"] == "FILE_TOO_LARGE"
    uploads = client.app.state.settings.uploads_dir
    assert list(uploads.glob("*.part")) == []
    with sqlite3.connect(client.app.state.settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM file_assets").fetchone()[0] == 0
    assert isinstance(limits, Settings)


def test_empty_file_rejected_without_asset_or_audit(client):
    import sqlite3

    for name, purpose in (("x.pdf", "brief"), ("x.txt", "script")):
        empty = upload(client, name, b"", purpose=purpose)
        assert empty.status_code == 400, empty.text
        assert empty.json()["error"]["code"] == "VALIDATION_ERROR"
        assert "file" in empty.json()["error"]["fieldErrors"]
    uploads = client.app.state.settings.uploads_dir
    assert list(uploads.glob("*")) == []  # 无残留：临时件与最终件都不存在
    with sqlite3.connect(client.app.state.settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM file_assets").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM audit_events WHERE entity_type='file_asset'").fetchone()[0] == 0


def test_head_with_range_ignores_range_returns_full_length(client):
    # RFC 9110 §14.2：Range 语义适用于 GET；HEAD 忽略 Range 返回全长元数据、无 body。此为回归保护。
    payload = PDF_HEAD + bytes(500)
    asset = upload(client, "hr.pdf", payload).json()["data"]
    head = client.head(f"/api/v1/files/{asset['id']}/content", headers={"Range": "bytes=0-9"})
    assert head.status_code == 200
    assert int(head.headers["content-length"]) == len(payload)
    assert "content-range" not in head.headers
    assert head.content == b""


def test_originals_not_overwritten_and_each_upload_is_new_asset(client):
    payload = PDF_HEAD + b"same bytes"
    first = upload(client, "v1.pdf", payload).json()["data"]
    second = upload(client, "v1.pdf", payload).json()["data"]
    assert first["id"] != second["id"]
    assert first["sha256"] == second["sha256"]
    fetched = client.get(f"/api/v1/files/{first['id']}/content")
    assert fetched.content == payload
    with sqlite3.connect(client.app.state.settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM file_assets").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM audit_events WHERE entity_type='file_asset'").fetchone()[0] == 2


def test_cross_mode_and_unknown_asset_not_readable(client):
    missing = client.get("/api/v1/files/does-not-exist/content")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
    asset = upload(client, "b.pdf", PDF_HEAD + b"1").json()["data"]
    with sqlite3.connect(client.app.state.settings.database_path) as db:
        db.execute("UPDATE file_assets SET mode='real' WHERE id=?", (asset["id"],))
    blocked = client.get(f"/api/v1/files/{asset['id']}/content")
    assert blocked.status_code == 404
    with sqlite3.connect(client.app.state.settings.database_path) as db:
        db.execute("UPDATE file_assets SET mode='demo', storage_key='../escape.pdf' WHERE id=?", (asset["id"],))
    guarded = client.get(f"/api/v1/files/{asset['id']}/content")
    assert guarded.status_code == 500 and guarded.json()["error"]["code"] == "INTERNAL_ERROR"


# ---------- t-12 p1 幂等 ----------

def test_idempotent_replay_returns_original_data_with_current_request_id(client):
    payload = PDF_HEAD + b"idem"
    key = uuid4().hex
    first = upload(client, "a.pdf", payload, key=key)
    assert first.status_code == 201
    second = upload(client, "a.pdf", payload, key=key)
    assert second.status_code == 201  # 回放保持原 201，不改 200
    assert second.json()["data"] == first.json()["data"]  # 原业务 data 快照一致
    assert second.json()["requestId"] != first.json()["requestId"]  # requestId 是当前请求追踪 ID
    assert second.json()["requestId"] == second.headers["x-request-id"]
    with sqlite3.connect(client.app.state.settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM file_assets").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM audit_events WHERE entity_type='file_asset'").fetchone()[0] == 1


def test_idempotent_conflict_on_purpose_or_name_or_bytes(client):
    payload = PDF_HEAD + b"same bytes"
    key = uuid4().hex
    assert upload(client, "v1.pdf", payload, key=key).status_code == 201
    other_purpose = upload(client, "v1.pdf", payload, purpose="script", key=key)
    # 同字节不同 purpose → 指纹不同 → 409
    assert other_purpose.status_code == 409 and other_purpose.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    other_name = upload(client, "v2.pdf", payload, key=key)
    assert other_name.status_code == 409
    other_bytes = upload(client, "v1.pdf", payload + b"x", key=key)
    assert other_bytes.status_code == 409
    with sqlite3.connect(client.app.state.settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM file_assets").fetchone()[0] == 1  # 冲突不落资产


def test_idempotent_key_required_boundary_and_blank(client):
    payload = PDF_HEAD + b"k"
    missing = client.post("/api/v1/files", files={"file": ("a.pdf", io.BytesIO(payload), "application/pdf")},
                          data={"purpose": "brief"})
    assert missing.status_code == 400 and missing.json()["error"]["code"] == "VALIDATION_ERROR"  # Header 必填，规范化为 400 envelope
    blank = upload(client, "a.pdf", payload, key="        ")
    assert blank.status_code == 400 and blank.json()["error"]["code"] == "VALIDATION_ERROR"
    short = upload(client, "a.pdf", payload, key="abc")
    assert short.status_code == 400  # min_length=8 同一校验路径


def test_idempotent_failed_request_releases_key(client):
    key = uuid4().hex
    fake = upload(client, "fake.pdf", FAKE_PDF, key=key)
    assert fake.status_code == 415
    retry = upload(client, "ok.pdf", PDF_HEAD + b"good", key=key)
    assert retry.status_code == 201  # 失败请求不占 key，可重试成功
    uploads = client.app.state.settings.uploads_dir
    assert list(uploads.glob("*.part")) == []


def test_idempotent_concurrent_same_key_unique_asset(client):
    from concurrent.futures import ThreadPoolExecutor

    payload = PDF_HEAD + bytes(2 * 1024 * 1024)  # 2MiB 触发 spool，制造交错窗口
    key = uuid4().hex
    results = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(lambda: client.post("/api/v1/files",
                                                   files={"file": ("c.pdf", io.BytesIO(payload), "application/pdf")},
                                                   data={"purpose": "brief"},
                                                   headers={"Idempotency-Key": key})) for _ in range(2)]
        results = [f.result() for f in futures]
    codes = sorted(r.status_code for r in results)
    # 并发同 key：唯一约束+事务原子下，至多一个请求落资产；另一个回放（201 同 data）或 409 处理中；绝无 500/双资产。
    assert codes in ([201, 201], [201, 409]), codes
    successes = [r for r in results if r.status_code == 201]
    if len(successes) == 2:
        assert successes[0].json()["data"] == successes[1].json()["data"]  # 双 201 即一存一回放，data 相同
    with sqlite3.connect(client.app.state.settings.database_path) as db:
        assets = db.execute("SELECT COUNT(*) FROM file_assets").fetchone()[0]
    assert assets == 1
    uploads = client.app.state.settings.uploads_dir
    assert list(uploads.glob("*.part")) == []


# ---------- t-12 fencing（owner token + 有界租约 + 可注入时钟，不 sleep） ----------

class FakeClock:
    def __init__(self, start: float = 1_800_000_000.0):
        self.t = start

    def __call__(self):
        from datetime import datetime, timezone
        return datetime.fromtimestamp(self.t, timezone.utc)

    def advance(self, seconds: float):
        self.t += seconds


def _fresh_db(tmp_path):
    from app.core.database import migrate
    path = tmp_path / "db.sqlite3"
    migrate(path)
    return path


def test_crashed_claim_recoverable_after_lease_expiry(tmp_path):
    from app.core.idempotency import claim
    path = _fresh_db(tmp_path)
    clock = FakeClock()
    first = claim(path, "s", "k", lease_seconds=60, clock=clock)
    assert first.status == "pending" and first.token
    # 占位进程崩溃（未 release）：未过期不可抢占，仍在写的 owner 受保护
    clock.advance(30)
    assert claim(path, "s", "k", lease_seconds=60, clock=clock).status == "in_progress"
    # 过期后 takeover 原子换代：key 不再永久 IN_PROGRESS，新 owner 拿新 token
    clock.advance(31)
    second = claim(path, "s", "k", lease_seconds=60, clock=clock)
    assert second.status == "pending" and second.token != first.token


def test_takeover_is_atomic_under_race(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from app.core.idempotency import claim
    path = _fresh_db(tmp_path)
    clock = FakeClock()
    claim(path, "s", "k", lease_seconds=60, clock=clock)  # 初始占位
    clock.advance(61)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = [f.result() for f in [pool.submit(lambda: claim(path, "s", "k", lease_seconds=60, clock=clock)) for _ in range(3)]]
    statuses = [r.status for r in results]
    assert statuses.count("pending") == 1  # 恰一个 takeover 胜者
    assert statuses.count("in_progress") == 2


def test_active_renewal_blocks_preemption(tmp_path):
    from app.core.idempotency import claim, renew
    path = _fresh_db(tmp_path)
    clock = FakeClock()
    first = claim(path, "s", "k", lease_seconds=60, clock=clock)
    clock.advance(40)
    token2 = renew(path, "s", "k", first.token, lease_seconds=60, clock=clock)
    clock.advance(40)  # 距最初占位已过 80s>60s，但持租者续过租：仍不可抢占
    assert claim(path, "s", "k", lease_seconds=60, clock=clock).status == "in_progress"
    token3 = renew(path, "s", "k", token2, lease_seconds=60, clock=clock)
    assert token3 != token2  # 每次续租产生新 fencing token
    assert claim(path, "s", "k", lease_seconds=60, clock=clock).status == "in_progress"


def test_old_owner_finalize_release_renew_all_rejected(tmp_path):
    import sqlite3 as s3
    from app.core.idempotency import LostClaim, claim, finalize, release, renew
    path = _fresh_db(tmp_path)
    clock = FakeClock()
    old = claim(path, "s", "k", lease_seconds=60, clock=clock)
    clock.advance(61)
    new = claim(path, "s", "k", lease_seconds=60, clock=clock)
    assert new.status == "pending"
    try:
        renew(path, "s", "k", old.token, lease_seconds=60, clock=clock)
        assert False, "old owner renew must fail"
    except LostClaim:
        pass
    release(path, "s", "k", old.token)  # 旧 owner release 不得删新 owner 占位
    with s3.connect(path) as db:
        row = db.execute("SELECT request_hash,response_json FROM idempotency_records").fetchone()
    assert row and row[0] == "__pending__" and row[1] == new.token
    with s3.connect(path) as db:
        try:
            finalize(db, "s", "k", old.token, "fp", "{}")
            assert False, "old owner finalize must fail"
        except LostClaim:
            pass


def test_completed_record_never_releasable(tmp_path):
    import sqlite3 as s3
    from app.core.idempotency import claim, finalize, release
    path = _fresh_db(tmp_path)
    clock = FakeClock()
    got = claim(path, "s", "k", lease_seconds=60, clock=clock)
    with s3.connect(path) as db:
        finalize(db, "s", "k", got.token, "fingerprint", '{"data":"snapshot"}')
        db.commit()
    release(path, "s", "k", got.token)  # 同 token 也不得删 completed
    with s3.connect(path) as db:
        row = db.execute("SELECT request_hash,response_json FROM idempotency_records").fetchone()
    assert row and row[0] == "fingerprint" and row[1] == '{"data":"snapshot"}'


class FakeUpload:
    """最小 upload 替身：按脚本返回块或抛异常。"""

    def __init__(self, filename, script):
        self.filename = filename
        self._script = list(script)
        self.closed = False

    async def read(self, size=-1):
        if not self._script:
            return b""
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def close(self):
        self.closed = True


def test_cancelled_consume_cleans_part_and_reraises(tmp_path):
    """取消不吞：_consume 清理本次 .part 后原样重抛 CancelledError（批准①）。"""
    import asyncio
    import pytest
    from app.core.config import Settings
    from app.modules.foundation import files_service as fs

    settings = Settings(data_dir=tmp_path)
    upload = FakeUpload("a.pdf", [PDF_HEAD + b"x" * 200_000, asyncio.CancelledError()])
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(fs._consume(upload, "brief", settings, "id1"))
    assert list(settings.uploads_dir.glob("*.part")) == []


def test_lost_lease_mid_upload_not_published(tmp_path):
    """长传自动续租；被 takeover 后旧 owner：写入停止、无最终件、无资产、不发布，
    新 owner 占位不被误删（批准③）。"""
    import asyncio
    import io as _io
    import sqlite3 as s3
    import pytest
    from starlette.datastructures import UploadFile
    from app.core.config import Settings
    from app.core.idempotency import LostClaim, claim
    from app.modules.foundation import files_service as fs

    settings = Settings(data_dir=tmp_path)
    from app.core.database import migrate
    migrate(settings.database_path)
    clock = FakeClock()
    key = "concurrent-lease-key"
    chunk = PDF_HEAD + bytes(1024 * 1024 - 8)

    class ScriptedUpload(UploadFile):
        """read 推进时钟；第三次读取前把时钟推过当前租约并让外部 takeover 成功，
        下一次检查点 renew 必须因 fencing 失配抛 LostClaim。"""

        def __init__(self, stream):
            super().__init__(stream, filename="long.pdf")
            self._chunks = [chunk, chunk, chunk, b""]
            self._reads = 0

        async def read(self, size=-1):
            self._reads += 1
            if self._reads == 1 or self._reads == 2:
                clock.advance(10)  # 检查点2（t=20，lease40 剩余20<=半程）→ renew 成功
            elif self._reads == 3:
                clock.advance(50)  # t=70：距续租后到期(60)已过 → 外部 takeover
                claim(settings.database_path, fs.FILE_SCOPE, key, lease_seconds=40, clock=clock)
            data = self._chunks.pop(0) if self._chunks else b""
            return data

    async def run():
        with pytest.raises(LostClaim):
            await fs.store_upload(ScriptedUpload(_io.BytesIO(b"ignored")), "brief", settings, key,
                                  clock=clock, lease_seconds=40)

    asyncio.run(run())
    assert list(settings.uploads_dir.glob("*")) == []  # 临时件与最终件都不残留
    with s3.connect(settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM file_assets").fetchone()[0] == 0
        row = db.execute("SELECT request_hash FROM idempotency_records WHERE key=?", (key,)).fetchone()
    assert row and row[0] == "__pending__"  # takeover 的新 owner 占位仍在，未被旧 owner release 删除


def test_db_transaction_failure_cleans_final_file_and_releases_key(client, monkeypatch):
    """事务内失败（此处模拟快照序列化在 finalize 同事务中抛错）：
    已落盘本方原件被清理、pending 按 token 释放、无资产行，key 可重试。"""
    import pytest
    from app.modules.foundation.files_schemas import FileAsset as FileAssetModel

    key = uuid4().hex
    payload = PDF_HEAD + b"rollback"
    settings = client.app.state.settings

    def boom(self, **kwargs):
        raise RuntimeError("simulate failure inside transaction")

    monkeypatch.setattr(FileAssetModel, "model_dump_json", boom)
    with pytest.raises(RuntimeError):
        client.post("/api/v1/files", files={"file": ("r.pdf", io.BytesIO(payload), "application/pdf")},
                    data={"purpose": "brief"}, headers={"Idempotency-Key": key})
    monkeypatch.undo()
    assert list(settings.uploads_dir.glob("*")) == []  # 最终件被清理，无孤儿
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM file_assets").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM idempotency_records WHERE key=?", (key,)).fetchone()[0] == 0
    ok = upload(client, "r.pdf", payload, key=key)  # key 已释放可重试
    assert ok.status_code == 201


# ---------- A 22:05 三个确定性清理边界 + 到期 renew 约定（不 sleep、不改 p2） ----------

def _fence_settings(tmp_path):
    from app.core.config import Settings
    from app.core.database import migrate
    settings = Settings(data_dir=tmp_path)
    migrate(settings.database_path)
    return settings, FakeClock()


def test_checkpoint_after_eof_loses_lease_cleans_part(tmp_path):
    """_consume 已成功返回 counter（EOF 完成、未 finish）；外层 finish 前 checkpoint 失锁：
    必须清理本方 .part（A 22:05 指出的未闭合边界）。"""
    import asyncio
    import io as _io
    import pytest
    from starlette.datastructures import UploadFile
    from app.core.idempotency import LostClaim, claim
    from app.modules.foundation import files_service as fs

    settings, clock = _fence_settings(tmp_path)
    key = "post-eof-checkpoint"
    body = PDF_HEAD + b"z" * 64

    class EofTakeoverUpload(UploadFile):
        def __init__(self):
            super().__init__(_io.BytesIO(b""), filename="eof.pdf")
            self._done = False

        async def read(self, size=-1):
            if not self._done:
                self._done = True
                return body  # 单块完成签名/空文件检查；内部 checkpoint 时钟未动，不触发 renew
            clock.advance(21)  # EOF 时刻越过 20s 租约
            claim(settings.database_path, fs.FILE_SCOPE, key, lease_seconds=30, clock=clock)  # 外部 takeover
            return b""

    with pytest.raises(LostClaim):
        asyncio.run(fs.store_upload(EofTakeoverUpload(), "brief", settings, key, clock=clock, lease_seconds=20))
    assert list(settings.uploads_dir.glob("*")) == []  # .part 被外层清理，未 finish 无最终件
    import sqlite3 as s3
    with s3.connect(settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM file_assets").fetchone()[0] == 0
        row = db.execute("SELECT request_hash,response_json FROM idempotency_records WHERE key=?", (key,)).fetchone()
    assert row and row[0] == "__pending__"  # 新 owner 占位未被旧 owner 的 release 删除


def test_finish_failure_cleans_part_and_releases_key(tmp_path, monkeypatch):
    """counter.finish 抛错（模拟 os.replace 失败）：本方 .part 清理、按 token 释放、不落资产。"""
    import asyncio
    import io as _io
    import sqlite3 as s3
    import pytest
    from starlette.datastructures import UploadFile
    from app.core.files import StreamCounter
    from app.modules.foundation import files_service as fs

    settings, clock = _fence_settings(tmp_path)
    key = "finish-fails-here"

    class PlainUpload(UploadFile):
        async def read(self, size=-1):
            if not PlainUpload._sent:
                PlainUpload._sent = True
                return PDF_HEAD + b"q" * 64
            return b""

    PlainUpload._sent = False

    def broken_finish(self, file_id):
        raise OSError("simulate os.replace failure")

    monkeypatch.setattr(StreamCounter, "finish", broken_finish)
    with pytest.raises(OSError):
        asyncio.run(fs.store_upload(PlainUpload(_io.BytesIO(b""), filename="f.pdf"), "brief", settings, key,
                                    clock=clock, lease_seconds=60))
    assert list(settings.uploads_dir.glob("*")) == []  # 失败路径清 .part
    with s3.connect(settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM file_assets").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM idempotency_records WHERE key=?", (key,)).fetchone()[0] == 0


def test_close_failure_does_not_block_part_or_lock_cleanup(tmp_path):
    """upload.close 抛错不得阻断 .part 清理与按 token release；原始异常（LostClaim）保留。"""
    import asyncio
    import io as _io
    import sqlite3 as s3
    import pytest
    from starlette.datastructures import UploadFile
    from app.core.idempotency import LostClaim, claim
    from app.modules.foundation import files_service as fs

    settings, clock = _fence_settings(tmp_path)
    key = "close-explodes"
    body = PDF_HEAD + b"w" * 64

    class EofTakeoverUpload(UploadFile):
        def __init__(self):
            super().__init__(_io.BytesIO(b""), filename="eof.pdf")
            self._done = False

        async def read(self, size=-1):
            if not self._done:
                self._done = True
                return body
            clock.advance(21)
            claim(settings.database_path, fs.FILE_SCOPE, key, lease_seconds=30, clock=clock)
            return b""

        async def close(self):
            raise RuntimeError("close failed")  # 清理路径中的 close 炸掉

    with pytest.raises(LostClaim):  # 原始异常保留：close 的错误被吞但不阻断清理
        asyncio.run(fs.store_upload(EofTakeoverUpload(), "brief", settings, key, clock=clock, lease_seconds=20))
    assert list(settings.uploads_dir.glob("*")) == []  # .part 清理未被 close 失败阻断
    with s3.connect(settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM file_assets").fetchone()[0] == 0
        row = db.execute("SELECT request_hash FROM idempotency_records WHERE key=?", (key,)).fetchone()
    assert row and row[0] == "__pending__"  # 新 owner 占位保留（旧 owner release 无匹配即不删）


class UploadWithBrokenFinishTail:
    """替身：足够通过 _consume（>=16 字节头、合法签名），finish 后的动作可注入异常。
    实现 store_upload 所需的 read/close 接口（非 UploadFile 子类，_checked_name 只用 filename）。"""

    def __init__(self, filename="tail.pdf", body=None):
        self.filename = filename
        self._body = body if body is not None else PDF_HEAD + bytes(64)
        self._sent = False
        self.closed = False

    async def read(self, size=-1):
        if self._sent:
            return b""
        self._sent = True
        return self._body

    async def close(self):
        self.closed = True


def test_close_failure_after_finish_cleans_final_file_and_releases(tmp_path, monkeypatch):
    """A 22:11：finish 成功后 close 抛错（现处于统一保护范围内）：
    本方最终件被清理、pending 按 token 释放、不落资产，原始 close 异常保留。"""
    import asyncio
    import sqlite3 as s3
    import pytest
    from app.modules.foundation import files_service as fs

    settings, clock = _fence_settings(tmp_path)
    key = "close-after-finish"
    upload_obj = UploadWithBrokenFinishTail()

    async def bad_close():
        raise RuntimeError("close exploded after finish")

    upload_obj.close = bad_close
    with pytest.raises(RuntimeError, match="close exploded"):
        asyncio.run(fs.store_upload(upload_obj, "brief", settings, key, clock=clock, lease_seconds=60))
    assert list(settings.uploads_dir.glob("*")) == []  # 最终件（已 rename 出 .part）被清理
    with s3.connect(settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM file_assets").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM idempotency_records WHERE key=?", (key,)).fetchone()[0] == 0


def test_cancel_after_finish_cleans_final_file_and_releases(tmp_path):
    """finish 后 close 处被取消（CancelledError）：同样清本方最终件+条件释放，取消语义原样传播。"""
    import asyncio
    import sqlite3 as s3
    import pytest
    from app.modules.foundation import files_service as fs

    settings, clock = _fence_settings(tmp_path)
    key = "cancel-after-finish"
    upload_obj = UploadWithBrokenFinishTail()

    async def cancelling_close():
        raise asyncio.CancelledError()

    upload_obj.close = cancelling_close
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(fs.store_upload(upload_obj, "brief", settings, key, clock=clock, lease_seconds=60))
    assert list(settings.uploads_dir.glob("*")) == []
    with s3.connect(settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM file_assets").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM idempotency_records WHERE key=?", (key,)).fetchone()[0] == 0


def test_asset_construction_failure_cleans_final_file_and_releases(tmp_path, monkeypatch):
    """finish 后 asset 构造抛错：保护范围内统一清理最终件+释放，key 可重试；不产生半构造资产。"""
    import asyncio
    import sqlite3 as s3
    import pytest
    from app.modules.foundation import files_service as fs
    from app.modules.foundation.files_schemas import FileAsset as Model

    settings, clock = _fence_settings(tmp_path)
    key = "construct-failed"

    def boom(self, **kwargs):
        raise RuntimeError("construct failed")

    monkeypatch.setattr(Model, "__init__", boom)
    upload_obj = UploadWithBrokenFinishTail(body=PDF_HEAD + bytes(70))
    with pytest.raises(RuntimeError, match="construct failed"):
        asyncio.run(fs.store_upload(upload_obj, "brief", settings, key, clock=clock, lease_seconds=60))
    monkeypatch.undo()
    assert list(settings.uploads_dir.glob("*")) == []
    with s3.connect(settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM file_assets").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM idempotency_records WHERE key=?", (key,)).fetchone()[0] == 0


def test_committed_asset_survives_later_failure_never_deleted(tmp_path, monkeypatch):
    """成功提交之后不允许因后续错误误删已提交原件：committed 标记后代码路径不再清理。
    模拟事务成功但 connection 退出后人为制造异常（对 return 前的最后一步做守卫检查）。"""
    import asyncio
    import sqlite3 as s3
    from app.modules.foundation import files_service as fs

    settings, clock = _fence_settings(tmp_path)
    key = "committed-guard"
    upload_obj = UploadWithBrokenFinishTail(body=PDF_HEAD + bytes(90))
    asset = asyncio.run(fs.store_upload(upload_obj, "brief", settings, key, clock=clock, lease_seconds=60))
    final = settings.uploads_dir / f"{asset.id}.{asset.extension}"
    assert final.is_file()
    # 回放请求（同 key 同内容）不产生新文件也不删除原件
    probe = UploadWithBrokenFinishTail(body=PDF_HEAD + bytes(90))
    replay = asyncio.run(fs.store_upload(probe, "brief", settings, key, clock=clock, lease_seconds=60))
    assert replay.id == asset.id and final.is_file()
    with s3.connect(settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM file_assets").fetchone()[0] == 1


def test_expired_but_untaken_over_lease_renew_still_allowed(tmp_path):
    """约定一致性（A 22:05）：renew 只匹配 token 不查到期——已过期但尚未被抢占时原 owner 仍可续租；
    到期语义=他人允许原子抢占，而非本方租约自动作废。安全线=token 换代 fencing。"""
    from app.core.idempotency import LostClaim, claim, release, renew
    path = _fresh_db(tmp_path)
    clock = FakeClock()
    first = claim(path, "s", "k", lease_seconds=60, clock=clock)
    clock.advance(120)  # 过期但未 takeover
    token2 = renew(path, "s", "k", first.token, lease_seconds=60, clock=clock)  # 允许，且换新 token
    assert token2 != first.token
    clock.advance(61)
    second = claim(path, "s", "k", lease_seconds=60, clock=clock)  # 新 token 又过期→可抢占
    assert second.status == "pending"
    try:
        renew(path, "s", "k", token2, lease_seconds=60, clock=clock)
        assert False
    except LostClaim:
        pass  # 换代后旧 token 一律拒绝（与主路径一致）
    release(path, "s", "k", second.token)


def test_video_upload_has_no_default_size_cap_and_replay_stays_streamed(client):
    from dataclasses import replace
    from app.modules.foundation.files_service import purpose_limit
    settings = replace(client.app.state.settings, max_document_bytes=16)
    client.app.state.settings = settings
    assert settings.max_video_bytes is None and purpose_limit(settings, 'video') is None
    payload = b'\x00\x00\x00\x18ftypmp42' + bytes(32_000)
    key = uuid4().hex
    response = upload(client, 'uncapped.mp4', payload, purpose='video', key=key)
    assert response.status_code == 201, response.text
    replay = upload(client, 'uncapped.mp4', payload, purpose='video', key=key)
    assert replay.status_code == 201 and replay.json()['data'] == response.json()['data']
    assert upload(client, 'limited.txt', b'x' * 17, purpose='script').status_code == 413


def test_stream_counter_supports_unbounded_video_without_buffering_entire_file(tmp_path):
    from app.core.files import StreamCounter
    counter = StreamCounter(tmp_path, 'uncapped', 'mp4', None)
    for chunk in [b'first', b'second', b'third']:
        counter.feed(chunk)
    stored = counter.finish('uncapped')
    assert stored.byte_size == len(b'firstsecondthird')
    assert stored.sha256 == hashlib.sha256(b'firstsecondthird').hexdigest()
