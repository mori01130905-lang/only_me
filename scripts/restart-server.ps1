# 重启 dwell 后端
# 用法：双击，或在终端跑  D:\only_me\scripts\restart-server.ps1
# 作用：停掉计划任务里的后端进程，再重新拉起来（约 30 秒后可用，因为要加载依赖）

$ErrorActionPreference = 'SilentlyContinue'
$root = 'D:\only_me'
$log  = Join-Path $root 'data\server.log'

Write-Host ''
Write-Host '  正在重启 dwell 后端…' -ForegroundColor Cyan

# 1) 停任务（会带走它启动的 pythonw 进程）
Stop-ScheduledTask -TaskName 'dwell-server' 2>$null
Start-Sleep -Seconds 2

# 2) 兜底：还有残留的 pythonw 在跑 serve.py 就一起清掉
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -like '*serve.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force 2>$null }
Start-Sleep -Seconds 1

# 3) 重新拉起
Start-ScheduledTask -TaskName 'dwell-server'

# 4) 等端口起来（最多 60 秒）
$ok = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    $r = curl.exe -sS --max-time 4 -o NUL -w "%{http_code}" 'http://127.0.0.1:5000/healthz' 2>$null
    if ($r -eq '200') { $ok = $true; break }
}

if ($ok) {
    Write-Host '  好了。地址：https://dwell.taild9fc55.ts.net' -ForegroundColor Green
} else {
    Write-Host '  没起来，看日志最后几行：' -ForegroundColor Yellow
    Get-Content $log -Tail 15 -Encoding UTF8
}

Write-Host ''
Read-Host '按回车键关闭'
