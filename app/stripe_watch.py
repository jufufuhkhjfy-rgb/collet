"""Stripe の支払いを見張って、利用キーを自動で渡す（作者のPCだけで動く）。

1分ごとに Stripe へ「新しい支払い」を聞きに行く（.env の STRIPE_API_KEY を使う。読み取りだけの制限付きキー）。
- 新しい申し込みを見つけたら、利用キーを作り、購入者だけが開ける形にして販売ページの k/ に置く
  （購入者は支払いのあとに開く thanks.html で、自動で受け取る）
- 毎月の更新やプランの変更を見つけたら、新しい利用キーを r/ に置く
  （購入者の Collet が自動で取りに来る。renewal.py）
置いたあと、販売ページのフォルダ（collet-site）を GitHub に上げる。
"""
import json
import logging
import os
import secrets
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from . import edition, keybox
from .config import ROOT, reload_env

log = logging.getLogger("collet")
API = "https://api.stripe.com/v1"
JST = timezone(timedelta(hours=9))
GRACE_DAYS = 3  # 更新の支払いから利用キーが届くまでの余裕
# 支払いのページは最大24時間開いたままにできるので、申し込みは2日前の分から見る。
# 更新の支払いは、カードの失敗などで数週間遅れることがあるので、35日前の分から見る。
# 一度渡したものは done に記録して、二度は渡さない。
SESSION_LOOKBACK = 2 * 24 * 3600
INVOICE_LOOKBACK = 35 * 24 * 3600
PERMISSIONS = "Checkout Sessions・Invoices・Payment Links・Prices・Subscriptions（すべて「読み取り」）"

status = {"text": "まだ確認していません。", "ok": None, "at": None}
_lock = threading.Lock()
_link_urls: dict[str, str] = {}


class WatchError(Exception):
    pass


def site_dir() -> Path:
    return Path(os.getenv("COLLET_SITE_DIR") or ROOT.parent / "collet-site")


def _state_path() -> Path:
    return edition.OWNER_DIR / "stripe_state.json"


def _subs_path() -> Path:
    return edition.OWNER_DIR / "subscriptions.json"


def _read(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _save(path: Path, data) -> None:
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def configured() -> bool:
    reload_env()
    return edition.is_owner() and bool(os.getenv("STRIPE_API_KEY"))


def _say(text: str, ok: bool) -> None:
    status.update(text=text, ok=ok, at=datetime.now().isoformat(timespec="seconds"))
    (log.info if ok else log.warning)("Stripe: %s", text)


# ---------- Stripe に聞く ----------

def _get(path: str, params: dict | None = None) -> dict:
    try:
        r = requests.get(API + path, params=params, auth=(os.getenv("STRIPE_API_KEY", ""), ""), timeout=20)
    except requests.RequestException as e:
        raise WatchError(f"Stripe につながりませんでした（{e.__class__.__name__}）。") from e
    if r.status_code == 401:
        raise WatchError("Stripe のキーが正しくありません。.env の STRIPE_API_KEY を確かめてください。")
    if r.status_code == 403:
        raise WatchError(f"Stripe のキーに権限が足りません。制限付きキーに {PERMISSIONS} を付けてください。")
    if r.status_code >= 400:
        raise WatchError(f"Stripe がエラーを返しました（{r.status_code}）。")
    return r.json()


def _list(path: str, params: dict) -> list[dict]:
    items, params = [], dict(params)
    while True:
        page = _get(path, params)
        items += page.get("data", [])
        if not page.get("has_more") or not page.get("data"):
            return items
        params["starting_after"] = page["data"][-1]["id"]


def _period_end(sub: dict) -> int:
    """今の支払い期間が終わる日時。新しい Stripe では、契約の中の各商品に入っている。"""
    if sub.get("current_period_end"):
        return sub["current_period_end"]
    ends = [it.get("current_period_end") for it in sub.get("items", {}).get("data", []) if it.get("current_period_end")]
    if not ends:
        raise WatchError(f"契約 {sub.get('id')} の期間がわかりませんでした。")
    return max(ends)


def _until(sub: dict) -> str:
    end = datetime.fromtimestamp(_period_end(sub), JST).date()
    return (end + timedelta(days=GRACE_DAYS)).isoformat()


def _plan_for(sub: dict, payment_link: str | None = None) -> str | None:
    """どのプランの支払いか。まず払った金額で見分け、だめなら決済用リンクのURLで見分ける。

    1つのリンクに2つのプランが入っていることもあるので、金額を先に見る。
    """
    plans = edition.load()["plans"]
    amounts = {(it.get("price") or {}).get("unit_amount") for it in sub.get("items", {}).get("data", [])}
    by_amount = [pid for pid, p in plans.items() if p["price"] in amounts]
    if len(by_amount) == 1:
        return by_amount[0]
    if payment_link:
        if payment_link not in _link_urls:
            _link_urls[payment_link] = _get(f"/payment_links/{payment_link}").get("url", "")
        url = _link_urls[payment_link].rstrip("/")
        matched = [pid for pid, p in plans.items() if p["shop_url"] and p["shop_url"].rstrip("/") == url]
        if len(matched) == 1:
            return matched[0]
    return by_amount[0] if by_amount else None


def _invoice_subscription(inv: dict) -> str | None:
    sub = inv.get("subscription")
    if not sub:
        sub = ((inv.get("parent") or {}).get("subscription_details") or {}).get("subscription")
    if isinstance(sub, dict):
        sub = sub.get("id")
    return sub


# ---------- 販売ページに置く ----------

def _put(folder: str, name: str, box: dict) -> None:
    path = site_dir() / folder / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(box), encoding="utf-8")


