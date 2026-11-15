"""Makes the Text Share handbook: "Text Share Handbook.pdf" in the project folder and, for the Releases page,
release/Text-Share-Handbook-<version>.pdf.

    C:\\venvs\\textshare-build\\Scripts\\python.exe handbook\\build_handbook.py              pictures, then the PDF
    C:\\venvs\\textshare-build\\Scripts\\python.exe handbook\\build_handbook.py --no-shots   the PDF only

Needs Microsoft Edge, and Playwright and Pillow in the build environment (py build.py installs them). The pictures
come from the real pages: Text Share runs inside this script on a spare port with a made-up history and made-up
devices (OFFICE-PC, LAPTOP-1, ...), never this computer's name. The app's own pages (setup, the pop-up) are drawn from
app.py with a stand-in for the app behind them. A window frame is drawn around the full-window pictures.
"""

import argparse
import base64
import contextlib
import io
import json
import shutil
import sys
import tempfile
import threading
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
IMG = HERE / "img"
sys.path.insert(0, str(ROOT))
import textshare  # noqa: E402
import app  # noqa: E402

PORT = 18799
URL = f"http://localhost:{PORT}/"
MIN = 60_000
DEV = {name: (name.lower().replace("-", "") + "0" * 24)[:24] for name in
       ("OFFICE-PC", "LAPTOP-1", "LAPTOP-2", "RECEPTION", "PHONE")}
KINDS = {"OFFICE-PC": "app", "LAPTOP-1": "app", "LAPTOP-2": "app", "RECEPTION": "browser", "PHONE": "browser"}
ME = "OFFICE-PC"

LOG = "\n".join(["2026-11-15 07:00:01  INFO   Nightly backup started",
                 "2026-11-15 07:00:01  INFO   Source: D:\\Data\\Projects",
                 "2026-11-15 07:00:02  INFO   Target: \\\\NAS\\Backups\\2026-11-15"]
                + [f"2026-11-15 07:{i // 3:02d}:{i * 19 % 60:02d}  INFO   Copied folder {i:03d} ({i * 37 % 900 + 40} files)"
                   for i in range(1, 56)]
                + ["2026-11-15 07:19:58  INFO   Nightly backup finished: 18,402 files, 6.1 GB, 19 min 57 s"])
DRAFT = ("Hi Sara, the new build is on the test server:\nhttp://192.168.1.20:3000\n\n"
         "Please check the invoice screen before 3 pm.\nThanks!")


def site_photo():
    """A made-up "photo": a scaffold plan sketched on a whiteboard."""
    w, h = 720, 460
    im = Image.new("RGB", (w, h), (236, 238, 232))
    d = ImageDraw.Draw(im)
    for x in range(0, w, 40):
        d.line([(x, 0), (x, h)], fill=(225, 228, 222))
    for y in range(0, h, 40):
        d.line([(0, y), (w, y)], fill=(225, 228, 222))
    blue, red = (38, 84, 160), (190, 50, 50)
    d.rectangle([120, 90, 600, 380], outline=blue, width=6)
    for x in range(160, 600, 80):
        d.line([(x, 90), (x, 380)], fill=blue, width=3)
    for y in (180, 280):
        d.line([(120, y), (600, y)], fill=blue, width=3)
    d.line([(100, 400), (620, 400)], fill=(60, 60, 60), width=5)
    d.ellipse([500, 50, 560, 110], outline=red, width=5)
    font = ImageFont.truetype("C:/Windows/Fonts/segoeprb.ttf", 34) if Path("C:/Windows/Fonts/segoeprb.ttf").exists() \
        else ImageFont.load_default()
    d.text((130, 30), "Level 2 - east side", fill=blue, font=font)
    d.text((440, 410), "check ties!", fill=red, font=font)
    out = io.BytesIO()
    im.save(out, "JPEG", quality=82)
    return out.getvalue()


def pdf_file():
    text = "Site induction checklist"
    stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for n, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % x for x in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)


