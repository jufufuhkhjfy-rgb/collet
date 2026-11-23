# Collet のセットアップ。setup.bat から呼ばれる。
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "Collet のセットアップを始めます。" -ForegroundColor Cyan

# 1. Python があるか
$python = $null
foreach ($cmd in @("py -3", "python")) {
    try {
        $exe = & ([scriptblock]::Create("$cmd -c `"import sys; print(sys.executable) if sys.version_info >= (3, 10) else sys.exit(1)`"")) 2>$null
        if ($LASTEXITCODE -eq 0 -and $exe) { $python = $exe.Trim(); break }
    } catch {}
}
if (-not $python) {
    Write-Host "Python 3.10 以上が見つかりませんでした。https://www.python.org/downloads/ から入れてから、もう一度 setup.bat を実行してください。" -ForegroundColor Red
    exit 1
}
Write-Host "Python: $python"

# 2. 必要な部品を入れる
Write-Host "必要な部品をインストールしています（数分かかります）…"
& $python -m pip install -r requirements.txt --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { Write-Host "部品のインストールに失敗しました。上のエラーを確認してください。" -ForegroundColor Red; exit 1 }

# 3. 設定ファイルを用意する
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host ".env を作りました。楽天のアプリIDとアクセスキーを書き込んでください。"
}

# 4. デスクトップにショートカットを作る
$pythonw = Join-Path (Split-Path $python) "pythonw.exe"
if (-not (Test-Path $pythonw)) { $pythonw = $python }
$desktop = [Environment]::GetFolderPath("Desktop")
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut((Join-Path $desktop "Collet.lnk"))
$link.TargetPath = $pythonw
$link.Arguments = "`"$PSScriptRoot\launch.pyw`""
$link.WorkingDirectory = $PSScriptRoot
$link.IconLocation = "$PSScriptRoot\collet.ico,0"
$link.Description = "Collet（楽天ROOMの投稿を手伝うツール）"
$link.Save()
Write-Host "デスクトップに Collet のショートカットを作りました。"

Write-Host ""
Write-Host "セットアップが終わりました。" -ForegroundColor Green
Write-Host "次は README.md の「3. 楽天のアプリIDを設定する」から進めてください。"
notepad ".env"
