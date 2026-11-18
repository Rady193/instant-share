"""Makes the Instant Share handbook: release/Instant-Share-Handbook-<version>.pdf, its only copy (the name the SCAFFApps
Suite looks for; attached to the GitHub release).

    C:\\venvs\\textshare-build\\Scripts\\python.exe handbook\\build_handbook.py              pictures, then the PDF
    C:\\venvs\\textshare-build\\Scripts\\python.exe handbook\\build_handbook.py --no-shots   the PDF only
    ... --preview                                                                   also a PNG of every page

The layout and every word are in handbook.html, in the SCAFFApps brochure style; kit.py, brochure.css and handbook.css
are the same in every SCAFFApps app. Needs Microsoft Edge, and Playwright, Pillow and pypdfium2 in the build
environment. The pictures
come from the real pages: Instant Share runs inside this script on a spare port with a made-up history and made-up
devices (OFFICE-PC, LAPTOP-1, ...), never this computer's name. The app's own pages (setup, the pop-up) are drawn from
app.py with a stand-in for the app behind them. A window frame is drawn around the full-window pictures.
"""

import argparse
import contextlib
import io
import json
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import quote

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
IMG = HERE / "img"
sys.path.insert(0, str(ROOT))
import instantshare as textshare  # noqa: E402
import app  # noqa: E402
sys.path.insert(0, str(HERE))
import kit  # noqa: E402

PORT = 18799
URL = f"http://localhost:{PORT}/"
MIN = 60_000
DEV = {name: (name.lower().replace("-", "") + "0" * 24)[:24] for name in
       ("OFFICE-PC", "LAPTOP-1", "LAPTOP-2", "RECEPTION", "PHONE")}
KINDS = {"OFFICE-PC": "app", "LAPTOP-1": "app", "LAPTOP-2": "app", "RECEPTION": "browser", "PHONE": "browser"}
ME = "OFFICE-PC"
KEYS = {}                        # each made-up device's key (1.2: only joined devices see the history)

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
    blue, red = (91, 44, 131), (190, 50, 50)            # (the SCAFFApps purple marker)
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


def xlsx_file():
    out = io.BytesIO()
    import zipfile
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/workbook.xml", "<workbook/>")
    return out.getvalue()


PHOTO, PDF, XLSX = site_photo(), pdf_file(), xlsx_file()
MP3 = b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" + b"\x00" * 2000
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
    (55, "LAPTOP-2", "Prices for the Al Khuwair job", None,
     ("Quotation 1147.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", XLSX)),
    (12, "LAPTOP-2", "Please read and sign before Monday.", None, ("Site induction checklist.pdf", "application/pdf", PDF)),
    (7, "PHONE", "", None, ("voice note.mp3", "audio/mpeg", MP3)),
    (18, "LAPTOP-1", "Measured on site: bay width 2.57 m, lift height 2.0 m, 14 bays on the east side", None, None),
    (3, "LAPTOP-1", "git pull origin main\nnpm ci\nnpm run build", None, None),
]


@contextlib.contextmanager
def demo_server():
    """Instant Share with the made-up history and devices, running inside this script; gone afterwards."""
    with tempfile.TemporaryDirectory() as tmp:
        store = textshare.Store(Path(tmp) / "history.db")
        now = textshare.now_ms()
        for minutes, who, text, to, file in HISTORY:
            kind = textshare.check_file(file[0], file[2])[2] if file else None
            store.db.execute(
                "INSERT INTO items (at, device, text, sender, recipients, file_name, file_type, file_size, file_key, "
                "file, file_kind) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (now - minutes * MIN, who, text, DEV[who], json.dumps([DEV[t] for t in to]) if to else None,
                 file[0] if file else None, file[1] if file else None, len(file[2]) if file else None,
                 "k" + DEV[who][:10] + str(minutes) if file else None, file[2] if file else None, kind))
        # OFFICE-PC changed its own message a little later; it pinned the delivery address
        store.db.execute("UPDATE items SET edited_at = ? WHERE text LIKE 'SELECT id%'", (now - 20 * MIN,))
        # LAPTOP-1 measured on site, away from OFFICE-PC: written 70 minutes ago, shared when it was back (Send later)
        store.db.execute("UPDATE items SET written_at = ? WHERE text LIKE 'Measured on site%'", (now - 70 * MIN,))
        store.db.execute("INSERT INTO marks (device, item, pinned, archived) SELECT ?, id, ?, 0 FROM items "
                         "WHERE text LIKE 'Delivery address%'", (DEV[ME], now - 60 * MIN))
        store.db.commit()
        for name, device_id in DEV.items():
            KEYS[name] = store.issue_key(device_id, name, KINDS[name])[1]
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


