# 上云部署（Render + Supabase + Netlify）

目标：手机随时打开、不依赖家里电脑。架构和你那份 PDF 一样，只是后端用 Python（Render 原生支持，不用重写成 Node）。

```
手机 / 电脑
    │  https://<你的>.netlify.app          ← 前端（静态托管，永久地址，可装主屏）
    ▼
Netlify  ──/api/* 反向代理──►  https://<你的>.onrender.com   ← 后端（Flask，跑在 Render）
                                        │
                                        ▼
                                   Supabase（Postgres：消息 / 会话 / 记忆 / 设置）
```

代码这边已经改完了（`db.py` 双后端、`app.py` 口令登录、`render.yaml`、PWA 资产），
剩下的都是"点几下"的事。

---

## 第 1 步：Supabase 建库（约 3 分钟）

1. 打开 <https://supabase.com> → 用 GitHub 登录 → **New project**
2. 填：
   - Name：`dwell`
   - Database Password：**记下来**（后面连接串要用）
   - Region：**Southeast Asia (Singapore)**（离国内最近）
3. 建好后：**Project Settings → Database → Connection string → Connection pooling**
4. 复制那条 URI，把 `[YOUR-PASSWORD]` 换成你刚设的密码，应该长这样：

```
postgresql://postgres.abcdefgh:[你的密码]@aws-1-ap-southeast-1.pooler.supabase.com:6543/postgres
```

> ⚠️ **一定要用 Connection pooling（带 `pooler.supabase.com` 的那条）**。
> Supabase 的 Direct connection 现在只给 IPv6，而 Render 免费实例只有 IPv4，连不上。

## 第 2 步：把本地数据搬上去（约 1 分钟）

在 `D:\only_me` 下执行（连接串换成你上面复制的那条）：

```powershell
.\.venv\Scripts\python.exe -m pip install "psycopg[binary]"
.\.venv\Scripts\python.exe scripts\migrate_to_pg.py --target "postgresql://postgres.xxxx:密码@aws-1-ap-southeast-1.pooler.supabase.com:6543/postgres"
```

看到 `云端：1 个会话 / 92 条消息 / 1 条记忆 —— 搬完了` 就成了。
（重复跑是安全的：同 id 会跳过。）

## 第 3 步：Render 部署后端（约 5 分钟）

1. 打开 <https://render.com> → 用 GitHub 登录
2. **New +** → **Blueprint** → 选仓库 `only_me`（仓库里已经有 `render.yaml`）
   - 如果 Blueprint 找不到仓库，就选 **Web Service** → 填 Public Git URL：
     `https://github.com/mori01130905-lang/only_me`
     然后手动填：Runtime = Python 3、Build = `pip install -r requirements.txt`、
     Start = `gunicorn app:app --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:$PORT`
3. 它会让你填三个环境变量（`render.yaml` 里标了 `sync: false` 的那几个）：

| 变量 | 值 |
|---|---|
| `DWELL_DATABASE_URL` | 第 1 步那条连接串 |
| `DEEPSEEK_API_KEY` | 你的 DeepSeek key（就在本机 `.env` 里） |
| `DWELL_PASS` | 你自己定一个登录口令（手机打开时要输一次） |

`SECRET_KEY` 会自动生成，不用管。

4. 等它构建完（第一次 2~4 分钟），访问 `https://<你的服务>.onrender.com/healthz`，
   能看到 `{"db":"postgres","ok":true,...}` 就说明后端通了、而且连的是 Supabase。

## 第 4 步：Netlify 放前端（约 2 分钟）

1. 打开 `web/_redirects.template`，把里面的 `https://REPLACE-ME.onrender.com`
   换成你第 3 步拿到的 Render 地址，然后**把文件改名为 `_redirects`**（去掉 `.template`）
2. 打开 <https://app.netlify.com> → 你的站点 → **Deploys**
3. 把 **`D:\only_me\web` 这个文件夹**直接拖进页面上的虚线框（拖文件夹本身，不是里面的文件）
4. 等十几秒，打开 `https://earnest-froyo-ea99d0.netlify.app` → 应该出现口令输入框

> 为什么用拖拽：不用把 Netlify 账号授权给任何工具，也不影响你原来的部署方式。
> 以后想推送自动部署，可以再连 GitHub（Publish directory 填 `web`）。

## 第 5 步：保活（很重要，2 分钟）

Render 免费实例 **15 分钟没人访问就会睡**，醒来要 30 秒——而 Netlify 的反向代理
26 秒就超时，所以刚打开时会觉得"点了没反应"。

去 <https://uptimerobot.com> 注册（免费）→ **Add New Monitor**：

- Monitor Type：`HTTP(s)`
- URL：`https://<你的服务>.onrender.com/healthz`
- Interval：`5 minutes`

这样服务一直醒着，打开就是即时的。

> 免费额度：Render 每月 750 小时，一个服务常驻 730 小时，刚好放得下。
> 如果你还有别的免费服务，就会超额，那时候再考虑升级或改回睡觉+等待。

## 第 6 步：装到手机主屏

用手机浏览器打开 `https://earnest-froyo-ea99d0.netlify.app`，输入口令进去后：

- **iPhone**：分享 → 添加到主屏幕
- **Android**：菜单 → 添加到主屏幕 / 安装应用

之后从桌面图标打开，没有地址栏，跟 App 一样。

---

## 验收清单

| 检查 | 怎么验 |
|---|---|
| 后端活着 | `https://<render>/healthz` 返回 `db: postgres` |
| 数据库连上 | 上面那行返回后，进 Supabase → Table Editor 能看到 `sessions/messages/memories/settings` 四张表 |
| 页面能开 | Netlify 地址出现口令框，输入后进聊天 |
| 能聊 | 发一句话，AI 正常流式回复 |
| 刷新不丢 | F5 后历史还在 |
| 记忆在 | 设置 → 我的记忆，能看到之前那条 |
| 手机可用 | 手机 4G（关 WiFi）也能打开 → 证明真的不依赖你家网络 |

## 出问题先看这里

| 现象 | 多半是 |
|---|---|
| Netlify 打开 404 / 转圈 | `_redirects` 没改名，或里面的 Render 地址写错了 |
| 打开很慢，第一次要等半分钟 | Render 在冷启动 → 检查 UptimeRobot 有没有配 |
| 提示"数据库连不上" | 用了 Direct 连接串（IPv6）；换成 Connection pooling 那条 |
| 输入口令一直转 | `DWELL_PASS` 没设（那样反而不设防）或 Render 构建失败，看 Logs |
| 聊天报 401 | 登录 cookie 过期了，重新打开地址输一次口令 |
| 回复很慢 | 正常：DeepSeek 首字延迟 + Render 新加坡到国内网络 |

## 成本

| 项 | 免费额度 | 够不够 |
|---|---|---|
| Render Web Service | 750 小时/月 | 一个常驻服务 730 小时，刚好 |
| Supabase | 500MB Postgres，闲置 7 天暂停 | 你这个量级用不到 1MB，够用几年 |
| Netlify | 100GB 流量/月 | 静态页面 + 代理，够用 |
| DeepSeek | 按量 | 一次对话几分钱 |

> Supabase 免费项目 **7 天没有任何请求会被暂停**。只要你每周聊一次，或者把
> UptimeRobot 也指向 Supabase 的 REST 健康端点，就不会被暂停（被暂停也能一键恢复，数据不丢）。
