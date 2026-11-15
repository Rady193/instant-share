"""Draws the Text Share icon (icon.png, icon.ico) from the same shapes as icon.svg. Needs Pillow; build.py runs it."""

from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).parent
PURPLE, GOLD = (91, 44, 131, 255), (240, 195, 60, 255)
ARROWS = [[(7, 12), (24, 12), (20, 8)], [(25, 20), (8, 20), (12, 24)]]    # on the 32 x 32 grid of icon.svg


def draw(size, stroke):
    big = size * 8                                   # drawn large, then shrunk: smooth edges
    k = big / 32
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, big - 1, big - 1), radius=8 * k, fill=PURPLE)
    w = stroke * k
    for arrow in ARROWS:
        pts = [(x * k, y * k) for x, y in arrow]
        d.line(pts, fill=GOLD, width=round(w), joint="curve")
        for x, y in pts:                             # round ends, like stroke-linecap="round"
            d.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=GOLD)
    return img.resize((size, size), Image.LANCZOS)


def arrows(size, stroke=2.6):
    """The arrows alone, on nothing: an Android adaptive icon's foreground (108 dp, the arrows in its middle 72 dp,
    so any mask shape keeps them whole)."""
    big = size * 8
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    k, off = big / 108 * 72 / 32, big / 108 * 18
    w = stroke * k
    for arrow in ARROWS:
        pts = [(off + x * k, off + y * k) for x, y in arrow]
        d.line(pts, fill=GOLD, width=round(w), joint="curve")
        for x, y in pts:
            d.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=GOLD)
    return img.resize((size, size), Image.LANCZOS)


def android(res):
    """The Android app's icons: the whole icon for old launchers, the arrows for adaptive ones."""
    for density, dp in (("mdpi", 1), ("hdpi", 1.5), ("xhdpi", 2), ("xxhdpi", 3), ("xxxhdpi", 4)):
        folder = res / f"mipmap-{density}"
        folder.mkdir(parents=True, exist_ok=True)
        draw(round(48 * dp), 2.9).save(folder / "ic_launcher.png")
        arrows(round(108 * dp)).save(folder / "ic_launcher_foreground.png")


def main():
    draw(512, 2.6).save(HERE / "icon.png")
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    frames = [draw(s, 3.4 if s <= 24 else 2.9 if s <= 48 else 2.6) for s in sizes]   # thicker arrows when small
    frames[-1].save(HERE / "icon.ico", sizes=[(s, s) for s in sizes], append_images=frames[:-1])
    android(HERE.parent / "android" / "res")
    print("icon.png, icon.ico, android/res/mipmap-*")


if __name__ == "__main__":
    main()
