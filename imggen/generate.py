"""商品写真を、AIで作った部屋やデスクの背景に合成して、イメージ画像を作る。

Python 3.12 の専用環境（imggen\\.venv）で動かす。Collet から次のように呼ばれる:
    imggen\\.venv\\Scripts\\python.exe imggen\\generate.py <商品画像URL> <ジャンル> <保存先フォルダ> [枚数]

決まりごと:
- 商品そのものはAIに描かせない。生成のあと、元の商品写真をそのまま上に重ね直す
- AIで作った画像であることは、Collet が紹介文の先頭の注意書きで伝える
"""
import importlib.util
import io
import json
import random
import sys
from pathlib import Path

# Windows の「スマート アプリ コントロール」が scipy の部品（DLL）を止めることがある。
# この画像作りでは scipy を使わないので、diffusers に「scipy は入っていない」と思わせて読み込ませない。
_find_spec = importlib.util.find_spec
importlib.util.find_spec = lambda name, *a, **k: None if name.split(".")[0] == "scipy" else _find_spec(name, *a, **k)

import requests  # noqa: E402
import torch  # noqa: E402
from diffusers import StableDiffusionInpaintPipeline  # noqa: E402
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

MODEL = "stable-diffusion-v1-5/stable-diffusion-inpainting"
SIZE = 512

PHOTO = "high quality photo, natural soft daylight, clean, minimal, japanese apartment"
SCENES = {
    "インテリア": [
        "a bright cozy bedroom corner, wooden floor, white wall, small plant",
        "a minimal living room, light wood furniture, linen curtains",
        "a tidy one-room apartment, warm afternoon light, beige tones",
    ],
    "雑貨": [
        "a clean wooden shelf in a bright room, small plant",
        "a white kitchen counter, tidy, morning light",
        "a simple wooden table near a window, soft shadows",
    ],
    "ファッション": [
        "a simple bedroom with a wooden clothes rack, white wall",
        "a minimal entryway with a mirror, light wood floor",
        "a bright room with a neatly made bed, beige linen",
    ],
    "PCガジェット": [
        "a tidy wooden desk setup near a window, small plant",
        "a minimal white desk, monitor in the background, soft light",
        "a cozy study desk at night, warm desk lamp light",
    ],
}
NEGATIVE = ("text, letters, watermark, logo, person, people, hands, face, blurry, low quality, distorted, "
            "cluttered, black blobs, stains, dark shapes, floating objects, messy")


def load_product(url: str) -> Image.Image:
    # 楽天の画像は小さいサイズで来るので、大きいサイズを頼む
    url = url.replace("_ex=128x128", "_ex=500x500")
    res = requests.get(url, timeout=30)
    res.raise_for_status()
    return Image.open(io.BytesIO(res.content)).convert("RGBA")


def white_ratio(img: Image.Image) -> float:
    """四隅からつながる白い背景が、画像のどれくらいを占めるか。"""
    w, h = img.size
    r, g, b = (c.point(lambda v: 255 if v > 232 else 0) for c in img.convert("RGB").split())
    fill = ImageChops.multiply(ImageChops.multiply(r, g), b)
    for corner in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        if fill.getpixel(corner) == 255:
            ImageDraw.floodfill(fill, corner, 128)
    return fill.histogram()[128] / (w * h)


MIN_WHITE = 0.45  # 白い背景がこれより少ない写真は、宣伝の文字や飾りが多いので使わない
UNSUITABLE = 3  # 終了コード: この商品の写真は合成に向かない


def pick_product(urls: list[str]) -> Image.Image:
    """商品写真が何枚かあれば、白い背景が一番多いもの（宣伝の文字が少ないもの）を使う。"""
    best, best_score = None, -1.0
    for url in urls[:3]:
        try:
            img = load_product(url)
        except requests.RequestException:
            continue
        score = white_ratio(img)
        if score > best_score:
            best, best_score = img, score
    if best is None:
        raise RuntimeError("商品写真を読み込めませんでした。")
    return best


