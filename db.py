# -*- coding: utf-8 -*-
"""
dwell 数据层（V0.2，阶段 3）

本模块是"存储接口层"：app.py 只认识这里的函数，
底下是 SQLite（现在）还是 Supabase PostgreSQL（以后）由本模块内部决定。

表：
  sessions  —— 聊天会话
  messages  —— 原始消息（role: user / assistant / tool）
  memories  —— 长期记忆 / 历史摘要（全局共享）
  settings  —— 全局 AI 配置（单行 id=1）

当前阶段只做：连接管理（单连接 + 锁 + WAL）、建表、默认配置。
后续阶段逐个加读写函数（sessions/messages → memories → settings）。
"""

import json
import os
import sqlite3
import threading
import time

# ---------- 位置 ----------

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DB_DIR, "dwell.db")

# ---------- 连接：单连接 + 锁，SQLite 同一时刻只有一个写者 ----------

_conn = None
_conn_lock = threading.Lock()


def _get_conn():
    global _conn
    if _conn is None:
        os.makedirs(DB_DIR, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode = WAL")
        _conn.execute("PRAGMA foreign_keys = ON")
    return _conn


# ---------- 表结构 ----------

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    archived   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    role        TEXT    NOT NULL,
    content     TEXT    NOT NULL,
    extra             TEXT    NOT NULL DEFAULT '{}',
    reasoning_content TEXT    NOT NULL DEFAULT '',
    created_at        INTEGER NOT NULL,
    visible           INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_messages_session_seq ON messages(session_id, seq);

CREATE TABLE IF NOT EXISTS memories (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    content           TEXT    NOT NULL,
    metadata          TEXT    NOT NULL DEFAULT '{}',
    conversation_id   INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
    source_message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    created_at        INTEGER NOT NULL,
    updated_at        INTEGER NOT NULL,
    active            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS settings (
    id                   INTEGER PRIMARY KEY CHECK (id = 1),
    system_prompt        TEXT    NOT NULL DEFAULT '',
    temperature          REAL    NOT NULL DEFAULT 1.0,
    max_context_rounds   INTEGER NOT NULL DEFAULT 20,
    max_context_tokens   INTEGER NOT NULL DEFAULT 100000,
    compress_threshold   INTEGER NOT NULL DEFAULT 80000,
    compress_keep_rounds INTEGER NOT NULL DEFAULT 10,
    max_reply_tokens     INTEGER NOT NULL DEFAULT 4096,
    updated_at           INTEGER NOT NULL
);
"""

# 默认设置：全部对齐现在 app.py 里硬编码的行为，不配置时聊天表现不变
DEFAULT_SETTINGS = {
    "system_prompt": "你是住在这个界面里的 AI，说话自然、简洁，不要暴露你是通过 API 调用的。",
    "temperature": 1.0,
    "max_context_rounds": 20,        # 现状：取最近 40 条消息 ≈ 20 轮
    "max_context_tokens": 100000,
    "compress_threshold": 80000,     # 阈值设高，正常聊天不触发；阶段 8 再实现压缩
    "compress_keep_rounds": 10,
    "max_reply_tokens": 4096,        # 现状 max_tokens
}

SCHEMA_VERSION = 2


def init():
    """建库建表，并保证 settings 单行默认配置存在。幂等，可重复调用。"""
    with _conn_lock:
        conn = _get_conn()
        conn.executescript(SCHEMA)
        _migrate(conn)
        cur = conn.execute("SELECT COUNT(*) AS n FROM settings")
        if cur.fetchone()["n"] == 0:
            vals = {**DEFAULT_SETTINGS, "updated_at": int(time.time())}
            conn.execute(
                "INSERT INTO settings (id, system_prompt, temperature,"
                " max_context_rounds, max_context_tokens, compress_threshold,"
                " compress_keep_rounds, max_reply_tokens, updated_at)"
                " VALUES (1, :system_prompt, :temperature, :max_context_rounds,"
                " :max_context_tokens, :compress_threshold, :compress_keep_rounds,"
                " :max_reply_tokens, :updated_at)",
                vals,
            )
        conn.commit()


def _migrate(conn):
    """按 PRAGMA user_version 做增量迁移：老库缺列就补列。"""
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    if ver < 2:
        # V2：messages 加 reasoning_content，存模型思考过程
        # 不用 IF NOT EXISTS（老 SQLite 不支持），先查列再决定
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(messages)").fetchall()]
        if "reasoning_content" not in cols:
            conn.execute(
                "ALTER TABLE messages ADD COLUMN"
                " reasoning_content TEXT NOT NULL DEFAULT ''")
    conn.execute("PRAGMA user_version = %d" % SCHEMA_VERSION)


# ---------- sessions：会话 ----------

def create_session(name=""):
    """新建一个会话，返回 session_id。"""
    now = int(time.time())
    with _conn_lock:
        conn = _get_conn()
        cur = conn.execute(
            "INSERT INTO sessions (name, created_at, updated_at) VALUES (?, ?, ?)",
            (name, now, now))
        conn.commit()
        return cur.lastrowid


def list_sessions(include_archived=False):
    """会话列表，按最后活动时间倒序。"""
    with _conn_lock:
        conn = _get_conn()
        sql = ("SELECT id, name, created_at, updated_at, archived FROM sessions"
               + ("" if include_archived else " WHERE archived = 0")
               + " ORDER BY updated_at DESC, id DESC")
        return [dict(r) for r in conn.execute(sql).fetchall()]


def rename_session(sid, name):
    """改会话名。"""
    with _conn_lock:
        conn = _get_conn()
        conn.execute("UPDATE sessions SET name = ?, updated_at = ? WHERE id = ?",
                     (name, int(time.time()), sid))
        conn.commit()


def delete_session(sid):
    """删除会话；messages 由外键 ON DELETE CASCADE 一并清掉。"""
    with _conn_lock:
        conn = _get_conn()
        conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        conn.commit()


def set_session_archived(sid, archived):
    """设置会话收纳状态：1=已收纳 0=在用。"""
    with _conn_lock:
        conn = _get_conn()
        conn.execute("UPDATE sessions SET archived = ?, updated_at = ? WHERE id = ?",
                     (1 if archived else 0, int(time.time()), sid))
        conn.commit()


def get_session(sid):
    """按 id 查单条会话；不存在返回 None。"""
    with _conn_lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT id, name, created_at, updated_at, archived"
            " FROM sessions WHERE id = ?", (sid,)).fetchone()
        return dict(row) if row else None


# ---------- messages：消息 ----------

def add_message(session_id, role, content, reasoning_content="", extra=None, visible=1):
    """写一条消息，seq 在会话内递增。返回 dict（含 seq）。

    role: user / assistant / tool
    """
    with _conn_lock:
        conn = _get_conn()
        now = int(time.time())
        seq = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM messages WHERE session_id = ?",
            (session_id,)).fetchone()["n"]
        cur = conn.execute(
            "INSERT INTO messages (session_id, seq, role, content, extra,"
            " reasoning_content, created_at, visible)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, seq, role, content,
             json.dumps(extra or {}, ensure_ascii=False),
             reasoning_content, now, visible))
        conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
        conn.commit()
        return {"id": cur.lastrowid, "seq": seq, "role": role, "content": content,
                "reasoning_content": reasoning_content, "at": now, "visible": visible}


def get_messages(session_id, limit=400, before=None, visible_only=True):
    """分页读一个会话的消息，按 seq 升序返回。返回 (rows, more)。

    - limit：每页条数
    - before：只取 seq < before 的更旧消息（前端"翻老账"用）
    - visible_only：只取未压缩的可见消息
    """
    with _conn_lock:
        conn = _get_conn()
        sql = ("SELECT id, seq, role, content, extra, reasoning_content,"
               " created_at AS at, visible FROM messages WHERE session_id = ?")
        params = [session_id]
        if visible_only:
            sql += " AND visible = 1"
        if before is not None:
            sql += " AND seq < ?"
            params.append(before)
        sql += " ORDER BY seq DESC LIMIT ?"
        params.append(limit + 1)   # 多取一条，判断还有没有更旧的
        rows = conn.execute(sql, params).fetchall()
        more = len(rows) > limit
        page = list(reversed(rows[:limit]))
        return [dict(r) for r in page], more


def hide_messages(message_ids):
    """把一批消息置为 visible=0：不再参与上下文、不再显示，但数据保留。"""
    if not message_ids:
        return
    with _conn_lock:
        conn = _get_conn()
        conn.executemany("UPDATE messages SET visible = 0 WHERE id = ?",
                         [(i,) for i in message_ids])
        conn.commit()


# ---------- memories：长期记忆 / 历史摘要（全局共享） ----------

def add_memory(content, metadata=None, source_message_id=None, conversation_id=None):
    """写一条记忆；metadata 是 dict，转 JSON 存。返回记忆 id。"""
    with _conn_lock:
        conn = _get_conn()
        now = int(time.time())
        cur = conn.execute(
            "INSERT INTO memories (content, metadata, conversation_id,"
            " source_message_id, created_at, updated_at, active)"
            " VALUES (?, ?, ?, ?, ?, ?, 1)",
            (content, json.dumps(metadata or {}, ensure_ascii=False),
             conversation_id, source_message_id, now, now))
        conn.commit()
        return cur.lastrowid


def get_memories(limit=50, active_only=True):
    """读记忆（AI 上下文用），按检索优先级排序。等价于不带筛选的 search_memories。"""
    return search_memories(q=None, kind=None, active_only=active_only, limit=limit)


def search_memories(q=None, kind=None, active_only=True, limit=200):
    """记忆中心的组合查询：q 搜内容+关键词，kind 精确过滤，active 过滤。

    返回按优先级排序（未了结 → 情绪强度 → 时间新在前）的记忆列表，metadata 已解析。
    """
    with _conn_lock:
        conn = _get_conn()
        sql = ("SELECT id, content, metadata, conversation_id, source_message_id,"
               " created_at, updated_at, active FROM memories WHERE 1=1")
        params = []
        if active_only is not None:
            sql += " AND active = ?"
            params.append(1 if active_only else 0)
        if kind:
            sql += " AND json_extract(metadata, '$.kind') = ?"
            params.append(kind)
        if q:
            sql += " AND (content LIKE ? OR metadata LIKE ?)"
            like = "%" + q + "%"
            params.append(like)
            params.append(like)
        sql += (" ORDER BY COALESCE(json_extract(metadata, '$.unresolved'), 0) DESC,"
                " COALESCE(json_extract(metadata, '$.intensity'), 0) DESC,"
                " updated_at DESC, id DESC LIMIT ?")
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["metadata"] = json.loads(d["metadata"] or "{}")
            except ValueError:
                d["metadata"] = {}
            out.append(d)
        return out


def get_memory(memory_id):
    """按 id 查一条记忆；metadata 解析好。不存在返回 None。"""
    with _conn_lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT id, content, metadata, conversation_id, source_message_id,"
            " created_at, updated_at, active FROM memories WHERE id = ?",
            (memory_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        try:
            d["metadata"] = json.loads(d["metadata"] or "{}")
        except ValueError:
            d["metadata"] = {}
        return d


def update_memory(memory_id, content=None, kind=None, keywords=None,
                  unresolved=None, intensity=None):
    """更新一条记忆：只改传入的字段，自动 updated_at；created_at/active 不动。

    返回更新后的记忆（dict）；不存在返回 None。
    """
    with _conn_lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT content, metadata FROM memories WHERE id = ?",
            (memory_id,)).fetchone()
        if row is None:
            return None
        meta = {}
        try:
            meta = json.loads(row["metadata"] or "{}")
        except ValueError:
            meta = {}
        if kind is not None:
            meta["kind"] = kind
        if keywords is not None:
            meta["keywords"] = keywords
        if unresolved is not None:
            meta["unresolved"] = 1 if unresolved else 0
        if intensity is not None:
            try:
                meta["intensity"] = max(0, min(5, int(intensity)))
            except (TypeError, ValueError):
                pass
        new_content = row["content"] if content is None else content
        conn.execute(
            "UPDATE memories SET content = ?, metadata = ?, updated_at = ?"
            " WHERE id = ?",
            (new_content, json.dumps(meta, ensure_ascii=False),
             int(time.time()), memory_id))
        conn.commit()
        row2 = conn.execute(
            "SELECT id, content, metadata, conversation_id, source_message_id,"
            " created_at, updated_at, active FROM memories WHERE id = ?",
            (memory_id,)).fetchone()
        d = dict(row2)
        try:
            d["metadata"] = json.loads(d["metadata"] or "{}")
        except ValueError:
            d["metadata"] = {}
        return d


def restore_memory(memory_id):
    """恢复一条软删除的记忆（active=1）。存在则恢复并返回 True，否则 False。"""
    with _conn_lock:
        conn = _get_conn()
        cur = conn.execute(
            "UPDATE memories SET active = 1, updated_at = ? WHERE id = ?",
            (int(time.time()), memory_id))
        conn.commit()
        return cur.rowcount > 0


def search_messages_across_sessions(keywords, limit=5, exclude_sid=None):
    """跨所有会话的可见消息按关键词搜（LIKE），返回 [{session_id, content, at}]。"""
    if not keywords:
        return []
    with _conn_lock:
        conn = _get_conn()
        rows, seen = [], set()
        for kw in keywords:
            q = ("SELECT session_id, content, created_at AS at FROM messages"
                 " WHERE visible = 1 AND content LIKE ?")
            params = ["%" + kw + "%"]
            if exclude_sid is not None:
                q += " AND session_id != ?"
                params.append(exclude_sid)
            q += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            for r in conn.execute(q, params).fetchall():
                key = (r["session_id"], r["content"][:40])
                if key in seen:
                    continue
                seen.add(key)
                rows.append(dict(r))
                if len(rows) >= limit:
                    break
            if len(rows) >= limit:
                break
        return rows


def search_memories_by_keywords(keywords, limit=3):
    """按关键词搜全局记忆（内容 LIKE）。返回 [{id, content}]。"""
    if not keywords:
        return []
    with _conn_lock:
        conn = _get_conn()
        rows, seen = [], set()
        for kw in keywords:
            for r in conn.execute(
                "SELECT id, content FROM memories WHERE active = 1"
                " AND content LIKE ? ORDER BY updated_at DESC LIMIT ?",
                ("%" + kw + "%", limit)).fetchall():
                if r["content"] in seen:
                    continue
                seen.add(r["content"])
                rows.append(dict(r))
                if len(rows) >= limit:
                    break
            if len(rows) >= limit:
                break
        return rows


def deactivate_memory(mid):
    """软删除一条记忆（active=0），数据保留。"""
    with _conn_lock:
        conn = _get_conn()
        conn.execute("UPDATE memories SET active = 0, updated_at = ? WHERE id = ?",
                     (int(time.time()), mid))
        conn.commit()


# ---------- settings：全局 AI 配置（单行 id=1） ----------

def get_settings():
    """读全局配置；缺行时用默认值兜底。返回 dict。"""
    with _conn_lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT system_prompt, temperature, max_context_rounds, max_context_tokens,"
            " compress_threshold, compress_keep_rounds, max_reply_tokens, updated_at"
            " FROM settings WHERE id = 1").fetchone()
        if row is None:
            return {**DEFAULT_SETTINGS, "updated_at": int(time.time())}
        return dict(row)


def save_settings(**kw):
    """更新配置：只改传入的字段（未知字段忽略）。返回更新后的完整配置。"""
    allowed = {"system_prompt", "temperature", "max_context_rounds",
               "max_context_tokens", "compress_threshold",
               "compress_keep_rounds", "max_reply_tokens"}
    updates = {k: v for k, v in kw.items() if k in allowed}
    with _conn_lock:
        conn = _get_conn()
        if updates:
            sets = ", ".join("%s = ?" % k for k in updates)
            conn.execute(
                "UPDATE settings SET %s, updated_at = ? WHERE id = 1" % sets,
                list(updates.values()) + [int(time.time())])
            conn.commit()
    return get_settings()
