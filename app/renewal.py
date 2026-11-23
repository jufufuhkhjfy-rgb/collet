"""毎月の更新のあと、新しい利用キーを自動で受け取る（使う人のPCで動く）。

Stripe で自動で渡した利用キーには、更新用の合言葉（r）が入っている。
期限が近づいたら（または切れたら）、販売ページの r/ に新しい利用キーが置かれていないか見に行き、
あれば自動で入れ替える。作者のPCが更新の支払いを見つけると、そこに置かれる（stripe_watch.py）。
"""
import logging
import time
from datetime import date

import requests

from . import edition, keybox

log = logging.getLogger("collet")
CHECK_BEFORE_DAYS = 7  # 期限の何日前から見に行くか


def refresh_license() -> bool:
    """新しい利用キーがあれば入れ替える。入れ替えたら True。"""
    key = edition._saved_key()
    data = edition.key_payload(key) if key else None
    if not data or not data.get("r"):
        return False  # 手で発行した利用キーなど
    if (date.fromisoformat(data["u"]) - date.today()).days > CHECK_BEFORE_DAYS:
        return False
    site = edition.load()["site_url"]
    if not site:
        return False
    name = keybox.box_name("renew", data["r"])
    try:
        r = requests.get(f"{site}/r/{name}.json", params={"t": int(time.time())}, timeout=15)
        if r.status_code != 200:
            return False
        new_key = keybox.open_box("renew", data["r"], r.json())["key"]
    except (requests.RequestException, ValueError, KeyError) as e:
        log.info("新しい利用キーを確かめられませんでした: %s", e)
        return False
    except Exception as e:  # 開けない（中身が壊れている）など
        log.info("新しい利用キーを開けませんでした: %s", e)
        return False
    result = edition.check_key(new_key)
    if result["ok"] and result["until"] > data["u"]:
        edition.save_license(new_key)
        log.info("新しい利用キーに入れ替えました（%s まで）", result["until"])
        return True
    return False