def cut_out_ai(img: Image.Image) -> Image.Image:
    """切り抜きAI（rembg）で、写真の中の一番目立つ物だけを残す。商品の色や形は変えない。"""
    from rembg import new_session, remove
    out = remove(img, session=new_session("isnet-general-use"))
    return out.crop(out.getbbox() or (0, 0, *out.size))


def cut_out(img: Image.Image) -> Image.Image:
    """四隅からつながる白っぽい背景を透明にする。背景が白でなければ、そのまま返す。"""
    w, h = img.size
    # R・G・B すべてが明るいところだけを「白」とする
    r, g, b = (c.point(lambda v: 255 if v > 232 else 0) for c in img.convert("RGB").split())
    fill = ImageChops.multiply(ImageChops.multiply(r, g), b)
    for corner in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        if fill.getpixel(corner) == 255:
            ImageDraw.floodfill(fill, corner, 128)
    background = fill.point(lambda v: 255 if v == 128 else 0)
    if sum(background.histogram()[255:]) < w * h * 0.15:
        return img  # 背景が白くない写真
    alpha = Image.eval(background, lambda v: 255 - v).filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(1))
    out = img.copy()
    out.putalpha(alpha)
    return out.crop(out.getbbox() or (0, 0, w, h))


def place(product: Image.Image) -> tuple[Image.Image, Image.Image, Image.Image]:
    """商品を下寄りに置いた画像、AIに描かせる場所のマスク、商品だけのレイヤーを返す。"""
    p = product.copy()
    p.thumbnail((int(SIZE * 0.55), int(SIZE * 0.55)))
    x = (SIZE - p.width) // 2
    y = int(SIZE * 0.86) - p.height
    layer = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    layer.paste(p, (x, y), p)
    base = Image.new("RGB", (SIZE, SIZE), (235, 230, 222))
    base.paste(layer, (0, 0), layer)
    # 白＝AIが描く場所。商品のまわりだけ少し残して、なじませる
    keep = layer.split()[3].point(lambda v: 255 if v > 20 else 0).filter(ImageFilter.MaxFilter(5))
    mask = Image.eval(keep, lambda v: 255 - v)
    return base, mask, layer


def main() -> None:
    urls, group, out_dir = sys.argv[1].split(","), sys.argv[2], Path(sys.argv[3])
    count = int(sys.argv[4]) if len(sys.argv) > 4 else 3
    out_dir.mkdir(parents=True, exist_ok=True)

    product = cut_out_ai(pick_product(urls))
    base, mask, layer = place(product)

    # 公開する画像なので、不適切な画像を止める安全フィルターは付けたままにする
    pipe = StableDiffusionInpaintPipeline.from_pretrained(MODEL, torch_dtype=torch.float16)
    pipe = pipe.to("cuda" if torch.cuda.is_available() else "cpu")
    pipe.set_progress_bar_config(disable=True)

    scenes = SCENES.get(group, SCENES["雑貨"])
    paths = []
    for i in range(count):
        prompt = f"{scenes[i % len(scenes)]}, an object placed on the floor or table, {PHOTO}"
        gen = torch.Generator(device=pipe.device).manual_seed(random.randint(0, 2**31 - 1))
        result = pipe(prompt=prompt, negative_prompt=NEGATIVE, image=base, mask_image=mask,
                      height=SIZE, width=SIZE, num_inference_steps=30, guidance_scale=7.5, generator=gen).images[0]
        # 商品はAIの絵を使わず、元の写真を重ね直す
        result = result.convert("RGBA")
        result.alpha_composite(layer)
        path = out_dir / f"{i + 1}.png"
        result.convert("RGB").save(path)
        paths.append(str(path))
    print(json.dumps({"images": paths}))


if __name__ == "__main__":
    main()
