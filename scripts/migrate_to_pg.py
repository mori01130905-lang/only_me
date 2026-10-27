# -*- coding: utf-8 -*-
"""把本地 SQLite 里的一切搬到 Supabase / Postgres。

用法（在本机跑，不是在 Render 上跑）：

    .\.venv\Scripts\python.exe scripts\migrate_to_pg.py --target "postgresql://..."

参数：
    --source   本地库路径，默认 data/dwell.db
    --target   Postgres 连接串（Supabase 后台 → Connect → Connection pooling）
    --wipe     先清空目标库的这四张表再搬（默认不删，遇到同 id 就跳过）
    --dry-run  只统计，不写

它做四件事：建表 → 搬 sessions → 搬 messages → 搬 memories + settings，
最后把三个自增序列对齐，免得以后新消息跟老 id 撞车。
"""

import argparse
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=os.path.join(ROOT, "data", "dwell.db"))
    ap.add_argument("--target", default=os.environ.get("DWELL_DATABASE_URL", ""))
    ap.add_argument("--wipe", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.target.startswith("postgres"):
        sys.exit("请用 --target 给一个 postgres 连接串（或设 DWELL_DATABASE_URL）")
    if not os.path.exists(args.source):
        sys.exit("找不到本地库：%s" % args.source)

    src = sqlite3.connect(args.source)
    src.row_factory = sqlite3.Row

    def read(sql):
        return [dict(r) for r in src.execute(sql).fetchall()]

    sessions = read("SELECT id, name, created_at, updated_at, archived FROM sessions ORDER BY id")
    messages = read("SELECT id, session_id, seq, role, content, extra,"
                    " reasoning_content, created_at, visible FROM messages ORDER BY id")
    memories = read("SELECT id, content, metadata, conversation_id, source_message_id,"
                    " created_at, updated_at, active FROM memories ORDER BY id")
    settings = read("SELECT * FROM settings")

    print("本地：%d 个会话 / %d 条消息 / %d 条记忆"
          % (len(sessions), len(messages), len(memories)))
    if args.dry_run:
        return

    os.environ["DWELL_DATABASE_URL"] = args.target
    sys.path.insert(0, ROOT)
    import db                     # noqa: E402  —— 必须在设好环境变量之后导入

    if not db.IS_PG:
        sys.exit("db 模块没认出 Postgres，检查连接串是不是以 postgres 开头")
    db.init()
    conn = db._get_conn()

    if args.wipe:
        print("清空目标库…")
        for t in ("memories", "messages", "sessions", "settings"):
            conn.execute("DELETE FROM " + t)

    def put(sql, params):
        conn.execute(db._sql(sql), params)

    for s in sessions:
        put("INSERT INTO sessions (id, name, created_at, updated_at, archived)"
            " VALUES (?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING",
            (s["id"], s["name"], s["created_at"], s["updated_at"], s["archived"]))

    for m in messages:
        put("INSERT INTO messages (id, session_id, seq, role, content, extra,"
            " reasoning_content, created_at, visible)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING",
            (m["id"], m["session_id"], m["seq"], m["role"], m["content"],
             m["extra"] or "{}", m["reasoning_content"] or "",
             m["created_at"], m["visible"]))

    for m in memories:
        put("INSERT INTO memories (id, content, metadata, conversation_id,"
            " source_message_id, created_at, updated_at, active)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING",
            (m["id"], m["content"], m["metadata"] or "{}", m["conversation_id"],
             m["source_message_id"], m["created_at"], m["updated_at"], m["active"]))

    for st in settings:
        put("INSERT INTO settings (id, system_prompt, temperature, max_context_rounds,"
            " max_context_tokens, compress_threshold, compress_keep_rounds,"
            " max_reply_tokens, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO UPDATE"
            " SET system_prompt = EXCLUDED.system_prompt,"
            "     temperature = EXCLUDED.temperature,"
            "     max_context_rounds = EXCLUDED.max_context_rounds,"
            "     max_context_tokens = EXCLUDED.max_context_tokens,"
            "     compress_threshold = EXCLUDED.compress_threshold,"
            "     compress_keep_rounds = EXCLUDED.compress_keep_rounds,"
            "     max_reply_tokens = EXCLUDED.max_reply_tokens,"
            "     updated_at = EXCLUDED.updated_at",
            (st["id"], st["system_prompt"], st["temperature"],
             st["max_context_rounds"], st["max_context_tokens"],
             st["compress_threshold"], st["compress_keep_rounds"],
             st["max_reply_tokens"], st["updated_at"]))

    for t in ("sessions", "messages", "memories"):
        row = conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM " + t).fetchone()
        mx = row["m"] if isinstance(row, dict) else row[0]
        seq = "pg_get_serial_sequence('%s', 'id')" % t
        if mx:
            conn.execute("SELECT setval(%s, %s)" % (seq, mx))
        else:
            conn.execute("SELECT setval(%s, 1, false)" % seq)

    got = conn.execute(
        "SELECT (SELECT COUNT(*) FROM sessions) AS s,"
        " (SELECT COUNT(*) FROM messages) AS m,"
        " (SELECT COUNT(*) FROM memories) AS r").fetchone()
    print("云端：%d 个会话 / %d 条消息 / %d 条记忆 —— 搬完了"
          % (got["s"], got["m"], got["r"]))


if __name__ == "__main__":
    main()
