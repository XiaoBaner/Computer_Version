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


def body(value):
    """A stored memory item is a short phrase, not a paragraph."""
    text = required(value, "value")
    if len(text) > 300 or any(unicodedata.category(ch).startswith("C") for ch in text):
        raise invalid("记忆正文必须为 1–300 字且不含控制字符。")
    return text


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
    """Collapse a rephrased setting onto one record.

    A known category (speed, address, language, length) gets a stable slot, so
    "叫我小王" followed by "叫我老王" replaces instead of duplicating. Anything
    else stays without a slot: the server cannot tell two unrelated preferences
    apart, so Flask must pass ``preference_key`` for those (see README).
    """
    if key is not None:
        if kind != "preference":
            raise invalid("只有 preference 可带 preference_key。")
        return required(key, "preference_key")
    if kind != "preference":
        return None
    value = text.strip("，。！？,.!? ")
    if (value in {"慢一点", "快一点", "按正常速度", "按正常速度就行", "正常速度"}
            or re.fullmatch(r"(?:再|稍微|请|麻烦)?(?:慢|快)(?:一点|点|一些|些|儿)?", value)
            or any(term in value for term in (
                "语速", "说慢", "说快", "说话慢", "说话快", "慢点说", "快点说",
                "正常速度说", "正常速度讲"))):
        return "voice_speed"
    # Only a request to use a name should replace a confirmed address.
    # Descriptions ("室友叫我老师") and reminders ("叫我起床") are separate items.
    address = re.fullmatch(
        r"(?:我希望[你您]?|以后|今后|从现在起|[你您](?:就|可以|以后|直接|还是)|请|麻烦|就|都|直接|还是)*"
        r"(?:叫我|称呼我(?:为)?)\s*([^，。！？,!?；;]+)", value)
    if address and not re.match(r"(?:(?:按时|准时|早点|记得|去)\s*)?(?:起床|吃饭|吃药|上课|开会|交作业|出门|回家)",
                                address[1].strip()):
        return "address_name"
    if any(term in value for term in ("字幕", "字幕开关", "显示文字", "关掉字幕", "打开字幕")):
        return "subtitle"
    if any(term in value for term in ("中文", "英文", "英语", "普通话", "语言", "说中文", "说英文")):
        return "language"
    if any(term in value for term in ("短句", "长句", "简短", "简洁", "少说", "多说")):
        return "sentence_length"
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
CREATE TABLE IF NOT EXISTS conv_slot_order (
    uid TEXT, slot TEXT, last_source_order INTEGER NOT NULL, PRIMARY KEY(uid,slot)
);
CREATE TABLE IF NOT EXISTS conv_item_withdrawals (
    uid TEXT, kind TEXT, item_hash TEXT, last_source_order INTEGER NOT NULL,
    PRIMARY KEY(uid,kind,item_hash)
);
"""


_REQUIRED = object()


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
                self._upgrade_sources(db)
        except Exception:
            self.close()
            raise

    def _upgrade_sources(self, db):
        """One transaction adds a stable input order and reconciles v0 preferences."""
        if any(r["name"] == "accepted_order" for r in db.execute("PRAGMA table_info(conv_sources)")):
            try:
                db.execute("CREATE UNIQUE INDEX IF NOT EXISTS conv_accepted_order ON conv_sources(accepted_order)")
            except sqlite3.IntegrityError as exc:
                raise DomainError(503, "MEMORY_MIGRATION_CONFLICT",
                                  "记忆库存在重复的来源顺序，需从备份恢复或由维护者处理。") from exc
            return
        db.execute("ALTER TABLE conv_sources ADD COLUMN accepted_order INTEGER")
        # Old sources were inserted on final-input acceptance and never replaced.
        # Old pending grants may stand for a whole utterance. Only a new explicit
        # grant or a user-confirmed draft may authorize further writes.
        db.execute("UPDATE conv_sources SET accepted_order=rowid, authorized_text=NULL")
        db.execute("CREATE UNIQUE INDEX conv_accepted_order ON conv_sources(accepted_order)")

        groups = {}
        rows = db.execute("""SELECT m.*, s.accepted_order FROM conv_memories m
            JOIN conv_sources s ON (s.uid,s.sid,s.turn)=(m.uid,m.sid,m.turn)
            WHERE m.kind='preference'
            ORDER BY (m.status='active' AND (m.expires_at IS NULL OR m.expires_at>?)) DESC,
            m.updated_at DESC, s.accepted_order DESC, m.rowid DESC""", (self.clock(),))
        for row in rows:
            slot = row["slot"] or preference_slot("preference", row["text"], None)
            if slot is not None:
                groups.setdefault((row["uid"], slot), []).append(row)
        for (uid, slot), records in groups.items():
            winner = records[0]
            # Deletion of one ID does not withdraw another still-valid confirmed
            # ID. Release the old unique index entry before assigning its slot.
            for older in records[1:]:
                if older["status"] == "active":
                    db.execute("""UPDATE conv_memories SET status='superseded'
                        WHERE uid=? AND id=?""", (uid, older["id"]))
            db.execute("UPDATE conv_memories SET slot=? WHERE uid=? AND id=?", (slot, uid, winner["id"]))
            self._mark_slot(db, uid, slot, max(r["accepted_order"] for r in records))

        drafts = db.execute("""SELECT d.*, s.accepted_order FROM conv_drafts d
            JOIN conv_sources s ON (s.uid,s.sid,s.turn)=(d.uid,d.sid,d.turn)
            WHERE d.status='pending' AND d.kind='preference' AND d.slot IS NULL""").fetchall()
        for draft in drafts:
            slot = preference_slot("preference", draft["text"], None)
            if slot is None:
                continue
            base_id, base_rev = None, None
            records = groups.get((draft["uid"], slot), [])
            if records:
                previous = records[0]
                if (previous["accepted_order"] < draft["accepted_order"]
                        and previous["status"] == "active" and previous["source"] != "user_edited"
                        and (previous["expires_at"] is None or previous["expires_at"] > self.clock())):
                    base_id, base_rev = previous["id"], previous["revision"]
            db.execute("""UPDATE conv_drafts SET slot=?,base_id=?,base_rev=?
                WHERE uid=? AND id=?""", (slot, base_id, base_rev, draft["uid"], draft["id"]))

    @staticmethod
    def _mark_slot(db, uid, slot, order):
        db.execute("""INSERT INTO conv_slot_order(uid,slot,last_source_order) VALUES(?,?,?)
            ON CONFLICT(uid,slot) DO UPDATE SET
            last_source_order=MAX(last_source_order,excluded.last_source_order)""", (uid, slot, order))

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
                      save_authorized=False, authorized_text=_REQUIRED):
        """Register the server-accepted final input and its already-verified grant.

        ``authorized_text`` is the exact excerpt the user asked to keep, and it is
        required whenever ``save_authorized`` is true. That is deliberate: a grant
        that is not bound to words cannot stop "记住语速慢一点，别记今天失眠" from
        storing "今天失眠", and a silent whole-utterance default would do exactly
        that. Callers that only know a preference is approved (not which sentence
        says so) must use ``propose`` + ``confirm`` instead of ``save``.
        """
        uid = identity(user_id)
        sid, turn, inp, original = (required(v, k) for v, k in (
            (session_id, "session_id"), (turn_id, "turn_id"), (input_id, "input_id"), (text, "text")))
        if turn == "R0" or type(save_authorized) is not bool:
            raise invalid("欢迎回合不能登记；保存授权必须是服务端布尔值。")
        if not save_authorized:
            if authorized_text not in (_REQUIRED, None):
                raise invalid("未授权的输入不能绑定待存片段。")
            grant = None
        else:
            if authorized_text is _REQUIRED or authorized_text is None or not str(authorized_text).strip():
                raise DomainError(403, "SAVE_NOT_AUTHORIZED",
                                  "服务端未绑定本轮授权片段：record_source 必须传 authorized_text"
                                  "（整轮即片段时传 text）；只有确认过的草稿可直接写入。")
            grant = required(authorized_text, "authorized_text")
            if squash(grant) not in squash(original):
                raise invalid("授权片段必须来自本轮原话。")
            # "别记今天失眠" is an exclusion, never a grant: accepting it would let
            # the very thing the user refused be stored verbatim.
            if re.search(r"(别|不要|不用|无需|不需要|不准)(?:再)?(?:记|保存|存|写)", squash(grant)):
                raise invalid("授权片段不能是用户要求排除的内容。")
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
            db.execute("""INSERT INTO conv_sources
                (uid,sid,turn,input_id,body_hash,authorized,authorized_text,accepted_order)
                VALUES(?,?,?,?,?,?,?,(SELECT COALESCE(MAX(accepted_order),0)+1 FROM conv_sources))""",
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

    @staticmethod
    def _check_withdrawal(db, uid, sid, turn, kind, value):
        blocked = db.execute("""SELECT 1 FROM conv_item_withdrawals w JOIN conv_sources s
            ON s.uid=w.uid AND s.accepted_order<=w.last_source_order
            WHERE w.uid=? AND w.kind=? AND w.item_hash=? AND s.sid=? AND s.turn=?""",
            (uid, kind, digest(uid, kind, squash(value)), sid, turn)).fetchone()
        if blocked:
            raise conflict("这条授权早于用户撤回该事项，请从新的明确授权开始。")

    def _commit_record(self, db, uid, sid, turn, kind, value, slot, source, expires, now):
        if slot is not None:
            order = db.execute("SELECT accepted_order FROM conv_sources WHERE uid=? AND sid=? AND turn=?",
                               (uid, sid, turn)).fetchone()["accepted_order"]
            previous = db.execute("SELECT last_source_order FROM conv_slot_order WHERE uid=? AND slot=?",
                                  (uid, slot)).fetchone()
            if previous and order <= previous["last_source_order"]:
                raise conflict("此偏好已有较新的决定，请按用户最近的要求处理。")
            self._mark_slot(db, uid, slot, order)
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
        with self._transaction(write=True) as db:
            now = self.clock()
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
        value = body(cmd.get(field))
        expires = cmd.get("expires_at")
        if expires is not None and (type(expires) not in (int, float) or not math.isfinite(expires)):
            raise invalid("expires_at 必须是有限 Unix 秒数。")
        slot = preference_slot(kind, value, cmd.get("preference_key"))
        fp = digest(kind, value, slot, expires)
        prior = db.execute("SELECT * FROM conv_intents WHERE uid=? AND sid=? AND turn=? AND op=?",
                           (uid, sid, turn, op)).fetchone()
        if prior:
            if op == "save" and prior["target"] == "revoked":
                raise DomainError(403, "SAVE_NOT_AUTHORIZED", "该回合的保存授权已撤回。")
            legacy_unslotted = (cmd.get("preference_key") is None and slot is not None
                                and prior["payload_hash"] == digest(kind, value, None, expires))
            if prior["payload_hash"] != fp and not legacy_unslotted:
                raise conflict("相同来源和操作的正文发生变化。")
            ident = prior["target"]
            if op == "save":
                return self._public(self._live(self._load(db, uid, ident), now)), 200
            draft = self._load(db, uid, ident, draft=True)
            if draft["status"] != "pending":
                raise conflict("草稿已处理。")
            if draft["expires_at"] is not None and draft["expires_at"] <= now:
                raise missing()
            self._check_withdrawal(db, uid, sid, turn, kind, value)
            return {"draft_id": ident}, 200
        if expires is not None and expires <= now:
            raise invalid("新记录已过期。")
        # Verify the grant before looking up withdrawal state. Unapproved input
        # must return the same authorization error for known and unknown items.
        if op == "save" and (not src["authorized"] or not src["authorized_text"]
                             or squash(src["authorized_text"]) != squash(value)):
            raise DomainError(403, "SAVE_NOT_AUTHORIZED",
                              "需从正式来源核实保存授权与原文，改写内容请先提交草稿。")
        self._check_withdrawal(db, uid, sid, turn, kind, value)
        if op == "save":
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

    def _revoke_source(self, db, uid, sid, turn):
        """Withdraw the round's save grant and tombstone any late ``save``.

        Without this, "confirm → delete → late save" recreates the record on the
        same source turn: the grant is still stored, and ``save`` is a different
        idempotency key from ``confirm``.
        """
        db.execute("UPDATE conv_sources SET authorized_text=NULL WHERE uid=? AND sid=? AND turn=?",
                   (uid, sid, turn))
        db.execute("INSERT OR IGNORE INTO conv_intents VALUES(?,?,?,?,?,?)",
                   (uid, sid, turn, "save", "*", "revoked"))

    def _from_draft(self, db, uid, op, cmd, draft, now):
        did, state = draft["id"], draft["status"]
        if op == "discard_draft":
            if state == "confirmed":
                raise conflict("草稿已确认。")
            db.execute("UPDATE conv_drafts SET status='discarded',text='' WHERE uid=? AND id=?", (uid, did))
            self._revoke_source(db, uid, draft["sid"], draft["turn"])
            return {"draft_id": did}, 204
        if state == "discarded":
            raise conflict("草稿已丢弃。")
        if state == "confirmed":
            self._live(self._load(db, uid, draft["memory_id"]), now)
        elif draft["expires_at"] is not None and draft["expires_at"] <= now:
            raise missing()
        value = body(cmd.get("value", draft["text"]))
        if state == "confirmed":
            if value != draft["confirmed_text"]:
                raise conflict("草稿已用其他正文确认。")
            return {"memory_id": draft["memory_id"], "kind": draft["kind"], "text": value,
                    "revision": draft["confirmed_rev"]}, 200
        self._check_withdrawal(db, uid, draft["sid"], draft["turn"], draft["kind"], draft["text"])
        if value != draft["text"]:
            self._check_withdrawal(db, uid, draft["sid"], draft["turn"], draft["kind"], value)
        current = self._slot(db, uid, draft["slot"], now)
        active_version = (current["id"], current["revision"]) if current else (None, None)
        if active_version != (draft["base_id"], draft["base_rev"]):
            raise conflict("偏好在草稿创建后已变更。")
        # Editing the text must not smuggle the draft into another slot: a speed
        # draft rewritten to "打开字幕" would otherwise overwrite the speed record.
        if preference_slot(draft["kind"], draft["text"], None) != preference_slot(draft["kind"], value, None):
            raise invalid("草稿改成了别的偏好类别，请重新提交草稿。")
        result = self._commit_record(db, uid, draft["sid"], draft["turn"], draft["kind"], value,
                                     draft["slot"], "user_confirmed", draft["expires_at"], now)
        db.execute("""UPDATE conv_drafts SET status='confirmed',memory_id=?,confirmed_text=?,
            confirmed_rev=? WHERE uid=? AND id=?""",
                   (result["memory_id"], value, result["revision"], uid, did))
        self._revoke_source(db, uid, draft["sid"], draft["turn"])
        return result, 201

    def _from_record(self, db, uid, op, cmd, row, now):
        mid = row["id"]
        if op == "delete":
            if row["status"] != "deleted":
                # An owner can erase a retained expired or superseded item by ID.
                if row["status"] not in {"active", "superseded"}:
                    raise missing()
                # An earlier matching grant may still be queued in another
                # session. Only mark sources accepted through this withdrawal.
                db.execute("""INSERT INTO conv_item_withdrawals(uid,kind,item_hash,last_source_order)
                    SELECT ?,?,?,MAX(accepted_order) FROM conv_sources WHERE uid=?
                    ON CONFLICT(uid,kind,item_hash) DO UPDATE SET
                    last_source_order=MAX(last_source_order,excluded.last_source_order)""",
                    (uid, row["kind"], digest(uid, row["kind"], squash(row["text"])), uid))
                db.execute("UPDATE conv_memories SET status='deleted',text='',updated_at=? WHERE uid=? AND id=?",
                           (now, uid, mid))
                db.execute("UPDATE conv_drafts SET text='',confirmed_text=NULL WHERE uid=? AND memory_id=?",
                           (uid, mid))
                # A stale client must not revive a withdrawn slot or original source
                # by confirming a draft which was pending at deletion time.
                db.execute("""UPDATE conv_drafts SET status='discarded',text='' WHERE uid=? AND status='pending'
                    AND (base_id=? OR (slot IS NOT NULL AND slot=?) OR (sid=? AND turn=? AND kind=?))""",
                           (uid, mid, row["slot"] if row["status"] == "active" else None,
                            row["sid"], row["turn"], row["kind"]))
                self._revoke_source(db, uid, row["sid"], row["turn"])
                if row["status"] == "active":
                    self._mark_page_edit(db, uid, row["slot"])
            return {"memory_id": mid}, 204
        self._live(row, now)
        rev = cmd.get("expected_revision")
        if type(rev) is not int or rev < 1:
            raise invalid("expected_revision 必须是正整数。")
        if rev != row["revision"]:
            raise DomainError(409, "REVISION_CONFLICT", "记忆已更新，请刷新后重试。")
        value = body(cmd.get("value"))
        if preference_slot(row["kind"], row["text"], None) != preference_slot(row["kind"], value, None):
            raise invalid("要改为其他偏好类别，请单独创建并确认该项。")
        db.execute("""UPDATE conv_memories SET text=?,revision=revision+1,source='user_edited',updated_at=?
            WHERE uid=? AND id=? AND revision=?""", (value, now, uid, mid, rev))
        self._mark_page_edit(db, uid, row["slot"])
        return self._public(self._load(db, uid, mid)), 200

    def _mark_page_edit(self, db, uid, slot):
        # A user-initiated PATCH/DELETE supersedes every already accepted input
        # for this slot, including a write which is still queued on another worker.
        if slot is not None:
            order = db.execute("SELECT MAX(accepted_order) FROM conv_sources WHERE uid=?", (uid,)).fetchone()[0]
            self._mark_slot(db, uid, slot, order)
