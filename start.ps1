# dwell 一键启动
# 由 start.bat 调用；中文提示都在这里（UTF-8 带 BOM，PowerShell 读取无乱码）。
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# ---------- 第一次使用：配置 API key ----------
if (-not (Test-Path ".env")) {
    Write-Host ""
    Write-Host "  [第一次使用] 还没找到 API Key。"
    Write-Host "  请到 https://platform.deepseek.com 申请，然后粘贴到下面："
    $key = Read-Host "  API Key"
    if ([string]::IsNullOrWhiteSpace($key)) {
        Write-Host "  没输入 key，先跳过（聊天时会提示你配好）"
    } else {
        [System.IO.File]::WriteAllLines(
            (Join-Path $PSScriptRoot ".env"),
            @("DEEPSEEK_API_KEY=$key"),
            (New-Object System.Text.UTF8Encoding($false)))
        Write-Host "  已保存到 .env（以后想改：删掉这个文件再双击 start.bat）"
    }
    Write-Host ""
}

# ---------- 检查虚拟环境 ----------
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "  找不到虚拟环境 .venv，请先在终端执行："
    Write-Host "    cd D:\only_me"
    Write-Host "    .venv\Scripts\pip install -r requirements.txt"
    Write-Host ""
    Read-Host "  按回车键退出"
    exit 1
}

# ---------- 启动服务器 ----------
Write-Host ""
Write-Host "  ============================================"
Write-Host "   dwell 聊天服务器 正在启动..."
Write-Host "   起来后浏览器会自动打开。"
Write-Host "   关掉这个窗口 = 停止服务。"
Write-Host "  ============================================"
Write-Host ""

& ".venv\Scripts\python.exe" "app.py"

Write-Host ""
Write-Host "  服务器已退出。"
