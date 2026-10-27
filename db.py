# -*- coding: utf-8 -*-
"""
dwell 数据层（V0.3 —— 可上云版）

一套接口，两种底座：
  - 本地：SQLite（data/dwell.db），双击 start.bat 就能跑
  - 云端：Postgres / Supabase（设 DWELL_DATABASE_URL 就走它）

怎么选：环境变量 DWELL_DATABASE_URL（或 DATABASE_URL）以 postgres 开头就走 Postgres。
app.py 只认识这里的函数，不需要知道底下是谁。

表：
  sessions  —— 聊天会话
  messages  —— 原始消息（role: user / assistant / tool）
  memories  —— 长期记忆 / 历史摘要（全局共享）
  settings  —— 全局 AI 配置（单行 id=1）

移植策略（为什么要这么写）：
  SQLite 和 Postgres 的方言差异集中在三处——占位符、自增主键、JSON 查询。
  这里的做法是：SQL 只用两边都认的最小集合（? 占位符、RETURNING id），
  JSON 相关的过滤/排序全部放到 Python 里做（记忆量级只有几百条，完全够）。
  这样两边的 SQL 几乎是同一份，不会出现"本地好好的、云上语法错"。
"""

import json
import os
import threading
import time

# ---------- 选底座 ----------

DB_URL = (os.environ.get("DWELL_DATABASE_URL")
          or os.environ.get("DATABASE_URL")
          or "").strip()
IS_PG = DB_URL.startswith("postgres")

# SQLite 路径（只在本地用）
DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DB_DIR, "dwell.db")

_conn = None
_conn_lock = threading.RLock()


def backend_name():
    return "postgres" if IS_PG else "sqlite"


# ---------- 连接 ----------

def _get_conn():
    global _conn
    if _conn is not None:
        return _conn
    if IS_PG:
        import psycopg
        from psycopg.rows import dict_row
        # prepare_threshold=None：Supabase 的连接池（PgBouncer 事务模式）
        # 不支持服务端预处理语句，关掉它才连得上。
        _conn = psycopg.connect(
            DB_URL, row_factory=dict_row, connect_timeout=15,
            prepare_threshold=None,
        )
        _conn.autocommit = True
    else:
        import sqlite3
        os.makedirs(DB_DIR, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode = WAL")
        _conn.execute("PRAGMA foreign_keys = ON")
    return _conn


def _sql(sql):
    """占位符翻译：这份代码里的 SQL 一律写 ?，Postgres 要 %s。"""
    return sql.replace("?", "%s") if IS_PG else sql


def _exec(sql, params=()):
    """执行并返回游标。"""
    conn = _get_conn()
    return conn.execute(_sql(sql), params)


def _rows(sql, params=()):
    return [dict(r) for r in _exec(sql, params).fetchall()]


def _one(sql, params=()):
    row = _exec(sql, params).fetchone()
    return dict(row) if row else None


def _commit():
    if not IS_PG:
        _conn.commit()


# ---------- 表结构 ----------

_SQLITE_SCHEMA = """
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

_PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id         BIGSERIAL PRIMARY KEY,
    name       TEXT    NOT NULL DEFAULT '',
    created_at BIGINT  NOT NULL,
    updated_at BIGINT  NOT NULL,
    archived   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id          BIGSERIAL PRIMARY KEY,
    session_id  BIGINT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    seq         BIGINT NOT NULL,
    role        TEXT   NOT NULL,
    content     TEXT   NOT NULL,
    extra             TEXT   NOT NULL DEFAULT '{}',
    reasoning_content TEXT   NOT NULL DEFAULT '',
    created_at        BIGINT NOT NULL,
    visible           INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_messages_session_seq ON messages(session_id, seq);

CREATE TABLE IF NOT EXISTS memories (
    id                BIGSERIAL PRIMARY KEY,
    content           TEXT   NOT NULL,
    metadata          TEXT   NOT NULL DEFAULT '{}',
    conversation_id   BIGINT REFERENCES sessions(id) ON DELETE SET NULL,
    source_message_id BIGINT REFERENCES messages(id) ON DELETE SET NULL,
    created_at        BIGINT NOT NULL,
    updated_at        BIGINT NOT NULL,
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
    updated_at           BIGINT  NOT NULL
);
"""

# 默认设置：全部对齐聊天主链路的现有行为，不配置时表现不变
DEFAULT_SETTINGS = {
    "system_prompt": "你是住在这个界面里的 AI，说话自然、简洁，不要暴露你是通过 API 调用的。",
    "temperature": 1.0,
    "max_context_rounds": 20,        # 取最近 40 条消息 ≈ 20 轮
    "max_context_tokens": 100000,
    "compress_threshold": 80000,     # 阈值设高，正常聊天不触发
    "compress_keep_rounds": 10,
    "max_reply_tokens": 4096,
}

SCHEMA_VERSION = 2


def init(retries=3):
    """建库建表 + 保证 settings 单行默认配置存在。幂等，可重复调用。

    云端冷启动时数据库可能慢一拍，所以带几次重试。
    """
    last = None
    for attempt in range(1, retries + 1):
        try:
            with _conn_lock:
                conn = _get_conn()
                if IS_PG:
                    for stmt in _split_sql(_PG_SCHEMA):
                        conn.execute(stmt)
                else:
                    conn.executescript(_SQLITE_SCHEMA)
                _migrate()
                n = _one("SELECT COUNT(*) AS n FROM settings")["n"]
                if not n:
                    vals = dict(DEFAULT_SETTINGS, updated_at=int(time.time()))
                    _exec(
                        "INSERT INTO settings (id, system_prompt, temperature,"
                        " max_context_rounds, max_context_tokens, compress_threshold,"
                        " compress_keep_rounds, max_reply_tokens, updated_at)"
                        " VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (vals["system_prompt"], vals["temperature"],
                         vals["max_context_rounds"], vals["max_context_tokens"],
                         vals["compress_threshold"], vals["compress_keep_rounds"],
                         vals["max_reply_tokens"], vals["updated_at"]),
                    )
                _commit()
            return
        except Exception as e:      # 冷启动/网络抖动，等一下再试
            last = e
            if attempt < retries:
                time.sleep(1.5 * attempt)
    raise last


