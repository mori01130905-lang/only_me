# -*- coding: utf-8 -*-
"""
dwell 前端 —— 聊天主线最小后端（DeepSeek 版）

先跑这一个模块，把它接通了，再去接日记/待办/日历那些独立小功能。

需要的环境变量：
  DEEPSEEK_API_KEY   你的 DeepSeek API key

可选环境变量：
  DEEPSEEK_BASE_URL   默认 https://api.deepseek.com
  DEEPSEEK_MODEL      默认 deepseek-v4-flash
  DWELL_HOST          默认 127.0.0.1（只给本机用；要对局域网开放才设 0.0.0.0）
  DWELL_PORT          默认 5000
  DWELL_DATABASE_URL  以 postgres 开头就用 Postgres/Supabase，否则用本地 SQLite
  DWELL_PASS          访问口令；设了就要求先登录，不设则完全不设防（只适合本机）
  SECRET_KEY          登录 cookie 的签名密钥；不设会按口令派生（重启不掉登录）
  DWELL_DEBUG         默认 0；设 1 才开调试模式（绝对不要带到公网）

运行方式：
  pip install flask openai
  $env:DEEPSEEK_API_KEY = 'sk-xxxx'
  python app.py
  然后浏览器打开 http://127.0.0.1:5000/
"""

import datetime
import hashlib
import json
import os
import random
import re
import socket
import threading
import time
import webbrowser

import db

from flask import (Flask, Response, jsonify, redirect, request, session,
                   send_from_directory)
from openai import OpenAI


def _load_env():
    """启动时把项目目录下的 .env 读进环境变量；真实环境变量优先，不覆盖。"""
    path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k:
                os.environ.setdefault(k, v)


_load_env()

# 初始化数据库：建库建表 + 增量迁移（幂等）
db.init()

# ---------- 基础配置 ----------

app = Flask(__name__, static_folder=None)

BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
# 系统提示词已进 settings 表（默认值见 db.DEFAULT_SETTINGS，启动时自动落库）

# 监听地址：0.0.0.0 = 手机/局域网也能访问；只想本机访问就设 DWELL_HOST=127.0.0.1
HOST = os.environ.get("DWELL_HOST", "127.0.0.1")
PORT = int(os.environ.get("DWELL_PORT", "5000"))

DEBUG = os.environ.get("DWELL_DEBUG", "0").strip().lower() not in ("0", "", "false", "no")

# ---------- 访问控制 ----------
# 有了这道门，这个服务才敢挂在公网上：没登录只会看到一页口令输入框。
# 本机自己用时不用设 DWELL_PASS，行为跟以前一模一样。
PASS = os.environ.get("DWELL_PASS", "")
app.secret_key = os.environ.get("SECRET_KEY") or hashlib.sha256(
    ("dwell::" + (PASS or "local-only")).encode()).hexdigest()
app.permanent_session_lifetime = datetime.timedelta(days=180)

