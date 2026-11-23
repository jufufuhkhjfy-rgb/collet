"""Collet をアプリとして開く。デスクトップのショートカットから pythonw で起動する（黒い画面は出ない）。

1. Collet が動いていなければ、裏で起動する（ログは data/collet.log）
2. 拡張機能を入れた Chrome で、アドレスバーのない専用ウィンドウとして開く

ウィンドウを閉じても Collet は裏で動き続ける（自動の候補集めを続けるため）。
止めるときは、画面左下の「Collet を終了」を押す。
"""
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
URL = "http://127.0.0.1:8765"
CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]


def running() -> bool:
    try:
        with urllib.request.urlopen(f"{URL}/api/status", timeout=2):
            return True
    except OSError:
        return False


def start_server() -> None:
    (ROOT / "data").mkdir(exist_ok=True)
    log = open(ROOT / "data" / "collet.log", "a", encoding="utf-8")
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    subprocess.Popen(
        [str(pythonw if pythonw.exists() else sys.executable), "-m", "app.main"],
        cwd=ROOT, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
    )


def open_window() -> None:
    chrome = next((p for p in CHROME_PATHS if os.path.exists(p)), None)
    if chrome:
        subprocess.Popen([chrome, f"--app={URL}", "--window-size=1360,900"])
    else:
        os.startfile(URL)


def main() -> None:
    if not running():
        start_server()
        for _ in range(40):
            if running():
                break
            time.sleep(0.5)
    open_window()


if __name__ == "__main__":
    main()
