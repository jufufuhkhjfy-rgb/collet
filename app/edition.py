"""販売モード（A・B・C）と、プラン・利用キーの確認。

A：全部の機能が無料。「応援する」リンクを出せる
B：基本は無料。プランに入れた機能だけ、そのプランの利用キーが必要
C：全部の機能に、利用キーが必要

プランは「スタンダード」と「画像生成」の2つ。どのプランにも入っていない機能は、だれでも使える。
モード・プラン・ページのURLは app/edition.json に入れて、アプリと一緒に配る。
利用キーは、作者のPCにだけある「発行用の鍵」（owner/signing_key.pem）で署名して作る。
配るアプリには確かめる用の鍵しか入っていないので、利用キーを勝手に作ることはできない。
ただし、プログラムを書き換えれば確認そのものを外せるので、完全には防げない。
"""
import base64
import calendar
import copy
import json
import logging
import os
import secrets
import threading
from datetime import date, datetime
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .config import DATA_DIR, ROOT, reload_env

log = logging.getLogger("collet")

EDITION_PATH = Path(__file__).parent / "edition.json"
OWNER_DIR = ROOT / "owner"  # 作者のPCだけにある。公開しない
SIGNING_KEY_PATH = OWNER_DIR / "signing_key.pem"
ISSUED_PATH = OWNER_DIR / "issued.json"
LICENSE_PATH = DATA_DIR / "license.json"
KEY_PREFIX = "COLLET-"

MODES = {
    "A": {"label": "無料＋応援", "summary": "全部の機能を無料で使えます。気に入った人は「応援する」から支援できます。"},
    "B": {"label": "一部だけ有料", "summary": "基本は無料。プランに入れた機能だけ、利用キー（月額）が必要です。"},
    "C": {"label": "全部有料", "summary": "全部の機能に、利用キー（月額）が必要です。"},
}
FEATURES = {
    "ai": "AIの紹介文",
    "cards": "紹介カード画像",
    "bulk": "まとめてROOMで開く",
    "room": "部屋に置いた画像",
}
DEFAULT_PLANS = {
    "standard": {"label": "スタンダード", "price": 300, "shop_url": "", "enabled": True,
                 "features": ["ai", "cards", "bulk"]},
    "image": {"label": "画像生成", "price": 1000, "shop_url": "", "enabled": True,
              "features": ["ai", "cards", "bulk", "room"]},
}
DEFAULTS = {"mode": "A", "support_url": "", "site_url": "", "plans": DEFAULT_PLANS, "public_key": ""}

_lock = threading.Lock()
preview = False  # 作者が「利用者の見え方」を確かめている間は True


class EditionError(Exception):
    pass


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def load() -> dict:
    with _lock:
        saved = json.loads(EDITION_PATH.read_text(encoding="utf-8")) if EDITION_PATH.exists() else {}
    edition = {**copy.deepcopy(DEFAULTS), **saved}
    # 足りないプランや項目は、最初の値で埋める
    edition["plans"] = {pid: {**DEFAULT_PLANS[pid], **edition["plans"].get(pid, {})} for pid in DEFAULT_PLANS}
    return edition


