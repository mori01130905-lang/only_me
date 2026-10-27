# -*- coding: utf-8 -*-
"""dwell 后端生产入口（用 pythonw 跑，没有控制台，不会被别的窗口带走）。

启动方式（由计划任务 dwell-server 调用）：
    pythonw.exe serve.py

它做三件事：
  1. 用 waitress 起 Flask（单进程多线程，够一个人用）
  2. 日志写 data/server.log（按大小滚动，10MB × 3）
  3. 启动时顺手确认 tailscale serve 的 443 → 5000 还在
"""

import logging
import logging.handlers
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

LOG_DIR = os.path.join(ROOT, "data")
os.makedirs(LOG_DIR, exist_ok=True)

handler = logging.handlers.RotatingFileHandler(
    os.path.join(LOG_DIR, "server.log"), maxBytes=10 * 1024 * 1024,
    backupCount=3, encoding="utf-8")
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                                       "%Y-%m-%d %H:%M:%S"))
logging.basicConfig(level=logging.INFO, handlers=[handler])
log = logging.getLogger("dwell")


def _keep_serve():
    """确认 Tailscale 的 https 443 还指着本机 5000（幂等）。"""
    exe = r"C:\Program Files\Tailscale\tailscale.exe"
    if not os.path.exists(exe):
        log.warning("没找到 Tailscale，跳过 serve 配置")
        return
    try:
        subprocess.run([exe, "serve", "--bg", "5000"], capture_output=True, timeout=20)
        log.info("tailscale serve 已确认（443 -> 127.0.0.1:5000）")
    except Exception as e:
        log.warning("tailscale serve 配置失败：%s" % e)


if __name__ == "__main__":
    _keep_serve()
    from waitress import serve
    from app import app

    host, port = "127.0.0.1", 5000
    log.info("=== dwell 后端启动：http://%s:%d（waitress，8 线程）===" % (host, port))
    try:
        serve(app, host=host, port=port, threads=8, ident="dwell",
              channel_timeout=120, cleanup_interval=30)
    except Exception as e:
        log.exception("后端挂了：%s", e)
        raise
