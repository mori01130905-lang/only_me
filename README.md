# dwell —— 我自己的小界面

一个住在本地浏览器里的聊天界面，后端接 DeepSeek API（`deepseek-v4-flash`）。

## 快速开始

### 一键启动（推荐）

**双击 `start.bat`**：

- 第一次双击：会问你要一次 DeepSeek API key，输一次就记住了（存在 `.env`，不会提交进 git）
- 之后双击：直接启动，浏览器会自动打开聊天页
- 关掉黑色窗口 = 停止服务
- 想换 key：删掉 `.env` 再双击 `start.bat`

### 手动启动

```powershell
cd D:\only_me
.venv\Scripts\activate
$env:DEEPSEEK_API_KEY = '你的 key'   # 或用 .env 文件
python app.py
```

浏览器打开 <http://127.0.0.1:5000/>（`AUTO_OPEN=0` 可以关掉自动开浏览器）

### 现在实际在用的方案（手机随时能用）

见 [RUNBOOK.md](RUNBOOK.md)：家里电脑 + Tailscale，网址 `https://dwell.taild9fc55.ts.net`，不要卡。

### 上云（以后想彻底摆脱电脑再看）

后端跑在 Render、数据库用 Supabase 的 Postgres、前端放 Netlify，
一步步的操作写在 [DEPLOY.md](DEPLOY.md) 里。设了 `DWELL_DATABASE_URL`
就会自动切到云端数据库，代码一行都不用改。

## 环境变量

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | 必填，DeepSeek API key | 无 |
| `DEEPSEEK_BASE_URL` | API 地址 | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | 模型名 | `deepseek-v4-flash` |
| `AUTO_OPEN` | 启动时是否自动开浏览器 | `1` |
| `DWELL_HOST` | 监听地址（`0.0.0.0` 才允许局域网访问） | `127.0.0.1` |
| `DWELL_PORT` | 端口 | `5000` |
| `DWELL_DATABASE_URL` | 以 `postgres` 开头就用 Postgres，否则用本地 SQLite | 无 |
| `DWELL_PASS` | 访问口令；设了就要先登录，不设则本机不设防 | 无 |
| `SECRET_KEY` | 登录 cookie 的签名密钥 | 按口令派生 |
| `DWELL_DEBUG` | 设 `1` 开调试模式（不要用于公网） | `0` |

也可以用项目目录下的 `.env` 文件配置（每行 `KEY=VALUE`，真实环境变量优先）。

## 目录结构

- `app.py` —— Flask 后端：聊天主链路（发送 / 流式事件 / 历史 / 打断 / 新会话）+ 前端加载时要用的小接口
- `web/index.html` —— 前端界面（聊天、锁屏、设置等）
- `start.bat` —— 一键启动入口（双击）
- `db.py` —— 存储接口层：本地 SQLite / 云端 Postgres，一套接口两种底座
- `render.yaml` —— Render 部署蓝图
- `scripts/migrate_to_pg.py` —— 把本地库搬到 Supabase
- `web/manifest.json`、`web/sw.js`、`web/icons/` —— PWA 用的资产
- `DEPLOY.md` —— 上云部署步骤
- `requirements.txt` —— Python 依赖（`flask` + `openai`）

## 当前进度

- [x] 聊天打通：页面 → 后端 → DeepSeek 流式回复
- [x] 一键启动（`start.bat`）
- [x] 数据层（V0.2）：
  - [x] 数据库：SQLite（`data/dwell.db`，`sessions / messages / memories / settings` 四张表）
  - [x] 会话与消息持久化（重启自动恢复最近会话、role↔kind 映射、可见性过滤）
  - [x] 长期记忆读写（全局共享、未了结/情绪强度优先级排序、软删除）
  - [x] 全局配置读写（settings 单行，默认值对齐原硬编码）
  - [x] 上下文组装（System Prompt + 长期记忆 + 最近可见历史 → DeepSeek）
  - [x] 记忆压缩（超阈值 → 摘要存入 memories → 旧消息 `visible=0`，数据保留）
  - [x] 记忆主动提取（每轮对话后 AI 自己判断哪些信息值得长期记住，去重 + 稀缺上限）
  - [x] 会话列表 / 切换 / 改名 / 收纳（`/api/chats`）
  - [x] AI 自动起名（新会话第一轮对话后自动起名，手动改名保留优先）
  - [x] 跨窗口回顾（切换窗口后，AI 能按关键词检索其他窗口的历史和记忆）
  - [x] Memory Center（设置 → 我的记忆：查看 / 搜索 / 分类 / 详情 / 编辑 / 软删除 / 恢复）
- [x] 上云能力（V0.3）：
  - [x] 数据层双后端（本地 SQLite / 云端 Postgres），函数接口不变
  - [x] 口令登录 + 登录页（公网可用）
  - [x] 生产部署（gunicorn 单进程多线程、健康检查、关调试）
  - [x] PWA：manifest / 图标 / service worker，可装到手机主屏
  - [x] Netlify 反向代理模板 + Render 蓝图 + 数据搬迁脚本
- [ ] 日记 / 待办 / 日历等独立功能
