"""Server-side SQLite store for the weekend memory contract (Python 3.11).

Only authenticated Flask integration calls this API. Never bind ``record_source``,
``save_authorized``, ``authorized_text``, ``preference_key`` or an internal ``cmd``
directly to a client body. Timestamps are Unix seconds. Every write uses one
SQLite transaction; ownership is checked before anything else, and another user's
resource is indistinguishable from a missing one.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import re
import sqlite3
import threading
import time
import unicodedata
from uuid import uuid4


class DomainError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = status == 503


def invalid(message):
    return DomainError(400, "INVALID_ARGUMENT", message)


def missing():
    return DomainError(404, "NOT_FOUND", "记录不可访问，请刷新列表。")


def conflict(message):
    return DomainError(409, "CONFLICT", message)


def required(value, key):
    if not isinstance(value, str) or not value.strip():
        raise invalid(f"{key} 必须为非空文本。")
    return value.strip()


def identity(user_id):
    if not isinstance(user_id, str) or not user_id.strip():
        raise DomainError(401, "UNAUTHENTICATED", "请重新建立会话。")
    return user_id.strip()


def digest(*values):
    payload = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def squash(text):
    """Compare user wording leniently: full-width forms and spaces do not matter."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def preference_slot(kind, text, key):
    """Preference slots collapse a rephrased speed setting onto one record."""
    if key is not None:
        if kind != "preference":
            raise invalid("只有 preference 可带 preference_key。")
        return required(key, "preference_key")
    speed_text = text.strip("，。！？,.!? ")
    if kind == "preference" and (speed_text in {
        "慢一点", "快一点", "按正常速度", "按正常速度就行", "正常速度",
    } or any(term in speed_text for term in (
        "语速", "说慢", "说快", "说话慢", "说话快", "慢点说", "快点说",
        "正常速度说", "正常速度讲",
    ))):
        return "voice_speed"
    return None


SCHEMA = """
CREATE TABLE IF NOT EXISTS conv_sources (
    uid TEXT, sid TEXT, turn TEXT, input_id TEXT NOT NULL, body_hash TEXT NOT NULL,
    authorized INTEGER NOT NULL, authorized_text TEXT, PRIMARY KEY(uid,sid,turn),
    UNIQUE(uid,sid,input_id)
);
CREATE TABLE IF NOT EXISTS conv_memories (
    uid TEXT, id TEXT, kind TEXT NOT NULL, text TEXT NOT NULL, revision INTEGER NOT NULL,
    source TEXT NOT NULL, sid TEXT NOT NULL, turn TEXT NOT NULL, slot TEXT,
    status TEXT NOT NULL, updated_at REAL NOT NULL, expires_at REAL, PRIMARY KEY(uid,id)
);
CREATE UNIQUE INDEX IF NOT EXISTS conv_live_slot ON conv_memories(uid,slot)
    WHERE status='active' AND slot IS NOT NULL;
CREATE TABLE IF NOT EXISTS conv_drafts (
    uid TEXT, id TEXT, sid TEXT NOT NULL, turn TEXT NOT NULL, kind TEXT NOT NULL,
    text TEXT NOT NULL, slot TEXT, base_id TEXT, base_rev INTEGER,
    status TEXT NOT NULL, expires_at REAL, memory_id TEXT, confirmed_text TEXT,
    confirmed_rev INTEGER, PRIMARY KEY(uid,id)
);
CREATE TABLE IF NOT EXISTS conv_intents (
    uid TEXT, sid TEXT, turn TEXT, op TEXT, payload_hash TEXT NOT NULL, target TEXT NOT NULL,
    PRIMARY KEY(uid,sid,turn,op)
);
CREATE TABLE IF NOT EXISTS conv_request_keys (
    uid TEXT, request_id TEXT, binding TEXT NOT NULL, PRIMARY KEY(uid,request_id)
);
"""