def _git(*args: str) -> subprocess.CompletedProcess:
    exe = shutil.which("git") or r"C:\Program Files\Git\cmd\git.exe"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # 黒い画面を出さない
    return subprocess.run([exe, *args], cwd=site_dir(), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120, creationflags=flags)


def push_site() -> None:
    if not (site_dir() / ".git").exists():
        raise WatchError("販売ページのフォルダ（collet-site）が、まだ GitHub につながっていません。")
    _git("add", "k", "r")
    _git("commit", "-m", "利用キーを追加")
    pushed = _git("push")
    if pushed.returncode != 0:
        raise WatchError("販売ページを GitHub に上げられませんでした: " + pushed.stderr.strip()[-200:])


# ---------- 利用キーを渡す ----------

def _deliver_new(session: dict) -> None:
    sub = session.get("subscription")
    if isinstance(sub, str):
        sub = _get(f"/subscriptions/{sub}")
    plan = _plan_for(sub, session.get("payment_link"))
    if not plan:
        raise WatchError(f"どのプランの支払いかわかりませんでした（{session['id']}）。販売モードの「支払いのリンク」と月額を確かめてください。")
    email = (session.get("customer_details") or {}).get("email") or ""
    until = _until(sub)
    secret = secrets.token_urlsafe(16)
    entry = edition.issue_key(email, plan=plan, until=until, extra={"s": sub["id"], "r": secret}, auto=True)
    subs = _read(_subs_path(), {})
    subs[sub["id"]] = {"r": secret, "plan": plan, "email": email}
    _save(_subs_path(), subs)
    data = {"key": entry["key"], "plan": plan, "until": until}
    _put("k", *keybox.seal("claim", session["id"], data))
    _put("r", *keybox.seal("renew", secret, data))


def _deliver_renewal(inv: dict) -> bool:
    sub_id = _invoice_subscription(inv)
    record = _read(_subs_path(), {}).get(sub_id or "")
    if not record:
        return False  # 自動で渡していない契約（手で発行した人など）
    sub = _get(f"/subscriptions/{sub_id}")
    plan = _plan_for(sub) or record["plan"]
    until = _until(sub)
    entry = edition.issue_key(record.get("email", ""), plan=plan, until=until,
                              extra={"s": sub_id, "r": record["r"]}, auto=True)
    subs = _read(_subs_path(), {})
    subs[sub_id] = {**record, "plan": plan}
    _save(_subs_path(), subs)
    _put("r", *keybox.seal("renew", record["r"], {"key": entry["key"], "plan": plan, "until": until}))
    return True


def poll() -> None:
    """新しい支払いを確かめて、利用キーを渡す。1分ごとに呼ばれる。"""
    if not configured():
        return
    if not _lock.acquire(blocking=False):
        return
    try:
        now = int(time.time())
        state = _read(_state_path(), {"done": [], "pending_push": False})
        done = list(state["done"])
        delivered, problems = 0, []

        sessions = _list("/checkout/sessions", {"status": "complete", "created[gte]": now - SESSION_LOOKBACK,
                                                "limit": 100, "expand[]": ["data.subscription"]})
        for s in sorted(sessions, key=lambda x: x["created"]):
            if s["id"] in done or s.get("mode") != "subscription":
                continue
            if s.get("payment_status") not in ("paid", "no_payment_required"):
                continue
            try:
                _deliver_new(s)
            except WatchError as e:  # 1件がうまくいかなくても、ほかは続ける。この1件は次の確認でやり直す
                problems.append(str(e))
                continue
            done.append(s["id"])
            delivered += 1

        invoices = _list("/invoices", {"status": "paid", "created[gte]": now - INVOICE_LOOKBACK, "limit": 100})
        for inv in sorted(invoices, key=lambda x: x["created"]):
            if inv["id"] in done or inv.get("billing_reason") not in ("subscription_cycle", "subscription_update"):
                continue
            try:
                if _deliver_renewal(inv):
                    delivered += 1
            except WatchError as e:
                problems.append(str(e))
                continue
            done.append(inv["id"])

        pending = state.get("pending_push", False) or delivered > 0
        _save(_state_path(), {"done": done[-5000:], "pending_push": pending})
        if pending:
            push_site()
            _save(_state_path(), {"done": done[-5000:], "pending_push": False})
        if problems:
            _say(problems[0], False)
        else:
            _say(f"{delivered}件の利用キーを渡しました。" if delivered else "新しい支払いはありません。", True)
    except WatchError as e:
        _say(str(e), False)
    except Exception as e:  # 見張りを止めないため、想定外のエラーも記録して続ける
        log.exception("Stripe の確認でエラー")
        _say(f"想定外のエラーが起きました: {e}", False)
    finally:
        _lock.release()


def state() -> dict:
    return {"configured": configured(), "permissions": PERMISSIONS, **status}
