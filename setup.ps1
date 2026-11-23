# Collet のセットアップ。setup.bat から呼ばれる。
# このファイルは「UTF-8（BOMつき）」で保存すること。BOM がないと、Windows の PowerShell で日本語が文字化けする。
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Python が入っていないときに入れる版（python.org の公式インストーラーがある版）
$PythonVersion = "3.12.10"

Write-Host "Collet のセットアップを始めます。" -ForegroundColor Cyan


function Test-PythonExe($exe) {
    # 3.10 以上の Python なら、その場所を返す
    try {
        $path = & $exe -c "import sys; print(sys.executable) if sys.version_info >= (3, 10) else sys.exit(1)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $path) { return $path.Trim() }
    } catch {}
    return $null
}

function Find-Python {
    foreach ($cmd in @("py -3", "python")) {
        try {
            $exe = & ([scriptblock]::Create("$cmd -c `"import sys; print(sys.executable) if sys.version_info >= (3, 10) else sys.exit(1)`"")) 2>$null
            if ($LASTEXITCODE -eq 0 -and $exe) { return $exe.Trim() }
        } catch {}
    }
    # 入れたばかりで、まだ PATH に反映されていないときのため、いつもの場所も見る
    $places = @("$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe", "$env:ProgramFiles\Python3*\python.exe")
    foreach ($found in (Get-ChildItem $places -ErrorAction SilentlyContinue)) {
        $ok = Test-PythonExe $found.FullName
        if ($ok) { return $ok }
    }
    return $null
}

function Update-PathFromSystem {
    # 入れたあとの PATH を、この画面にも反映する
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}

function Install-PythonByWinget {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { return }
    Write-Host "Windows の「winget」で Python を入れています…"
    try {
        & winget install --id "Python.Python.3.12" --exact --scope user --silent --source winget `
            --accept-package-agreements --accept-source-agreements
    } catch {}
}

function Install-PythonByInstaller {
    # winget がない・うまくいかなかったときは、python.org から公式のインストーラーを取ってきて入れる
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "arm64" } else { "amd64" }
    $url = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-$arch.exe"
    $installer = Join-Path $env:TEMP "python-$PythonVersion-$arch.exe"
    Write-Host "python.org から Python $PythonVersion をダウンロードしています…"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $oldProgress = $ProgressPreference
    $ProgressPreference = "SilentlyContinue"  # 進み具合の表示を出すと、ダウンロードがとても遅くなる
    try {
        Invoke-WebRequest $url -OutFile $installer -UseBasicParsing
    } finally {
        $ProgressPreference = $oldProgress
    }
    # 本物の Python のインストーラーか、署名で確かめる
    $sig = Get-AuthenticodeSignature $installer
    if ($sig.Status -ne "Valid" -or $sig.SignerCertificate.Subject -notmatch "Python Software Foundation") {
        Remove-Item $installer -Force -ErrorAction SilentlyContinue
        throw "ダウンロードしたファイルが、正しい Python のインストーラーではありませんでした。"
    }
    Write-Host "Python を入れています（数分かかります）…"
    # 自分のユーザーだけに入れるので、管理者の許可はいらない
    $p = Start-Process $installer -Wait -PassThru -ArgumentList @(
        "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_launcher=0", "Include_test=0", "Shortcuts=0")
    Remove-Item $installer -Force -ErrorAction SilentlyContinue
    if ($p.ExitCode -ne 0) { throw "Python のインストールが終了コード $($p.ExitCode) で止まりました。" }
}


# 1. Python があるか。なければ自動で入れる
$python = Find-Python
if (-not $python) {
    Write-Host ""
    Write-Host "Collet を動かすのに必要な「Python」（無料）が、このPCに入っていません。" -ForegroundColor Yellow
    Write-Host "今から、公式の Python を自動で入れます（5分ほどかかります）。"
    Read-Host "Enter キーを押すと始めます。やめるときは、この画面を閉じてください" | Out-Null

    Install-PythonByWinget
    Update-PathFromSystem
    $python = Find-Python
    if (-not $python) {
        try {
            Install-PythonByInstaller
        } catch {
            Write-Host $_.Exception.Message -ForegroundColor Red
        }
        Update-PathFromSystem
        $python = Find-Python
    }
    if (-not $python) {
        Write-Host ""
        Write-Host "Python を自動で入れられませんでした。" -ForegroundColor Red
        Write-Host "https://www.python.org/downloads/ から Python を入れて（「Add python.exe to PATH」にチェック）、もう一度 setup.bat を実行してください。"
        exit 1
    }
    Write-Host "Python を入れました。" -ForegroundColor Green
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
