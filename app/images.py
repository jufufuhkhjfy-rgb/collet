"""投稿候補に付ける画像を作る。2種類ある。

- card: 紹介カード3枚。作り置きのAI背景に商品写真を載せる（cards.py。すぐできる）
- room: 商品を切り抜いて、AIで描いた部屋に合成する（imggen の専用環境で動かす。1分くらい）

画像を付けた候補の紹介文には、必ず先頭に注意書きを入れる。
"""
import json
import logging
import shutil
import subprocess
import threading

from . import cards, store
from .config import DATA_DIR, ROOT
from .genres import GENRE_GROUPS
from .local_writer import available as local_available
from .local_writer import extract_points

log = logging.getLogger("collet")

IMGGEN_PYTHON = ROOT / "imggen" / ".venv" / "Scripts" / "python.exe"
IMGGEN_SCRIPT = ROOT / "imggen" / "generate.py"
IMAGES_DIR = DATA_DIR / "images"
NOTES = {
    "card": "※画像の背景はAIで作ったイメージです。",
    "room": "※画像はAIで作ったイメージです。実際に使っている写真ではありません。",
}

_GROUP_OF = {g["id"]: group["name"] for group in GENRE_GROUPS for g in group["genres"]}
_lock = threading.Lock()  # 画像は同時に1件ずつ作る
working: set[int] = set()
errors: dict[int, str] = {}  # 候補ごとの、画像を作れなかった理由


class ImageError(Exception):
    pass


def card_available() -> bool:
    return cards.available()


def room_available() -> bool:
    return IMGGEN_PYTHON.exists() and IMGGEN_SCRIPT.exists()


def kind_of(urls: list[str]) -> str | None:
    if not urls:
        return None
    return "card" if "/card" in urls[0] else "room"


def with_note(comment: str, kind: str | None) -> str:
    """注意書きを付け直す。古い注意書きは外してから、画像の種類に合うものを先頭に入れる。"""
    body = comment
    for note in NOTES.values():
        body = body.removeprefix(note).lstrip("\n")
    return f"{NOTES[kind]}\n\n{body}" if kind else body


def _make_card(item: dict, out) -> list[str]:
    points = extract_points(item) if local_available() else []
    return [p.name for p in cards.make_cards(item, out, points or None)]


def _make_room(item: dict, out) -> list[str]:
    if not room_available():
        raise ImageError("部屋に合成する画像生成AIが入っていません。")
    res = subprocess.run(
        [str(IMGGEN_PYTHON), str(IMGGEN_SCRIPT), ",".join(item.get("image_urls") or [item["image_url"]]),
         _GROUP_OF.get(item["genre_id"], "雑貨"), str(out), "3"],
        capture_output=True, text=True, encoding="utf-8", timeout=900,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if res.returncode != 0:
        log.warning("画像生成に失敗: %s", res.stderr[-2000:])
        raise ImageError("画像を作れませんでした。くわしくは data/collet.log を見てください。")
    paths = json.loads(res.stdout.strip().splitlines()[-1])["images"]
    return [p.replace("\\", "/").rsplit("/", 1)[-1] for p in paths]


def generate_for(queue_id: int, kind: str) -> None:
    entry = store.queue_get(queue_id)
    if not entry:
        raise ImageError("この候補はもうありません。")
    item = entry["item"]
    if not item.get("image_url"):
        raise ImageError("この商品には写真がないので、画像を作れません。")

    out = IMAGES_DIR / str(queue_id)
    with _lock:
        out.mkdir(parents=True, exist_ok=True)
        for old in out.iterdir():
            old.unlink()
        names = _make_card(item, out) if kind == "card" else _make_room(item, out)
    urls = [f"/images/{queue_id}/{name}" for name in names]
    latest = store.queue_get(queue_id)
    store.queue_update(queue_id, images=json.dumps(urls), comment=with_note(latest["comment"], kind))


def delete_for(queue_id: int) -> None:
    """投稿した・見送った候補の画像を消す。"""
    shutil.rmtree(IMAGES_DIR / str(queue_id), ignore_errors=True)


_timers: dict[int, threading.Timer] = {}


def delete_later(queue_id: int, seconds: int = 1800) -> None:
    """しばらくしてから画像を消す（拡張機能がROOMに入れる時間を残すため）。"""
    cancel_delete(queue_id)

    def run():
        _timers.pop(queue_id, None)
        delete_for(queue_id)
        entry = store.queue_get(queue_id)
        if entry and entry["status"] != "pending":
            store.queue_update(queue_id, images="[]")

    timer = threading.Timer(seconds, run)
    timer.daemon = True
    _timers[queue_id] = timer
    timer.start()


def cancel_delete(queue_id: int) -> None:
    timer = _timers.pop(queue_id, None)
    if timer:
        timer.cancel()


def cleanup(pending_ids: set[int]) -> None:
    """投稿待ちに残っていない候補の画像フォルダを、まとめて消す。"""
    if not IMAGES_DIR.exists():
        return
    for folder in IMAGES_DIR.iterdir():
        if folder.is_dir() and (not folder.name.isdigit() or int(folder.name) not in pending_ids):
            shutil.rmtree(folder, ignore_errors=True)


def generate_in_background(queue_id: int, kind: str) -> None:
    def run():
        try:
            generate_for(queue_id, kind)
        except ImageError as e:
            errors[queue_id] = str(e)
            log.warning("画像生成: %s", e)
        except Exception:
            errors[queue_id] = "画像を作る途中でエラーが起きました。"
            log.exception("画像生成で想定外のエラー")
        finally:
            working.discard(queue_id)

    errors.pop(queue_id, None)
    working.add(queue_id)
    threading.Thread(target=run, daemon=True).start()
