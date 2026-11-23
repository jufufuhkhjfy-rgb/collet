"""商品情報から ROOM 用のおすすめコメントを Claude に書いてもらう。"""
import os

import anthropic

from .genres import GENRE_NAMES

MODEL = "claude-opus-5-5"

# 投稿者の紹介。.env の COLLET_PERSONA で、使う人ごとに変えられる
DEFAULT_PERSONA = "インテリア・雑貨・ファッション・PCガジェットを、コスパ重視で集めている人"

SYSTEM_TEMPLATE = """あなたは楽天ROOMに投稿するおすすめコメントを書くアシスタントです。
投稿者は、{persona}です。

書き方:
- 120〜200文字の日本語。です・ます調で、明るく親しみやすく。
- 商品情報から読み取れる良い点（使う場面、サイズ感、価格のわりに良い点、レビュー評価）を具体的に書く。
- 投稿者が実際に使ったとは書かない。「使ってみた」「愛用中」などの体験談は作らない。
- 商品情報にない性能や効果を作らない。誇大な表現（最強、絶対、必ず）は使わない。
- 最後に関連するハッシュタグを2〜3個つける（例: #デスク周り #一人暮らし）。
- コメント本文だけを出力する。前置きや説明は書かない。"""


def system_prompt() -> str:
    return SYSTEM_TEMPLATE.format(persona=os.getenv("COLLET_PERSONA") or DEFAULT_PERSONA)


_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


class WriterError(Exception):
    pass


def item_info(item: dict) -> str:
    return (
        f"商品名: {item['name']}\n"
        f"ジャンル: {GENRE_NAMES.get(item['genre_id'], '')}\n"
        f"価格: {item['price']}円\n"
        f"レビュー: ★{item['review_average']}（{item['review_count']}件）\n"
        f"ショップ: {item['shop']}\n"
        f"商品説明（抜粋）: {item['caption']}"
    )


def write_comment(item: dict) -> str:
    info = item_info(item)
    try:
        response = _get_client().beta.messages.create(
            model=MODEL,
            max_tokens=2000,
            output_config={"effort": "low"},
            # 安全フィルターで断られたときは、サーバー側で別モデルに自動で切り替える
            betas=["server-side-fallback-2026-07-01"],
            extra_body={"fallbacks": "default"},
            system=system_prompt(),
            messages=[{"role": "user", "content": info}],
        )
    except anthropic.AuthenticationError as e:
        raise WriterError("Claude APIキーが正しくありません。.env の ANTHROPIC_API_KEY を確認してください。") from e
    except anthropic.RateLimitError as e:
        raise WriterError("Claude APIの利用上限に達しました。しばらく待ってから再実行してください。") from e
    except anthropic.APIStatusError as e:
        raise WriterError(f"Claude APIがエラーを返しました（{e.status_code}）。") from e
    except anthropic.APIConnectionError as e:
        raise WriterError("Claude APIに接続できませんでした。ネット接続を確認してください。") from e

    if response.stop_reason == "refusal":
        raise WriterError("この商品は紹介文を作れませんでした。別の商品で続けます。")

    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise WriterError("紹介文が空でした。")
    return text