PHOTO, PDF = site_photo(), pdf_file()
# (minutes ago, from, text, for (None = everyone), file)
HISTORY = [
    (3 * 1440 + 310, "LAPTOP-2", "Delivery address\nWarehouse 4, Street 18\nIndustrial Area\nCall on arrival: extension 214",
     None, None),
    (1440 + 95, "RECEPTION", "اجتماع المراجعة غداً الساعة العاشرة صباحاً في قاعة الاجتماعات\n"
                             "Review meeting tomorrow at 10:00 in the meeting room", None, None),
    (128, "LAPTOP-1", LOG, None, None),
    (41, "PHONE", "", None, ("east side.jpg", "image/jpeg", PHOTO)),
    (26, "OFFICE-PC", "SELECT id, name, created_at\nFROM customers\nWHERE created_at >= '2026-11-01'\n"
                      "ORDER BY created_at DESC;", ["LAPTOP-1"], None),
    (12, "LAPTOP-2", "Please read and sign before Monday.", None, ("Site induction checklist.pdf", "application/pdf", PDF)),
    (3, "LAPTOP-1", "git pull origin main\nnpm ci\nnpm run build", None, None),
]


@contextlib.contextmanager
def demo_server():
    """Text Share with the made-up history and devices, running inside this script; gone afterwards."""
    with tempfile.TemporaryDirectory() as tmp:
        store = textshare.Store(Path(tmp) / "history.db")
        now = textshare.now_ms()
        for minutes, who, text, to, file in HISTORY:
            store.db.execute(
                "INSERT INTO items (at, device, text, sender, recipients, file_name, file_type, file_size, file_key, "
                "file) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (now - minutes * MIN, who, text, DEV[who], json.dumps([DEV[t] for t in to]) if to else None,
                 file[0] if file else None, file[1] if file else None, len(file[2]) if file else None,
                 "k" + DEV[who][:10] + str(minutes) if file else None, file[2] if file else None))
        store.db.commit()
        server = textshare.make_server(PORT, store)
        if server is None:
            store.close()
            sys.exit(f"Port {PORT} is taken")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            yield store
        finally:
            server.shutdown()
            server.server_close()
            store.close()


def online(store, away=("RECEPTION",)):
    """The made-up devices, heard from just now (RECEPTION two hours ago)."""
    for name, device_id in DEV.items():
        store.touch(device_id, name, KINDS[name])
        if name in away:
            store.seen[device_id]["seen"] -= 2 * 60 * MIN


STUB = """
window.pywebview = { api: new Proxy({}, { get: (_, name) => (...args) => Promise.resolve(
  name === "find" ? { found: [{ name: "OFFICE-PC", address: "192.168.1.20", port: 8765, version: "1.0.0" }],
                      me: "LAPTOP-1", mode: null, host: "" } : { ok: true }) }) };
"""


ANDROID = """
window.TextShareAndroid = new Proxy({}, { get: (_, m) => (...a) => {
  const reply = (call, answer) => setTimeout(() => window.textShareDone(call, answer), 100);
  if (m === "info") return JSON.stringify({ me: "PHONE", host: "", version: "1.0.0", theme: "dark" });
  if (m === "find") return reply(a[0], { found: [{ name: "OFFICE-PC", address: "192.168.1.20", port: 8765 }], host: "" });
  if (m === "takeShared") return null;
} });
"""


