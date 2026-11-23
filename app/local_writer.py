"""自分のPCで動くAI（Ollama）に、ROOM 用のおすすめコメントを書いてもらう。

アカウントもAPIキーもいらない。Ollama が起動していれば http://127.0.0.1:11434 で使える。
"""
import json
import os

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
- 1つ20文字以内の日本語
- 商品情報に書かれていることだけを使う。使った感想や体験談は書かない
- 価格やレビュー件数は書かない（別の画像に載せるため）
- {"points": ["...", "...", "..."]} というJSONだけを出力する"""


def extract_points(item: dict) -> list[str]:
    """紹介カード用のおすすめポイント3つ。うまく作れなければ空のリストを返す。"""
    try:
        res = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": model_name(),
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.3},
                "messages": [{"role": "user", "content": POINTS_PROMPT + "\n\n" + item_info(item)}],
            },
            timeout=120,
        )
        points = json.loads(res.json()["message"]["content"]).get("points", [])
    except (requests.RequestException, ValueError, KeyError):
        return []
    points = [str(p).strip() for p in points if str(p).strip()]
    return [p[:26] for p in points][:3] if len(points) >= 3 else []


def write_comment_local(item: dict) -> str:
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
                        "1段落・200文字以内で書いてください。商品情報に書かれていないこと（環境への配慮、耐久性など）は書かないでください。"
                        "「探していたら」「見つけました」「買ってよかった」「使っています」のような、自分の行動や体験の話は書かないでください。"
                        "「〜な人におすすめ」「〜できそう」のように、商品の紹介として書いてください。ハッシュタグ以外に英単語は入れないでください。")},
                ],
            },
            timeout=180,
        )
    except requests.RequestException as e:
        raise WriterError("PCのAI（Ollama）に接続できませんでした。Ollama が起動しているか確認してください。") from e
    if res.status_code != 200:
        raise WriterError(f"PCのAI（Ollama）がエラーを返しました（{res.status_code}）: {res.text[:200]}")

    # 文字化け（�）が混ざることがあるので取り除く
    text = res.json().get("message", {}).get("content", "").replace("�", "").strip()
    if not text:
        raise WriterError("紹介文が空でした。")
    return text
