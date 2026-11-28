"""普绪喀的回信 · 对话、规则与记忆（服务端模块）。

导入本包不会发起任何模型、字幕、音频、HTTP 或记忆写入操作。
Flask 通过 ``configure()`` 注入存储与适配器，再调用四个入口：
``opening`` / ``respond`` / ``list_memories`` / ``apply_memory``。
"""