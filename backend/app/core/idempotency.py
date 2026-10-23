"""幂等原语：claim/renew/finalize/release，pending 带 owner token + 有界租约（lease）。
沿用 idempotency_records 表（scope+key 主键，0001 不改）：pending 时 request_hash 为哨兵、
response_json 存元数据 {"owner":...,"expires":...}；completed 后 response_json 仍为原响应快照。
fence=pending 元数据字符串的精确匹配（owner 为全局唯一 uuid4 token），旧 owner 无法
finalize/renew/release 覆盖新 owner。时钟可注入（clock），测试不 sleep。

租约到期约定（A 22:05 审定口径）：renew/finalize/release 只按 token 精确匹配，不校验到期——
已过期但尚未被抢占（无人 takeover）的 pending，原 owner 仍可合法续租/收尾。到期时刻的语义是
"他人自此允许原子抢占"，不是"本方租约自动作废"。主要安全线是 token 换代 fencing：一旦 takeover
发生，旧 token 的一切操作（renew/finalize/release）一律 LostClaim/无匹配，绝不作用于新 owner。"""

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app.core.database import connection

PENDING = "__pending__"
DEFAULT_LEASE_SECONDS = 120


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class LostClaim(RuntimeError):
    """本方 pending token 已失效（被 takeover 或状态已变更）：调用方必须停止写入且不得发布。"""


@dataclass(frozen=True)
class ClaimResult:
    status: str  # pending(我方持有) | completed(终态快照)
    token: str | None = None  # pending 元数据精确串，作为 fencing token
    request_hash: str | None = None
    response_json: str | None = None


def _pending_token(owner: str, expires: int) -> str:
    return json.dumps({"owner": owner, "expires": expires}, sort_keys=True)


def _parse(token: str) -> tuple[str, int]:
    data = json.loads(token)
    return str(data["owner"]), int(data["expires"])


parse_token = _parse


def _read(db: sqlite3.Connection, scope: str, key: str):
    return db.execute("SELECT request_hash, response_json FROM idempotency_records WHERE scope=? AND key=?",
                      (scope, key)).fetchone()


def claim(path: Path, scope: str, key: str, *, lease_seconds: int = DEFAULT_LEASE_SECONDS,
          clock: Callable[[], datetime] = utc_now) -> ClaimResult:
    """原子占位（BEGIN IMMEDIATE）：
    - 键不存在：以 owner=uuid4 + 有界 lease 插 pending，返回 token；
    - pending 未过期：in_progress（不可抢占，仍在写的 owner 受保护）；
    - pending 已过期：条件替换换代（新 owner），takeover 原子；
    - completed：返回指纹与快照（比对由调用方做）。"""
    now = int(clock().timestamp())
    with connection(path) as db:
        db.execute("BEGIN IMMEDIATE")
        row = _read(db, scope, key)
        if row is None:
            token = _pending_token(uuid.uuid4().hex, now + lease_seconds)
            db.execute("INSERT INTO idempotency_records VALUES (?,?,?,?,?)", (scope, key, PENDING, token,
                        clock().isoformat().replace("+00:00", "Z")))
            return ClaimResult("pending", token)
        if row["request_hash"] == PENDING:
            _owner, expires = _parse(row["response_json"])
            if expires > now:
                return ClaimResult("in_progress")
            # lease 过期：原子换代；并发 takeover 由精确匹配旧 token 决出唯一胜者。
            token = _pending_token(uuid.uuid4().hex, now + lease_seconds)
            cur = db.execute("UPDATE idempotency_records SET response_json=? WHERE scope=? AND key=? AND request_hash=? AND response_json=?",
                             (token, scope, key, PENDING, row["response_json"]))
            if cur.rowcount != 1:
                return ClaimResult("in_progress")
            return ClaimResult("pending", token)
        return ClaimResult("completed", None, row["request_hash"], row["response_json"])


def renew(path: Path, scope: str, key: str, token: str, *, lease_seconds: int = DEFAULT_LEASE_SECONDS,
          clock: Callable[[], datetime] = utc_now) -> str:
    """持租续租：仅当 pending 元数据与 token 逐字匹配才延期，返回新 token；失配抛 LostClaim。
    调用方收到 LostClaim 必须停止写入、不得发布或提交。"""
    now = int(clock().timestamp())
    owner, _ = _parse(token)
    new_token = _pending_token(owner, now + lease_seconds)
    with connection(path) as db:
        cur = db.execute("UPDATE idempotency_records SET response_json=? WHERE scope=? AND key=? AND request_hash=? AND response_json=?",
                         (new_token, scope, key, PENDING, token))
        if cur.rowcount != 1:
            raise LostClaim(f"renew lost claim: {scope}/{key}")
        return new_token


def finalize(db: sqlite3.Connection, scope: str, key: str, token: str, fingerprint: str, response_json: str) -> None:
    """与业务写入同事务收尾：仅当前持有该 pending token 的 owner 可终结，否则抛 LostClaim 令整事务回滚。"""
    cur = db.execute("UPDATE idempotency_records SET request_hash=?, response_json=? WHERE scope=? AND key=? AND request_hash=? AND response_json=?",
                     (fingerprint, response_json, scope, key, PENDING, token))
    if cur.rowcount != 1:
        raise LostClaim(f"finalize lost claim: {scope}/{key}")


def release(path: Path, scope: str, key: str, token: str) -> None:
    """失败释放：条件匹配 token 才删除本方占位；旧 token 与新 token 均不得删对方的 pending，
    completed 记录（request_hash 非哨兵）永不受影响。"""
    with connection(path) as db:
        db.execute("DELETE FROM idempotency_records WHERE scope=? AND key=? AND request_hash=? AND response_json=?",
                   (scope, key, PENDING, token))