OPTIONS = json.dumps({"update_check": True, "update": {"current": textshare.VERSION, "latest": textshare.VERSION,
                                                        "available": False, "checked": 1}, "windows": True,
                      "on_top": False, "auto_copy": True, "host": True,
                      "folder": "",
                      "save_folder": "C:\\Users\\Alex\\Downloads", "save_chosen": False})
UPDATE_OPTIONS = OPTIONS.replace('"available": false', '"available": true, "can_install": true').replace(
    '"latest": "' + textshare.VERSION + '"', '"latest": "1.4.0"')
STUB = """
window.pywebview = { api: new Proxy({}, { get: (_, name) => (...args) => Promise.resolve(
  name === "find" ? { found: [{ name: "OFFICE-PC", address: "192.168.1.20", port: 8765, version: "1.2.0" }],
                      me: "LAPTOP-1", mode: null, host: "" }
  : name === "get_options" ? """ + OPTIONS + """
  : name === "outbox_list" ? (window.__waiting || [])
  : { ok: true }) }) };
"""


def waiting_js(entries):
    """A made-up outbox ("Waiting to send"): (minutes ago, text, for, file name, file size)."""
    rows = []
    for minutes, text, to, fname, fsize in entries:
        rows.append({"uid": f"w{minutes:09d}aa", "text": text, "size": len(text),
                     "lines": text.count("\n") + 1 if text else 0, "to": [DEV[t] for t in to], "to_names": list(to),
                     "error": "", "minutes": minutes,
                     "file": {"name": fname, "type": "image/jpeg", "size": fsize} if fname else None})
    return ("window.__waiting = " + json.dumps(rows) + ".map((w) => Object.assign(w, { written: Date.now() - "
            "w.minutes * 60000 }));")


WAITING = [(38, "Site visit, Al Khuwair:\n- east side braced, ties checked\n- bay 3 needs two more ledgers", (), None, 0),
           (21, "The base plates, before the second lift", ("LAPTOP-2",), "base plates.jpg", 1_826_406)]
STUB_UPDATE = STUB.replace(OPTIONS, UPDATE_OPTIONS)


ANDROID = """
window.TextShareAndroid = new Proxy({}, { get: (_, m) => (...a) => {
  const reply = (call, answer) => setTimeout(() => window.textShareDone(call, answer), 100);
  if (m === "info") return JSON.stringify({ me: "PHONE", host: window.__host || "", version: "1.2.0", theme: "dark" });
  if (m === "getOptions") return JSON.stringify({ android: true, save_folder: "Download", save_chosen: false, update_check: true,
                                                  update: { current: "1.2.0", latest: "1.2.0", available: false, checked: 1 } });
  if (m === "join") { window.__host = "OFFICE-PC"; return reply(a[0], { ok: true, pair: true, host: "OFFICE-PC" }); }
  if (m === "find") return reply(a[0], { found: [{ name: "OFFICE-PC", address: "192.168.1.20", port: 8765 }], host: "" });
  if (m === "takeShared") return null;
} });
"""


