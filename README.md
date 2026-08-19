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

## 环境变量

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | 必填，DeepSeek API key | 无 |
| `DEEPSEEK_BASE_URL` | API 地址 | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | 模型名 | `deepseek-v4-flash` |
| `AUTO_OPEN` | 启动时是否自动开浏览器 | `1` |

也可以用项目目录下的 `.env` 文件配置（每行 `KEY=VALUE`，真实环境变量优先）。

## 目录结构

- `app.py` —— Flask 后端：聊天主链路（发送 / 流式事件 / 历史 / 打断 / 新会话）+ 前端加载时要用的小接口
- `web/index.html` —— 前端界面（聊天、锁屏、设置等）
- `start.bat` —— 一键启动入口（双击）
- `requirements.txt` —— Python 依赖（`flask` + `openai`）

## 当前进度

- [x] 聊天打通：页面 → 后端 → DeepSeek 流式回复
- [x] 一键启动（`start.bat`）
- [ ] 日记 / 待办 / 日历等独立功能（后端接口还没写）
