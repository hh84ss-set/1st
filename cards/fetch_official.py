"""『おねがいアイプリ』 공식 사이트에서 카드·캐릭터 이미지를 받아 게임용 크기로 저장한다.

개인적으로 즐기는 용도로만 사용하세요. 이미지는 타카라토미 아츠의 저작물이라
저장소에는 올리지 않습니다(.gitignore).

    pip install pillow
    python3 cards/fetch_official.py
"""
import io
import re
import urllib.request
from html import unescape
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
CARD_PAGE = "https://aipri.jp/card/oa4.html"
CHARA_BASE = "https://aipri.jp/anime/wp/wp-content/themes/onegai_v1/onegai/assets/webp/pc/character/list_visual_"
CHARAS = {
    "konomi_inori": "inori", "yumemiya_aoi": "aoi", "tomosaka_gumi": "gumi", "yuki_olivia": "olivia",
    "atami_nana": "nana", "mochinaga_emma": "emma", "fortu": "fortu", "azumin": "azumin", "kyoka": "kyoka",
    "riori": "riori", "meganee": "meganee", "prineko": "prineko", "priusa": "priusa",
}
MASCOTS = {"fortu", "prineko", "priusa"}


def get(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read()


def fetch_cards():
    out = HERE / "oa4"
    out.mkdir(exist_ok=True)
    page = get(CARD_PAGE).decode("utf-8")
    imgs = re.findall(r'data-img1="(img/oa4/[^"]+_O\.webp)"', page)
    for rel in imgs:
        im = Image.open(io.BytesIO(get("https://aipri.jp/card/" + rel))).convert("RGB")
        name = Path(rel).name.replace("_O", "")
        im.resize((320, 466), Image.LANCZOS).save(out / name, "WEBP", quality=84)
    print(f"cards: {len(imgs)}")


def alpha_box(im):
    return im.getchannel("A").point(lambda a: 255 if a > 40 else 0).getbbox()


def fetch_charas():
    out = HERE / "chara"
    out.mkdir(exist_ok=True)
    for src, cid in CHARAS.items():
        im = Image.open(io.BytesIO(get(CHARA_BASE + src + ".webp"))).convert("RGBA")
        x0, y0, x1, y1 = alpha_box(im)
        h = y1 - y0
        if cid in MASCOTS:  # 몸 전체
            side = max(x1 - x0, int(h * 0.75))
            cx, cy = (x0 + x1) // 2, y0 + side // 2
        else:  # 얼굴 부분
            side = int(h * 0.36)
            bx0, _, bx1, _ = alpha_box(im.crop((x0, y0, x1, y0 + int(h * 0.2))))
            cx, cy = x0 + (bx0 + bx1) // 2, y0 + int(side * 0.52)
        crop = Image.new("RGBA", (side, side))
        crop.paste(im.crop((cx - side // 2, cy - side // 2, cx + side // 2, cy + side // 2)), (0, 0))
        crop.resize((160, 160), Image.LANCZOS).save(out / f"{cid}.webp", "WEBP", quality=88)
    print(f"characters: {len(CHARAS)}")


if __name__ == "__main__":
    fetch_cards()
    fetch_charas()
