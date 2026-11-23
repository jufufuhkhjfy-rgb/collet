"""投稿候補の準備と、周期ごとの自動実行。

Collet が「商品＋紹介文」を投稿待ちリストに用意し、投稿は本人が設定画面からおこなう。
"""
import logging
import os
import random
import threading
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from . import edition, images, store
from .config import load_settings, reload_env
from .rakuten import RakutenError, pick_candidates
from .templates import template_comment
from .local_writer import available as local_available
from .local_writer import write_comment_local
from .writer import WriterError, write_comment

log = logging.getLogger("collet")

JOB_ID = "auto-collect"
scheduler = BackgroundScheduler(timezone="Asia/Tokyo")
_run_lock = threading.Lock()
last_message = {"text": "まだ実行していません。", "at": None, "ok": True}


def _say(text: str, ok: bool = True) -> None:
    last_message.update(text=text, at=datetime.now().isoformat(timespec="seconds"), ok=ok)
    (log.info if ok else log.warning)(text)


def use_ai() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def comment_mode() -> str:
    """紹介文を何で作るか。Claude → PCのAI（Ollama）→ テンプレート の順に使える方を選ぶ。"""
    if not edition.allowed("ai"):
        return "template"  # 販売モードで、AIの紹介文が有料になっている
    if use_ai():
        return "ai"
    if local_available():
        return "local"
    return "template"


def setup_checks() -> list[dict]:
    """必要な準備がそろっているか。ホーム画面の「はじめる前に」にも出す。"""
    reload_env()
    return [
        {"key": "rakuten", "label": "楽天アプリIDとアクセスキーを設定する", "required": True,
         "ok": bool(os.getenv("RAKUTEN_APP_ID") and os.getenv("RAKUTEN_ACCESS_KEY")),
         "message": "楽天アプリIDかアクセスキーが空です。collet フォルダの .env の RAKUTEN_APP_ID と RAKUTEN_ACCESS_KEY に入力してください。"},
        {"key": "ai", "label": "紹介文用のAIを用意する（任意。PCのAI「Ollama」か Claude APIキー。なければテンプレートで作ります）",
         "required": False, "ok": comment_mode() != "template", "message": ""},
    ]


def _in_active_hours(settings: dict) -> bool:
    return settings["active_start"] <= datetime.now().hour < settings["active_end"]


def _make_comment(item: dict) -> tuple[str, str]:
    """紹介文と、その作り方（ai / local / template）。AIで作れなければテンプレートにする。"""
    mode = comment_mode()
    try:
        if mode == "ai":
            return write_comment(item), "ai"
        if mode == "local":
            return write_comment_local(item), "local"
    except WriterError as e:
        log.warning("AIの紹介文をあきらめてテンプレートにします: %s", e)
    return template_comment(item), "template"


def run_once(force: bool = False) -> None:
    """設定に沿って投稿候補を用意する。force=True なら時間帯の制限を無視する。"""
    if not _run_lock.acquire(blocking=False):
        _say("前の候補集めがまだ実行中です。")
        return
    try:
        settings = load_settings()
        if not edition.allowed("app"):
            _say("利用キーが入っていないので、候補を集められません。", ok=False)
            return
        if not force and not _in_active_hours(settings):
            _say("候補を集める時間帯の外なので休んでいます。")
            return

        # 候補の数に上限はない。「今すぐ」は選んだジャンルから1件ずつ、自動のときはペースの件数だけ集める
        count = len(settings["genres"]) if force else settings["per_run"]
        if not settings["genres"]:
            _say("ジャンルが1つも選ばれていません。「投稿設定」で選んでください。", ok=False)
            return
        missing = [s["message"] for s in setup_checks() if s["required"] and not s["ok"]]
        if missing:
            _say(missing[0], ok=False)
            return

        added = 0
        genres = settings["genres"][:]
        random.shuffle(genres)
        for genre_id in genres:
            if added >= count:
                break
            try:
                candidates = [c for c in pick_candidates(settings, genre_id) if not store.already_used(c["code"])]
            except RakutenError as e:
                _say(str(e), ok=False)
                return
            if candidates:
                item = candidates[0]
                comment, source = _make_comment(item)
                queue_id = store.queue_add(item, comment, source)
                added += 1
                if queue_id and settings["ai_images"] and images.card_available() and edition.allowed("cards"):
                    images.generate_in_background(queue_id, "card")

        if added:
            _say(f"{added}件の投稿候補を用意しました。「今日の投稿」から投稿してください。")
        else:
            _say("条件に合う新しい商品が見つかりませんでした。条件をゆるめるか、ジャンルを増やしてください。")
    except Exception as e:  # スケジューラーを止めないため、想定外のエラーも記録して続ける
        log.exception("候補集めでエラー")
        _say(f"想定外のエラーが起きました: {e}", ok=False)
    finally:
        _run_lock.release()


def apply_schedule() -> None:
    """保存されている設定に合わせて、自動実行を開始・変更・停止する。"""
    settings = load_settings()
    if scheduler.get_job(JOB_ID):
        scheduler.remove_job(JOB_ID)
    if settings["enabled"]:
        scheduler.add_job(
            run_once, "interval", minutes=settings["interval_minutes"], id=JOB_ID,
            next_run_time=datetime.now().astimezone(), jitter=300, max_instances=1, coalesce=True,
        )


def next_run() -> str | None:
    job = scheduler.get_job(JOB_ID)
    return job.next_run_time.isoformat(timespec="minutes") if job and job.next_run_time else None