def _split_sql(script):
    """把 executescript 的写法拆成一条条（Postgres 一次只能执行一条）。"""
    out = []
    for chunk in script.split(";"):
        s = chunk.strip()
        if s:
            out.append(s)
    return out


def _migrate():
    """增量迁移：老库缺列就补列。两边都支持 ADD COLUMN，用异常兜住重复执行。"""
    try:
        _exec("ALTER TABLE messages ADD COLUMN reasoning_content TEXT NOT NULL DEFAULT ''")
    except Exception:
        pass        # 已经有了
    if not IS_PG:
        try:
            _exec("PRAGMA user_version = %d" % SCHEMA_VERSION)
        except Exception:
            pass


# ---------- sessions：会话 ----------

def create_session(name=""):
    """新建一个会话，返回 session_id。"""
    now = int(time.time())
    with _conn_lock:
        row = _one(
            "INSERT INTO sessions (name, created_at, updated_at) VALUES (?, ?, ?)"
            " RETURNING id", (name, now, now))
        _commit()
        return row["id"]


def list_sessions(include_archived=False):
    """会话列表，按最后活动时间倒序。"""
    with _conn_lock:
        sql = ("SELECT id, name, created_at, updated_at, archived FROM sessions"
               + ("" if include_archived else " WHERE archived = 0")
               + " ORDER BY updated_at DESC, id DESC")
        return _rows(sql)


def rename_session(sid, name):
    """改会话名。"""
    with _conn_lock:
        _exec("UPDATE sessions SET name = ?, updated_at = ? WHERE id = ?",
              (name, int(time.time()), sid))
        _commit()


def delete_session(sid):
    """删除会话；messages 由外键 ON DELETE CASCADE 一并清掉。"""
    with _conn_lock:
        _exec("DELETE FROM sessions WHERE id = ?", (sid,))
        _commit()


def set_session_archived(sid, archived):
    """设置会话收纳状态：1=已收纳 0=在用。"""
    with _conn_lock:
        _exec("UPDATE sessions SET archived = ?, updated_at = ? WHERE id = ?",
              (1 if archived else 0, int(time.time()), sid))
        _commit()


def get_session(sid):
    """按 id 查单条会话；不存在返回 None。"""
    with _conn_lock:
        return _one("SELECT id, name, created_at, updated_at, archived"
                    " FROM sessions WHERE id = ?", (sid,))


# ---------- messages：消息 ----------

