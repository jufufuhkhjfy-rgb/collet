"""紹介カード用の背景を、ジャンルごとにAIで作り置きする（商品は描かない）。

使い方: imggen\\.venv\\Scripts\\python.exe imggen\\make_backgrounds.py <保存先フォルダ> [1ジャンルあたりの枚数]
"""
import random
import sys
from pathlib import Path

import torch
from diffusers import StableDiffusionInpaintPipeline
from PIL import Image

MODEL = "stable-diffusion-v1-5/stable-diffusion-inpainting"
SIZE = 512
STYLE = "high quality photo, soft natural daylight, shallow depth of field, bokeh, calm, clean, minimal, no people"
SCENES = {
    "インテリア": [
        "a bright bedroom with linen bedding and a small plant",
        "a minimal living room with light wood furniture and white walls",
        "a cozy room corner with a rattan chair and soft curtains",
        "a japanese style room with wooden floor and warm light",
        "a white shelf with ceramic vases and books",
    ],
    "雑貨": [
        "a clean white kitchen counter with ceramic jars",
        "a wooden table near a window with a coffee cup",
        "a tidy bathroom shelf with folded towels",
        "a laundry room with baskets and soft light",
        "a simple entryway with a wooden bench",
    ],
    "ファッション": [
        "a minimal closet with neatly hung clothes",
        "a wooden clothes rack against a white wall",
        "a bright room with a full length mirror",
        "folded sweaters on a wooden shelf",
        "a calm street with autumn trees, blurred background",
    ],
    "PCガジェット": [
        "a tidy wooden desk setup near a window with a plant",
        "a minimal white desk with a monitor, soft light",
        "a cozy desk at night with a warm desk lamp",
        "a desk with a mechanical keyboard and a coffee mug, top view",
        "a modern home office with shelves and a large window",
    ],
}
NEGATIVE = "text, letters, watermark, logo, person, people, hands, face, blurry subject, low quality, distorted, cluttered"
FOLDER = {"インテリア": "interior", "雑貨": "goods", "ファッション": "fashion", "PCガジェット": "gadget"}


def main() -> None:
    out = Path(sys.argv[1])
    per_group = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    pipe = StableDiffusionInpaintPipeline.from_pretrained(MODEL, torch_dtype=torch.float16).to("cuda")
    pipe.set_progress_bar_config(disable=True)
    blank = Image.new("RGB", (SIZE, SIZE), (235, 230, 222))
    full_mask = Image.new("L", (SIZE, SIZE), 255)  # 全部AIに描かせる＝普通の画像生成

    for group, scenes in SCENES.items():
        folder = out / FOLDER[group]
        folder.mkdir(parents=True, exist_ok=True)
        for i in range(per_group):
            prompt = f"{scenes[i % len(scenes)]}, {STYLE}"
            gen = torch.Generator(device="cuda").manual_seed(random.randint(0, 2**31 - 1))
            img = pipe(prompt=prompt, negative_prompt=NEGATIVE, image=blank, mask_image=full_mask,
                       height=SIZE, width=SIZE, num_inference_steps=30, guidance_scale=7.5, generator=gen).images[0]
            if img.getextrema() == ((0, 0), (0, 0), (0, 0)):
                continue  # 安全フィルターで黒く塗られた画像は使わない
            img.save(folder / f"{i + 1:02d}.jpg", quality=92)
        print(group, "done", flush=True)


if __name__ == "__main__":
    main()