def _write(edition: dict) -> None:
    keep = {k: edition[k] for k in DEFAULTS}
    with _lock:
        EDITION_PATH.write_text(json.dumps(keep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def is_owner() -> bool:
    """作者のPCか。.env に COLLET_OWNER=1 があり、利用キーを作る鍵（owner/）も持っているときだけ。

    鍵を持っていない人が COLLET_OWNER=1 と書いても、作者にはならない。
    （まだ鍵がない、いちばん最初の1回だけは例外。ここで鍵が作られる）
    """
    reload_env()
    if os.getenv("COLLET_OWNER") != "1":
        return False
    return not load()["public_key"] or can_issue()


def owner_full() -> bool:
    """作者は、利用キーがなくても全部使える（「利用者の見え方で確認」中は除く）。"""
    return is_owner() and not preview


def paid_features(edition: dict) -> list[str]:
    """売っているプランのどれかに入っている機能。これ以外は無料。"""
    return [f for f in FEATURES if any(p["enabled"] and f in p["features"] for p in edition["plans"].values())]


def plans_for(edition: dict, feature: str) -> list[str]:
    """その機能が入っている、売っているプラン（安い順）。"""
    plans = [(p["price"], pid) for pid, p in edition["plans"].items() if p["enabled"] and feature in p["features"]]
    return [pid for _, pid in sorted(plans)]


# ---------- 発行用の鍵（作者のPCだけ） ----------

def _signing_key() -> Ed25519PrivateKey | None:
    if not SIGNING_KEY_PATH.exists():
        return None
    try:
        return serialization.load_pem_private_key(SIGNING_KEY_PATH.read_bytes(), password=None)
    except (ValueError, TypeError):
        log.warning("owner/signing_key.pem が読めませんでした")
        return None


def _public_b64(private: Ed25519PrivateKey) -> str:
    raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return _b64(raw)


def can_issue() -> bool:
    """このPCで利用キーを発行できるか（発行用の鍵が、配るアプリの確かめる用の鍵と対になっているか）。"""
    private = _signing_key()
    return bool(private and load()["public_key"] == _public_b64(private))


def ensure_signing_key() -> None:
    """発行用の鍵がまだなければ作り、確かめる用の鍵を edition.json に書き込む。"""
    edition = load()
    private = _signing_key()
    if private is None:
        if edition["public_key"]:
            return  # 別のPCで作った鍵がある。上書きすると、渡した利用キーが全部使えなくなる
        private = Ed25519PrivateKey.generate()
        OWNER_DIR.mkdir(exist_ok=True)
        SIGNING_KEY_PATH.write_bytes(private.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    if not edition["public_key"]:
        _write({**edition, "public_key": _public_b64(private)})


def _check_url(url: str) -> str:
    url = url.strip()
    if url and not url.startswith("https://"):
        raise EditionError("ページのURLは https:// から始まるものを入れてください。")
    return url


def update(changes: dict) -> dict:
    """モード・応援ページ・プランを変える（作者だけ）。プランは {"id": ..., 変える項目} で1つずつ。"""
    edition = load()
    if "mode" in changes:
        if changes["mode"] not in MODES:
            raise EditionError("モードは A・B・C から選んでください。")
        edition["mode"] = changes["mode"]
    if "support_url" in changes:
        edition["support_url"] = _check_url(changes["support_url"])
    if "site_url" in changes:
        # 販売ページのURL。thanks.html などを足すので、最後の / は外しておく
        url = _check_url(changes["site_url"])
        for tail in ("/index.html", "/"):
            if url.endswith(tail):
                url = url[: -len(tail)]
        edition["site_url"] = url

    plan = changes.get("plan")
    if plan:
        pid = plan.get("id")
        if pid not in edition["plans"]:
            raise EditionError("そのプランはありません。")
        p = dict(edition["plans"][pid])
        if plan.get("label") is not None:
            label = plan["label"].strip()
            if not 1 <= len(label) <= 20:
                raise EditionError("プランの名前は1〜20文字にしてください。")
            p["label"] = label
        if plan.get("price") is not None:
            p["price"] = int(plan["price"])
        if plan.get("shop_url") is not None:
            p["shop_url"] = _check_url(plan["shop_url"])
        if plan.get("features") is not None:
            p["features"] = [f for f in FEATURES if f in plan["features"]]
        if plan.get("enabled") is not None:
            p["enabled"] = bool(plan["enabled"])
            others = [q for k, q in edition["plans"].items() if k != pid and q["enabled"]]
            if not p["enabled"] and not others:
                raise EditionError("少なくとも1つのプランは「売る」にしてください。")
        edition["plans"] = {**edition["plans"], pid: p}

    _write(edition)
    return edition


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def issued_keys() -> list[dict]:
    if not ISSUED_PATH.exists():
        return []
    return json.loads(ISSUED_PATH.read_text(encoding="utf-8"))


def issue_key(memo: str, months: int = 1, plan: str = "standard",
              until: str | None = None, extra: dict | None = None, auto: bool = False) -> dict:
    """利用キーを作る。期限は until（なければ今日から months か月後）まで。

    extra には、自動で渡すときの Stripe の契約の番号（s）と、更新用の合言葉（r）を入れる。
    """
    if plan not in DEFAULT_PLANS:
        raise EditionError("そのプランはありません。")
    if not can_issue():
        raise EditionError("このPCには、利用キーを発行する鍵がありません。")
    private = _signing_key()
    key_id = secrets.token_hex(4)
    until = until or _add_months(date.today(), months).isoformat()
    payload = json.dumps({"i": key_id, "u": until, "p": plan, **(extra or {})}, separators=(",", ":")).encode()
    key = KEY_PREFIX + _b64(payload) + "." + _b64(private.sign(payload))
    entry = {"id": key_id, "memo": memo.strip(), "plan": plan, "until": until, "auto": auto,
             "created": datetime.now().isoformat(timespec="minutes"), "key": key}
    with _lock:
        issued = issued_keys()
        issued.insert(0, entry)
        ISSUED_PATH.write_text(json.dumps(issued, ensure_ascii=False, indent=2), encoding="utf-8")
    return entry


# ---------- 利用キーの確認（使う人のPC） ----------

def key_payload(key: str) -> dict | None:
    """利用キーの中身。署名が正しいときだけ返す（期限切れでも返す）。"""
    public = load()["public_key"]
    key = key.strip()
    try:
        if not public or not key.startswith(KEY_PREFIX):
            return None
        body, sig = key[len(KEY_PREFIX):].split(".")
        payload = _unb64(body)
        Ed25519PublicKey.from_public_bytes(_unb64(public)).verify(_unb64(sig), payload)
        data = json.loads(payload)
        date.fromisoformat(data["u"])
        if data.get("p", "standard") not in DEFAULT_PLANS:
            return None
        return data
    except (ValueError, KeyError, TypeError, AttributeError, InvalidSignature):
        return None


def check_key(key: str) -> dict:
    """利用キーを確かめる。{"ok", "until", "plan", "reason"} を返す。"""
    if not load()["public_key"]:
        return {"ok": False, "until": None, "plan": None, "reason": "この Collet には、利用キーを確かめる鍵が入っていません。"}
    data = key_payload(key)
    if data is None:
        return {"ok": False, "until": None, "plan": None,
                "reason": "利用キーが正しくありません。コピーし直して、もう一度入れてください。"}
    until = date.fromisoformat(data["u"])
    plan = data.get("p", "standard")
    if until < date.today():
        return {"ok": False, "until": until.isoformat(), "plan": plan,
                "reason": f"利用キーの期限（{until.month}月{until.day}日まで）が切れています。新しい利用キーを入れてください。"}
    return {"ok": True, "until": until.isoformat(), "plan": plan, "reason": ""}


def _saved_key() -> str:
    if not LICENSE_PATH.exists():
        return ""
    try:
        return json.loads(LICENSE_PATH.read_text(encoding="utf-8")).get("key", "")
    except ValueError:
        return ""


def license_state() -> dict:
    key = _saved_key()
    if not key:
        return {"has_key": False, "ok": False, "until": None, "plan": None, "reason": ""}
    return {"has_key": True, **check_key(key)}


def save_license(key: str) -> dict:
    result = check_key(key)
    if not result["ok"]:
        raise EditionError(result["reason"])
    LICENSE_PATH.write_text(json.dumps({"key": key.strip()}), encoding="utf-8")
    return result


def remove_license() -> None:
    LICENSE_PATH.unlink(missing_ok=True)


# ---------- どの機能が使えるか ----------

def unlocked() -> dict:
    """app（Collet 全体）と、各機能が使えるか。"""
    edition = load()
    if edition["mode"] == "A" or owner_full():
        return {"app": True, **{f: True for f in FEATURES}}
    paid = paid_features(edition)
    lic = license_state()
    if lic["ok"]:
        included = edition["plans"][lic["plan"]]["features"]
        return {"app": True, **{f: f in included or f not in paid for f in FEATURES}}
    if edition["mode"] == "B":
        return {"app": True, **{f: f not in paid for f in FEATURES}}
    return {"app": False, **{f: False for f in FEATURES}}


def allowed(feature: str) -> bool:
    return unlocked()[feature]


def locked_message(feature: str) -> str:
    if feature == "app":
        return "利用キーが入っていないので使えません。利用キーを入れてください。"
    edition = load()
    pids = plans_for(edition, feature)
    if not pids:
        return f"「{FEATURES[feature]}」は、いまは使えません。"
    p = edition["plans"][pids[0]]
    return (f"「{FEATURES[feature]}」は{p['label']}プラン（月{p['price']:,}円）の機能です。"
            "「投稿設定」の「有料プラン」に利用キーを入れると使えます。")


def state() -> dict:
    edition = load()
    owner = is_owner()
    return {
        "mode": edition["mode"],
        "support_url": edition["support_url"],
        "site_url": edition["site_url"],
        "plans": edition["plans"],
        "paid_features": paid_features(edition),
        "modes": MODES,
        "features": FEATURES,
        "owner": owner,
        "preview": preview,
        "can_issue": owner and can_issue(),
        "license": license_state(),
        "unlocked": unlocked(),
    }
