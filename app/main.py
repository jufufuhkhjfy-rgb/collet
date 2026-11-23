"""設定画面とAPI。起動: python -m app.main  → http://127.0.0.1:8765"""
import logging
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import edition, images, jobs, renewal, store, stripe_watch
from .config import PRESETS, load_settings, save_settings
from .genres import GENRE_GROUPS
from .room import post_url

STATIC = Path(__file__).parent / "static"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if edition.is_owner():
        edition.ensure_signing_key()
    jobs.scheduler.start()
    jobs.apply_schedule()
    # 作者のPC：Stripe の支払いを1分ごとに確かめて、利用キーを自動で渡す
    jobs.scheduler.add_job(stripe_watch.poll, "interval", minutes=1, id="stripe-watch",
                           next_run_time=datetime.now().astimezone(), max_instances=1, coalesce=True)
    # 使う人のPC：毎月の更新のあと、新しい利用キーを自動で受け取る
    jobs.scheduler.add_job(renewal.refresh_license, "interval", hours=6, id="license-renew",
                           next_run_time=datetime.now().astimezone(), max_instances=1, coalesce=True)
    images.cleanup({q["id"] for q in store.queue_list("pending")})
    yield
    jobs.scheduler.shutdown(wait=False)


app = FastAPI(title="Collet", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
images.IMAGES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/images", StaticFiles(directory=images.IMAGES_DIR), name="images")


@app.middleware("http")
async def no_cache(request, call_next):
    # 画面のファイルを古いまま使い回されないよう、毎回確認させる
    response = await call_next(request)
    if not request.url.path.startswith("/static/img"):
        response.headers["Cache-Control"] = "no-cache"
    return response


class SettingsIn(BaseModel):
    preset: str | None = None
    genres: list[str] | None = None
    active_start: int | None = Field(None, ge=0, le=23)
    active_end: int | None = Field(None, ge=1, le=24)
    min_review_average: float | None = Field(None, ge=0, le=5)
    min_review_count: int | None = Field(None, ge=0)
    max_price: int | None = Field(None, ge=0)
    ai_images: bool | None = None


@app.get("/")
def index():
    # 見た目とプログラムのファイルに更新日時を付けて、古いものが使い回されないようにする
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for name in ("style.css", "app.js"):
        version = int((STATIC / name).stat().st_mtime)
        html = html.replace(f"/static/{name}", f"/static/{name}?v={version}")
    return HTMLResponse(html)


@app.get("/api/options")
def options():
    return {"presets": PRESETS, "genre_groups": GENRE_GROUPS}


@app.get("/api/settings")
def get_settings():
    return load_settings()


@app.put("/api/settings")
def put_settings(body: SettingsIn):
    saved = save_settings(body.model_dump(exclude_none=True))
    jobs.apply_schedule()
    return saved


@app.get("/api/status")
def status():
    settings = load_settings()
    return {
        "enabled": settings["enabled"],
        "next_run": jobs.next_run(),
        "posted_today": store.posted_today(),
        "pending": store.pending_count(),
        "daily_limit": settings["daily_limit"],
        "comment_mode": jobs.comment_mode(),
        "card_available": images.card_available(),
        "room_available": images.room_available(),
        "last_message": jobs.last_message,
        "setup": [{k: s[k] for k in ("key", "label", "ok", "required")} for s in jobs.setup_checks()],
    }


def _require(feature: str) -> None:
    """販売モードで、その機能が使えないときは断る。"""
    if not edition.allowed(feature):
        raise HTTPException(402, edition.locked_message(feature))


def _owner_only() -> None:
    if not edition.is_owner():
        raise HTTPException(403, "この操作は、Collet の作者のPCでだけできます。")


class PlanIn(BaseModel):
    id: str
    label: str | None = Field(None, max_length=20)
    price: int | None = Field(None, ge=0, le=100000)
    shop_url: str | None = Field(None, max_length=500)
    features: list[str] | None = None
    enabled: bool | None = None


class EditionIn(BaseModel):
    mode: str | None = None
    support_url: str | None = Field(None, max_length=500)
    site_url: str | None = Field(None, max_length=500)
    plan: PlanIn | None = None


class PreviewIn(BaseModel):
    on: bool


class IssueIn(BaseModel):
    memo: str = Field("", max_length=60)
    months: int = Field(1, ge=1, le=12)
    plan: str = "standard"


class LicenseIn(BaseModel):
    key: str = Field(min_length=1, max_length=500)


@app.get("/api/edition")
def get_edition():
    data = edition.state()
    if data["owner"]:
        data["stripe"] = stripe_watch.state()
    return data


@app.post("/api/edition/stripe-check")
def stripe_check():
    """「今すぐ確認」。Stripe の支払いをすぐに確かめる。"""
    _owner_only()
    stripe_watch.poll()
    return stripe_watch.state()


@app.post("/api/license/refresh")
def license_refresh():
    """新しい利用キー（毎月の更新）が届いていないか、すぐに確かめる。"""
    return {"updated": renewal.refresh_license(), "license": edition.license_state()}


@app.put("/api/edition")
def put_edition(body: EditionIn):
    _owner_only()
    try:
        edition.update(body.model_dump(exclude_none=True))
    except edition.EditionError as e:
        raise HTTPException(400, str(e))
    return edition.state()


@app.post("/api/edition/preview")
def edition_preview(body: PreviewIn):
    _owner_only()
    edition.preview = body.on
    return edition.state()


@app.get("/api/edition/keys")
def edition_keys():
    _owner_only()
    return edition.issued_keys()


@app.post("/api/edition/keys")
def edition_issue(body: IssueIn):
    _owner_only()
    try:
        return edition.issue_key(body.memo, body.months, body.plan)
    except edition.EditionError as e:
        raise HTTPException(400, str(e))


@app.post("/api/license")
def license_save(body: LicenseIn):
    try:
        return edition.save_license(body.key)
    except edition.EditionError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/license")
def license_remove():
    edition.remove_license()
    return {"ok": True}


@app.post("/api/start")
def start():
    _require("app")
    save_settings({"enabled": True})
    jobs.apply_schedule()
    return status()


@app.post("/api/stop")
def stop():
    save_settings({"enabled": False})
    jobs.apply_schedule()
    return status()


@app.post("/api/run-now")
def run_now():
    _require("app")
    threading.Thread(target=jobs.run_once, kwargs={"force": True}, daemon=True).start()
    return {"started": True}


@app.post("/api/quit")
def quit_app():
    # 返事を返してから止まる
    threading.Timer(0.5, os._exit, args=[0]).start()
    return {"ok": True}


@app.get("/api/stats")
def stats(days: int = Query(7, ge=1, le=90)):
    series = store.daily_counts(days)
    return {
        "series": series,
        "posted": sum(d["posted"] for d in series),
        "failed": sum(d["failed"] for d in series),
    }


@app.get("/api/posts")
def posts(limit: int = 30):
    return store.recent(limit)


class CommentIn(BaseModel):
    comment: str = Field(min_length=1, max_length=2000)


def _queue_or_404(queue_id: int) -> dict:
    entry = store.queue_get(queue_id)
    if not entry or entry["status"] != "pending":
        raise HTTPException(404, "この候補はもう投稿待ちにありません。")
    return entry


@app.get("/api/queue")
def queue():
    return [
        {**q, "post_url": post_url(q["item_code"]), "images_working": q["id"] in images.working,
         "images_error": images.errors.get(q["id"])}
        for q in store.queue_list("pending")
    ]


@app.post("/api/queue/{queue_id}/images")
def queue_images(queue_id: int, kind: str = Query("card", pattern="^(card|room)$")):
    _queue_or_404(queue_id)
    _require("cards" if kind == "card" else "room")
    ready = images.card_available() if kind == "card" else images.room_available()
    if not ready:
        raise HTTPException(400, "この画像を作る準備ができていません。")
    if queue_id not in images.working:
        images.generate_in_background(queue_id, kind)
    return {"started": True}


@app.put("/api/queue/{queue_id}")
def queue_edit(queue_id: int, body: CommentIn):
    entry = _queue_or_404(queue_id)
    # AI画像を付けた候補は、注意書きを消せないようにする
    comment = images.with_note(body.comment, images.kind_of(entry["images"]))
    store.queue_update(queue_id, comment=comment)
    return {"ok": True}


@app.post("/api/queue/{queue_id}/posted")
def queue_posted(queue_id: int, opened: bool = False):
    """投稿済みにする。opened=true は「ROOMで開く」を押したとき。

    そのときは、拡張機能がROOMに画像を入れ終わるまで画像を残し、あとで消す。
    """
    entry = _queue_or_404(queue_id)
    if opened and entry["images"]:
        store.queue_update(queue_id, status="posted")
        images.delete_later(queue_id)
    else:
        store.queue_update(queue_id, status="posted", images="[]")
        images.delete_for(queue_id)  # 使った画像は消して、容量を空ける
    store.record(entry["item"], entry["comment"], "posted")
    return {"ok": True}


@app.post("/api/queue/{queue_id}/reopen")
def queue_reopen(queue_id: int):
    """「元に戻す」。投稿済みにした候補を、投稿待ちに戻す。"""
    entry = store.queue_get(queue_id)
    if not entry or entry["status"] != "posted":
        raise HTTPException(404, "この候補は元に戻せません。")
    images.cancel_delete(queue_id)
    if not (images.IMAGES_DIR / str(queue_id)).exists():
        store.queue_update(queue_id, images="[]")
    store.queue_update(queue_id, status="pending")
    store.unrecord_posted(entry["item_code"])
    return {"ok": True}


@app.get("/api/queue/lookup")
def queue_lookup(code: str):
    """拡張機能用。ROOMの投稿画面の商品に合う候補（投稿待ちか、さっき投稿済みにしたもの）を返す。"""
    entry = store.queue_find_recent(code)
    return {"entry": entry}


@app.post("/api/queue/{queue_id}/images-used")
def queue_images_used(queue_id: int):
    """拡張機能がROOMに画像を入れ終わったら呼ぶ。投稿済みなら画像をすぐ消す。"""
    entry = store.queue_get(queue_id)
    if entry and entry["status"] == "posted":
        images.cancel_delete(queue_id)
        images.delete_for(queue_id)
        store.queue_update(queue_id, images="[]")
    return {"ok": True}


@app.post("/api/queue/{queue_id}/skip")
def queue_skip(queue_id: int):
    entry = _queue_or_404(queue_id)
    store.queue_update(queue_id, status="skipped", images="[]")
    store.record(entry["item"], entry["comment"], "skipped", "自分で見送りました")
    images.delete_for(queue_id)
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765)