def frame(path, theme, title, scale):
    """Draws a window title bar (as Windows draws it for the app) above a picture of the window's inside."""
    inside = Image.open(path).convert("RGB")
    bar_h = round(32 * scale)
    caption, ink = app.TITLE_BAR[theme]
    out = Image.new("RGB", (inside.width, inside.height + bar_h), caption)
    out.paste(inside, (0, bar_h))
    d = ImageDraw.Draw(out)
    icon = Image.open(ROOT / "build" / "icon.png").convert("RGBA").resize((round(16 * scale),) * 2, Image.LANCZOS)
    out.paste(icon, (round(10 * scale), (bar_h - icon.height) // 2), icon)
    font = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", round(12 * scale))
    d.text((round(34 * scale), bar_h // 2), title, fill=ink, font=font, anchor="lm")
    s, cy, right = scale, bar_h // 2, inside.width
    for n, glyph in enumerate(("min", "max", "close")):
        cx = right - round((23 + 46 * (2 - n)) * s)
        if glyph == "min":
            d.line([(cx - 5 * s, cy), (cx + 5 * s, cy)], fill=ink, width=max(1, round(s)))
        elif glyph == "max":
            d.rectangle([cx - 5 * s, cy - 5 * s, cx + 5 * s, cy + 5 * s], outline=ink, width=max(1, round(s)))
        else:
            d.line([(cx - 5 * s, cy - 5 * s), (cx + 5 * s, cy + 5 * s)], fill=ink, width=max(1, round(s)))
            d.line([(cx - 5 * s, cy + 5 * s), (cx + 5 * s, cy - 5 * s)], fill=ink, width=max(1, round(s)))
    d.rectangle([0, 0, out.width - 1, out.height - 1], outline=(150, 150, 150) if theme == "light" else (60, 60, 60))
    out.save(path)


def shots(browser, store):
    IMG.mkdir(exist_ok=True)
    for old in IMG.glob("*.png"):
        old.unlink()

    def page(width, height, theme, scale=2, to=()):
        ctx = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=scale,
                                  locale="en-GB")
        ctx.add_init_script(STUB + f"localStorage.setItem('textshare.to', {json.dumps(json.dumps([DEV[t] for t in to]))});")
        ctx.route("**/api/hello", lambda route: route.fulfill(json={     # the made-up name, not this computer's
            "server": "OFFICE-PC", "version": textshare.VERSION, "you": "192.168.1.20", "name": ME, "id": DEV[ME],
            "addresses": ["http://OFFICE-PC:8765/", "http://192.168.1.20:8765/"]}))
        online(store)
        pg = ctx.new_page()
        pg.goto(f"{URL}?app=1&id={DEV[ME]}&me={ME}&theme={theme}")
        pg.wait_for_selector(".item")
        pg.wait_for_selector("#status.on")
        pg.wait_for_function("() => true")
        return pg

    def draft(pg):
        pg.locator("#editor").fill(DRAFT)
        pg.mouse.move(0, 0)
        pg.wait_for_timeout(600)

    # the whole window, in both appearances
    for theme in ("dark", "light"):
        pg = page(1400, 860, theme, 1.5, to=["LAPTOP-1"])
        draft(pg)
        pg.screenshot(path=IMG / f"main-{theme}.png")
        frame(IMG / f"main-{theme}.png", theme, "Text Share · OFFICE-PC", 1.5)
        if theme == "dark":
            # who should see it
            pg.locator("#to").click()
            pg.wait_for_timeout(500)
            box = pg.locator("#toPop").bounding_box()
            btn = pg.locator("#to").bounding_box()
            pad = 12
            x0, y0 = box["x"] - pad, box["y"] - pad
            pg.screenshot(path=IMG / "to.png", clip={"x": x0, "y": y0, "width": box["width"] + 2 * pad,
                                                     "height": btn["y"] + btn["height"] - y0 + pad})
            pg.keyboard.press("Escape")
            # the newest items
            bar = pg.locator(".pane").nth(1).locator(".bar").bounding_box()
            third = pg.locator(".item").nth(2).bounding_box()
            pg.screenshot(path=IMG / "history.png", clip={"x": bar["x"] - 10, "y": bar["y"] - 10,
                                                         "width": bar["width"] + 20,
                                                         "height": third["y"] + third["height"] - bar["y"] + 20})
            # the top when the computer that keeps the history cannot be reached
            pg.route("**/api/items*", lambda route: route.abort())
            pg.wait_for_selector("#status.off", timeout=10000)
            top = pg.locator(".top").bounding_box()
            pg.screenshot(path=IMG / "offline.png", clip={"x": 0, "y": 0, "width": 900, "height": top["height"]})
        pg.context.close()

    # a photo waiting to be shared, with a line about it (a low window, so the editor is short)
    pg = page(1400, 520, "dark", 1.5, to=["LAPTOP-1"])
    pg.locator("#fileInput").set_input_files({"name": "east side.jpg", "mimeType": "image/jpeg", "buffer": PHOTO})
    pg.locator("#editor").fill("The east side, as agreed this morning.")
    pg.wait_for_selector("#attach:not([hidden])")
    pg.mouse.move(0, 0)
    pg.wait_for_timeout(400)
    area = pg.locator("#compose").bounding_box()
    pg.screenshot(path=IMG / "attach.png", clip={"x": area["x"] - 10, "y": area["y"] - 6,
                                                "width": area["width"] + 20, "height": area["height"] + 16})
    pg.context.close()

    # the Android app: its setup, and the history (a stand-in answers for the app's Java side)
    phone = dict(viewport={"width": 390, "height": 760}, device_scale_factor=2, is_mobile=True, has_touch=True)
    ctx = browser.new_context(**phone)
    ctx.add_init_script(ANDROID)
    pg = ctx.new_page()
    pg.goto((ROOT / "android" / "assets" / "setup.html").as_uri() + "?theme=dark")
    pg.wait_for_selector("#found button")
    pg.wait_for_timeout(300)
    pg.screenshot(path=IMG / "android-setup.png")
    ctx.close()
    ctx = browser.new_context(**phone, locale="en-GB")
    ctx.add_init_script(ANDROID)
    ctx.route("**/api/hello", lambda route: route.fulfill(json={
        "server": "OFFICE-PC", "version": textshare.VERSION, "you": "192.168.1.31", "name": "PHONE", "id": DEV["PHONE"],
        "addresses": ["http://OFFICE-PC:8765/", "http://192.168.1.20:8765/"], "android": True}))
    online(store)
    pg = ctx.new_page()
    pg.goto(f"{URL}?app=1&id={DEV['PHONE']}&me=PHONE&theme=dark")
    pg.wait_for_selector(".item")
    pg.wait_for_selector("#status.on")
    pg.wait_for_timeout(500)
    pg.screenshot(path=IMG / "android-app.png")
    ctx.close()

    # the app's setup page (on a laptop, finding OFFICE-PC)
    ctx = browser.new_context(viewport={"width": 1000, "height": 640}, device_scale_factor=1.5)
    ctx.add_init_script(STUB)
    pg = ctx.new_page()
    pg.set_content(app.local_page("setup", {"mode": None, "me": "LAPTOP-1", "version": textshare.VERSION, "host": "",
                                            "autostart": True}, "dark"))
    pg.wait_for_selector("#found button")
    pg.wait_for_timeout(300)
    pg.screenshot(path=IMG / "setup.png")
    frame(IMG / "setup.png", "dark", "Text Share", 1.5)
    ctx.close()

    # the pop-ups by the tray
    photo_url = "data:image/jpeg;base64," + base64.b64encode(PHOTO).decode()
    for name, theme, data in (
            ("toast-text", "dark", {"id": 1, "who": "LAPTOP-1", "text": "git pull origin main\nnpm ci\nnpm run build",
                                    "size": 37, "file": None, "more": 0, "to_me": False}),
            ("toast-photo", "light", {"id": 2, "who": "PHONE", "text": "", "size": 0, "more": 1, "to_me": True,
                                      "file": {"name": "east side.jpg", "type": "image/jpeg", "size": len(PHOTO),
                                               "url": photo_url}})):
        ctx = browser.new_context(viewport={"width": 380, "height": 176}, device_scale_factor=2)
        ctx.add_init_script(STUB)
        pg = ctx.new_page()
        pg.set_content(app.toast_page(theme))
        pg.evaluate("d => show(d)", data)
        pg.wait_for_timeout(400)
        pg.screenshot(path=IMG / f"{name}.png")
        ctx.close()
    print("pictures: " + ", ".join(sorted(p.name for p in IMG.glob("*.png"))))


def pdf(browser):
    pg = browser.new_page()
    pg.goto((HERE / "handbook.html").as_uri())
    pg.wait_for_load_state("networkidle")
    out = ROOT / "Text Share Handbook.pdf"
    pg.pdf(path=out, format="A4", print_background=True,
           margin={"top": "0.6in", "bottom": "0.8in", "left": "0.6in", "right": "0.6in"},
           display_header_footer=True, header_template="<span></span>",
           footer_template='<div style="width:100%;padding:0 15mm;display:flex;justify-content:space-between;'
                           'font:7.5pt Segoe UI,sans-serif;color:#8b93a5"><span>Text Share Handbook</span>'
                           '<span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span></div>')
    release = ROOT / "release"
    release.mkdir(exist_ok=True)
    shutil.copy2(out, release / f"Text-Share-Handbook-{textshare.VERSION}.pdf")
    print(f"Saved {out} ({out.stat().st_size // 1024} KB) and release/Text-Share-Handbook-{textshare.VERSION}.pdf")


def main():
    ap = argparse.ArgumentParser(description="Make the Text Share handbook (PDF).")
    ap.add_argument("--no-shots", action="store_true", help="keep the pictures in handbook/img as they are")
    args = ap.parse_args()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        if not args.no_shots:
            with demo_server() as store:
                shots(browser, store)
        pdf(browser)
        browser.close()


if __name__ == "__main__":
    main()
