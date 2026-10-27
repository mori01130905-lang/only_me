# dwell 后端启动器
# 由计划任务在登录时调用（也可以双击）。作用：
#   1. 确认 Tailscale 在跑，并把 https 443 指到本机 5000
#   2. 用 waitress（生产级、单进程多线程）跑 Flask
#   3. 崩了自动重起；日志写在 data\server.log
# 想换口令：改 .env 里的 DWELL_PASS 这一行，然后重启本脚本。

$ErrorActionPreference = 'SilentlyContinue'
$Root = 'D:\only_me'
Set-Location $Root

$py      = Join-Path $Root '.venv\Scripts\waitress-serve.exe'
$tail    = 'C:\Program Files\Tailscale\tailscale.exe'
$log     = Join-Path $Root 'data\server.log'
$port    = 5000

New-Item -ItemType Directory -Force -Path (Join-Path $Root 'data') | Out-Null

function Log($msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Add-Content -Path $log -Value $line -Encoding UTF8
}

# 只留一份：已经有 dwell 的 waitress 在跑就不重复起
$existing = Get-CimInstance Win32_Process -Filter "Name='waitress-serve.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -like "*$Root*" }
if (@($existing).Count -gt 0) {
    Log ("已经有服务在跑（PID " + ($existing.ProcessId -join ',') + "），本次不重复启动")
    exit 0
}

# Tailscale 的 443 -> 本机 5000（幂等，重复执行没关系）
if (Test-Path $tail) {
    $st = & $tail status 2>$null
    if ($LASTEXITCODE -eq 0 -and $st) {
        & $tail serve --bg 5000 2>$null | Out-Null
        Log 'tailscale serve 已确认（https 443 -> 127.0.0.1:5000）'
    } else {
        Log 'Tailscale 没登录，先跑一次 tailscale up'
    }
}

Log '=== 启动 dwell 后端 ==='
while ($true) {
    & $py --listen="127.0.0.1:$port" --threads=8 app:app *>> $log
    Log "服务退出了（退出码 $LASTEXITCODE），5 秒后重起"
    Start-Sleep -Seconds 5
}