class MemoryStore:
    def __init__(self, db_path, *, clock=time.time):
        self.clock = clock
        self._guard = threading.RLock()
        self._in_memory = str(db_path) == ":memory:"
        self._path = f"file:conversation-{uuid4().hex}?mode=memory&cache=shared" if self._in_memory else str(db_path)
        self._anchor = None
        try:
            if self._in_memory:
                self._anchor = self._connect()
            with self._transaction(write=True) as db:
                for statement in SCHEMA.split(";"):
                    if statement.strip():
                        db.execute(statement)
        except Exception:
            self.close()
            raise

    def close(self):
        if self._anchor is not None:
            self._anchor.close()
            self._anchor = None

    def _connect(self):
        db = sqlite3.connect(self._path, uri=self._in_memory, isolation_level=None,
                             timeout=5, check_same_thread=False)
        db.row_factory = sqlite3.Row
        return db

    @contextmanager
    def _transaction(self, *, write=False):
        with self._guard:
            db = None
            try:
                db = self._connect()
                db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                yield db
                db.commit()
            except sqlite3.Error as exc:
                raise DomainError(503, "MEMORY_UNAVAILABLE", "记忆库暂不可用，请稍后重试。") from exc
            finally:
                if db is not None:
                    db.close()  # rolls back any uncommitted transaction, including DomainError.

    def record_source(self, user_id, session_id, turn_id, input_id, text, *,
                      save_authorized=False, authorized_text=None):
        """Register the server-accepted final input and its already-verified grant.

        ``authorized_text`` narrows the grant to the exact words the user asked to
        keep. "记住语速慢一点，别记今天失眠。" must pass "语速慢一点" and must not
        let "今天失眠" become a stored fact. ``None`` keeps the whole utterance,
        which is only appropriate when the whole utterance is the saved object.
        """
        uid = identity(user_id)
        sid, turn, inp, original = (required(v, k) for v, k in (
            (session_id, "session_id"), (turn_id, "turn_id"), (input_id, "input_id"), (text, "text")))
        if turn == "R0" or type(save_authorized) is not bool:
            raise invalid("欢迎回合不能登记；保存授权必须是服务端布尔值。")
        if not save_authorized:
            if authorized_text is not None:
                raise invalid("未授权的输入不能绑定待存片段。")
            grant = None
        else:
            grant = original if authorized_text is None else required(authorized_text, "authorized_text")
            if squash(grant) not in squash(original):
                raise invalid("授权片段必须来自本轮原话。")
        fingerprint = digest(original)
        with self._transaction(write=True) as db:
            existing = db.execute("""SELECT * FROM conv_sources WHERE uid=? AND sid=?
                AND (turn=? OR input_id=?)""", (uid, sid, turn, inp)).fetchall()
            if existing:
                row = existing[0]
                same_grant = (row["authorized_text"] is None or grant is None
                              or row["authorized_text"] == grant)
                if (len(existing) != 1 or not same_grant or
                        (row["turn"], row["input_id"], row["body_hash"], row["authorized"]) != (
                            turn, inp, fingerprint, int(save_authorized))):
                    raise conflict("输入或来源回合已绑定其他正文或授权。")
                return
            db.execute("INSERT INTO conv_sources VALUES(?,?,?,?,?,?,?)",
                       (uid, sid, turn, inp, fingerprint, int(save_authorized), grant))

    @staticmethod
    def _public(row):
        return {"memory_id": row["id"], "kind": row["kind"], "text": row["text"], "revision": row["revision"]}

    def list_memories(self, user_id):
        uid = identity(user_id)
        with self._transaction() as db:
            rows = db.execute("""SELECT * FROM conv_memories WHERE uid=? AND status='active'
                AND (expires_at IS NULL OR expires_at>?) ORDER BY updated_at DESC, id""",
                              (uid, self.clock()))
            return [self._public(r) for r in rows]

    @staticmethod
    def _load(db, uid, ident, *, draft=False):
        sql = ("SELECT * FROM conv_drafts WHERE uid=? AND id=?" if draft
               else "SELECT * FROM conv_memories WHERE uid=? AND id=?")
        row = db.execute(sql, (uid, ident)).fetchone()
        if row is None:
            raise missing()
        return row

    @staticmethod
    def _live(row, now):
        if row["status"] != "active" or (row["expires_at"] is not None and row["expires_at"] <= now):
            raise missing()
        return row

    @staticmethod
    def _slot(db, uid, slot, now):
        if slot is None:
            return None
        return db.execute("""SELECT * FROM conv_memories WHERE uid=? AND slot=? AND status='active'
            AND (expires_at IS NULL OR expires_at>?)""", (uid, slot, now)).fetchone()

    def _commit_record(self, db, uid, sid, turn, kind, value, slot, source, expires, now):
        current = self._slot(db, uid, slot, now)
        if current:
            ident = current["id"]
            db.execute("""UPDATE conv_memories SET text=?,source=?,sid=?,turn=?,revision=revision+1,
                expires_at=?,updated_at=? WHERE uid=? AND id=?""",
                       (value, source, sid, turn, expires, now, uid, ident))
        else:
            if slot is not None:
                db.execute("""UPDATE conv_memories SET status='expired',text='' WHERE uid=? AND slot=?
                    AND status='active' AND expires_at<=?""", (uid, slot, now))
            ident = "M" + uuid4().hex
            db.execute("""INSERT INTO conv_memories VALUES(?,?,?,?,1,?,?,?,?,'active',?,?)""",
                       (uid, ident, kind, value, source, sid, turn, slot, now, expires))
        return self._public(self._load(db, uid, ident))

    def apply_memory(self, user_id, cmd):
        return self.apply_memory_with_status(user_id, cmd)[0]

    def apply_memory_with_status(self, user_id, cmd):
        """Return (internal result, status); Flask emits an empty body for 204."""
        uid = identity(user_id)
        if not isinstance(cmd, dict):
            raise invalid("cmd 必须是对象。")
        op = required(cmd.get("op"), "op")
        req = required(cmd.get("request_id"), "request_id")
        now = self.clock()
        with self._transaction(write=True) as db:
            if op in ("propose", "save"):
                sid = required(cmd.get("session_id"), "session_id")
                turn = required(cmd.get("source_turn_id"), "source_turn_id")
                src = db.execute("SELECT * FROM conv_sources WHERE uid=? AND sid=? AND turn=?",
                                 (uid, sid, turn)).fetchone()
                if not src:
                    raise missing()
                binding = digest(op, sid, turn)
                result = self._from_source(db, uid, op, cmd, src, now)
            elif op in ("confirm", "discard_draft"):
                did = required(cmd.get("draft_id"), "draft_id")
                draft = self._load(db, uid, did, draft=True)
                binding = digest(op, did)
                result = self._from_draft(db, uid, op, cmd, draft, now)
            elif op in ("update", "delete"):
                mid = required(cmd.get("memory_id"), "memory_id")
                record = self._load(db, uid, mid)
                binding = digest(op, mid, cmd.get("expected_revision") if op == "update" else None)
                result = self._from_record(db, uid, op, cmd, record, now)
            else:
                raise invalid("不支持的记忆操作。")
            previous = db.execute("SELECT binding FROM conv_request_keys WHERE uid=? AND request_id=?",
                                  (uid, req)).fetchone()
            if previous is not None and previous["binding"] != binding:
                raise conflict("请求标识已绑定另一操作。")
            db.execute("INSERT OR IGNORE INTO conv_request_keys VALUES(?,?,?)", (uid, req, binding))
            return result

    def _from_source(self, db, uid, op, cmd, src, now):
        sid, turn = src["sid"], src["turn"]
        kind = cmd.get("kind")
        if kind not in ("preference", "follow_up"):
            raise invalid("未知记忆类型。")
        field = "text" if op == "propose" else "value"
        value = required(cmd.get(field), field)
        expires = cmd.get("expires_at")
        if expires is not None and (type(expires) not in (int, float) or not math.isfinite(expires)):
            raise invalid("expires_at 必须是有限 Unix 秒数。")
        slot = preference_slot(kind, value, cmd.get("preference_key"))
        fp = digest(kind, value, slot, expires)
        prior = db.execute("SELECT * FROM conv_intents WHERE uid=? AND sid=? AND turn=? AND op=?",
                           (uid, sid, turn, op)).fetchone()
        if prior:
            if prior["payload_hash"] != fp:
                raise conflict("相同来源和操作的正文发生变化。")
            ident = prior["target"]
            if op == "save":
                return self._public(self._live(self._load(db, uid, ident), now)), 200
            draft = self._load(db, uid, ident, draft=True)
            if draft["status"] != "pending":
                raise conflict("草稿已处理。")
            if draft["expires_at"] is not None and draft["expires_at"] <= now:
                raise missing()
            return {"draft_id": ident}, 200
        if expires is not None and expires <= now:
            raise invalid("新记录已过期。")
        if op == "save":
            # The grant is bound to the verified excerpt, not merely to the round
            # containing "记住", so an explicitly excluded item cannot be stored.
            if (not src["authorized"] or not src["authorized_text"]
                    or squash(value) not in squash(src["authorized_text"])):
                raise DomainError(403, "SAVE_NOT_AUTHORIZED",
                                  "需从正式来源核实保存授权与原文，改写内容请先提交草稿。")
            result = self._commit_record(db, uid, sid, turn, kind, value, slot, "user_authorized", expires, now)
            ident = result["memory_id"]
            db.execute("UPDATE conv_sources SET authorized_text=NULL WHERE uid=? AND sid=? AND turn=?",
                       (uid, sid, turn))
        else:
            ident = "D" + uuid4().hex
            current = self._slot(db, uid, slot, now)
            db.execute("""INSERT INTO conv_drafts(uid,id,sid,turn,kind,text,slot,base_id,base_rev,status,expires_at)
                VALUES(?,?,?,?,?,?,?,?,?,'pending',?)""",
                       (uid, ident, sid, turn, kind, value, slot, current["id"] if current else None,
                        current["revision"] if current else None, expires))
            result = {"draft_id": ident}
        db.execute("INSERT INTO conv_intents VALUES(?,?,?,?,?,?)",
                   (uid, sid, turn, op, fp, ident))
        return result, 201

    def _from_draft(self, db, uid, op, cmd, draft, now):
        did, state = draft["id"], draft["status"]
        if op == "discard_draft":
            if state == "confirmed":
                raise conflict("草稿已确认。")
            db.execute("UPDATE conv_drafts SET status='discarded',text='' WHERE uid=? AND id=?", (uid, did))
            return {"draft_id": did}, 204
        if state == "discarded":
            raise conflict("草稿已丢弃。")
        if state == "confirmed":
            self._live(self._load(db, uid, draft["memory_id"]), now)
        elif draft["expires_at"] is not None and draft["expires_at"] <= now:
            raise missing()
        value = required(cmd.get("value", draft["text"]), "value")
        if state == "confirmed":
            if value != draft["confirmed_text"]:
                raise conflict("草稿已用其他正文确认。")
            return {"memory_id": draft["memory_id"], "kind": draft["kind"], "text": value,
                    "revision": draft["confirmed_rev"]}, 200
        current = self._slot(db, uid, draft["slot"], now)
        active_version = (current["id"], current["revision"]) if current else (None, None)
        if active_version != (draft["base_id"], draft["base_rev"]):
            raise conflict("偏好在草稿创建后已变更。")
        result = self._commit_record(db, uid, draft["sid"], draft["turn"], draft["kind"], value,
                                     draft["slot"], "user_confirmed", draft["expires_at"], now)
        db.execute("""UPDATE conv_drafts SET status='confirmed',memory_id=?,confirmed_text=?,
            confirmed_rev=? WHERE uid=? AND id=?""",
                   (result["memory_id"], value, result["revision"], uid, did))
        return result, 201

    def _from_record(self, db, uid, op, cmd, row, now):
        mid = row["id"]
        if op == "delete":
            if row["status"] != "deleted":
                self._live(row, now)
                db.execute("UPDATE conv_memories SET status='deleted',text='',updated_at=? WHERE uid=? AND id=?",
                           (now, uid, mid))
                db.execute("UPDATE conv_drafts SET text='',confirmed_text=NULL WHERE uid=? AND memory_id=?",
                           (uid, mid))
                # A stale client must not revive a withdrawn slot or original source
                # by confirming a draft which was pending at deletion time.
                db.execute("""UPDATE conv_drafts SET status='discarded',text='' WHERE uid=? AND status='pending'
                    AND (base_id=? OR (slot IS NOT NULL AND slot=?) OR (sid=? AND turn=? AND kind=?))""",
                           (uid, mid, row["slot"], row["sid"], row["turn"], row["kind"]))
            return {"memory_id": mid}, 204
        self._live(row, now)
        rev = cmd.get("expected_revision")
        if type(rev) is not int or rev < 1:
            raise invalid("expected_revision 必须是正整数。")
        if rev != row["revision"]:
            raise DomainError(409, "REVISION_CONFLICT", "记忆已更新，请刷新后重试。")
        value = required(cmd.get("value"), "value")
        db.execute("""UPDATE conv_memories SET text=?,revision=revision+1,source='user_edited',updated_at=?
            WHERE uid=? AND id=? AND revision=?""", (value, now, uid, mid, rev))
        return self._public(self._load(db, uid, mid)), 200