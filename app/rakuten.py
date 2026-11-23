"""楽天市場ランキングAPIから投稿候補の商品を取ってくる。"""
import os

import requests

from .config import reload_env


class RakutenError(Exception):
    pass


RANKING_URL = "https://openapi.rakuten.co.jp/ichibaranking/api/IchibaItem/Ranking/20220601"


def fetch_ranking(genre_id: str) -> list[dict]:
    reload_env()
    app_id = os.getenv("RAKUTEN_APP_ID")
    access_key = os.getenv("RAKUTEN_ACCESS_KEY")
    if not app_id or not access_key:
        raise RakutenError("楽天アプリIDかアクセスキーが空です。.env の RAKUTEN_APP_ID と RAKUTEN_ACCESS_KEY に入力してください。")

    params = {"applicationId": app_id, "genreId": genre_id, "format": "json", "formatVersion": 2}
    if os.getenv("RAKUTEN_AFFILIATE_ID"):
        params["affiliateId"] = os.getenv("RAKUTEN_AFFILIATE_ID")

    # アクセスキーはURLに残らないよう、ヘッダーで送る
    res = requests.get(os.getenv("RAKUTEN_RANKING_URL") or RANKING_URL, params=params,
                       headers={"accessKey": access_key}, timeout=20)
    if res.status_code == 429:
        raise RakutenError("楽天APIへの問い合わせが多すぎました。しばらく待ってから試してください。")
    if res.status_code in (401, 403):
        raise RakutenError(
            f"楽天APIに接続を断られました（{res.status_code}）。アプリID・アクセスキーと、"
            f"楽天ウェブサービスの「許可されたWebサイト」に登録したIPアドレスを確認してください: {res.text[:200]}"
        )
    if res.status_code != 200:
        raise RakutenError(f"楽天APIがエラーを返しました（{res.status_code}）: {res.text[:200]}")

    items = []
    for it in res.json().get("Items", []):
        images = it.get("mediumImageUrls") or []
        items.append({
            "code": it["itemCode"],
            "name": it["itemName"],
            "price": int(it.get("itemPrice", 0)),
            "url": it.get("itemUrl", ""),
            "shop": it.get("shopName", ""),
            "caption": (it.get("itemCaption") or "")[:600],
            "review_average": float(it.get("reviewAverage") or 0),
            "review_count": int(it.get("reviewCount") or 0),
            "image_url": images[0] if images else "",
            "image_urls": images,
            "genre_id": genre_id,
        })
    return items


def pick_candidates(settings: dict, genre_id: str) -> list[dict]:
    """設定の条件（評価・件数・価格）に合う商品だけを残す。"""
    return [
        it for it in fetch_ranking(genre_id)
        if it["review_average"] >= settings["min_review_average"]
        and it["review_count"] >= settings["min_review_count"]
        and (not settings["max_price"] or it["price"] <= settings["max_price"])
    ]