LOGIN_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>进门前先敲一下</title><style>
body{margin:0;min-height:100dvh;display:flex;align-items:center;justify-content:center;
background:#faf9f5;color:#2b2a27;font:16px/1.7 -apple-system,"PingFang SC","Noto Sans SC",sans-serif}
.card{width:min(86vw,340px);text-align:center}
h1{font-size:17px;font-weight:600;margin:0 0 18px}
input{width:100%;box-sizing:border-box;border:0;outline:none;border-radius:14px;padding:14px 16px;
font:inherit;background:#f0eee6;color:#2b2a27;margin-bottom:12px}
button{width:100%;border:0;border-radius:14px;padding:14px;font:inherit;background:#c96442;color:#fff}
.err{color:#c0392b;font-size:14px;min-height:22px;margin:0 0 10px}
</style></head><body><form class="card" method="post">
<h1>这是私人地方</h1><p class="err">{ERR}</p>
<input name="p" type="password" placeholder="口令" autofocus autocomplete="current-password">
<button type="submit">进去</button></form></body></html>"""


@app.before_request
def _auth_gate():
    if not PASS:                                  # 本机用：不设防
        return None
    if request.path in ("/login", "/healthz", "/favicon.ico"):
        return None
    if session.get("ok"):
        return None
    if request.args.get("k") == PASS:             # 手机上第一次打开可以直接带口令
        session["ok"] = True
        session.permanent = True
        return redirect(request.path)
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    return redirect("/login")


@app.route("/login", methods=["GET", "POST"])
def login():
    if not PASS:
        return redirect("/")
    err = ""
    if request.method == "POST":
        if (request.form.get("p") or "") == PASS:
            session["ok"] = True
            session.permanent = True
            return redirect("/")
        err = "口令不对，再试一次"
    return Response(LOGIN_HTML.replace("{ERR}", err), mimetype="text/html")


@app.after_request
def _headers(resp):
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store, max-age=0"   # 反代/CDN 别缓存轮询
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"          # 别进搜索引擎
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


@app.route("/healthz")
def healthz():
    """给 Render 的保活探针用：不进数据库，也不要求登录。"""
    return jsonify({"ok": True, "db": db.backend_name(), "version": 3})


# 前端 index.html 放在这个目录旁边的 web/ 文件夹里
WEB_DIR = os.path.join(os.path.dirname(__file__), "web")

# 前端设置页的"用力档"是 low / medium / high / xhigh / max，
# DeepSeek 的 reasoning_effort 只认 low / medium / high，归一化一下。
_EFFORT_MAP = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}
cur_effort = "high"

_client = None
_client_lock = threading.Lock()


def get_client():
    """惰性建 OpenAI client：没配 key 时服务器照常起，聊到这儿才报清楚。"""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                key = os.environ.get("DEEPSEEK_API_KEY")
                if not key:
                    raise RuntimeError("没有设置 DEEPSEEK_API_KEY 环境变量，配好再聊")
                _client = OpenAI(api_key=key, base_url=BASE_URL)
    return _client


def _dg(delta, key, default=""):
    """取流式 delta 上的字段，兼容 pydantic 对象 / dict 两种形态。"""
    if delta is None:
        return default
    if isinstance(delta, dict):
        return delta.get(key, default)
    return getattr(delta, key, default)


# ---------- 会话与事件流 ----------
# 消息/会话已持久化到数据库（db.py）；这里只留"实时"状态：事件流、busy、stop_flag。

current_session_id = None     # 当前会话 id；首次收发消息或重启后自动确定

# 库里的 role → 前端渲染要的 kind
KIND_MAP = {"user": "me", "assistant": "gu", "tool": "tool"}

events_lock = threading.Lock()
events = []              # 事件流，poll 用游标(since)增量读取（不清空，游标才不失效）
UI_VER = "v1"

busy = False              # 当前是否正在生成回复
stop_flag = {"stop": False}

START_TIME = int(time.time())


def push_event(ev):
    """把一个事件塞进事件流，poll 接口靠这个推给前端。"""
    with events_lock:
        events.append(ev)


def _ensure_session():
    """确定当前会话：进程内有就用，没有就取最近一条，数据库还是空的才新建。"""
    global current_session_id
    if current_session_id is None:
        sessions = db.list_sessions(include_archived=True)
        current_session_id = sessions[0]["id"] if sessions else db.create_session()
    return current_session_id


def add_message(kind, text, reasoning_content=""):
    """写一条正式消息到当前会话。kind: me(用户)/gu(AI) → role: user/assistant。"""
    role = "user" if kind == "me" else "assistant"
    return db.add_message(current_session_id, role, text,
                          reasoning_content=reasoning_content)


# ---------- 静态文件：前端也从这个 Flask 一起 served，天然同源 ----------

@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.route("/<path:path>")
def static_files(path):
    return send_from_directory(WEB_DIR, path)


# ---------- 1. 拉历史消息 ----------

@app.route("/api/messages")
def api_messages():
    limit = int(request.args.get("limit", 400))
    before = request.args.get("before")
    sid = _ensure_session()

    page, more = db.get_messages(
        sid, limit=limit,
        before=int(before) if before else None,
        visible_only=True)
    msgs = [{"seq": m["seq"], "kind": KIND_MAP.get(m["role"], m["role"]),
             "text": m["content"], "at": m["at"]} for m in page]

    # F5 修复：upto 返回"事件游标"（事件流当前长度），前端拿它去 poll，
    # 不再和消息 seq 混用——刷新页面不会重放旧事件。
    with events_lock:
        upto = len(events)

    return jsonify({"msgs": msgs, "more": more, "upto": upto})


# ---------- 2. 长轮询：拿新事件 ----------

@app.route("/api/poll")
def api_poll():
    since = int(request.args.get("since", 0))
    # 简单起见用短超时轮询而不是真的 hang 住连接；前端本来就是失败了自己重试
    deadline = time.time() + 12      # 别超过反代的 26 秒上限
    while time.time() < deadline:
        with events_lock:
            new = events[since:]
        if new:
            next_cursor = since + len(new)
            return jsonify({"ver": UI_VER, "next": next_cursor, "events": new})
        time.sleep(0.5)
    return jsonify({"ver": UI_VER, "next": since, "events": []})


# ---------- 3. 发消息 ----------

@app.route("/api/send", methods=["POST"])
def api_send():
    global busy
    data = request.get_json(force=True) or {}
    text = data.get("text", "")

    sid = _ensure_session()
    add_message("me", text)
    push_event({"type": "echo", "text": text})  # 前端立刻把这句话画到聊天里

    stop_flag["stop"] = False
    busy = True
    threading.Thread(target=run_model_turn, args=(sid, text), daemon=True).start()
    return jsonify({"ok": True})


def run_model_turn(sid, user_text):
    """真正调 DeepSeek（OpenAI 兼容）接口，把流式事件透传给前端。"""
    global busy
    err = None
    think_full = ""
    try:
        settings = db.get_settings()

        # 阶段 7：上下文组装 = System(含长期记忆) + 最近可见历史 + 当前消息
        # 历史条数按配置的"轮数"取（一轮 = 一问一答 = 2 条消息）
        history, _ = db.get_messages(
            sid, limit=settings["max_context_rounds"] * 2, visible_only=True)
        payload = []
        for m in history:
            if m["role"] in ("user", "assistant"):
                payload.append({"role": m["role"], "content": m["content"]})

        # 全局长期记忆注入 system 层（最多取优先级最高的 10 条）
        memories = db.get_memories(limit=10)
        system_content = settings["system_prompt"]
        if memories:
            lines = "\n".join("- " + m["content"] for m in memories)
            system_content = system_content + (
                "\n\n【长期记忆】以下是关于她 / 这个家的事，"
                "自然地用上它们，但不要主动说“我记得你…”之类的话：\n" + lines)

        # 跨窗口回顾：去其他窗口的历史和记忆里搜与当前问题相关的片段
        recall = _recall_other_windows(sid, user_text)
        if recall:
            system_content = system_content + (
                "\n\n【回顾·其他窗口】以下是你可能想起来的、其他窗口里聊过的"
                "相关内容（若与当前话题无关就忽略，不要主动提起）：\n" + recall)

        effort = _EFFORT_MAP.get(cur_effort, "high")
        full_text = ""

        stream = get_client().chat.completions.create(
            model=MODEL,
            max_tokens=settings["max_reply_tokens"],
            temperature=settings["temperature"],
            messages=[{"role": "system", "content": system_content}] + payload,
            stream=True,
            reasoning_effort=effort,
        )
        for chunk in stream:
            if stop_flag["stop"]:
                break
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue
            think = _dg(delta, "reasoning_content")
            if think:
                think_full += think
                push_event({
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "thinking_delta", "thinking": think, "text": ""},
                    },
                })
            text = _dg(delta, "content")
            if text:
                full_text += text
                push_event({
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": text, "thinking": ""},
                    },
                })

        if full_text:
            db.add_message(sid, "assistant", full_text,
                           reasoning_content=think_full)
            push_event({
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": full_text}]},
            })

    except Exception as e:
        err = e
    finally:
        busy = False
        if err is not None:
            push_event({
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": f"（出错了：{err}）"}]},
            })
            push_event({"type": "result", "is_error": True, "result": str(err)})
        else:
            push_event({"type": "result"})
        # 阶段 8：回复定稿后检查上下文长度，过长则把最早的几轮压成记忆
        if err is None:
            try:
                _maybe_compress(sid)
            except Exception:
                pass    # 压缩失败不影响聊天
            # 记忆主动提取：判断这轮有没有值得长期记住的信息
            try:
                _extract_turn_memories(sid, user_text, full_text)
            except Exception:
                pass    # 提取失败不影响聊天
            # AI 自动起名：新会话第一次对话后
            try:
                _auto_name_session(sid, user_text, full_text)
            except Exception:
                pass    # 起名失败不影响聊天


# ---------- 上下文长度估算与记忆压缩（阶段 8） ----------

def _estimate_tokens(text):
    """粗略估算 token 数（不引第三方分词）：中英混排约 0.75 token/字符。"""
    return max(1, int(len(text) * 0.75))


def _summarize_with_model(material, settings):
    """用 DeepSeek 把一段对话材料压成一段简短摘要；失败返回空串。"""
    prompt = (
        "把下面这段对话压缩成一段简短的中文摘要（200 字以内），"
        "保留值得长期记住的事实、偏好、进展、未了结的事；"
        "丢弃寒暄和无关内容。\n\n对话材料：\n" + material[:6000]
    )
    try:
        r = get_client().chat.completions.create(
            model=MODEL,
            max_tokens=512,
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}],
        )
        return (r.choices[0].message.content or "").strip()
    except Exception:
        return ""


def _maybe_compress(sid):
    """上下文超阈值时：最早的几轮 → 总结成记忆 → 旧消息 visible=0。

    只保留最近 compress_keep_rounds 轮；被压的消息不再进上下文、不再显示，
    但数据保留在库里。
    """
    settings = db.get_settings()
    history, _ = db.get_messages(sid, limit=100000, visible_only=True)
    total = sum(_estimate_tokens(m["content"]) for m in history)
    if total <= settings["compress_threshold"]:
        return

    keep = settings["compress_keep_rounds"] * 2        # 保留的最近消息条数
    compress_count = len(history) - keep
    if compress_count < 4:                              # 太少不值得压
        return
    to_compress = history[:compress_count]              # 最旧的这些

    material = "\n".join("%s: %s" % (m["role"], m["content"]) for m in to_compress)
    summary = _summarize_with_model(material, settings)
    if summary:
        db.add_memory(summary, metadata={"kind": "summary"}, conversation_id=sid)
        db.hide_messages([m["id"] for m in to_compress])


# ---------- 记忆主动提取（对话后判断哪些值得长期记住） ----------

# 明显不值得提取的短回复 / 寒暄（省一次模型调用）
_TRIVIAL = {"嗯", "好", "好的", "行", "ok", "okay", "继续", "继续吧",
            "谢谢", "哈哈", "哈哈哈", "666", "嗯嗯", "知道了", "收到"}


def _worth_extracting(text):
    """粗筛：太短或寒暄就不值得让模型跑一趟。"""
    t = (text or "").strip()
    if not t or len(t) < 4:
        return False
    if t.lower() in _TRIVIAL:
        return False
    return True


def _ask_memories_to_extract(user_text, reply_text):
    """让 DeepSeek 判断这一轮有没有值得长期记住的信息。返回列表（可为空）。"""
    material = "用户：" + (user_text or "")[:2000] + "\nAI：" + (reply_text or "")[:2000]
    prompt = (
        "你是这个家的记忆管家。根据这一轮对话，判断有没有值得长期记住的信息。\n"
        "值得记住的：用户基本信息、偏好、长期目标、正在进行的项目、"
        "长期学习方向、重要经历、未了结的事、对未来对话有帮助的事实。\n"
        "不值得记住的：寒暄、一次性提问、AI 自己的回复、临时小事。\n"
        "如果没有任何值得记住的，只输出：无\n"
        "如果有，输出 JSON 数组（最多 3 条），每条格式：\n"
        '{"content":"记忆内容（一句话）","kind":"fact|preference|goal|project",'
        '"keywords":["关键词"],"intensity":0到5,"unresolved":0或1}\n'
        "对话内容：\n" + material
    )
    r = get_client().chat.completions.create(
        model=MODEL,
        max_tokens=600,
        temperature=0.2,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = (r.choices[0].message.content or "").strip()
    items = []
    try:
        items = json.loads(raw)
    except Exception:
        m = re.search(r"\[.*\]", raw, re.S)   # 容忍 ```json ``` 或前后有废话
        if m:
            try:
                items = json.loads(m.group(0))
            except Exception:
                items = []
    return items if isinstance(items, list) else []


def _memory_duplicate(content, keywords):
    """去重：已有记忆内容相近，或关键词有重叠，就认为已经记过了。"""
    existing = db.get_memories(limit=300)
    kw = set(keywords)
    for m in existing:
        c = (m["content"] or "").strip()
        if c and (c == content or (len(content) >= 10 and content in c)
                  or (len(c) >= 10 and c in content)):
            return True
        mk = set((m.get("metadata") or {}).get("keywords", []) or [])
        if kw and mk and (kw & mk):
            return True
    return False


def _unresolved_count():
    """当前未了结的记忆条数（“重要必须稀缺”：最多 5 条）。"""
    return sum(1 for m in db.get_memories(limit=500)
               if (m.get("metadata") or {}).get("unresolved"))


def _extract_turn_memories(sid, user_text, reply_text):
    """对话一轮后：粗筛 → 模型判断 → 去重 → 稀缺上限 → 落库。"""
    if not _worth_extracting(user_text):
        return
    for item in _ask_memories_to_extract(user_text, reply_text):
        if not isinstance(item, dict):
            continue
        content = (item.get("content") or "").strip()
        if not content:
            continue
        kind = item.get("kind", "fact")
        if kind not in ("fact", "preference", "goal", "project", "summary"):
            kind = "fact"
        keywords = [str(k) for k in (item.get("keywords") or []) if str(k).strip()]
        try:
            intensity = int(item.get("intensity") or 0)
        except (TypeError, ValueError):
            intensity = 0
        intensity = max(0, min(5, intensity))
        unresolved = 1 if item.get("unresolved") else 0

        if _memory_duplicate(content, keywords):
            continue
        if unresolved and _unresolved_count() >= 5:
            continue    # 重要必须稀缺：未了结最多 5 条
        db.add_memory(content,
                      metadata={"kind": kind, "keywords": keywords,
                                "intensity": intensity, "unresolved": unresolved},
                      conversation_id=sid)


# ---------- AI 自动起名（新会话第一次对话后） ----------

def _ask_for_session_name(user_text, reply_text):
    """让 DeepSeek 根据第一轮对话起个简短的名字；返回名字（可能为空）。"""
    prompt = (
        "根据下面这段对话的开头，给这段聊天起一个简短的名字（10 个字以内），"
        "像给聊天窗口起的备注那样自然、贴切。"
        "只输出名字本身，不要引号、不要解释、不要加句号。\n\n"
        "对话：\n用户：" + (user_text or "")[:200] + "\nAI：" + (reply_text or "")[:200]
    )
    r = get_client().chat.completions.create(
        model=MODEL,
        max_tokens=30,
        temperature=0.7,
        messages=[{"role": "user", "content": prompt}],
    )
    name = (r.choices[0].message.content or "").strip().strip('"').strip("'")
    return name[:20]


def _auto_name_session(sid, user_text, reply_text):
    """新会话第一次对话后自动起名；已有名字（含手动改的）就不动。"""
    s = db.get_session(sid)
    if not s or s["name"]:
        return
    name = _ask_for_session_name(user_text, reply_text)
    if name:
        db.rename_session(sid, name)
        push_event({"type": "system", "subtype": "renamed", "text": name})


# ---------- 跨窗口回顾（翻旧账） ----------

# 常见词停用：搜了全是噪音
_STOPWORDS = {"你", "我", "他", "她", "它", "我们", "你们", "他们", "的", "了", "吗",
              "呢", "啊", "吧", "是", "在", "有", "就", "不", "都", "也", "和", "与",
              "或", "这", "那", "什么", "怎么", "为什么", "一个", "一下", "可以", "能",
              "会", "要", "想", "说", "做", "现在", "今天", "昨天", "明天", "上次",
              "之前", "这个", "那个", "是不是", "有没有", "觉得", "感觉", "然后",
              "但是", "还是", "就是", "因为", "所以", "如果", "可能", "应该", "已经",
              "开始", "时候", "地方", "东西", "事情", "一下", "这些", "那些", "这样"}


def _extract_keywords(text):
    """从一句话里抠检索关键词：中文段切成二字窗口 + 英文整词，过滤停用词。"""
    kws = []
    for run in re.findall(r"[\u4e00-\u9fa5]{2,}", text or ""):
        for i in range(len(run) - 1):
            kws.append(run[i:i + 2])
    for w in re.findall(r"[A-Za-z]{3,}", text or ""):
        kws.append(w.lower())
    seen, out = set(), []
    for w in kws:
        if w in _STOPWORDS or w in seen:
            continue
        seen.add(w)
        out.append(w)
        if len(out) >= 8:
            break
    return out


def _recall_other_windows(sid, user_text):
    """根据当前问题，去其他窗口的历史和记忆里捞相关片段；没有就返回空串。"""
    kws = _extract_keywords(user_text)
    if not kws:
        return ""
    msgs = db.search_messages_across_sessions(kws, limit=5, exclude_sid=sid)
    mems = db.search_memories_by_keywords(kws, limit=3)
    if not msgs and not mems:
        return ""

    names = {s["id"]: s["name"] or "另一个窗口"
             for s in db.list_sessions(include_archived=True)}
    parts = []
    for m in msgs:
        name = names.get(m["session_id"], "另一个窗口")
        ts = time.strftime("%m-%d %H:%M", time.localtime(m["at"]))
        parts.append("【%s · %s】%s" % (name, ts, (m["content"] or "")[:80]))
    for mm in mems:
        parts.append("（记忆）%s" % (mm["content"] or "")[:80])
    return "\n".join(parts)[:1500]


# ---------- 4. 打断 ----------

@app.route("/api/stop", methods=["POST"])
def api_stop():
    stop_flag["stop"] = True
    return jsonify({"ok": True})


# ---------- 5. 新会话 ----------

@app.route("/api/newchat", methods=["POST"])
def api_newchat():
    global current_session_id
    data = request.get_json(silent=True) or {}
    if data.get("arm") is False:
        # 前端"取消换新"：什么也不做，还住在当前会话
        return jsonify({"ok": True})
    current_session_id = db.create_session()   # 历史留在库里，界面开新一段
    # 注意：不清 events——事件游标是连续的，清了会导致新会话的事件收不到
    push_event({"type": "system", "subtype": "newchat", "text": "新的一段开始了"})
    return jsonify({"ok": True})


# ---------- 6. 状态查询 ----------

@app.route("/api/status")
def api_status():
    return jsonify({"busy": busy, "alive": True, "since": START_TIME, "armed": False})


# ---------- 7. 前端加载时就要用到的小接口（先给最小实现） ----------

@app.route("/api/model", methods=["GET", "POST"])
def api_model():
    global MODEL, cur_effort
    if request.method == "POST":
        data = request.get_json(force=True) or {}
        if data.get("model"):
            MODEL = data["model"]
        if data.get("effort"):
            cur_effort = data["effort"]
    return jsonify({"model": MODEL, "effort": cur_effort})


@app.route("/api/authmode")
def api_authmode():
    return jsonify({"mode": "api"})


def _chat_preview(sid):
    """会话列表用的预览：最后一条可见消息的开头。"""
    page, _ = db.get_messages(sid, limit=1, visible_only=True)
    return (page[-1]["content"] or "")[:60] if page else ""


@app.route("/api/chats")
def api_chats():
    """会话列表。scope=live 在用 / box 已收纳。"""
    scope = request.args.get("scope", "live")
    wanted = 1 if scope == "box" else 0
    items = []
    for s in db.list_sessions(include_archived=True):
        if s["archived"] != wanted:
            continue
        items.append({
            "id": s["id"],
            "name": s["name"] or "",
            "current": s["id"] == current_session_id,
            "archived": bool(s["archived"]),
            "preview": _chat_preview(s["id"]),
            "last": s["updated_at"],
            "created": s["created_at"],
        })
    return jsonify({"items": items})


@app.route("/api/chats", methods=["POST"])
def api_chats_post():
    """会话动作：switch 切换 / rename 改名 / archive 收纳（on=true/false）。"""
    global current_session_id
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    sid = data.get("id")
    if sid == "CURRENT":
        sid = _ensure_session()
    try:
        sid = int(sid)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "why": "bad id"}), 400

    if action == "switch":
        current_session_id = sid
        return jsonify({"ok": True})
    if action == "rename":
        db.rename_session(sid, (data.get("name") or "").strip()[:60])
        return jsonify({"ok": True})
    if action == "archive":
        on = 1 if data.get("on") else 0
        db.set_session_archived(sid, on)
        if on and sid == current_session_id:
            current_session_id = db.create_session()   # 收起来后落到新窗
        return jsonify({"ok": True})
    return jsonify({"ok": False, "why": "unknown action"}), 400


# ---------- 8. 记忆管理（Memory Center） ----------

def _memory_source_name(cid):
    """记忆的来源会话名（显示用）。"""
    if not cid:
        return ""
    s = db.get_session(cid)
    return (s["name"] or ("会话 #%d" % cid)) if s else ""


def _memory_to_client(m):
    """db 记忆行 → 前端要的 JSON 结构。"""
    meta = m.get("metadata") or {}
    return {
        "id": m["id"],
        "content": m["content"],
        "kind": meta.get("kind", "fact"),
        "keywords": meta.get("keywords") or [],
        "intensity": meta.get("intensity", 0),
        "unresolved": 1 if meta.get("unresolved") else 0,
        "conversation_id": m.get("conversation_id"),
        "source_message_id": m.get("source_message_id"),
        "created_at": m["created_at"],
        "updated_at": m["updated_at"],
        "source": _memory_source_name(m.get("conversation_id")),
    }


@app.route("/api/memories")
def api_memories():
    """记忆列表：?q= 搜索内容/关键词，?kind= 分类，?active=0 看已删除。"""
    q = (request.args.get("q") or "").strip() or None
    kind = (request.args.get("kind") or "").strip() or None
    active = request.args.get("active")
    if active is None:
        active_only = True          # 默认只看 active=1
    else:
        active_only = active in ("1", "true", "yes")
    rows = db.search_memories(q=q, kind=kind, active_only=active_only, limit=300)
    return jsonify({"ok": True, "memories": [_memory_to_client(m) for m in rows]})


@app.route("/api/memories/<int:mid>", methods=["PATCH"])
def api_memory_update(mid):
    """编辑一条记忆：可改 content / kind / keywords / unresolved。"""
    data = request.get_json(silent=True) or {}
    content = data.get("content")
    kind = data.get("kind")
    keywords = data.get("keywords")
    unresolved = data.get("unresolved")

    if content is not None and (not isinstance(content, str) or not content.strip()):
        return jsonify({"ok": False, "error": "内容不能为空"}), 400
    if kind is not None and kind not in ("fact", "preference", "goal", "project", "summary"):
        return jsonify({"ok": False, "error": "类型不合法"}), 400
    if keywords is not None:
        if not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords):
            return jsonify({"ok": False, "error": "关键词必须是字符串数组"}), 400
        keywords = [k.strip() for k in keywords if k.strip()]

    updated = db.update_memory(
        mid,
        content=content.strip() if isinstance(content, str) else None,
        kind=kind,
        keywords=keywords,
        unresolved=unresolved,
    )
    if updated is None:
        return jsonify({"ok": False, "error": "记忆不存在"}), 404
    return jsonify({"ok": True, "memory": _memory_to_client(updated)})


@app.route("/api/memories/<int:mid>", methods=["DELETE"])
def api_memory_delete(mid):
    """删除记忆：软删除（active=0），数据保留。"""
    if db.get_memory(mid) is None:
        return jsonify({"ok": False, "error": "记忆不存在"}), 404
    db.deactivate_memory(mid)
    return jsonify({"ok": True})


@app.route("/api/memories/<int:mid>/restore", methods=["POST"])
def api_memory_restore(mid):
    """恢复一条软删除的记忆（active=1）。"""
    if db.get_memory(mid) is None:
        return jsonify({"ok": False, "error": "记忆不存在"}), 404
    db.restore_memory(mid)
    return jsonify({"ok": True})


# ---------- 9. 头像库 ----------
# 头像文件放 data/avatars/（不进 git），当前用哪张记在 data/ui.json。
# 默认两张是 static 里的 SVG：一只鲸鱼 + 一个"我"。

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
AVATAR_DIR = os.path.join(DATA_DIR, "avatars")
UI_JSON = os.path.join(DATA_DIR, "ui.json")

AV_BUILTIN = [
    {"name": "whale.svg", "url": "avatars/whale.svg", "label": "鲸鱼"},
    {"name": "me.svg", "url": "avatars/me.svg", "label": "默认的我"},
]
AV_FALLBACK = {"ai": "whale.svg", "me": "me.svg"}
AV_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
AV_MAX_BYTES = 8 * 1024 * 1024


def _ui_read():
    try:
        with open(UI_JSON, "r", encoding="utf-8") as f:
            d = json.load(f)
            if isinstance(d, dict):
                return d
    except Exception:
        pass
    return {}


def _ui_write(d):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(UI_JSON, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    return d


def _avatar_name(slot):
    """某一格现在用的是哪个文件（ai / me）。"""
    return _ui_read().get("%s_avatar" % slot) or AV_FALLBACK[slot]


def _avatar_url(name):
    if not name or name in ("whale.svg", "me.svg"):
        return "avatars/" + (name or "whale.svg")
    return "ava/" + name


def _avatar_payload():
    items = [dict(x, builtin=True, url=x["url"]) for x in AV_BUILTIN]
    if os.path.isdir(AVATAR_DIR):
        names = [f for f in os.listdir(AVATAR_DIR)
                 if os.path.splitext(f)[1].lower() in AV_EXTS
                 and os.path.isfile(os.path.join(AVATAR_DIR, f))]
        names.sort(key=lambda f: os.path.getmtime(os.path.join(AVATAR_DIR, f)),
                   reverse=True)
        for fn in names:
            items.append({"name": fn, "url": "ava/" + fn, "builtin": False,
                          "at": int(os.path.getmtime(os.path.join(AVATAR_DIR, fn)))})
    picked = {"ai": _avatar_name("ai"), "me": _avatar_name("me")}
    return {
        "ok": True,
        "items": items,
        "current": {"ai": _avatar_url(picked["ai"]), "me": _avatar_url(picked["me"])},
        "picked": picked,
    }


@app.route("/ava/<path:name>")
def avatar_file(name):
    return send_from_directory(AVATAR_DIR, name)


@app.route("/api/avatars")
def api_avatars():
    return jsonify(_avatar_payload())


@app.route("/api/avatars", methods=["POST"])
def api_avatar_upload():
    f = request.files.get("file")
    if f is None or not (f.filename or "").strip():
        return jsonify({"ok": False, "error": "没收到图片"}), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in AV_EXTS:
        ext = ".jpg"
    data = f.read()
    if not data:
        return jsonify({"ok": False, "error": "图片是空的"}), 400
    if len(data) > AV_MAX_BYTES:
        return jsonify({"ok": False, "error": "图片太大了（上限 8MB）"}), 400
    os.makedirs(AVATAR_DIR, exist_ok=True)
    name = "av-%d-%04d%s" % (int(time.time()), random.randint(0, 9999), ext)
    with open(os.path.join(AVATAR_DIR, name), "wb") as fp:
        fp.write(data)
    return jsonify({"ok": True, "item": {"name": name, "url": "ava/" + name,
                                         "builtin": False},
                    "picked": {"ai": _avatar_name("ai"), "me": _avatar_name("me")}})


@app.route("/api/avatars/<path:name>", methods=["DELETE"])
def api_avatar_delete(name):
    safe = os.path.basename(name)
    if safe in ("whale.svg", "me.svg"):
        return jsonify({"ok": False, "error": "默认头像不能删"}), 400
    path = os.path.join(AVATAR_DIR, safe)
    if not os.path.isfile(path):
        return jsonify({"ok": False, "error": "没有这张"}), 404
    os.remove(path)
    d = _ui_read()
    changed = False
    for slot in ("ai", "me"):
        if d.get("%s_avatar" % slot) == safe:
            d.pop("%s_avatar" % slot, None)
            changed = True
    if changed:
        _ui_write(d)
    return jsonify(_avatar_payload())


@app.route("/api/avatar", methods=["POST"])
def api_avatar_pick():
    data = request.get_json(silent=True) or {}
    slot = data.get("slot")
    name = (data.get("name") or "").strip()
    if slot not in ("ai", "me"):
        return jsonify({"ok": False, "error": "slot 只能是 ai 或 me"}), 400
    if name not in ("whale.svg", "me.svg"):
        safe = os.path.basename(name)
        if not safe or not os.path.isfile(os.path.join(AVATAR_DIR, safe)):
            return jsonify({"ok": False, "error": "没有这张头像"}), 404
        name = safe
    d = _ui_read()
    d["%s_avatar" % slot] = name
    _ui_write(d)
    out = _avatar_payload()
    out["slot"] = slot
    return jsonify(out)


def _lan_ips():
    """检测电脑的局域网 IP（给手机访问用）。"""
    ips = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                ips.add(ip)
    except Exception:
        pass
    return sorted(ips)


def _print_startup_info():
    """启动时在控制台打印状态：key 有没有（打码）、模型、本机和手机访问地址。"""
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    masked = ("sk-****" + key[-4:]) if key else "未设置（聊天时会提示你配好）"
    print("=" * 44)
    print(" dwell 聊天服务器")
    print("=" * 44)
    print(" 模型   :", MODEL)
    print(" API key:", masked)
    print(" 本机   : http://127.0.0.1:%d/" % PORT)
    if HOST in ("0.0.0.0", "::"):
        for ip in _lan_ips():
            print(" 手机访问: http://%s:%d/  （手机连同一个 WiFi）" % (ip, PORT))
    print("=" * 44)


if __name__ == "__main__":
    # debug 重载器会先起父进程再起子进程：只有真正干活的子进程才有
    # WERKZEUG_RUN_MAIN=true，打印状态和开浏览器都只在这一份里做一次。
    if not DEBUG or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        _print_startup_info()
        if (os.environ.get("AUTO_OPEN", "1") != "0"
                and HOST in ("127.0.0.1", "localhost")):
            threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:%d/" % PORT)).start()
    app.run(host=HOST, port=PORT, debug=DEBUG)
