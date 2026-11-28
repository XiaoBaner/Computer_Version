"""Synthetic identities only. Every test uses a real, private temporary SQLite file."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from conversation.memory import DomainError, MemoryStore


A = "student-A"
B = "student-B"
SESSION = "session-a"
WISH = "下次继续练答辩"


def source(store, *, value=WISH, number=1, grant=True, uid=A, authorized_text=None):
    text = "记住，" + value if grant else value
    # The grant must now name the excerpt; default it to the value being saved.
    excerpt = authorized_text if authorized_text is not None else (value if grant else None)
    store.record_source(uid, SESSION, f"R{number}", f"asr-{number}", text,
                        save_authorized=grant, authorized_text=excerpt)


def from_source(op, number=1, value=WISH, kind="follow_up", **extra):
    field = "text" if op == "propose" else "value"
    return {"op": op, "session_id": SESSION, "source_turn_id": f"R{number}",
            "kind": kind, field: value, "request_id": f"{SESSION}-R{number}-{op}", **extra}


def fail(status, operation):
    with pytest.raises(DomainError) as exc:
        operation()
    assert exc.value.status == status


@pytest.fixture
def path(tmp_path):
    return tmp_path / "two-users.sqlite"


@pytest.fixture
def store(path):
    return MemoryStore(path)


def test_M01_final_asr_explicit_save_and_fresh_session(path, store):
    # ASR and Flask are emulated up to their final-turn callback. No microphone.
    assert store.list_memories(A) == []
    source(store)
    record = store.apply_memory(A, from_source("save"))
    assert record == dict(memory_id=record["memory_id"], kind="follow_up", text=WISH, revision=1)
    store.close()
    assert MemoryStore(path).list_memories(A) == [record]


def test_M02_source_replay_once_and_changed_payload_conflicts(store):
    source(store)
    command = from_source("save")
    first = store.apply_memory(A, command)
    assert store.apply_memory(A, command) == first
    fail(409, lambda: store.apply_memory(A, {**command, "value":"另外一种约定"}))
    assert store.list_memories(A) == [first]


def test_M03_correction_replaces_preference_and_fresh_context_uses_latest(path, store):
    source(store, value="语速慢一点")
    old = store.apply_memory(A, from_source("save", value="语速慢一点", kind="preference"))
    source(store, number=2, value="按正常速度")
    new = store.apply_memory(A, from_source("save", 2, value="按正常速度", kind="preference"))
    assert old["memory_id"] == new["memory_id"] and new["revision"] == 2
    assert MemoryStore(path).list_memories(A) == [new]


def test_M04_delete_restart_and_late_save_replay_cannot_resurrect(path, store):
    source(store)
    saved = store.apply_memory(A, from_source("save"))
    command = dict(op="delete", memory_id=saved["memory_id"], request_id="delete-1")
    assert store.apply_memory_with_status(A, command)[1] == 204
    assert store.apply_memory_with_status(A, command)[1] == 204
    fresh = MemoryStore(path)
    assert fresh.list_memories(A) == []
    fail(404, lambda: fresh.apply_memory(A, from_source("save")))
    assert fresh.list_memories(A) == []


def test_M05_expiry_uses_clock_and_filters_at_boundary(path):
    now = [1000.0]
    local = MemoryStore(path, clock=lambda: now[0])
    source(local)
    command = from_source("save", expires_at=1005)
    local.apply_memory(A, command)
    assert local.list_memories(A)
    now[0] = 1005
    assert local.list_memories(A) == []
    fail(404, lambda: local.apply_memory(A, command))


def test_M06_draft_edit_confirm_replay_and_body_conflict(store):
    source(store, grant=False)
    draft = store.apply_memory(A, from_source("propose"))
    assert store.list_memories(A) == []
    cmd = dict(op="confirm", **draft, value="下次练答辩结尾", request_id="confirm-1")
    first, status = store.apply_memory_with_status(A, cmd)
    assert status == 201 and first["text"] == cmd["value"]
    assert store.apply_memory_with_status(A, cmd) == (first, 200)
    fail(409, lambda: store.apply_memory(A, {**cmd, "value":"改成另一项"}))
    assert store.list_memories(A) == [first]


def test_M07_patch_stale_revision_even_after_same_request_replay(store):
    source(store)
    rec = store.apply_memory(A, from_source("save"))
    cmd = dict(op="update", memory_id=rec["memory_id"], value="改练第二句",
               expected_revision=1, request_id="patch-1")
    changed = store.apply_memory(A, cmd)
    assert changed["revision"] == 2
    fail(409, lambda: store.apply_memory(A, cmd))
    assert store.list_memories(A) == [changed]


def test_M08_foreign_ids_never_expose_source_draft_or_memory(store):
    source(store)
    draft = store.apply_memory(A, from_source("propose"))
    memory = store.apply_memory(A, from_source("save"))
    assert store.list_memories(B) == []
    attempts = [
        from_source("save"),
        dict(op="confirm", **draft, request_id="confirm-foreign"),
        dict(op="discard_draft", **draft, request_id="discard-foreign"),
        dict(op="delete", memory_id=memory["memory_id"], request_id="delete-foreign"),
        dict(op="update", memory_id=memory["memory_id"], expected_revision=1,
             value="冒名改写", request_id="edit-foreign"),
    ]
    for cmd in attempts:
        fail(404, lambda: store.apply_memory(B, cmd))
    assert store.list_memories(A) == [memory]


def test_M09_no_source_no_grant_and_camera_guesses(store):
    fail(404, lambda: store.apply_memory(A, from_source("save")))
    source(store, grant=False)
    fail(403, lambda: store.apply_memory(A, from_source("save")))
    source(store, number=2)
    fail(403, lambda: store.apply_memory(A, from_source("save", 2, "看起来很焦虑")))
    assert store.list_memories(A) == []


def test_M10_concurrent_confirm_vs_discard_only_one_commits(path, store):
    source(store)
    draft = store.apply_memory(A, from_source("propose"))
    barrier = Barrier(2)

    def submit(op):
        db = MemoryStore(path)
        barrier.wait(timeout=10)
        try:
            return db.apply_memory_with_status(A, dict(op=op, **draft, request_id=op))[1]
        except DomainError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(submit, ["confirm", "discard_draft"]))
    assert statuses in ([201, 409], [409, 204])
    assert len(store.list_memories(A)) == (statuses[0] == 201)


def test_M11_discard_repeat_and_confirm_after_discard(store):
    source(store)
    draft = store.apply_memory(A, from_source("propose"))
    cmd = dict(op="discard_draft", **draft, request_id="discard")
    assert store.apply_memory_with_status(A, cmd)[1] == 204
    assert store.apply_memory_with_status(A, cmd)[1] == 204
    fail(409, lambda: store.apply_memory(A, dict(op="confirm", **draft, request_id="confirm")))
    assert store.list_memories(A) == []


def test_M12_opening_has_no_user_source(store):
    fail(400, lambda: store.record_source(A, SESSION, "R0", None, "你好"))
    assert store.list_memories(A) == []


def test_M13_backend_outage_is_retryable_not_empty_state(tmp_path):
    with pytest.raises(DomainError) as exc:
        MemoryStore(tmp_path / "missing-directory" / "unavailable.sqlite")
    assert exc.value.status == 503 and exc.value.retryable


def test_M19_grant_binds_the_requested_item_not_the_whole_round(store):
    """"记住语速慢一点，别记今天失眠。" may store only the speed setting."""
    store.record_source(A, SESSION, "R1", "asr-1", "记住，语速慢一点，别记今天失眠。",
                        save_authorized=True, authorized_text="语速慢一点")
    fail(403, lambda: store.apply_memory(
        A, from_source("save", value="今天失眠", kind="preference")))
    assert store.list_memories(A) == []
    saved = store.apply_memory(A, from_source("save", value="语速慢一点", kind="preference"))
    assert saved["text"] == "语速慢一点" and saved["kind"] == "preference"
    assert store.list_memories(A) == [saved]


def test_M22_save_requires_an_excerpt_even_when_authorized(store):
    """漏传授权片段必须显式失败，不能默默把整轮当授权。"""
    fail(403, lambda: store.record_source(
        A, SESSION, "R1", "asr-1", "记住，语速慢一点，别记今天失眠。", save_authorized=True))
    assert store.list_memories(A) == []


def test_M23_same_preference_category_replaces_instead_of_duplicating(store):
    """改口"叫我小王"→"叫我老王"必须替换，不是新增第二条。"""
    source(store, value="叫我小王", number=1, authorized_text="叫我小王")
    first = store.apply_memory(A, from_source("save", number=1, kind="preference", value="叫我小王"))
    source(store, value="叫我老王", number=2, authorized_text="叫我老王")
    second = store.apply_memory(A, from_source("save", number=2, kind="preference", value="叫我老王"))
    assert first["memory_id"] == second["memory_id"]
    assert second["revision"] == 2
    assert [m["text"] for m in store.list_memories(A)] == ["叫我老王"]


def test_M24_unrelated_preference_categories_coexist(store):
    """称呼、语言、语速是三个槽位，不能互相覆盖。"""
    for number, value in enumerate(("叫我小王", "用中文", "语速慢一点"), start=1):
        source(store, value=value, number=number, authorized_text=value)
        store.apply_memory(A, from_source("save", number=number, kind="preference", value=value))
    assert sorted(m["text"] for m in store.list_memories(A)) == sorted(["叫我小王", "用中文", "语速慢一点"])


def test_M25_emergency_phrase_is_not_an_address_preference(store):
    """'别叫救护车' 不是称呼偏好，不能和'叫我小王'共用一个槽位。"""
    for number, value in enumerate(("别叫救护车", "叫我小王"), start=1):
        source(store, value=value, number=number, authorized_text=value)
        store.apply_memory(A, from_source("save", number=number, kind="preference", value=value))
    assert sorted(m["text"] for m in store.list_memories(A)) == sorted(["别叫救护车", "叫我小王"])


def test_M26_negated_excerpt_cannot_authorize_its_opposite(store):
    """'不喜欢慢速' 不能授权 '喜欢慢速'，'别记今天失眠' 不能授权 '今天失眠'。"""
    store.record_source(A, SESSION, "R1", "asr-1", "记住，我不喜欢慢速。",
                        save_authorized=True, authorized_text="不喜欢慢速")
    fail(403, lambda: store.apply_memory(
        A, from_source("save", number=1, kind="preference", value="喜欢慢速")))
    store.record_source(A, SESSION, "R2", "asr-2", "记住，别记今天失眠。",
                        save_authorized=True, authorized_text="别记今天失眠")
    fail(403, lambda: store.apply_memory(
        A, from_source("save", number=2, kind="follow_up", value="今天失眠")))
    assert store.list_memories(A) == []


def test_M27_late_save_after_confirm_and_delete_cannot_resurrect(store):
    source(store, number=1)
    draft = store.apply_memory(A, from_source("propose", number=1, text=WISH))
    record = store.apply_memory(A, from_source("confirm", number=1, draft_id=draft["draft_id"]))
    store.apply_memory(A, from_source("delete", number=1, memory_id=record["memory_id"]))
    fail(403, lambda: store.apply_memory(A, from_source("save", number=1)))
    assert store.list_memories(A) == []


def test_M28_discarded_draft_revokes_the_round_grant(store):
    source(store, number=1)
    draft = store.apply_memory(A, from_source("propose", number=1, text=WISH))
    store.apply_memory(A, from_source("discard_draft", number=1, draft_id=draft["draft_id"]))
    fail(403, lambda: store.apply_memory(A, from_source("save", number=1)))
    assert store.list_memories(A) == []


def test_M29_draft_edit_cannot_move_to_another_slot(store):
    """语速草稿被改成"打开字幕"时，不能沿用语速槽覆盖原记录。"""
    source(store, value="语速慢一点", number=1)
    draft = store.apply_memory(A, from_source("propose", number=1, kind="preference", text="语速慢一点"))
    fail(400, lambda: store.apply_memory(
        A, from_source("confirm", number=1, kind="preference", value="打开字幕", draft_id=draft["draft_id"])))
    record = store.apply_memory(A, from_source("confirm", number=1, kind="preference",
                                               value="语速慢一点", draft_id=draft["draft_id"]))
    assert record["text"] == "语速慢一点"
    assert [m["text"] for m in store.list_memories(A)] == ["语速慢一点"]


def test_M20_authorized_excerpt_comes_from_the_round_and_needs_a_grant(store):
    fail(400, lambda: store.record_source(A, SESSION, "R1", "asr-1", "记住，语速慢一点。",
                                          save_authorized=True,
                                          authorized_text="下次继续练答辩"))
    fail(400, lambda: store.record_source(A, SESSION, "R2", "asr-2", "语速慢一点",
                                          authorized_text="语速慢一点"))
    fail(400, lambda: store.record_source(A, SESSION, "R3", "asr-3", "记住，语速慢一点",
                                          save_authorized=1))
    assert store.list_memories(A) == []