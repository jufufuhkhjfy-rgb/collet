"""楽天ROOMの投稿画面へのリンク。

ROOMには投稿用の公開APIがなく、ログイン画面は自動操作のブラウザを受け付けないため、
投稿そのものは、いつものブラウザで本人がおこなう。
"""
from urllib.parse import quote

# 楽天市場の「ROOMに投稿」ボタンと同じ入口
POST_URL = "https://room.rakuten.co.jp/mix?itemcode={code}&scid=we_room_upc60"


def post_url(item_code: str) -> str:
    return POST_URL.format(code=quote(item_code, safe=":"))