# the made-up devices run this version
ANDROID = ANDROID.replace('"1.2.0"', '"' + textshare.VERSION + '"')
STUB = STUB.replace('version: "1.2.0"', 'version: "' + textshare.VERSION + '"')
STUB_UPDATE = STUB_UPDATE.replace('version: "1.2.0"', 'version: "' + textshare.VERSION + '"')

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

    def page(width, height, theme, scale=2, to=(), stub=None, who=ME):
        ctx = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=scale,
                                  locale="en-GB")
        ctx.add_init_script((stub or STUB) + f"localStorage.setItem('textshare.to', {json.dumps(json.dumps([DEV[t] for t in to]))});")
        ctx.route("**/api/hello", lambda route: route.fulfill(json={     # the made-up name, not this computer's
            "server": "OFFICE-PC", "version": textshare.VERSION, "you": "192.168.1.20", "name": who, "id": DEV[who],
            "addresses": ["https://OFFICE-PC:8765/", "https://192.168.1.20:8765/"], "paired": True, "encrypted": True}))
        online(store)
        pg = ctx.new_page()
        pg.goto(f"{URL}login?key={KEYS[who]}&next=" + quote(f"/?app=1&me={who}&theme={theme}", safe=""))
        pg.wait_for_selector(".item")
        pg.wait_for_selector("#status.on")
        pg.wait_for_function("() => true")
        return pg

    def draft(pg):
        pg.locator("#editor").fill(DRAFT)
        pg.mouse.move(0, 0)
        pg.wait_for_timeout(600)

    # the whole window, in both appearances; the close-ups mixed between the two through the handbook
    for theme in ("dark", "light"):
        pg = page(1400, 860, theme, 1.5, to=["LAPTOP-1"])
        draft(pg)
        pg.screenshot(path=IMG / f"main-{theme}.png")
        frame(IMG / f"main-{theme}.png", theme, "Instant Share · OFFICE-PC", 1.5)
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
        else:
            # an item's menu: OFFICE-PC's own message (edited) can be changed and deleted by it
            own = pg.locator(".item").filter(has_text="SELECT id")
            own.locator(".more-btn").click()
            pg.wait_for_timeout(300)
            card_box, menu_box = own.bounding_box(), pg.locator("#menu").bounding_box()
            x0 = min(card_box["x"], menu_box["x"]) - 10
            y0 = card_box["y"] - 10
            pg.screenshot(path=IMG / "menu.png", clip={
                "x": x0, "y": y0, "width": max(card_box["x"] + card_box["width"], menu_box["x"] + menu_box["width"]) - x0 + 10,
                "height": max(card_box["y"] + card_box["height"], menu_box["y"] + menu_box["height"]) - y0 + 10})
            pg.keyboard.press("Escape")
            pg.locator(".top").screenshot(path=IMG / "top.png")       # the top: status, this device, pin, Settings
        pg.context.close()

    # writing while away (LAPTOP-1, out of reach of OFFICE-PC; light): the dot red, Share says Send later, and two
    # messages waiting above the history (the app's outbox, made up)
    pg = page(1400, 560, "light", 1.5, stub=STUB + waiting_js(WAITING), who="LAPTOP-1")   # a low window
    pg.route("**/api/**", lambda route: route.abort())
    pg.wait_for_selector("#status.off", timeout=10000)
    pg.locator("#editor").fill("Bay 3: two more ledgers, and toe boards on the second lift.")
    pg.wait_for_selector("#outbox:not([hidden]) .item.waiting")
    pg.mouse.move(0, 0)
    pg.wait_for_timeout(600)
    pg.screenshot(path=IMG / "away.png")
    frame(IMG / "away.png", "light", "Instant Share · OFFICE-PC", 1.5)
    pg.context.close()
    # opened while away: the app's can't-reach page, with Write for later and one message waiting (dark)
    ctx = browser.new_context(viewport={"width": 800, "height": 900}, device_scale_factor=1.5, locale="en-GB")
    ctx.add_init_script(STUB + waiting_js(WAITING[:1]))
    pg = ctx.new_page()
    pg.set_content(app.local_page("unreachable", {"name": "OFFICE-PC", "devices": [
        {"id": DEV[n], "name": n} for n in ("LAPTOP-2", "RECEPTION", "PHONE")]}, "dark"))
    pg.wait_for_selector("#waiting:not([hidden]) .wcard")
    pg.locator("#laterText").fill("Toe boards for the second lift: 24 pieces, 3.0 m")
    pg.mouse.move(0, 0)
    pg.set_viewport_size({"width": 800, "height": pg.evaluate("document.documentElement.scrollHeight")})
    pg.wait_for_timeout(400)
    pg.screenshot(path=IMG / "later.png")
    frame(IMG / "later.png", "dark", "Instant Share", 1.5)
    ctx.close()

    # Settings, on the computer that keeps the history: a laptop-sized window, its sections in columns (light)
    def settings(theme, stub=None):
        pg = page(1280, 860, theme, 1.5, stub=stub)
        pg.locator("#gear").click()
        pg.wait_for_selector(".hero")
        pg.wait_for_timeout(500)
        return pg

    pg = settings("light")
    sheet = pg.locator(".sheet").bounding_box()
    bottom = pg.evaluate("Math.max(...[...document.querySelectorAll('.sheet .sec')].filter(s => s.offsetParent)"
                         ".map(s => s.getBoundingClientRect().bottom))")
    pg.screenshot(path=IMG / "settings.png", clip={"x": sheet["x"], "y": sheet["y"], "width": sheet["width"],
                                                  "height": bottom + 12 - sheet["y"]})
    # Devices, with a pairing code made for the next one (light)
    pg.locator("#addDevice").click()
    pg.wait_for_selector("#codeLine:not([hidden])")
    pg.wait_for_timeout(400)
    pg.locator("#secDevices").screenshot(path=IMG / "devices.png")
    pg.context.close()
    # the storage (dark)
    pg = settings("dark")
    pg.locator(".sec.viz").screenshot(path=IMG / "storage.png")
    pg.context.close()
    # an update waiting: the About part of Settings (the version and its notes are an example; light)
    pg = page(1280, 860, "light", 1.5, stub=STUB_UPDATE)
    pg.locator("#gear").click()
    pg.wait_for_selector(".hero")
    pg.wait_for_timeout(500)
    pg.locator(".sheet .sec").filter(has=pg.locator("h3", has_text="About")).screenshot(path=IMG / "updates.png")
    pg.context.close()

    # the first start on a Windows account: the welcome page, your name and e-mail (light)
    ctx = browser.new_context(viewport={"width": 1000, "height": 700}, device_scale_factor=1.5)
    ctx.add_init_script(STUB)
    pg = ctx.new_page()
    pg.set_content(app.local_page("register", {}, "light"))
    pg.wait_for_timeout(400)
    pg.screenshot(path=IMG / "register.png")
    frame(IMG / "register.png", "light", "Instant Share", 1.5)
    ctx.close()

    # a laptop joining OFFICE-PC: the pairing code
    ctx = browser.new_context(viewport={"width": 1000, "height": 560}, device_scale_factor=1.5)
    ctx.add_init_script(STUB)
    pg = ctx.new_page()
    pg.set_content(app.local_page("pair", {"name": "OFFICE-PC", "why": ""}, "dark"))
    pg.locator("#code").fill("482913")
    pg.mouse.move(0, 0)
    pg.wait_for_timeout(300)
    pg.screenshot(path=IMG / "pair.png")
    frame(IMG / "pair.png", "dark", "Instant Share", 1.5)
    ctx.close()

    # a photo waiting to be shared, with a line about it (a low window, so the editor is short; light)
    pg = page(1400, 520, "light", 1.5, to=["LAPTOP-1"])
    pg.locator("#fileInput").set_input_files({"name": "east side.jpg", "mimeType": "image/jpeg", "buffer": PHOTO})
    pg.locator("#editor").fill("The east side, as agreed this morning.")
    pg.wait_for_selector("#attach:not([hidden])")
    pg.mouse.move(0, 0)
    pg.wait_for_timeout(400)
    area = pg.locator("#compose").bounding_box()
    pg.screenshot(path=IMG / "attach.png", clip={"x": area["x"] - 10, "y": area["y"] - 6,
                                                "width": area["width"] + 20, "height": area["height"] + 16})
    pg.context.close()

    # the Android app: its setup (light), and the history (dark) (a stand-in answers for the app's Java side)
    phone = dict(viewport={"width": 390, "height": 760}, device_scale_factor=2, is_mobile=True, has_touch=True)
    ctx = browser.new_context(**phone)
    ctx.add_init_script(ANDROID)
    pg = ctx.new_page()
    pg.goto((ROOT / "android" / "assets" / "setup.html").as_uri() + "?theme=light")
    pg.wait_for_selector("#found button")
    pg.wait_for_timeout(300)
    pg.screenshot(path=IMG / "android-setup.png")
    pg.locator("#found button").first.click()                 # chosen: it asks for the pairing code
    pg.wait_for_selector("#pair:not([hidden])")
    pg.locator("#code").fill("482 913")
    pg.wait_for_timeout(300)
    foot = pg.locator(".foot").bounding_box()
    pg.screenshot(path=IMG / "android-pair.png", clip={"x": 0, "y": 0, "width": 390, "height": foot["y"] + foot["height"] + 26})
    ctx.close()
    ctx = browser.new_context(**phone, locale="en-GB")
    ctx.add_init_script(ANDROID)
    ctx.route("**/api/hello", lambda route: route.fulfill(json={
        "server": "OFFICE-PC", "version": textshare.VERSION, "you": "192.168.1.31", "name": "PHONE", "id": DEV["PHONE"],
        "addresses": ["https://OFFICE-PC:8765/", "https://192.168.1.20:8765/"], "android": True, "paired": True,
        "encrypted": True}))
    online(store)
    pg = ctx.new_page()
    pg.goto(f"{URL}login?key={KEYS['PHONE']}&next=" + quote("/?app=1&me=PHONE&theme=dark", safe=""))
    pg.wait_for_selector(".item")
    pg.wait_for_selector("#status.on")
    pg.wait_for_timeout(500)
    pg.screenshot(path=IMG / "android-app.png")
    pg.locator("#gear").click()
    pg.wait_for_selector(".hero")
    pg.wait_for_timeout(400)
    pg.screenshot(path=IMG / "android-settings.png")
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
    frame(IMG / "setup.png", "dark", "Instant Share", 1.5)
    ctx.close()

    # the pop-ups by the tray
    print("pictures: " + ", ".join(sorted(p.name for p in IMG.glob("*.png"))))


