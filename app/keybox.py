"""利用キーを、受け取る人だけが開ける形（暗号化）にする。

販売ページ（GitHub で公開）に置くので、だれでもファイル自体は見られる。
中身は「合言葉」を知っている人だけが開ける。
- claim：支払い直後の受け取り用。合言葉は Stripe の支払いの番号（購入者のページのURLにだけ入っている）
- renew：毎月の更新用。合言葉は利用キーの中に入っている番号（その利用キーを持っている人だけが知っている）
販売ページの thanks.html（JavaScript）も、同じ決まりで開く。
"""
import base64
import hashlib
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def box_name(kind: str, secret: str) -> str:
    """置き場所のファイル名。合言葉から作るので、合言葉を知らない人には場所もわからない。"""
    return hashlib.sha256(f"collet-{kind}-name:{secret}".encode()).hexdigest()[:32]


def _key(kind: str, secret: str) -> bytes:
    return hashlib.sha256(f"collet-{kind}-key:{secret}".encode()).digest()


def seal(kind: str, secret: str, data: dict) -> tuple[str, dict]:
    nonce = os.urandom(12)
    sealed = AESGCM(_key(kind, secret)).encrypt(nonce, json.dumps(data, ensure_ascii=False).encode(), None)
    return box_name(kind, secret), {"n": base64.b64encode(nonce).decode(), "c": base64.b64encode(sealed).decode()}


def open_box(kind: str, secret: str, box: dict) -> dict:
    plain = AESGCM(_key(kind, secret)).decrypt(base64.b64decode(box["n"]), base64.b64decode(box["c"]), None)
    return json.loads(plain)
