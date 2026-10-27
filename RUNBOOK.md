# dwell 运行手册（现在这套是怎么跑起来的）

> 写给三个月后的自己：忘了就照这个查。

## 一句话架构

```
手机 / 电脑 ──(Tailscale 虚拟局域网)──► 这台电脑
                                         ├─ tailscaled：https 443 → 本机 5000
                                         └─ pythonw serve.py → waitress → Flask(app.py)
                                                                       └─ data/dwell.db（SQLite）
```

**没有公网暴露**：只有登录了同一个 Tailscale 账号的设备才能打开。
**不需要任何账号费、域名、信用卡。**

## 网址与口令

| | |
|---|---|
| 地址 | `https://dwell.taild9fc55.ts.net` |
| 口令 | 在 `D:\only_me\.env` 的 `DWELL_PASS=` 里（现在是 `dwell2026`） |
| 换口令 | 改 `.env` 那一行 → 运行 `scripts\restart-server.ps1` |

## 谁在后台跑

| 组件 | 怎么起来的 | 挂了怎么办 |
|---|---|---|
| `pythonw.exe serve.py` | 计划任务 `dwell-server`（登录 Windows 时自动启动） | 任务设了失败重试 5 次；还不行就运行 `scripts\restart-server.ps1` |
| `tailscaled` | Tailscale 自己的服务（开机自启） | 服务里重启 Tailscale；`tailscale up` 重新登录 |
| `https 443 → 5000` | `serve.py` 启动时会自动 `tailscale serve --bg 5000` | 同上，或手动执行那条命令 |

日志：`D:\only_me\data\server.log`（10MB 滚动，留 3 份）

## 日常操作

```powershell
# 重启后端
D:\only_me\scripts\restart-server.ps1

# 看它活没活
curl.exe -sS https://dwell.taild9fc55.ts.net/healthz

# 看日志
Get-Content D:\only_me\data\server.log -Tail 30 -Encoding UTF8

# 备份数据（强烈建议每月一次，复制到网盘/U盘）
Copy-Item D:\only_me\data\dwell.db "D:\备份\dwell-$(Get-Date -f yyyyMMdd).db"
```

> 备份要连 `dwell.db` 一起看：如果同时存在 `-wal`/`-shm`，先把服务停一下再复制，
> 或者直接用 `sqlite3 data\dwell.db ".backup 备份.db"`。

## 三个必须记住的限制

1. **电脑睡眠 = 网站下线**。电源设置里把"睡眠"改成"从不"。
2. **重启后要登录 Windows** 才会自启（计划任务是登录触发的）。
3. **手机上的 Tailscale 开关要留着开**，关了就打不开。

## 为什么不用 Netlify / Render

- **Render**：免费版要绑信用卡做风控验证，国内账号经常过不去；而且免费实例 15 分钟
  没人访问就休眠，醒来 30 秒，跟 Netlify 26 秒的反代超时打架。
- **Netlify**：只能放静态页面，连不到私网地址。除非 Tailscale 的 Funnel 能对外发布
  （这次试了十几分钟公网 DNS 都没生效，可能是新 tailnet 需要验证）。

想让 `xxx.netlify.app` 这个门面也能用，需要先让 Funnel 通；那条路代码不用改，
只是把 `web/_redirects` 里的地址指过去。

## 以后想搬去云服务器（不依赖电脑）

代码已经准备好了：设了 `DWELL_DATABASE_URL` 就走 Postgres，不设就走本地 SQLite。
1. 买台轻量服务器（香港，支付宝，不要卡，¥30~60/月）
2. `pip install -r requirements.txt` + `gunicorn app:app --workers 1 --threads 8`
3. Caddy 反代 + 自动 HTTPS
4. 数据库照旧用本地 SQLite 也行（单用户完全够），想上 Supabase 就跑 `scripts/migrate_to_pg.py`

## 还没做的（想继续时从这里挑）

- 前端还有约 30 个接口没后端：待办、日历、日记、留言板、搜索、共读、日报、仓库、健康
- 附件（发图片）后端还没接，现在会被丢掉
- 思考过程存了但 `/api/messages` 没返回，刷新后看不到
- 会话列表 / `settings` 的编辑界面还没有