def main():
    ap = argparse.ArgumentParser(description="Make the Instant Share handbook (PDF).")
    ap.add_argument("--no-shots", action="store_true", help="keep the pictures in handbook/img as they are")
    ap.add_argument("--preview", action="store_true", help="also a PNG of every page, in handbook/preview")
    args = ap.parse_args()
    if not args.no_shots:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            with demo_server() as store:
                shots(browser, store)
            browser.close()
        # the back cover, the same in every SCAFFApps handbook: the apps' icons, the Suite's views
        kit.family_icons(IMG, ROOT.parent)
        kit.suite_pictures(IMG, ROOT.parent)
    # printed in handbook/build (never open anywhere), then moved into release/, named for the release: the only
    # copy (the name the SCAFFApps Suite looks for, attached to the GitHub release)
    (HERE / "build").mkdir(exist_ok=True)
    pdf = kit.print_pdf(HERE / "handbook.html", HERE / "build" / "Instant Share Handbook.pdf",
                        HERE / "preview" if args.preview else None)
    kit.to_release(pdf, ROOT / "release" / f"Instant-Share-Handbook-{textshare.VERSION}.pdf")
    # release/ keeps only the newest handbook; the earlier ones stay on their GitHub releases
    newest = tuple(int(x) for x in textshare.VERSION.split("."))
    for old in (ROOT / "release").glob("Instant-Share-Handbook-*.pdf"):
        v = old.stem.rsplit("-", 1)[-1]
        if v.replace(".", "").isdigit() and tuple(int(x) for x in v.split(".")) < newest:
            old.unlink(missing_ok=True)
            print(f"removed the older {old.name}")


if __name__ == "__main__":
    main()
