"""紹介カード画像（1080×1080）を3枚作る。背景は imggen で作り置きしたAI画像を使う。

商品写真は描き変えず、白いパネルに載せるだけ。AIの背景であることは、紹介文の先頭の注意書きで伝える。
"""
import hashlib
import io
import re
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .config import ROOT
from .genres import GENRE_GROUPS

S = 1080
ASSETS = ROOT / "assets"
BG_DIR = ASSETS / "backgrounds"
FONT_DIR = ASSETS / "fonts"
FOLDER = {"インテリア": "interior", "雑貨": "goods", "ファッション": "fashion", "PCガジェット": "gadget"}

# デザインシステム Collet の色
INK = (23, 42, 51)
MUTED = (94, 107, 112)
BRAND = (9, 93, 123)
BRAND_SOFT = (227, 239, 243)
ACCENT = (194, 85, 58)
WHITE = (255, 255, 255)

_GROUP_OF = {g["id"]: group["name"] for group in GENRE_GROUPS for g in group["genres"]}


def _font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    path = FONT_DIR / f"MPLUSRounded1c-{weight}.ttf"
    if path.exists():
        return ImageFont.truetype(str(path), size)
    return ImageFont.truetype(r"C:\Windows\Fonts\meiryob.ttc" if weight != "Medium" else r"C:\Windows\Fonts\meiryo.ttc", size)


def available() -> bool:
    return any(BG_DIR.glob("*/*.jpg"))


def _background(item: dict, salt: int) -> Image.Image:
    folder = BG_DIR / FOLDER.get(_GROUP_OF.get(item["genre_id"], "雑貨"), "goods")
    files = sorted(folder.glob("*.jpg")) or sorted(BG_DIR.glob("*/*.jpg"))
    # 同じ商品なら同じ背景になるよう、商品コードから選ぶ
    n = int(hashlib.md5(f"{item['code']}{salt}".encode()).hexdigest(), 16)
    bg = Image.open(files[n % len(files)]).convert("RGB").resize((S, S), Image.LANCZOS)
    return bg.filter(ImageFilter.GaussianBlur(4))


def _product(item: dict) -> Image.Image:
    urls = item.get("image_urls") or [item["image_url"]]
    res = requests.get(urls[0].replace("_ex=128x128", "_ex=600x600"), timeout=30)
    res.raise_for_status()
    return Image.open(io.BytesIO(res.content)).convert("RGB")


def _panel(canvas: Image.Image, box: tuple[int, int, int, int], radius: int = 44, fill=WHITE) -> None:
    """やわらかい影つきの角丸パネルを描く。"""
    x0, y0, x1, y1 = box
    shadow = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(shadow).rounded_rectangle((x0, y0 + 14, x1, y1 + 14), radius=radius, fill=70)
    canvas.paste((0, 0, 0), (0, 0), shadow.filter(ImageFilter.GaussianBlur(22)))
    ImageDraw.Draw(canvas).rounded_rectangle(box, radius=radius, fill=fill)


def _fit(img: Image.Image, w: int, h: int) -> Image.Image:
    img = img.copy()
    img.thumbnail((w, h), Image.LANCZOS)
    return img


