"""自分のPCで動くAI（Ollama）に、ROOM 用のおすすめコメントを書いてもらう。

アカウントもAPIキーもいらない。Ollama が起動していれば http://127.0.0.1:11434 で使える。
"""
import json
import os
import re

import requests

from .writer import WriterError, item_info, system_prompt

OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen2.5:7b"


def model_name() -> str:
    return os.getenv("OLLAMA_MODEL") or DEFAULT_MODEL


def available() -> bool:
    """Ollama が起動していて、使うモデルがダウンロード済みか。"""
    try:
        res = requests.get(f"{OLLAMA_URL}/api/tags", timeout=2)
        names = {m["name"] for m in res.json().get("models", [])}
    except (requests.RequestException, ValueError):
        return False
    want = model_name()
    return want in names or f"{want}:latest" in names


POINTS_PROMPT = """次の商品情報から、紹介画像に載せる「おすすめポイント」を3つ作ってください。
- 1つ20文字以内の日本語。英語は使わない
- 商品情報に書かれていることだけを使う。使った感想や体験談は書かない
- 価格やレビュー件数は書かない（別の画像に載せるため）
- {"points": ["...", "...", "..."]} というJSONだけを出力する
（書き方の例。別の商品なので、中身はまねしない: {"points": ["電子レンジで使える", "たっぷり入る350ml", "食洗機で洗えて楽"]}）"""

# 小さいAIはときどき英単語や中国語を混ぜるので、そのときは書き直してもらう（最初の1回＋やり直し2回まで）
TRIES = 3


def has_english(text: str) -> bool:
    """英単語（小文字3文字以上）が混ざっているか。A4・USB・LED のような大文字の略語は大丈夫。"""
    return re.search(r"[a-z]{3,}", text) is not None


def has_foreign_kanji(text: str) -> bool:
    """日本語では使わない漢字（中国語の簡体字など。例: 咖啡・电脑）が混ざっているか。

    日本語の漢字は Windows の日本語の文字コード（cp932）に入っているので、入っていない漢字で見分ける。
    """
    for ch in text:
        if "㐀" <= ch <= "鿿":
            try:
                ch.encode("cp932")
            except UnicodeEncodeError:
                return True
    return False


def extract_points(item: dict) -> list[str]:
    """紹介カード用のおすすめポイント3つ。うまく作れなければ空のリストを返す。"""
    for attempt in range(TRIES):
        try:
            res = requests.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": model_name(),
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0.3 + 0.4 * attempt},  # やり直すときは、少し違う答えが出るようにする
                    "messages": [{"role": "user", "content": POINTS_PROMPT + "\n\n" + item_info(item)}],
                },
                timeout=120,
            )
            points = json.loads(res.json()["message"]["content"]).get("points", [])
        except (requests.RequestException, ValueError, KeyError, AttributeError):
            return []
        points = [str(p).strip(" 　、,") for p in points if str(p).strip(" 　、,")]
        good = [p for p in points if not has_english(p) and not has_foreign_kanji(p)]
        if len(good) >= 3:
            return [p[:26] for p in good][:3]
    return []


def _ask_comment(item: dict) -> str:
    try:
        res = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": model_name(),
                "stream": False,
                "options": {"temperature": 0.8},
                "messages": [
                    {"role": "system", "content": system_prompt()},
                    # 小さいモデルは長く書きがちなので、長さと事実の制限を最後にもう一度伝える
                    {"role": "user", "content": item_info(item) + "\n\n" + (
                        "1段落・200文字以内で書いてください。商品情報に書かれていないこと（環境への配慮、耐久性、体への効果など）は書かないでください。"
                        "「探していたら」「見つけました」「買ってよかった」「使っています」のような、自分の行動や体験の話は書かないでください。"
                        "「〜な人におすすめ」「〜できそう」のように、商品の紹介として書いてください。"
                        "日本語だけで書いてください。中国語の言葉や、ハッシュタグ以外の英単語は入れないでください。")},
                ],
            },
            timeout=180,
        )
    except requests.RequestException as e:
        raise WriterError("PCのAI（Ollama）に接続できませんでした。Ollama が起動しているか確認してください。") from e
    if res.status_code != 200:
        raise WriterError(f"PCのAI（Ollama）がエラーを返しました（{res.status_code}）: {res.text[:200]}")

    # 文字化け（�）が混ざることがあるので取り除く
    return res.json().get("message", {}).get("content", "").replace("�", "").strip()


def write_comment_local(item: dict) -> str:
    text = ""
    for _ in range(TRIES):
        text = _ask_comment(item)
        body = re.sub(r"#\S+", "", text)  # ハッシュタグの英語は大丈夫
        if text and not has_foreign_kanji(text) and not has_english(body):
            return text
    if not text:
        raise WriterError("紹介文が空でした。")
    return text  # やり直してもだめなときは、そのまま出す（投稿前に読んで直せる）