def add_message(session_id, role, content, reasoning_content="", extra=None, visible=1):
    """写一条消息，seq 在会话内递增。返回 dict（含 seq）。

    role: user / assistant / tool
    """
    with _conn_lock:
        now = int(time.time())
        seq = _one("SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM messages"
                   " WHERE session_id = ?", (session_id,))["n"]
        row = _one(
            "INSERT INTO messages (session_id, seq, role, content, extra,"
            " reasoning_content, created_at, visible)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (session_id, seq, role, content,
             json.dumps(extra or {}, ensure_ascii=False),
             reasoning_content, now, visible))
        _exec("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
        _commit()
        return {"id": row["id"], "seq": seq, "role": role, "content": content,
                "reasoning_content": reasoning_content, "at": now, "visible": visible}


def get_messages(session_id, limit=400, before=None, visible_only=True):
    """分页读一个会话的消息，按 seq 升序返回。返回 (rows, more)。

    - limit：每页条数
    - before：只取 seq < before 的更旧消息（前端"翻老账"用）
    - visible_only：只取未压缩的可见消息
    """
    with _conn_lock:
        sql = ("SELECT id, seq, role, content, extra, reasoning_content,"
               " created_at AS at, visible FROM messages WHERE session_id = ?")
        params = [session_id]
        if visible_only:
            sql += " AND visible = 1"
        if before is not None:
            sql += " AND seq < ?"
            params.append(before)
        sql += " ORDER BY seq DESC LIMIT ?"
        params.append(limit + 1)        # 多取一条，判断还有没有更旧的
        rows = _rows(sql, tuple(params))
        more = len(rows) > limit
        page = list(reversed(rows[:limit]))
        return page, more


def hide_messages(message_ids):
    """把一批消息置为 visible=0：不再参与上下文、不再显示，但数据保留。"""
    if not message_ids:
        return
    with _conn_lock:
        for i in message_ids:
            _exec("UPDATE messages SET visible = 0 WHERE id = ?", (i,))
        _commit()


# ---------- memories：长期记忆 / 历史摘要（全局共享） ----------

def _parse_meta(row):
    d = dict(row)
    try:
        d["metadata"] = json.loads(d.get("metadata") or "{}")
    except ValueError:
        d["metadata"] = {}
    return d


def add_memory(content, metadata=None, source_message_id=None, conversation_id=None):
    """写一条记忆；metadata 是 dict，转 JSON 存。返回记忆 id。"""
    with _conn_lock:
        now = int(time.time())
        row = _one(
            "INSERT INTO memories (content, metadata, conversation_id,"
            " source_message_id, created_at, updated_at, active)"
            " VALUES (?, ?, ?, ?, ?, ?, 1) RETURNING id",
            (content, json.dumps(metadata or {}, ensure_ascii=False),
             conversation_id, source_message_id, now, now))
        _commit()
        return row["id"]


_MEM_COLS = ("id, content, metadata, conversation_id, source_message_id,"
             " created_at, updated_at, active")


def search_memories(q=None, kind=None, active_only=True, limit=200):
    """记忆中心的组合查询：q 搜内容+关键词，kind 精确过滤，active 过滤。

    过滤和排序都在 Python 里做——SQLite 和 Postgres 的 JSON 函数不一样，
    而记忆总量只有几百条，捞出来再筛反而更稳、更不容易两边行为不一致。
    排序：未了结 → 情绪强度 → 更新时间新在前。
    """
    with _conn_lock:
        sql = "SELECT " + _MEM_COLS + " FROM memories WHERE 1=1"
        params = []
        if active_only is not None:
            sql += " AND active = ?"
            params.append(1 if active_only else 0)
        rows = [_parse_meta(r) for r in _rows(sql, tuple(params))]

    if q:
        ql = q.lower()
        rows = [r for r in rows
                if ql in (r["content"] or "").lower()
                or ql in json.dumps(r["metadata"], ensure_ascii=False).lower()]
    if kind:
        rows = [r for r in rows if (r["metadata"] or {}).get("kind") == kind]

    def key(r):
        m = r["metadata"] or {}
        try:
            unresolved = int(m.get("unresolved") or 0)
        except (TypeError, ValueError):
            unresolved = 0
        try:
            intensity = int(m.get("intensity") or 0)
        except (TypeError, ValueError):
            intensity = 0
        return (unresolved, intensity, r["updated_at"], r["id"])

    rows.sort(key=key, reverse=True)
    return rows[:limit]


def get_memories(limit=50, active_only=True):
    """读记忆（AI 上下文用），按检索优先级排序。"""
    return search_memories(q=None, kind=None, active_only=active_only, limit=limit)


def get_memory(memory_id):
    """按 id 查一条记忆；metadata 解析好。不存在返回 None。"""
    with _conn_lock:
        row = _one("SELECT " + _MEM_COLS + " FROM memories WHERE id = ?", (memory_id,))
        return _parse_meta(row) if row else None


def update_memory(memory_id, content=None, kind=None, keywords=None,
                  unresolved=None, intensity=None):
    """更新一条记忆：只改传入的字段，自动 updated_at；created_at/active 不动。

    返回更新后的记忆（dict）；不存在返回 None。
    """
    with _conn_lock:
        row = _one("SELECT content, metadata FROM memories WHERE id = ?", (memory_id,))
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
        _exec("UPDATE memories SET content = ?, metadata = ?, updated_at = ?"
              " WHERE id = ?",
              (new_content, json.dumps(meta, ensure_ascii=False),
               int(time.time()), memory_id))
        _commit()
        row2 = _one("SELECT " + _MEM_COLS + " FROM memories WHERE id = ?", (memory_id,))
        return _parse_meta(row2) if row2 else None


def restore_memory(memory_id):
    """恢复一条软删除的记忆（active=1）。存在则恢复并返回 True，否则 False。"""
    with _conn_lock:
        cur = _exec("UPDATE memories SET active = 1, updated_at = ? WHERE id = ?",
                    (int(time.time()), memory_id))
        _commit()
        return (cur.rowcount or 0) > 0


def deactivate_memory(mid):
    """软删除一条记忆（active=0），数据保留。"""
    with _conn_lock:
        _exec("UPDATE memories SET active = 0, updated_at = ? WHERE id = ?",
              (int(time.time()), mid))
        _commit()


def search_messages_across_sessions(keywords, limit=5, exclude_sid=None):
    """跨所有会话的可见消息按关键词搜（LIKE），返回 [{session_id, content, at}]。"""
    if not keywords:
        return []
    op = "ILIKE" if IS_PG else "LIKE"      # Postgres 的 LIKE 区分大小写
    with _conn_lock:
        rows, seen = [], set()
        for kw in keywords:
            q = ("SELECT session_id, content, created_at AS at FROM messages"
                 " WHERE visible = 1 AND content " + op + " ?")
            params = ["%" + kw + "%"]
            if exclude_sid is not None:
                q += " AND session_id != ?"
                params.append(exclude_sid)
            q += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            for r in _rows(q, tuple(params)):
                key = (r["session_id"], (r["content"] or "")[:40])
                if key in seen:
                    continue
                seen.add(key)
                rows.append(r)
                if len(rows) >= limit:
                    break
            if len(rows) >= limit:
                break
        return rows


def search_memories_by_keywords(keywords, limit=3):
    """按关键词搜全局记忆（内容 LIKE）。返回 [{id, content}]。"""
    if not keywords:
        return []
    op = "ILIKE" if IS_PG else "LIKE"
    with _conn_lock:
        rows, seen = [], set()
        for kw in keywords:
            for r in _rows(
                "SELECT id, content FROM memories WHERE active = 1"
                " AND content " + op + " ? ORDER BY updated_at DESC LIMIT ?",
                ("%" + kw + "%", limit),
            ):
                if r["content"] in seen:
                    continue
                seen.add(r["content"])
                rows.append(r)
                if len(rows) >= limit:
                    break
            if len(rows) >= limit:
                break
        return rows


# ---------- settings：全局 AI 配置（单行 id=1） ----------

def get_settings():
    """读全局配置；缺行时用默认值兜底。返回 dict。"""
    with _conn_lock:
        row = _one(
            "SELECT system_prompt, temperature, max_context_rounds, max_context_tokens,"
            " compress_threshold, compress_keep_rounds, max_reply_tokens, updated_at"
            " FROM settings WHERE id = 1")
        if row is None:
            return dict(DEFAULT_SETTINGS, updated_at=int(time.time()))
        return row


def save_settings(**kw):
    """更新配置：只改传入的字段（未知字段忽略）。返回更新后的完整配置。"""
    allowed = {"system_prompt", "temperature", "max_context_rounds",
               "max_context_tokens", "compress_threshold",
               "compress_keep_rounds", "max_reply_tokens"}
    updates = {k: v for k, v in kw.items() if k in allowed}
    with _conn_lock:
        if updates:
            sets = ", ".join("%s = ?" % k for k in updates)
            _exec("UPDATE settings SET " + sets + ", updated_at = ? WHERE id = 1",
                  tuple(list(updates.values()) + [int(time.time())]))
            _commit()
        return get_settings()
