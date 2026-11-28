"""Offline end-to-end walkthrough: no model, no network, no microphone.

    cd F:\\git\\Computer_Version
    .\\.venv\\Scripts\\python.exe -m conversation.demo

Every step prints what Flask would receive and what the user would hear. It uses a
real file-backed SQLite store in a temporary directory, so the "restart and recall"
step is the same code path as production.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from . import service
from .memory import DomainError, MemoryStore

PASS = "  ✓"


def _ctx(user, session, turn, input_id, **extra):
    return dict({"user_id": user, "session_id": session, "turn_id": turn,
                 "input_id": input_id, "background": {}, "history": []}, **extra)


def _say(reply):
    return " / ".join(reply["sentences"])


def _check(label, condition, detail=""):
    print(f"{PASS if condition else '  ✗'} {label}{('  ' + detail) if detail else ''}")
    if not condition:
        raise SystemExit(f"演示失败：{label}")


def main() -> int:
    print("① 建库、注入文件型 store")
    with tempfile.TemporaryDirectory() as folder:
        db = Path(folder) / "demo.sqlite"
        store = MemoryStore(db)
        service.configure(store=store)
        print(f"  库: {db}")

        print("② 欢迎轮：语速与语调分开")
        welcome = service.opening(_ctx("demo-a", "S1", "R0", None))
        rate = service.speech_rate(_ctx("demo-a", "S1", "R0", None))
        print(f"  voice_style={welcome['voice_style']}  speech_rate={rate}  台词: {_say(welcome)}")
        _check("欢迎轮不因慢速偏好改语调", welcome["voice_style"] == "neutral")

        print("③ 普通倾诉（无模型时走预审短句）")
        reply = service.respond(_ctx("demo-a", "S1", "R1", "I1"), "今天有点累，想找人说话。", lambda: False)
        print(f"  intent={reply['intent']}  台词: {_say(reply)}")
        _check("回复最多两句", 1 <= len(reply["sentences"]) <= 2)

        print("④ 明确保存：只存授权的那一项")
        ctx = _ctx("demo-a", "S1", "R2", "I2")
        service.record_source(ctx, "记住语速慢一点，别记今天失眠。",
                              save_authorized=True, authorized_text="语速慢一点")
        saved = service.apply_memory("demo-a", {
            "op": "save", "kind": "preference", "session_id": "S1", "source_turn_id": "R2",
            "value": "语速慢一点", "request_id": "demo-save-1"})
        print(f"  已存: {saved['text']}  revision={saved['revision']}")
        _check("保存绑定到具体事项", saved["text"] == "语速慢一点" and saved["revision"] == 1)

        print("⑤ 用户明确排除的项不可保存（换一个回合，避免幂等键冲突）")
        excluded = _ctx("demo-a", "S1", "R3", "I3")
        service.record_source(excluded, "记住语速慢一点，别记今天失眠。",
                              save_authorized=True, authorized_text="语速慢一点")
        try:
            service.apply_memory("demo-a", {
                "op": "save", "kind": "preference", "session_id": "S1", "source_turn_id": "R3",
                "value": "今天失眠", "request_id": "demo-save-excluded"})
            _check("被排除项被拒绝", False)
        except DomainError as error:
            print(f"  拒绝: {error.status} {error.code}")
            _check("被排除项被拒绝", error.status == 403)

        print("⑥ 重启（新连接）+ 新会话：偏好仍生效")
        store.close()
        store = MemoryStore(db)
        service.configure(store=store)
        again = _ctx("demo-a", "S2", "R0", None)
        reopened = service.opening(again)
        print(f"  speech_rate={service.speech_rate(again)}  台词: {_say(reopened)}")
        _check("重开后慢速偏好仍在", service.speech_rate(again) == "slow")
        _check("语速不改语调", reopened["voice_style"] == "neutral")

        print("⑦ 改口：同槽替换，不新增第二条")
        ctx = _ctx("demo-a", "S2", "R1", "I1")
        service.record_source(ctx, "我改主意了，还是按正常速度。",
                              save_authorized=True, authorized_text="按正常速度")
        changed = service.apply_memory("demo-a", {
            "op": "save", "kind": "preference", "session_id": "S2", "source_turn_id": "R1",
            "value": "按正常速度", "request_id": "demo-save-3"})
        items = service.list_memories("demo-a")
        print(f"  revision={changed['revision']}  列表: {[m['text'] for m in items]}")
        _check("改口后只有一条", len(items) == 1 and changed["revision"] == 2)

        print("⑧ 跨用户隔离")
        try:
            service.apply_memory("demo-b", {"op": "delete", "memory_id": changed["memory_id"],
                                            "request_id": "demo-b-1"})
            _check("B 不能删 A 的记录", False)
        except DomainError as error:
            print(f"  拒绝: {error.status} {error.code}")
            _check("B 不能删 A 的记录", error.status == 404)
        _check("B 的列表为空", service.list_memories("demo-b") == [])

        print("⑨ 撤回：列表立即为空，下一轮不再召回")
        service.apply_memory("demo-a", {"op": "delete", "memory_id": changed["memory_id"],
                                        "request_id": "demo-del-1"})
        _check("撤回后列表为空", service.list_memories("demo-a") == [])
        recall = service.respond(_ctx("demo-a", "S3", "R1", "I9"), "你还记得我上次要练什么吗？", lambda: False)
        print(f"  台词: {_say(recall)}")
        _check("撤回后不召回", "答辩" not in _say(recall))

        print("⑩ 取消：抛 TurnCancelled，不交付任何文字")
        try:
            service.respond(_ctx("demo-a", "S4", "R1", "I1"), "我想聊聊今天的事。", lambda: True)
            _check("取消被识别", False)
        except service.TurnCancelled:
            _check("取消被识别且无输出", True)

        print("⑪ 否定词不能被截掉：不喜欢慢速 ≠ 喜欢慢速")
        deny = _ctx("demo-a", "S5", "R1", "I1")
        service.record_source(deny, "记住，我不喜欢慢速。",
                              save_authorized=True, authorized_text="不喜欢慢速")
        try:
            service.apply_memory("demo-a", {
                "op": "save", "kind": "preference", "session_id": "S5", "source_turn_id": "R1",
                "value": "喜欢慢速", "request_id": "demo-negate"})
            _check("否定项不能反转保存", False)
        except DomainError as error:
            print(f"  拒绝: {error.status} {error.code}")
            _check("否定项不能反转保存", error.status == 403)

        print("⑫ 撤回后同回合迟到的 save 不能复活")
        revive = _ctx("demo-a", "S6", "R1", "I1")
        service.record_source(revive, "记住，下次练答辩。",
                              save_authorized=True, authorized_text="下次练答辩")
        draft = service.apply_memory("demo-a", {
            "op": "propose", "kind": "follow_up", "text": "下次练答辩", "session_id": "S6",
            "source_turn_id": "R1", "request_id": "demo-propose"})
        record = service.apply_memory("demo-a", {
            "op": "confirm", "draft_id": draft["draft_id"], "session_id": "S6",
            "source_turn_id": "R1", "request_id": "demo-confirm"})
        service.apply_memory("demo-a", {"op": "delete", "memory_id": record["memory_id"],
                                        "request_id": "demo-delete-2"})
        try:
            service.apply_memory("demo-a", {
                "op": "save", "kind": "follow_up", "value": "下次练答辩", "session_id": "S6",
                "source_turn_id": "R1", "request_id": "demo-late-save"})
            _check("撤回后不能复活", False)
        except DomainError as error:
            print(f"  拒绝: {error.status} {error.code}")
            _check("撤回后不能复活", error.status == 403)

        print("⑬ 草稿不能跨槽改写")
        slot_ctx = _ctx("demo-a", "S7", "R1", "I1")
        service.record_source(slot_ctx, "记住，语速慢一点。",
                              save_authorized=True, authorized_text="语速慢一点")
        slot_draft = service.apply_memory("demo-a", {
            "op": "propose", "kind": "preference", "text": "语速慢一点", "session_id": "S7",
            "source_turn_id": "R1", "request_id": "demo-slot-propose"})
        try:
            service.apply_memory("demo-a", {
                "op": "confirm", "draft_id": slot_draft["draft_id"], "value": "打开字幕",
                "session_id": "S7", "source_turn_id": "R1", "request_id": "demo-slot-bad"})
            _check("跨槽确认被拒", False)
        except DomainError as error:
            print(f"  拒绝: {error.status} {error.code}")
            _check("跨槽确认被拒", error.status == 400)
        service.apply_memory("demo-a", {
            "op": "confirm", "draft_id": slot_draft["draft_id"], "value": "语速慢一点",
            "session_id": "S7", "source_turn_id": "R1", "request_id": "demo-slot-ok"})
        rate_ctx = _ctx("demo-a", "S8", "R0", None)
        print(f"  speech_rate={service.speech_rate(rate_ctx)}  factor={service.speech_rate_factor(rate_ctx)}")
        _check("TTS 拿到数值倍率", service.speech_rate_factor(rate_ctx) == 0.85)

        print("⑭ 未注入 store：记忆 I/O 明确失败，而不是静默丢数据")
        store.close()
        service.configure()
        try:
            service.list_memories("demo-a")
            _check("未注入时拒绝读记忆", False)
        except DomainError as error:
            print(f"  拒绝: {error.status} {error.code} retryable={error.retryable}")
            _check("未注入时拒绝读记忆", error.status == 503 and error.retryable)

    print("\nDEMO OK：14 步全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())