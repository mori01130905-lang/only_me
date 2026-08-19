# -*- coding: utf-8 -*-
"""
dwell 前端 —— 聊天主线最小后端（DeepSeek 版）

先跑这一个模块，把它接通了，再去接日记/待办/日历那些独立小功能。

需要的环境变量：
  DEEPSEEK_API_KEY   你的 DeepSeek API key

可选环境变量：
  DEEPSEEK_BASE_URL  默认 https://api.deepseek.com
  DEEPSEEK_MODEL     默认 deepseek-v4-flash

运行方式：
  pip install flask openai
  $env:DEEPSEEK_API_KEY = 'sk-xxxx'
  python app.py
  然后浏览器打开 http://127.0.0.1:5000/
"""

import os
import threading
import time
import webbrowser
from itertools import count

from flask import Flask, jsonify, request, send_from_directory
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

# ---------- 基础配置 ----------

app = Flask(__name__, static_folder=None)

BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
SYSTEM_PROMPT = "你是住在这个界面里的 AI，说话自然、简洁，不要暴露你是通过 API 调用的。"

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


# ---------- 内存态存储（先用内存，能跑通了再换数据库） ----------

messages_lock = threading.Lock()
messages = []            # 完整对话历史：[{seq, kind, text, at}, ...]  kind: me=用户 / gu=AI
seq_counter = count(1)

events_lock = threading.Lock()
events = []              # 事件流，poll 用游标(since)增量读取
UI_VER = "v1"

busy = False              # 当前是否正在生成回复
stop_flag = {"stop": False}

START_TIME = int(time.time())


def push_event(ev):
    """把一个事件塞进事件流，poll 接口靠这个推给前端。"""
    with events_lock:
        events.append(ev)


def add_message(kind, text):
    """写入一条正式消息（用户说的/AI说的），api/messages 靠这个读历史。"""
    with messages_lock:
        seq = next(seq_counter)
        m = {"seq": seq, "kind": kind, "text": text, "at": int(time.time())}
        messages.append(m)
        return seq


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

    with messages_lock:
        pool = messages
        if before:
            before = int(before)
            pool = [m for m in pool if m["seq"] < before]
        page = pool[-limit:]
        more = len(pool) > limit
        upto = messages[-1]["seq"] if messages else 0

    return jsonify({"msgs": page, "more": more, "upto": upto})


# ---------- 2. 长轮询：拿新事件 ----------

@app.route("/api/poll")
def api_poll():
    since = int(request.args.get("since", 0))
    # 简单起见用短超时轮询而不是真的 hang 住连接；前端本来就是失败了自己重试
    deadline = time.time() + 25
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

    add_message("me", text)
    push_event({"type": "echo", "text": text})  # 前端立刻把这句话画到聊天里

    stop_flag["stop"] = False
    busy = True
    threading.Thread(target=run_model_turn, args=(text,), daemon=True).start()
    return jsonify({"ok": True})


def run_model_turn(user_text):
    """真正调 DeepSeek（OpenAI 兼容）接口，把流式事件透传给前端。"""
    global busy
    err = None
    try:
        # 简单版本：只用最近一些历史做上下文，不做 tool_use / 附件
        with messages_lock:
            history = messages[-40:]
        payload = []
        for m in history:
            if m["kind"] == "me":
                payload.append({"role": "user", "content": m["text"]})
            elif m["kind"] == "gu":
                payload.append({"role": "assistant", "content": m["text"]})

        effort = _EFFORT_MAP.get(cur_effort, "high")
        full_text = ""

        stream = get_client().chat.completions.create(
            model=MODEL,
            max_tokens=4096,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + payload,
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
            add_message("gu", full_text)
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


# ---------- 4. 打断 ----------

@app.route("/api/stop", methods=["POST"])
def api_stop():
    stop_flag["stop"] = True
    return jsonify({"ok": True})


# ---------- 5. 新会话 ----------

@app.route("/api/newchat", methods=["POST"])
def api_newchat():
    with messages_lock, events_lock:
        messages.clear()
        events.clear()
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


@app.route("/api/chats")
def api_chats():
    return jsonify({"items": []})


def _print_startup_info():
    """启动时在控制台打印状态：key 有没有（打码）、用的哪个模型、地址。"""
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    masked = ("sk-****" + key[-4:]) if key else "未设置（聊天时会提示你配好）"
    print("=" * 44)
    print(" dwell 聊天服务器")
    print("=" * 44)
    print(" 模型   :", MODEL)
    print(" API key:", masked)
    print(" 地址   : http://127.0.0.1:5000/")
    print("=" * 44)


if __name__ == "__main__":
    # debug 重载器会先起父进程再起子进程：只有真正干活的子进程才有
    # WERKZEUG_RUN_MAIN=true，打印状态和开浏览器都只在这一份里做一次。
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        _print_startup_info()
        if os.environ.get("AUTO_OPEN", "1") != "0":
            threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:5000/")).start()
    app.run(debug=True, port=5000)