def _pill(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, font, fg, bg, anchor_right=False) -> None:
    box = draw.textbbox((0, 0), text, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    x, y = xy
    if anchor_right:
        x -= w + 40
    draw.rounded_rectangle((x, y, x + w + 40, y + h + 24), radius=(h + 24) // 2, fill=bg)
    draw.text((x + 20, y + 12 - box[1]), text, font=font, fill=fg)



def _wrap(text: str, font, width: int, draw: ImageDraw.ImageDraw) -> list[str]:
    lines, line = [], ""
    for ch in text:
        if draw.textlength(line + ch, font=font) > width:
            lines.append(line)
            line = ch
        else:
            line += ch
    return lines + [line] if line else lines


def _star(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float, fill) -> None:
    import math
    pts = []
    for i in range(10):
        rad = r if i % 2 == 0 else r * 0.45
        a = math.radians(-90 + i * 36)
        pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
    draw.polygon(pts, fill=fill)


def card_product(item: dict, product: Image.Image) -> Image.Image:
    c = _background(item, 1)
    _panel(c, (150, 130, 930, 910))
    p = _fit(product, 700, 700)
    c.paste(p, (540 - p.width // 2, 520 - p.height // 2))
    draw = ImageDraw.Draw(c, "RGBA")
    _pill(draw, (150, 60), _GROUP_OF.get(item["genre_id"], "おすすめ"), _font("Bold", 30), WHITE, BRAND + (235,))
    return c


def card_review(item: dict, product: Image.Image) -> Image.Image:
    c = _background(item, 2)
    _panel(c, (80, 80, 1000, 1000))
    p = _fit(product, 480, 420)
    c.paste(p, (540 - p.width // 2, 130 + (420 - p.height) // 2))
    draw = ImageDraw.Draw(c)
    draw.line((160, 600, 920, 600), fill=(232, 226, 218), width=3)

    avg = float(item["review_average"])
    for i in range(5):
        # 満点の星は濃く、端数の星は薄く、届かない星はグレー
        fill = ACCENT if avg >= i + 0.75 else ((233, 170, 150) if avg >= i + 0.25 else (232, 226, 218))
        _star(draw, 290 + i * 70, 680, 30, fill)
    draw.text((660, 680), f"{avg:.1f}", font=_font("ExtraBold", 72), fill=INK, anchor="lm")
    draw.text((540, 770), f"レビュー {int(item['review_count']):,}件", font=_font("Bold", 40), fill=MUTED, anchor="mm")
    draw.text((540, 885), f"¥{int(item['price']):,}", font=_font("ExtraBold", 96), fill=BRAND, anchor="mm")
    return c


def card_points(item: dict, product: Image.Image, points: list[str]) -> Image.Image:
    c = _background(item, 3)
    _panel(c, (80, 80, 1000, 1000))
    p = _fit(product, 260, 260)
    c.paste(p, (900 - p.width, 130))
    draw = ImageDraw.Draw(c)
    draw.text((140, 150), "おすすめ", font=_font("ExtraBold", 64), fill=INK)
    draw.text((140, 235), "ポイント", font=_font("ExtraBold", 64), fill=INK)
    font = _font("Bold", 42)
    y = 440
    for pt in points[:3]:
        draw.ellipse((140, y + 4, 196, y + 60), fill=BRAND_SOFT)
        draw.line((154, y + 33, 164, y + 44, 182, y + 20), fill=BRAND, width=7, joint="curve")
        lines = _wrap(pt, font, 680, draw)[:2]
        for j, line in enumerate(lines):
            draw.text((230, y + j * 58), line, font=font, fill=INK)
        y += 70 + 58 * len(lines) + 20
    return c


def fallback_points(item: dict) -> list[str]:
    """AIがないときのおすすめポイント。商品説明の短い文から取り、足りなければ数字で補う。"""
    parts = re.split(r"[。！!\n■◆●★☆【】]|・|※", item.get("caption") or "")
    picked = [s.strip(" 　,、") for s in parts if 8 <= len(s.strip(" 　,、")) <= 26][:3]
    extras = [
        f"レビュー{int(item['review_count']):,}件、★{float(item['review_average']):.1f}の高評価",
        f"{int(item['price']):,}円で手に取りやすい",
        f"{_GROUP_OF.get(item['genre_id'], '人気')}ランキングで人気",
    ]
    return (picked + extras)[:3]


def make_cards(item: dict, out_dir: Path, points: list[str] | None = None) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    product = _product(item)
    cards = [card_product(item, product), card_review(item, product),
             card_points(item, product, points or fallback_points(item))]
    paths = []
    for i, card in enumerate(cards, 1):
        path = out_dir / f"card{i}.jpg"
        card.save(path, quality=92)
        paths.append(path)
    return paths
