"""設定の読み書き。設定は data/settings.json に保存する。"""
import json
import threading
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SETTINGS_PATH = DATA_DIR / "settings.json"
DB_PATH = DATA_DIR / "collet.db"


def reload_env() -> None:
    """.env を読み直す。書き換えたあと、アプリを起動し直さなくても反映される。"""
    load_dotenv(ROOT / ".env", override=True)


reload_env()
DATA_DIR.mkdir(exist_ok=True)

# 設定画面のワンボタンで選ぶ周期。間隔（分）・1回の件数・1日の上限をまとめて決める。
PRESETS = {
    "slow": {"label": "ゆっくり", "interval_minutes": 240, "per_run": 1, "daily_limit": 3},
    "normal": {"label": "ふつう", "interval_minutes": 120, "per_run": 1, "daily_limit": 6},
    "active": {"label": "しっかり", "interval_minutes": 60, "per_run": 1, "daily_limit": 10},
}

DEFAULTS = {
    "preset": "normal",
    **{k: v for k, v in PRESETS["normal"].items() if k != "label"},
    "genres": ["100804", "215783", "551177", "100026"],
    "active_start": 8,
    "active_end": 22,
    "min_review_average": 4.0,
    "min_review_count": 10,
    "max_price": 10000,
    "ai_images": False,
    "enabled": False,
}

_lock = threading.Lock()


def load_settings() -> dict:
    with _lock:
        if SETTINGS_PATH.exists():
            saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            return {**DEFAULTS, **saved}
        return dict(DEFAULTS)


def save_settings(changes: dict) -> dict:
    current = load_settings()
    allowed = {k: v for k, v in changes.items() if k in DEFAULTS}
    # プリセットを選んだら、そのプリセットの値で上書きする
    preset = allowed.get("preset")
    if preset in PRESETS:
        allowed.update({k: v for k, v in PRESETS[preset].items() if k != "label"})
    merged = {**current, **allowed}
    with _lock:
        SETTINGS_PATH.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return merged
