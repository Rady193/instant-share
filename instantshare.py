"""Instant Share: pass text between the computers on your own network.

This file is the heart of Instant Share: the server that keeps the history, and the page every computer shows. On Windows
the app (app.py, packed into the portable .exe by build.py) runs it behind its own window and tray icon. On its own,
with nothing but Python's standard library, it runs as a plain server for Mac and Linux, or for anyone who prefers a
browser:

    python3 instantshare.py               start it and open it in the browser; close the window (Ctrl+C) to stop it
        --no-open                      start it without opening the browser
        --port 9000                    use another port (default 8765)
        --data FOLDER                  keep the history in another folder

Copyright (c) 2026 SCAFFApps. All rights reserved. See LICENSE.
"""

import argparse
import base64
import binascii
import ipaddress
import json
import os
import re
import secrets
import socket
import socketserver
import sqlite3
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

VERSION = "1.1.0"
FROZEN = getattr(sys, "frozen", False)                       # running as the packed .exe
APP_FILE = Path(sys.executable if FROZEN else __file__).resolve()
DEFAULT_PORT = 8765
MAX_BODY = 30 * 1024 * 1024      # biggest share accepted, in bytes (20 MB of text, or a 5 MB file with some)
LIST_LIMIT = 300                 # the history shows the newest this many (search reaches older ones)
FULL_LIMIT = 64 * 1024           # items longer than this come as a preview in the list, the rest on demand
PREVIEW = 4000                   # characters of a long item's preview


def android_app():
    """The Android app (Instant-Share.apk), if this copy of Instant Share carries one: packed into the .exe, or built
    next to the source (android/build)."""
    for folder in ([Path(sys._MEIPASS) / "android"] if FROZEN else []) + [Path(__file__).resolve().parent / "android" / "build"]:
        apk = folder / "Instant-Share.apk"
        if apk.is_file():
            return apk
    return None


def now_ms():
    return int(time.time() * 1000)


def default_data_dir():
    r"""%LOCALAPPDATA%\InstantShare (~/.instantshare elsewhere). A history kept under the old name (Text Share 1.0)
    moves there the first time."""
    base = os.environ.get("LOCALAPPDATA")
    new, old = ((Path(base) / "InstantShare", Path(base) / "TextShare") if base
                else (Path.home() / ".instantshare", Path.home() / ".textshare"))
    if not new.exists() and old.exists():
        try:
            old.rename(new)
        except OSError:                    # still open by Text Share 1.0: used where it is, this time
            return old
    return new


def log(message):
    """A line in the window. It never stops a share: a name the window cannot show comes out as "?"."""
    try:
        print(f"  {time.strftime('%H:%M')}  {message}", flush=True)
    except (AttributeError, OSError, UnicodeError):
        pass


# ---------------------------------------------------------------- the history (one SQLite file)

LINES = "length(i.text) - length(replace(i.text, char(10), '')) + 1"
COLUMNS = (f"i.id, i.at, i.device, length(i.text), {LINES}, "
           f"CASE WHEN length(i.text) <= {FULL_LIMIT} THEN i.text ELSE substr(i.text, 1, {PREVIEW}) END, "
           "i.sender, i.recipients, i.file_name, i.file_type, i.file_size, i.file_key, i.edited_at, i.file_kind, "
           "COALESCE(m.pinned, 0), COALESCE(m.archived, 0)")
MARKS = "items i LEFT JOIN marks m ON m.item = i.id AND m.device = ?"          # this device's pins and archive
VISIBLE = "(i.recipients IS NULL OR i.sender = ? OR instr(i.recipients, ?) > 0)"   # for everyone, from me, or to me
DEVICE_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")
ONLINE_MS = 15_000               # a device is online when it was heard from in the last 15 seconds
FORGET_MS = 30 * 86_400_000      # devices not heard from for 30 days leave the list

FILE_MAX = 5 * 1024 * 1024       # one file, up to 5 MB
HISTORY_MAX = 2 * 1024 ** 3      # the whole history (text and files), up to 2 GB


def _ftyp(d):
    return d[4:8] == b"ftyp"


def _text(d):
    if d.startswith((b"\xff\xfe", b"\xfe\xff")):
        return True
    try:
        d.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return b"\0" not in d


_ZIP = lambda d: d.startswith(b"PK\x03\x04") or d.startswith(b"PK\x05\x06")      # noqa: E731
_OLE = lambda d: d.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")                # noqa: E731
_MP3 = lambda d: d.startswith(b"ID3") or (len(d) > 1 and d[0] == 0xFF and d[1] & 0xE0 == 0xE0)   # noqa: E731
_OGG = lambda d: d.startswith(b"OggS")                                            # noqa: E731
OFFICE = "application/vnd.openxmlformats-officedocument."
OPEN = "application/vnd.oasis.opendocument."

# What can be shared: by name, each checked by its first bytes (a renamed program or web page is refused).
# kind: the storage category. Photos, PDFs, audio and video are shown in the page; everything else only downloads.
FILE_TYPES = {
    ".png": ("image/png", "photo", lambda d: d.startswith(b"\x89PNG\r\n\x1a\n")),
    ".jpg": ("image/jpeg", "photo", lambda d: d.startswith(b"\xff\xd8\xff")),
    ".jpeg": ("image/jpeg", "photo", lambda d: d.startswith(b"\xff\xd8\xff")),
    ".gif": ("image/gif", "photo", lambda d: d[:6] in (b"GIF87a", b"GIF89a")),
    ".webp": ("image/webp", "photo", lambda d: d[:4] == b"RIFF" and d[8:12] == b"WEBP"),
    ".bmp": ("image/bmp", "photo", lambda d: d.startswith(b"BM")),
    ".heic": ("image/heic", "photo", _ftyp),
    ".pdf": ("application/pdf", "document", lambda d: d.startswith(b"%PDF-")),
    ".docx": (OFFICE + "wordprocessingml.document", "document", _ZIP),
    ".xlsx": (OFFICE + "spreadsheetml.sheet", "document", _ZIP),
    ".pptx": (OFFICE + "presentationml.presentation", "document", _ZIP),
    ".doc": ("application/msword", "document", _OLE),
    ".xls": ("application/vnd.ms-excel", "document", _OLE),
    ".ppt": ("application/vnd.ms-powerpoint", "document", _OLE),
    ".odt": (OPEN + "text", "document", _ZIP),
    ".ods": (OPEN + "spreadsheet", "document", _ZIP),
    ".odp": (OPEN + "presentation", "document", _ZIP),
    ".rtf": ("application/rtf", "document", lambda d: d.startswith(b"{\\rtf")),
    ".txt": ("text/plain", "document", _text),
    ".csv": ("text/csv", "document", _text),
    ".md": ("text/markdown", "document", _text),
    ".mp3": ("audio/mpeg", "audio", _MP3),
    ".m4a": ("audio/mp4", "audio", _ftyp),
    ".aac": ("audio/aac", "audio", lambda d: len(d) > 1 and d[0] == 0xFF and d[1] & 0xF0 == 0xF0),
    ".wav": ("audio/wav", "audio", lambda d: d[:4] == b"RIFF" and d[8:12] == b"WAVE"),
    ".ogg": ("audio/ogg", "audio", _OGG),
    ".opus": ("audio/ogg", "audio", _OGG),
    ".flac": ("audio/flac", "audio", lambda d: d.startswith(b"fLaC")),
    ".amr": ("audio/amr", "audio", lambda d: d.startswith(b"#!AMR")),
    ".mp4": ("video/mp4", "video", _ftyp),
    ".mov": ("video/quicktime", "video", lambda d: d[4:8] in (b"ftyp", b"moov", b"wide", b"mdat", b"free")),
    ".3gp": ("video/3gpp", "video", _ftyp),
    ".zip": ("application/zip", "archive", _ZIP),
}
SHOWN = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "application/pdf", "audio/mpeg",
         "audio/mp4", "audio/aac", "audio/wav", "audio/ogg", "audio/flac", "video/mp4", "video/quicktime"}
KINDS = [("text", "Messages"), ("photo", "Photos"), ("document", "Documents"), ("audio", "Audio"),
         ("video", "Videos"), ("archive", "Archives")]


def check_file(name, data):
    """(clean name, type, kind) for a file that may be shared, or None. A name without a known ending gets one when
    the contents are a photo or a PDF (a pasted screenshot, say)."""
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "", str(name or "").replace("\\", "/").rsplit("/", 1)[-1]).strip(" .")
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if ext not in FILE_TYPES:
        for guess in (".png", ".jpg", ".gif", ".webp", ".pdf"):
            if FILE_TYPES[guess][2](data):
                name, ext = (name or ("photo" if guess != ".pdf" else "document")) + guess, guess
                break
        else:
            return None
    mime, kind, looks_right = FILE_TYPES[ext]
    if not looks_right(data):
        return None
    if name.lower() == ext:
        name = ("photo" if kind == "photo" else "file") + ext
    return name[-120:], mime, kind


class HistoryFull(Exception):
    pass


class NotYours(Exception):
    pass


class Store:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS items (
            id     INTEGER PRIMARY KEY AUTOINCREMENT,   -- AUTOINCREMENT: a deleted id is never handed out again
            at     INTEGER NOT NULL,                    -- when it was shared, ms since 1970 (UTC)
            device TEXT NOT NULL,                       -- the name of the device that shared it, at the time
            text   TEXT NOT NULL)""")
        have = {r[1] for r in self.db.execute("PRAGMA table_info(items)")}
        for column, kind in (("sender", "TEXT"),          # the id of the device that shared it
                             ("recipients", "TEXT"),      # JSON list of device ids; NULL = everyone
                             ("file_name", "TEXT"), ("file_type", "TEXT"), ("file_size", "INTEGER"),
                             ("file_key", "TEXT"),        # secret part of the file's address
                             ("file", "BLOB"),
                             ("file_kind", "TEXT"),       # photo, document, audio, video, archive (storage view)
                             ("edited_at", "INTEGER")):   # when its sender last changed its text
            if column not in have:
                self.db.execute(f"ALTER TABLE items ADD COLUMN {column} {kind}")
        self.db.execute("UPDATE items SET file_kind = CASE WHEN file_type LIKE 'image/%' THEN 'photo' "
                        "ELSE 'document' END WHERE file_key IS NOT NULL AND file_kind IS NULL")    # from 1.0
        self.db.execute("""CREATE TABLE IF NOT EXISTS devices (
            id        TEXT PRIMARY KEY,                 -- made up by the device the first time
            name      TEXT NOT NULL,
            kind      TEXT NOT NULL,                    -- "app" or "browser"
            last_seen INTEGER NOT NULL)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS marks (
            device   TEXT NOT NULL,                     -- each device pins and archives for itself only
            item     INTEGER NOT NULL,
            pinned   INTEGER NOT NULL DEFAULT 0,        -- when, in ms (0 = not pinned)
            archived INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (device, item))""")
        self.db.commit()
        self.lock = threading.Lock()
        self.boot = secrets.token_hex(4)    # a restart changes the revision, so every page reloads its list
        self.changes = 0
        self.news = threading.Condition()   # wakes the devices waiting for something new (long polls)
        self.waiting = {}                   # device id -> how many of its requests are waiting right now
        self.closed = False
        self.seen = {r[0]: {"name": r[1], "kind": r[2], "seen": r[3], "saved": r[3]}
                     for r in self.db.execute("SELECT id, name, kind, last_seen FROM devices")}

    @property
    def rev(self):
        return f"{self.boot}.{self.changes}"

    # ---- devices

    def touch(self, device_id, name, kind):
        """A device was heard from: it is online. Written down at most once a minute, or when it changes."""
        now = now_ms()
        before = self.seen.get(device_id)
        entry = {"name": name, "kind": kind, "seen": now, "saved": before["saved"] if before else 0}
        self.seen[device_id] = entry
        if not before or before["name"] != name or before["kind"] != kind or now - entry["saved"] > 60_000:
            entry["saved"] = now
            with self.lock:
                self.db.execute("INSERT INTO devices (id, name, kind, last_seen) VALUES (?, ?, ?, ?) "
                                "ON CONFLICT(id) DO UPDATE SET name = excluded.name, kind = excluded.kind, "
                                "last_seen = excluded.last_seen", (device_id, name, kind, now))
                self.db.commit()

    def devices(self, me=""):
        now = now_ms()
        out = [{"id": i, "name": d["name"], "kind": d["kind"], "seen": d["seen"], "me": i == me,
                "online": now - d["seen"] < ONLINE_MS or self.waiting.get(i, 0) > 0}
               for i, d in list(self.seen.items()) if now - d["seen"] < FORGET_MS]
        out.sort(key=lambda d: (not d["online"], d["name"].lower()))
        return {"now": now, "devices": out}

    def _names(self):
        return {i: d["name"] for i, d in list(self.seen.items())}

    # ---- items

    def _item(self, r, names, me):
        (item_id, at, device, size, lines, text, sender, recipients, fname, ftype, fsize, fkey, edited, fkind,
         pinned, archived) = r
        to = json.loads(recipients) if recipients else None
        return {"id": item_id, "at": at, "device": device, "from": sender or "", "size": size,
                "lines": lines if size else 0, "text": text, "big": size > len(text),
                "mine": bool(sender) and sender == me, "edited": edited, "pinned": bool(pinned),
                "archived": bool(archived),
                "to": [{"id": d, "name": names.get(d, "a device")} for d in to] if to else None,
                "file": {"name": fname, "type": ftype, "size": fsize, "kind": fkind or "document",
                         "shown": ftype in SHOWN,
                         "url": f"/files/{item_id}/{fkey}/{quote(fname)}"} if fkey else None}

    @staticmethod
    def _me(me):
        return me if DEVICE_ID.fullmatch(me or "") else "-"           # "-" is nobody's id

    @classmethod
    def _visible(cls, me):
        me = cls._me(me)
        return [me, json.dumps(me)]

    def list(self, q="", me="", archived=False):
        """What this device sees: its pinned items first (always, however old), then the newest. The archive (items
        this device put away) is a list of its own."""
        me_ = self._me(me)
        where = f"{VISIBLE} AND COALESCE(m.archived, 0) {'> 0' if archived else '= 0'}"     # archived: when, in ms
        params = self._visible(me)
        if q:
            where += (" AND (instr(lower(i.text), lower(?)) > 0 OR instr(lower(i.device), lower(?)) > 0"
                      " OR instr(lower(COALESCE(i.file_name, '')), lower(?)) > 0)")
            params += [q, q, q]
        with self.lock:
            total, newest = self.db.execute(
                f"SELECT COUNT(*), COALESCE(MAX(i.id), 0) FROM {MARKS} WHERE {VISIBLE} AND COALESCE(m.archived, 0) = 0",
                [me_] + self._visible(me)).fetchone()
            stored = self.db.execute(f"SELECT COUNT(*) FROM {MARKS} WHERE {VISIBLE} AND COALESCE(m.archived, 0) > 0",
                                     [me_] + self._visible(me)).fetchone()[0]
            matches = self.db.execute(f"SELECT COUNT(*) FROM {MARKS} WHERE {where}", [me_] + params).fetchone()[0]
            pinned = [] if archived else self.db.execute(
                f"SELECT {COLUMNS} FROM {MARKS} WHERE {where} AND COALESCE(m.pinned, 0) > 0 ORDER BY m.pinned DESC",
                [me_] + params).fetchall()
            rest = where if archived else where + " AND COALESCE(m.pinned, 0) = 0"     # pinned ones are above
            rows = self.db.execute(f"SELECT {COLUMNS} FROM {MARKS} WHERE {rest} ORDER BY i.id DESC LIMIT ?",
                                   [me_] + params + [LIST_LIMIT]).fetchall()
            rev = self.rev
        names = self._names()
        return {"rev": rev, "now": now_ms(), "total": total, "archived": stored, "matches": matches, "newest": newest,
                "items": [self._item(r, names, me) for r in pinned + rows]}

    def get(self, item_id, me=""):
        """One item with all its text, if this device may see it."""
        with self.lock:
            r = self.db.execute(f"SELECT {COLUMNS}, i.text FROM {MARKS} WHERE i.id = ? AND {VISIBLE}",
                                [self._me(me), item_id] + self._visible(me)).fetchone()
        if not r:
            return None
        item = self._item(r[:-1], self._names(), me)
        item["text"], item["big"] = r[-1], False
        return item

    def get_file(self, item_id, key):
        with self.lock:
            r = self.db.execute("SELECT file_name, file_type, file FROM items WHERE id = ? AND file_key = ?",
                                (item_id, key)).fetchone()
        return r

    def changed(self):
        """Something was added, changed or deleted: new revision, and the waiting devices look again."""
        with self.news:
            self.changes += 1
            self.news.notify_all()

    def events(self, me, after, wait=0):
        """For the app on a device: what others shared for it since `after` (the newest id it has seen). With
        `wait` (seconds, up to 50) the answer waits until there is something, or the time is up: the phone and the
        computers keep one such request open and so hear of a share the moment it is made. A device waiting like
        that counts as online."""
        end = time.monotonic() + min(max(wait, 0), 50)
        device = me if DEVICE_ID.fullmatch(me or "") else None
        if device:
            with self.news:
                self.waiting[device] = self.waiting.get(device, 0) + 1
        try:
            while True:
                before = self.changes
                answer = self._events(me, after)
                left = end - time.monotonic()
                if answer["items"] or after is None or left <= 0 or self.closed:
                    return answer
                with self.news:
                    if self.changes == before and not self.closed:
                        self.news.wait(left)
        finally:
            if device:
                with self.news:
                    self.waiting[device] -= 1
                if device in self.seen:
                    self.seen[device]["seen"] = now_ms()

    def _events(self, me, after):
        me_ = self._me(me)
        with self.lock:
            newest = self.db.execute(f"SELECT COALESCE(MAX(i.id), 0) FROM {MARKS} WHERE {VISIBLE}",
                                     [me_] + self._visible(me)).fetchone()[0]
            rows = [] if after is None else self.db.execute(
                f"SELECT {COLUMNS} FROM {MARKS} WHERE i.id > ? AND {VISIBLE} AND COALESCE(i.sender, '') != ? "
                "ORDER BY i.id DESC LIMIT 20", [me_, after] + self._visible(me) + [me_]).fetchall()
        names = self._names()
        items = [self._item(r, names, me) for r in rows]
        for item in items:
            item["text"] = item["text"][:400]
        return {"newest": newest, "items": items}

    def _used(self):
        r = self.db.execute("SELECT COALESCE(SUM(length(CAST(text AS BLOB))), 0), COALESCE(SUM(file_size), 0) "
                            "FROM items").fetchone()
        return r[0] + r[1]

    def add(self, text, device, sender="", to=None, file=None):
        """Keeps a new item. The same text again (no file), right after itself, is not kept twice. Refused when
        the history would pass 2 GB."""
        sender = sender if DEVICE_ID.fullmatch(sender or "") else None
        recipients = json.dumps(sorted(set(to))) if to else None
        with self.lock:
            if not file:
                same = self.db.execute(
                    "SELECT id FROM items WHERE id = (SELECT MAX(id) FROM items) AND text = ? AND file_key IS NULL "
                    "AND recipients IS ? AND sender IS ?", (text, recipients, sender)).fetchone()
                if same:
                    return self._summary(same[0]), True
            fname, ftype, fkind, fdata = file if file else (None, None, None, None)
            if self._used() + len(text.encode("utf-8")) + (len(fdata) if file else 0) > HISTORY_MAX:
                raise HistoryFull()
            cur = self.db.execute(
                "INSERT INTO items (at, device, text, sender, recipients, file_name, file_type, file_size, file_key, "
                "file, file_kind) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (now_ms(), device, text, sender, recipients, fname, ftype, len(fdata) if file else None,
                 secrets.token_urlsafe(12) if file else None, fdata, fkind))
            self.db.commit()
            self.changed()
            return self._summary(cur.lastrowid), False

    def _summary(self, item_id):
        r = self.db.execute("SELECT id, at, device, length(text), length(text) - length(replace(text, char(10), '')) "
                            "+ 1, file_name, file_size FROM items WHERE id = ?", (item_id,)).fetchone()
        return {"id": r[0], "at": r[1], "device": r[2], "size": r[3], "lines": r[4] if r[3] else 0,
                "file": {"name": r[5], "size": r[6]} if r[5] else None}

    def _owner(self, item_id, me):
        """(sender, visible) of an item for this device; None when it is gone or not for it."""
        return self.db.execute(f"SELECT i.sender, i.file_key FROM {MARKS} WHERE i.id = ? AND {VISIBLE}",
                               [self._me(me), item_id] + self._visible(me)).fetchone()

    def edit(self, item_id, me, text):
        """Its sender changes the text, in place; the item says when."""
        with self.lock:
            found = self._owner(item_id, me)
            if not found:
                return None
            if found[0] != self._me(me):
                raise NotYours()
            if not text.strip() and not found[1]:
                raise ValueError("empty")
            self.db.execute("UPDATE items SET text = ?, edited_at = ? WHERE id = ?", (text, now_ms(), item_id))
            self.db.commit()
            self.changed()
            return self._summary(item_id)

    def delete(self, item_id, me=""):
        """Only its sender deletes it, for everyone (items from 1.0, with no sender, anyone who sees them)."""
        with self.lock:
            found = self._owner(item_id, me)
            if not found:
                return False
            if found[0] and found[0] != self._me(me):
                raise NotYours()
            self.db.execute("DELETE FROM items WHERE id = ?", (item_id,))
            self.db.execute("DELETE FROM marks WHERE item = ?", (item_id,))
            self.db.commit()
            self.changed()
            return True

    def mark(self, item_id, me, pinned=None, archived=None):
        """This device pins or archives an item for itself. Archiving takes the pin off."""
        me_ = self._me(me)
        if me_ == "-":
            raise NotYours()
        with self.lock:
            if not self._owner(item_id, me):
                return False
            row = self.db.execute("SELECT pinned, archived FROM marks WHERE device = ? AND item = ?",
                                  (me_, item_id)).fetchone() or (0, 0)
            pin, arch = row
            if pinned is not None:
                pin = now_ms() if pinned else 0
            if archived is not None:
                arch = now_ms() if archived else 0
                if archived:
                    pin = 0
            self.db.execute("INSERT INTO marks (device, item, pinned, archived) VALUES (?, ?, ?, ?) ON CONFLICT"
                            "(device, item) DO UPDATE SET pinned = excluded.pinned, archived = excluded.archived",
                            (me_, item_id, pin, arch))
            self.db.commit()
            self.changed()
            return True

    def storage(self):
        """What the history holds, by kind, against its 2 GB."""
        with self.lock:
            text = self.db.execute("SELECT COALESCE(SUM(length(CAST(text AS BLOB))), 0), COUNT(*) FROM items "
                                   "WHERE length(text) > 0").fetchone()
            files = {k: (b, n) for k, b, n in self.db.execute(
                "SELECT file_kind, COALESCE(SUM(file_size), 0), COUNT(*) FROM items WHERE file_key IS NOT NULL "
                "GROUP BY file_kind")}
        sizes = {"text": text}
        sizes.update(files)
        kinds = [{"kind": k, "label": label, "bytes": sizes.get(k, (0, 0))[0], "count": sizes.get(k, (0, 0))[1]}
                 for k, label in KINDS]
        disk = sum(p.stat().st_size for p in self.path.parent.glob(self.path.name + "*") if p.is_file())
        return {"limit": HISTORY_MAX, "used": sum(k["bytes"] for k in kinds), "kinds": kinds, "on_disk": disk,
                "folder": str(self.path.parent)}

    def close(self):
        with self.news:
            self.closed = True
            self.news.notify_all()
        with self.lock:
            self.db.close()


# ---------------------------------------------------------------- who may ask

def client_ok(addr, neighbour_nets):
    """Only this computer and computers on its own network: private, link-local, or the same IPv6 /64."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_loopback or ip.is_private or ip.is_link_local:
        return True
    return ip.version == 6 and any(ip in net for net in neighbour_nets)


def host_ok(host):
    """The page must be opened by IP address or by a local computer name, never by an outside domain name
    (which is how a web page elsewhere could otherwise trick a browser into reading the history)."""
    host = host.strip().lower()
    if host.startswith("["):
        name = host[1:host.find("]")] if "]" in host else ""
    else:
        name = host.split(":")[0]
    if not name:
        return False
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    if name == "localhost" or "." not in name:
        return True
    return name.endswith((".local", ".lan", ".home", ".internal", ".home.arpa", ".localdomain"))


_names = {}


def guess_name(ip):
    """This computer's name as the network knows it, to fill in a new page's "This computer" name."""
    if ip not in _names:
        try:
            name = socket.gethostbyaddr(ip)[0].split(".")[0]
        except OSError:
            name = ""
        _names[ip] = name
    return _names[ip]


# ---------------------------------------------------------------- the web side

def guarded(handler):
    def run(self):
        try:
            handler(self)
        except (ConnectionError, TimeoutError):
            self.close_connection = True
        except Exception:
            traceback.print_exc()
            try:
                self.fail(500, "Something went wrong on the server.")
            except Exception:
                self.close_connection = True
    return run


class Handler(BaseHTTPRequestHandler):
    server_version = "TextShare/" + VERSION
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 120

    def log_message(self, *args):    # the window shows what is shared and deleted, not every request
        pass

    def reply(self, code, body=b"", ctype="application/json; charset=utf-8", headers=()):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if not any(name.lower() == "cache-control" for name, _ in headers):
            self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def fail(self, code, message, api=True):
        if api:
            self.reply(code, {"error": message})
        else:
            self.reply(code, message + "\n", "text/plain; charset=utf-8")

    def gate(self, api):
        """True when the request may go on; otherwise it has been answered."""
        problem = None
        if not client_ok(self.client_address[0], self.server.neighbour_nets()):
            problem = "Instant Share only answers computers on its own network."
        elif not host_ok(self.headers.get("Host", "")):
            problem = "Open Instant Share by the server's IP address or computer name, e.g. http://192.168.1.20:8765"
        elif api and self.headers.get("X-TextShare") != "1":
            problem = "This address is for the Instant Share page only."
        if problem:
            self.close_connection = True
            self.fail(403, problem, api)
        return problem is None

    def who(self):
        """The device asking (its id, made up by itself), noted as online. "" for one that does not say."""
        device_id = self.headers.get("X-TextShare-Device", "")
        if not DEVICE_ID.fullmatch(device_id):
            return ""
        name = unquote(self.headers.get("X-TextShare-Name", "")).strip()[:60] or "a device"
        kind = "app" if self.headers.get("X-TextShare-Kind") == "app" else "browser"
        self.server.store.touch(device_id, name, kind)
        return device_id

    @guarded
    def do_GET(self):
        url = urlsplit(self.path)
        path, api = url.path, url.path.startswith("/api/")
        if not self.gate(api):
            return
        store = self.server.store
        qs = parse_qs(url.query)
        if path == "/":
            self.reply(200, PAGE, "text/html; charset=utf-8", PAGE_HEADERS)
        elif path == "/download":     # this very program, to run Instant Share on another computer
            ctype = "application/vnd.microsoft.portable-executable" if FROZEN else "text/x-python; charset=utf-8"
            self.reply(200, APP_FILE.read_bytes(), ctype,
                       [("Content-Disposition", f'attachment; filename="{APP_FILE.name}"')])
        elif path == "/download/android":   # the phone app, for phones on the same network
            apk = android_app()
            if not apk:
                return self.fail(404, "The Android app is not included in this copy of Instant Share.", False)
            self.reply(200, apk.read_bytes(), "application/vnd.android.package-archive",
                       [("Content-Disposition", 'attachment; filename="Instant-Share.apk"')])
        elif m := re.fullmatch(r"/files/(\d+)/([A-Za-z0-9_-]+)(?:/[^/]*)?", path):
            found = store.get_file(int(m[1]), m[2])
            if not found:
                return self.fail(404, "That file is no longer in the history.", False)
            name, ctype, data = found
            shown = ctype in SHOWN and not qs.get("download")
            headers = [("Content-Disposition", f"{'inline' if shown else 'attachment'}; filename*=UTF-8''{quote(name)}"),
                       ("Cache-Control", "private, max-age=31536000, immutable")]
            if not shown:                     # a document never runs as a page here, whatever is inside it
                headers.append(("Content-Security-Policy", "sandbox; default-src 'none'"))
            self.reply(200, data, ctype if shown else "application/octet-stream", headers)
        elif path == "/api/hello":
            me = self.who()
            ip = ipaddress.ip_address(self.client_address[0])
            if ip.version == 6 and ip.ipv4_mapped:
                ip = ip.ipv4_mapped
            ip = str(ip).split("%")[0]
            name = socket.gethostname() if ipaddress.ip_address(ip).is_loopback else guess_name(ip)
            port = self.server.server_port
            addresses = [f"http://{h}:{port}/" for h in [socket.gethostname(), *lan_addresses()]]
            self.reply(200, {"server": socket.gethostname(), "version": VERSION, "you": ip, "name": name,
                             "addresses": addresses, "id": me, "android": android_app() is not None})
        elif path == "/api/items":
            me = self.who()
            q = qs.get("q", [""])[0].strip()[:200]
            rev = qs.get("rev", [""])[0]
            archived = qs.get("archived", [""])[0] == "1"
            if rev and rev == store.rev:
                self.reply(200, {"same": True, "rev": rev, "now": now_ms()})
            else:
                self.reply(200, store.list(q, me, archived))
        elif m := re.fullmatch(r"/api/items/(\d+)", path):
            item = store.get(int(m[1]), self.who())
            if item:
                self.reply(200, item)
            else:
                self.fail(404, "That is no longer in the history.")
        elif path == "/api/devices":
            self.reply(200, store.devices(self.who()))
        elif path == "/api/storage":
            self.reply(200, store.storage())
        elif path == "/api/events":
            me = self.who()
            if not me:
                return self.fail(400, "Say which device is asking.")
            after, wait = qs.get("after", [""])[0], qs.get("wait", ["0"])[0]
            self.reply(200, store.events(me, int(after) if after.isdigit() else None,
                                         int(wait) if wait.isdigit() else 0))
        else:
            self.fail(404, "Not found.", api)

    def body(self):
        """The request's JSON, or None (then it has been answered)."""
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self.close_connection = True
            self.fail(411, "Length required.")
            return None
        if length > MAX_BODY:
            self.close_connection = True
            self.fail(413, f"That is too big to share (more than {MAX_BODY // 1048576} MB).")
            return None
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            if isinstance(data, dict):
                return data
        except ValueError:
            pass
        self.fail(400, "That was not a proper request.")
        return None

    @guarded
    def do_POST(self):
        if not self.gate(True):
            return
        path = urlsplit(self.path).path
        if m := re.fullmatch(r"/api/items/(\d+)/mark", path):
            return self.mark(int(m[1]))
        if path != "/api/items":
            self.close_connection = True
            return self.fail(404, "Not found.")
        me = self.who()
        data = self.body()
        if data is None:
            return
        text, device, to, file = data.get("text") or "", data.get("device"), data.get("to"), data.get("file")
        if not isinstance(text, str):
            return self.fail(400, "That was not a proper share.")
        upload = None
        if file:
            try:
                raw = base64.b64decode(str(file["data"]), validate=True)
            except (TypeError, KeyError, ValueError, binascii.Error):
                return self.fail(400, "The file did not arrive whole. Please try again.")
            if len(raw) > FILE_MAX:
                return self.fail(413, f"That file is {len(raw) / 1048576:.2f} MB, over the {FILE_MAX // 1048576} MB "
                                      "that one file can be.")
            checked = check_file(file.get("name"), raw)
            if not checked:
                return self.fail(415, "That kind of file can't be shared. Photos, PDFs, Office documents, text, "
                                      "audio, short videos and ZIP files can.")
            upload = (*checked, raw)
        if not text.strip() and not upload:
            return self.fail(400, "There is no text to share.")
        text = text.encode("utf-8", "replace").decode("utf-8")
        device = (device if isinstance(device, str) else "").encode("utf-8", "replace").decode("utf-8")
        device = device.strip()[:60] or self.client_address[0]
        to = [d for d in to if isinstance(d, str) and DEVICE_ID.fullmatch(d)][:50] if isinstance(to, list) else []
        if to and me:
            to = [d for d in to if d != me]
        try:
            item, duplicate = self.server.store.add(text, device, me, to or None, upload)
        except HistoryFull:
            return self.fail(507, f"The history on {socket.gethostname()} is full ({HISTORY_MAX // 1024 ** 3} GB). "
                                  "Delete some old files there to make room.")
        if not duplicate:
            what = []
            if item["size"]:
                what.append(f"{item['lines']} line{'' if item['lines'] == 1 else 's'} ({item['size']:,} characters)")
            if upload:
                what.append(f"a file of kind {upload[2]} ({len(upload[3]) // 1024} KB)")
            log(f"{device} shared {' and '.join(what)}" + (f", for {len(to)} device{'s' if len(to) > 1 else ''}"
                                                           if to else ""))
        self.reply(200 if duplicate else 201, {"item": item, "duplicate": duplicate})

    def mark(self, item_id):
        """Pin or archive, for this device only."""
        me = self.who()
        data = self.body()
        if data is None:
            return
        pinned, archived = data.get("pinned"), data.get("archived")
        try:
            done = self.server.store.mark(item_id, me, None if pinned is None else bool(pinned),
                                          None if archived is None else bool(archived))
        except NotYours:
            return self.fail(400, "Say which device is asking.")
        if done:
            self.reply(200, {"ok": True})
        else:
            self.fail(404, "That is no longer in the history.")

    @guarded
    def do_PUT(self):
        """Its sender changes an item's text, in place."""
        if not self.gate(True):
            return
        m = re.fullmatch(r"/api/items/(\d+)", urlsplit(self.path).path)
        if not m:
            self.close_connection = True
            return self.fail(404, "Not found.")
        me = self.who()
        data = self.body()
        if data is None:
            return
        text = data.get("text")
        if not isinstance(text, str) or len(text.encode("utf-8")) > 20 * 1024 * 1024:
            return self.fail(400, "That was not a proper change.")
        try:
            item = self.server.store.edit(int(m[1]), me, text.encode("utf-8", "replace").decode("utf-8"))
        except NotYours:
            return self.fail(403, "Only the device that shared it can change it.")
        except ValueError:
            return self.fail(400, "There is no text left. Delete it instead.")
        if item:
            log(f"item {m[1]} edited")
            self.reply(200, {"item": item})
        else:
            self.fail(404, "That is no longer in the history.")

    @guarded
    def do_DELETE(self):
        if not self.gate(True):
            return
        m = re.fullmatch(r"/api/items/(\d+)", urlsplit(self.path).path)
        if not m:
            return self.fail(404, "Not found.")
        try:
            gone = self.server.store.delete(int(m[1]), self.who())
        except NotYours:
            return self.fail(403, "Only the device that shared it can delete it. You can archive it instead.")
        if gone:
            log(f"item {m[1]} deleted")
            self.reply(200, {"ok": True})
        else:
            self.fail(404, "That is no longer in the history.")

class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"   # Windows "reuse" lets a second copy share the port; elsewhere it is safe

    def __init__(self, address, family, store):
        self.address_family = family
        self.store = store
        self._nets, self._nets_at = [], -1e9
        super().__init__(address, Handler)

    def server_bind(self):
        if self.address_family == socket.AF_INET6:
            try:
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)    # IPv4 as well
            except (AttributeError, OSError):
                pass
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = socket.gethostname(), self.server_address[1]

    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            super().handle_error(request, client_address)

    def neighbour_nets(self):
        """The public IPv6 /64 networks this computer is on (looked up once a minute): a computer on the same
        one is on the same network, even though its address is not a private one."""
        if time.monotonic() - self._nets_at > 60:
            nets = set()
            try:
                for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET6):
                    ip = ipaddress.ip_address(info[4][0].split("%")[0])
                    if ip.is_global:
                        nets.add(ipaddress.ip_network(f"{ip}/64", strict=False))
            except (OSError, ValueError):
                pass
            self._nets, self._nets_at = list(nets), time.monotonic()
        return self._nets


def make_server(port, store):
    for family, host in ((socket.AF_INET6, "::"), (socket.AF_INET, "0.0.0.0")):
        try:
            return Server((host, port), family, store)
        except OSError:
            continue
    return None


DISCOVERY_PORT = 8765            # UDP: the apps on the other computers ask here where Instant Share is
ASK = b"TEXTSHARE?"


class Beacon(threading.Thread):
    """Answers the Instant Share apps that look for this computer on the network, with its name and port."""

    def __init__(self, port):
        super().__init__(daemon=True, name="beacon")
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        self.sock.bind(("", DISCOVERY_PORT))        # OSError when another program has it: then nobody is answered

    def run(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(512)
            except ConnectionResetError:            # Windows reports an earlier reply that was not received
                continue
            except OSError:                         # closed: Instant Share is stopping
                return
            if data.startswith(ASK) and client_ok(addr[0], []):
                reply = {"app": "textshare", "name": socket.gethostname(), "port": self.port, "version": VERSION}
                try:
                    self.sock.sendto(json.dumps(reply).encode("utf-8"), addr)
                except OSError:
                    pass

    def close(self):
        self.sock.close()


def discover(timeout=1.5):
    """The computers on this network running Instant Share: [{name, address, port, version}], by name."""
    found = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.bind(("", 0))
        targets = {"255.255.255.255", "127.0.0.1"} | {ip.rsplit(".", 1)[0] + ".255" for ip in lan_addresses()}
        for target in targets:
            try:
                s.sendto(ASK, (target, DISCOVERY_PORT))
            except OSError:
                pass
        end = time.monotonic() + timeout
        while (left := end - time.monotonic()) > 0:
            s.settimeout(left)
            try:
                data, addr = s.recvfrom(1024)
                info = json.loads(data)
                name, port = str(info["name"])[:63], int(info["port"])
            except ConnectionResetError:
                continue
            except (OSError, ValueError, KeyError, TypeError):    # also the time running out
                if time.monotonic() >= end:
                    break
                continue
            if info.get("app") == "textshare" and name:
                found.setdefault(name.lower(), {"name": name, "address": addr[0], "port": port,
                                                "version": str(info.get("version", ""))})
    return sorted(found.values(), key=lambda f: f["name"].lower())


def lan_addresses():
    """This computer's IPv4 addresses on its networks, the main one first."""
    found = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.254.254.254", 1))     # sends nothing: only asks which address would be used
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        found += [info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    except OSError:
        pass
    out = []
    for a in found:
        ip = ipaddress.ip_address(a)
        if not (ip.is_loopback or ip.is_link_local) and a not in out:
            out.append(a)
    return out


def running_here(port):
    """True when Instant Share already answers on this port of this computer."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))      # never through a proxy
    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/hello", headers={"X-TextShare": "1"})
    try:
        with opener.open(request, timeout=3) as r:
            return "version" in json.loads(r.read())
    except (OSError, ValueError):
        return False


def set_title(text):
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW(text)
        except Exception:
            pass


def main(argv=None):
    ap = argparse.ArgumentParser(prog=APP_FILE.name, description=f"Instant Share {VERSION} as a plain server: pass "
                                 "text between the computers on your network. Starts it and opens it in the browser.")
    ap.add_argument("--no-open", action="store_true", help="start it without opening the browser")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port to listen on (default {DEFAULT_PORT})")
    ap.add_argument("--data", type=Path, default=default_data_dir(), help="folder for the history (default %(default)s)")
    ap.add_argument("--open", action="store_true", help=argparse.SUPPRESS)      # 1.0 spelling; opening is the default
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")    # Arabic names etc. in a window or log that cannot show them
        except (AttributeError, ValueError):
            pass

    url = f"http://localhost:{args.port}/"
    store = Store(args.data / "history.db")
    server = make_server(args.port, store)
    if server is None:
        store.close()
        if running_here(args.port):
            print(f"Instant Share is already running on this computer: {url}")
            if not args.no_open:
                webbrowser.open(url)
            return 0
        print(f"Port {args.port} is used by another program. Start Instant Share on another port, e.g.  --port {args.port + 1}")
        return 1

    try:
        beacon = Beacon(args.port)
        beacon.start()
    except OSError:
        beacon = None               # another program has the discovery port: apps must be given the address
    name = socket.gethostname()
    set_title(f"Instant Share {VERSION}  -  http://{name}:{args.port}/")
    print(f"\n  Instant Share {VERSION} is running on {name}.\n")
    print(f"  On this computer:      {url}")
    print(f"  From other computers:  http://{name}:{args.port}/")
    for ip in lan_addresses():
        print(f"                     or  http://{ip}:{args.port}/")
    print(f"\n  History: {store.path}")
    print("  Leave this window open (minimised is fine). Close it, or press Ctrl+C, to stop.\n", flush=True)
    if not args.no_open:
        threading.Timer(0.4, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("  Stopped.")
    finally:
        if beacon:
            beacon.close()
        server.server_close()
        store.close()
    return 0


def run():
    try:
        code = main()
    except SystemExit as e:          # --help, or a mistyped option
        code = e.code
    except Exception:
        traceback.print_exc()
        code = 1
    if FROZEN and code not in (0, None):
        try:                         # double-clicked: keep the message on screen instead of closing the window
            input("\n  Press Enter to close this window.")
        except (EOFError, OSError, RuntimeError):
            pass
    return code


# ---------------------------------------------------------------- the page

PAGE_HEADERS = [
    ("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                                "connect-src 'self'; img-src 'self' data:; media-src 'self'; base-uri 'none'; form-action 'none'; "
                                "frame-ancestors 'none'"),
    ("X-Frame-Options", "DENY"),
]

PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Instant Share</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='8' fill='%235b2c83'/><path d='M7 12h17l-4-4M25 20H8l4 4' fill='none' stroke='%23f0c33c' stroke-width='2.6' stroke-linecap='round' stroke-linejoin='round'/></svg>">
<script>
/* before anything is drawn: the appearance last used (the app passes its own), so it never flashes the other one */
(function () {
  var t = new URLSearchParams(location.search).get("theme");
  if (t !== "light" && t !== "dark") { try { t = localStorage.getItem("textshare.theme"); } catch (e) {} }
  document.documentElement.dataset.theme = t === "light" ? "light" : "dark";
})();
</script>
<style>
/* Dark: the colours of VS Code's Dark 2026 theme, as in Multi Screens. Light: Multi Screens' light appearance. */
:root {
  --bg: #121314; --surface: #191a1b; --surface-2: #202122; --surface-3: #2a2b2c;
  --line: #2a2b2c; --line-strong: #333536; --text: #cccccc; --dim: #8c8c8c;
  --accent: #48a0c7; --accent-strong: #297aa0; --accent-hover: #2b7da3;
  --accent-soft: rgba(57, 148, 188, .16); --accent-line: rgba(57, 148, 188, .55);
  --danger: #f48771; --danger-fill: #c74e39; --ok: #73c991;
  --shadow: 0 12px 36px rgba(0, 0, 0, .55);
  --mono: "Cascadia Mono", "Cascadia Code", Consolas, ui-monospace, monospace;
  --sans: "Segoe UI Variable Text", "Segoe UI", system-ui, -apple-system, sans-serif;
  color-scheme: dark;
}
:root[data-theme="light"] {
  --bg: #eef0f4; --surface: #ffffff; --surface-2: #f4f5f8; --surface-3: #e7e9ef;
  --line: #dfe2e9; --line-strong: #c9cdd8; --text: #171a21; --dim: #667085;
  --accent: #3f63f0; --accent-strong: #4a6cf5; --accent-hover: #3f63f0;
  --accent-soft: rgba(63, 99, 240, .1); --accent-line: rgba(63, 99, 240, .4);
  --danger: #e5484d; --danger-fill: #e5484d; --ok: #1a7f37;
  --shadow: 0 12px 30px rgba(20, 28, 50, .2);
  color-scheme: light;
}
* { box-sizing: border-box }
[hidden] { display: none !important }
html, body { height: 100% }
body { margin: 0; display: flex; flex-direction: column; background: var(--bg); color: var(--text);
  font: 13px/1.4 var(--sans); -webkit-font-smoothing: antialiased }
button, input, textarea { font: inherit; color: inherit }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px }
input[type="checkbox"] { accent-color: var(--accent-strong) }
::-webkit-scrollbar { width: 10px; height: 10px }
::-webkit-scrollbar-thumb { background: var(--surface-3); border-radius: 999px; border: 2px solid var(--bg) }
::-webkit-scrollbar-corner { background: transparent }
svg.icon { display: block; width: 18px; height: 18px; flex: none }

.top { display: flex; align-items: center; gap: 14px; padding: 8px 10px 8px 14px; background: var(--surface); border-bottom: 1px solid var(--line) }
:root[data-theme="light"] .top { box-shadow: 0 6px 18px rgba(20, 28, 50, .08) }
.brand { display: flex; align-items: center; gap: 9px; font-weight: 600; font-size: 14px; white-space: nowrap }
.status { display: flex; align-items: center; gap: 8px; color: var(--dim); min-width: 0 }
.status span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap }
.status i { flex: none; width: 8px; height: 8px; border-radius: 50%; background: var(--dim) }
.status.on i { background: var(--ok) }
.status.off i { background: var(--danger) }
.status.off span { color: var(--danger) }
.me, .icon-btn { display: inline-flex; align-items: center; gap: 6px; height: 32px; padding: 0 10px; background: transparent; border: 1px solid transparent; border-radius: 8px; color: var(--dim); cursor: pointer; white-space: nowrap; transition: background .12s, color .12s }
.me { margin-inline-start: auto }
.me:hover, .icon-btn:hover { background: var(--surface-3); color: var(--text) }
.me b { color: var(--text); font-weight: 600 }
.icon-btn { width: 32px; padding: 0; justify-content: center }

main { flex: 1; min-height: 0; display: grid; grid-template-columns: minmax(0, 1.15fr) minmax(0, 1fr); gap: 16px; padding: 14px }
.pane { display: flex; flex-direction: column; min-height: 0; gap: 8px }
.bar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; min-height: 32px }
.bar h2 { margin: 0; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .06em; color: var(--dim) }
.grow { flex: 1 }
.muted { color: var(--dim) }
.chk { display: flex; align-items: center; gap: 6px; color: var(--dim); cursor: pointer; user-select: none }
#editor { flex: 1; min-height: 220px; width: 100%; resize: none; padding: 12px 14px; background: var(--surface); border: 1px solid var(--line); border-radius: 10px; font: 13.5px/1.6 var(--mono); tab-size: 4; outline: none; transition: border-color .12s, box-shadow .12s }
#editor:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft) }
#editor[wrap="off"] { white-space: pre; overflow-x: auto }
#editor::placeholder, #search::placeholder { color: var(--dim); opacity: .8 }
#search { width: min(240px, 100%); height: 32px; padding: 0 10px; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; outline: none; transition: border-color .12s, box-shadow .12s }
#search:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft) }

.btn { display: inline-flex; align-items: center; justify-content: center; gap: 6px; height: 32px; padding: 0 12px; background: var(--surface-2); border: 1px solid var(--line); border-radius: 8px; font-weight: 500; white-space: nowrap; cursor: pointer; transition: background .12s, border-color .12s, color .12s, transform .06s }
.btn:hover { background: var(--surface-3); border-color: var(--line-strong) }
.btn:active { transform: translateY(1px) }
.btn:disabled { opacity: .55; cursor: default }
.btn.primary { padding: 0 16px; color: #fff; background: var(--accent-strong); border-color: transparent; font-weight: 600 }
.btn.primary:hover { background: var(--accent-hover); border-color: transparent }
.btn.danger, .btn.danger:hover { color: #fff; background: var(--danger-fill); border-color: transparent; font-weight: 600 }
.btn.done { color: var(--ok); border-color: var(--ok) }
.btn.sm { height: 26px; padding: 0 9px; font-size: 12px; border-radius: 6px }
kbd { font: 11px var(--mono); opacity: .75 }

.scroll { flex: 1; min-height: 0; overflow-y: auto; padding-inline-end: 4px }
#list { display: flex; flex-direction: column; gap: 8px }
.item { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; overflow: hidden; transition: border-color .15s }
.item:hover { border-color: var(--line-strong) }
.item.fresh { animation: fresh 2.6s ease-out }
@keyframes fresh { 0%, 35% { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft) } }
.head { display: flex; align-items: center; gap: 10px; padding: 7px 8px 0 12px; font-size: 12px; color: var(--dim) }
.who { color: var(--text); font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 14em; min-width: 3em }
.head time { white-space: nowrap }
.meta { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0 }
.acts { margin-inline-start: auto; display: flex; gap: 4px; flex: none }
.item pre { margin: 0; padding: 6px 12px 11px; font: 12.5px/1.55 var(--mono); white-space: pre-wrap; overflow-wrap: anywhere; max-height: calc(1.55em * 8 + 17px); overflow: hidden }
.item.open pre { max-height: none }
.more { display: block; width: 100%; padding: 5px; background: var(--surface-2); border: 0; border-top: 1px solid var(--line); color: var(--dim); font-size: 12px; cursor: pointer }
.more:hover { color: var(--text); background: var(--surface-3) }
.empty { color: var(--dim); text-align: center; padding: 40px 20px; border: 1px dashed var(--line-strong); border-radius: 10px; margin: 0 }
.note { color: var(--dim); font-size: 12px; text-align: center; margin: 14px 0 4px }

footer { display: flex; gap: 16px; flex-wrap: wrap; align-items: center; padding: 7px 14px; background: var(--surface); border-top: 1px solid var(--line); color: var(--dim); font-size: 12px }
footer a { color: var(--dim) }
footer a:hover { color: var(--accent) }
#toast { position: fixed; left: 50%; bottom: 24px; transform: translate(-50%, 8px); opacity: 0; transition: opacity .18s, transform .18s; max-width: min(560px, 90vw); padding: 10px 16px; background: var(--surface-3); border: 1px solid var(--line-strong); border-radius: 12px; box-shadow: var(--shadow); pointer-events: none; text-align: center }
#toast.show { opacity: 1; transform: translate(-50%, 0) }
#toast.bad { border-color: var(--danger) }

.btn.targeted { color: var(--accent); border-color: var(--accent-line) }
a.btn { color: inherit; text-decoration: none }
.btn .icon { width: 16px; height: 16px }
.btn.sm .icon { width: 14px; height: 14px }
.tools { display: flex; align-items: center; gap: 2px; margin-inline-start: auto }
.tools .me { margin-inline-start: 0 }
.icon-btn.on { color: var(--accent); background: var(--accent-soft) }
.group { display: flex; align-items: center; gap: 8px }
.break { display: none }

.editing { display: flex; align-items: center; gap: 10px; padding: 6px 6px 6px 12px; background: var(--accent-soft); border: 1px solid var(--accent-line); border-radius: 10px }
.editing .icon { color: var(--accent) }
.editing b { font-weight: 600 }

.attach { display: flex; align-items: center; gap: 10px; padding: 6px; background: var(--surface); border: 1px solid var(--line); border-radius: 10px }
.attach img { width: 44px; height: 44px; object-fit: cover; border-radius: 6px; border: 1px solid var(--line); flex: none }
.attach .icon-btn { margin-inline-start: auto; flex: none }
.att-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0 }
.badge { display: inline-grid; place-items: center; width: 40px; height: 40px; flex: none; border-radius: 6px; color: #fff; font-size: 10px; font-weight: 700; letter-spacing: .03em; background: #6b7280 }
.badge.b-pdf { background: #c74e39 } .badge.b-doc { background: #2b579a } .badge.b-xls { background: #217346 }
.badge.b-ppt { background: #c43e1c } .badge.b-zip { background: #8a6d3b } .badge.b-media { background: #6250d6 }
#compose.drop #editor { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft) }

.to-wrap { position: relative }
.pop { position: absolute; right: 0; bottom: calc(100% + 6px); z-index: 10; width: 310px; max-height: 380px; overflow-y: auto; padding: 6px; background: var(--surface); border: 1px solid var(--line-strong); border-radius: 10px; box-shadow: var(--shadow); animation: pop-in .14s ease-out }
@keyframes pop-in { from { opacity: 0; transform: translateY(4px) scale(.985) } }
.pop-head { padding: 6px 8px 8px; font-weight: 600 }
.pop-row { display: flex; align-items: center; gap: 8px; padding: 6px 8px; border-radius: 6px; cursor: pointer }
.pop-row:hover { background: var(--surface-3) }
.pop-row .nm { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0 }
.pop-row small { margin-inline-start: auto; padding-inline-start: 8px; color: var(--dim); font-size: 11px; white-space: nowrap }
.pop-row .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--dim); opacity: .6; flex: none }
.pop-row .dot.on { background: var(--ok); opacity: 1 }
.pop-note { margin: 6px 8px 4px; color: var(--dim); font-size: 11px }

.row { position: relative; border-radius: 10px }
.row::before { content: attr(data-swipe); position: absolute; inset: 0; display: flex; align-items: center; justify-content: flex-end; padding-inline-end: 20px; border-radius: 10px; background: var(--accent-strong); color: #fff; font-weight: 600; opacity: 0; transition: opacity .15s }
.row.swiping::before { opacity: 1 }
.item { position: relative; touch-action: pan-y }
.item.back { transition: transform .2s ease-out }
.item.gone { transform: translateX(-110%) !important; opacity: 0; transition: transform .22s ease-in, opacity .22s }
.group-label { margin: 4px 2px 0; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .06em; color: var(--dim) }
.pinned-mark { display: inline-flex; color: var(--accent) }
.pinned-mark .icon { width: 13px; height: 13px }
.edited { white-space: nowrap; font-style: italic }
.to { color: var(--accent); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; max-width: 16em }
.btn.more-btn { width: 26px; padding: 0; font-size: 16px; line-height: 1 }

.menu { position: fixed; z-index: 30; min-width: 190px; padding: 4px; background: var(--surface); border: 1px solid var(--line-strong); border-radius: 10px; box-shadow: var(--shadow); animation: pop-in .12s ease-out }
.menu button { display: flex; align-items: center; gap: 10px; width: 100%; height: 34px; padding: 0 10px; background: none; border: 0; border-radius: 6px; color: var(--text); cursor: pointer; text-align: left }
.menu button:hover, .menu button:focus-visible { background: var(--surface-3) }
.menu button.danger { color: var(--danger) }
.menu .icon { width: 16px; height: 16px; color: var(--dim) }
.menu button.danger .icon { color: var(--danger) }

.file { padding: 8px 12px 4px }
.file + pre { padding-top: 4px }
.file img.thumb { display: block; max-width: 100%; max-height: 200px; border-radius: 8px; border: 1px solid var(--line); background: var(--surface-2); cursor: zoom-in }
.file audio { display: block; width: 100%; max-width: 440px; height: 38px; margin-top: 6px }
.file video { display: block; max-width: 100%; max-height: 260px; border-radius: 8px; background: #000 }
.file .chip { display: inline-flex; align-items: center; gap: 10px; max-width: 100%; padding: 5px 12px 5px 5px; color: var(--text); text-decoration: none; background: var(--surface-2); border: 1px solid var(--line); border-radius: 8px; cursor: pointer; font: inherit }
.file .chip:hover { border-color: var(--accent) }
.file .chip .badge { width: 34px; height: 34px; font-size: 9px }
.file .chip .nm { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0 }
.file .chip small { color: var(--dim); white-space: nowrap }
.file:not([hidden]) ~ .more { margin-top: 6px }
.lightbox { position: fixed; inset: 0; z-index: 50; display: grid; place-items: center; background: rgba(0, 0, 0, .82); cursor: zoom-out }
.lightbox img { max-width: 94vw; max-height: 92vh; border-radius: 6px; box-shadow: 0 20px 60px rgba(0, 0, 0, .6) }

.sheet-back { position: fixed; inset: 0; z-index: 40; display: flex; justify-content: flex-end; background: rgba(0, 0, 0, .45) }
:root[data-theme="light"] .sheet-back { background: rgba(20, 28, 50, .22) }
.sheet { width: min(460px, 100%); height: 100%; overflow-y: auto; background: var(--bg); border-left: 1px solid var(--line-strong); box-shadow: var(--shadow); animation: sheet-in .18s ease-out }
@keyframes sheet-in { from { transform: translateX(24px); opacity: 0 } }
.sheet-head { position: sticky; top: 0; z-index: 1; display: flex; align-items: center; gap: 8px; padding: 8px 8px 8px 16px; background: var(--surface); border-bottom: 1px solid var(--line) }
.sheet-head h2 { flex: 1; margin: 0; font-size: 15px; font-weight: 600 }
.sec { margin: 12px; padding: 12px 14px; background: var(--surface); border: 1px solid var(--line); border-radius: 10px }
.sec h3 { margin: 0 0 6px; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .06em; color: var(--dim) }
.line { display: flex; align-items: center; gap: 10px; min-height: 40px; padding: 4px 0 }
.line + .line { border-top: 1px solid var(--line) }
.line .label { flex: 1; min-width: 0 }
.sub { display: block; color: var(--dim); font-size: 12px; overflow-wrap: anywhere }
.sec input[type="text"] { flex: 1; min-width: 0; height: 32px; padding: 0 10px; background: var(--bg); border: 1px solid var(--line); border-radius: 8px; outline: none; transition: border-color .12s, box-shadow .12s }
.sec input[type="text"]:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft) }
.seg { display: inline-flex; padding: 2px; background: var(--surface-2); border: 1px solid var(--line); border-radius: 8px }
.seg button { height: 28px; padding: 0 14px; background: none; border: 0; border-radius: 6px; color: var(--dim); cursor: pointer }
.seg button.on { background: var(--surface-3); color: var(--text) }
:root[data-theme="light"] .seg button.on { background: #fff; box-shadow: 0 1px 3px rgba(20, 28, 50, .18) }
input.switch { appearance: none; flex: none; position: relative; width: 38px; height: 22px; margin: 0; border-radius: 99px; background: var(--surface-3); border: 1px solid var(--line-strong); cursor: pointer; transition: background .15s }
input.switch::after { content: ""; position: absolute; top: 2px; left: 2px; width: 16px; height: 16px; border-radius: 50%; background: var(--dim); transition: transform .15s, background .15s }
input.switch:checked { background: var(--accent-strong); border-color: transparent }
input.switch:checked::after { transform: translateX(16px); background: #fff }
:root[data-theme="light"] input.switch::after { background: #fff; box-shadow: 0 0 0 1px var(--line-strong) }

/* storage: the share of 2 GB used (a meter), then what it is made of (a segmented bar and its legend) */
.viz { --c1: #3987e5; --c2: #d95926; --c3: #199e70; --c4: #c98500; --c5: #d55181; --c6: #008300 }
:root[data-theme="light"] .viz { --c1: #2a78d6; --c2: #eb6834; --c3: #1baf7a; --c4: #eda100; --c5: #e87ba4; --c6: #008300 }
.hero { font-size: 48px; font-weight: 600; letter-spacing: -.02em; line-height: 1.05 }
.hero small { margin-inline-start: 6px; font-size: 15px; font-weight: 400; color: var(--dim); letter-spacing: 0 }
.meter { height: 8px; margin: 10px 0 4px; border-radius: 99px; background: var(--accent-soft); overflow: hidden }
.meter span { display: block; height: 100%; min-width: 4px; border-radius: 99px; background: var(--accent-strong) }
.segbar { display: flex; gap: 2px; height: 8px; margin: 16px 0 8px }
.segbar span { height: 100%; min-width: 3px; border-radius: 99px }
.legend { display: grid; grid-template-columns: 10px 1fr auto auto; gap: 6px 10px; align-items: center; margin: 6px 0 4px }
.legend i { width: 10px; height: 10px; border-radius: 50% }
.legend .num { color: var(--dim); font-size: 12px; text-align: end; white-space: nowrap }
.legend .size { text-align: end; white-space: nowrap; font-variant-numeric: tabular-nums }

.dialog-back { position: fixed; inset: 0; z-index: 60; display: grid; place-items: center; padding: 20px; background: rgba(0, 0, 0, .5) }
:root[data-theme="light"] .dialog-back { background: rgba(20, 28, 50, .3) }
.dialog { width: min(400px, 100%); padding: 18px; background: var(--surface); border: 1px solid var(--line-strong); border-radius: 12px; box-shadow: var(--shadow); animation: pop-in .14s ease-out }
.dialog h3 { margin: 0 0 6px; font-size: 15px; font-weight: 600 }
.dialog p { margin: 0 0 16px; color: var(--dim) }
.dialog .btns { display: flex; justify-content: flex-end; gap: 8px }
#toast { display: flex; align-items: center; gap: 12px }
#toast.act { pointer-events: auto }
#toast button { background: none; border: 0; padding: 0; color: var(--accent); font-weight: 600; cursor: pointer }

@media (max-width: 860px) {
  .hide-narrow, footer, #count, .send-grow { display: none !important }
  .break { display: block; flex-basis: 100%; height: 0 }
  .to-wrap { flex: 1; min-width: 0 }
  #to { width: 100%; justify-content: flex-start }
  #toLabel { overflow: hidden; text-overflow: ellipsis; white-space: nowrap }
  .pop { position: fixed; left: 10px; right: 10px; bottom: 10px; width: auto; max-height: 70vh }
  .sheet { width: 100%; border-left: 0 }
  .menu { min-width: 210px }
}
@media (max-width: 860px) {
  html, body { height: auto }
  main { grid-template-columns: minmax(0, 1fr) }      /* never wider than the phone */
  .head { flex-wrap: wrap; row-gap: 6px }
  .acts { flex-wrap: wrap }
  #editor { min-height: 38vh }
  .scroll { overflow: visible }
  .top { flex-wrap: wrap; row-gap: 2px }
  .status { order: 3; width: 100% }
  .me .lbl, .meta { display: none }
}
@media (pointer: coarse) { kbd { display: none } }
@media (prefers-reduced-motion: reduce) { .item.fresh { animation: none } #toast, .btn, .item { transition: none } }
</style>
</head>
<body>
<header class="top">
  <div class="brand">
    <svg width="20" height="20" viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="8" fill="#5b2c83"/><path d="M7 12h17l-4-4M25 20H8l4 4" fill="none" stroke="#f0c33c" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></svg>
    Instant Share
  </div>
  <div id="status" class="status"><i></i><span>Connecting…</span></div>
  <div class="tools">
    <button id="me" class="me hide-narrow" type="button" title="Rename this device"><span class="lbl">This device: </span><b id="meName">…</b> ✎</button>
    <button id="onTop" class="icon-btn hide-narrow" type="button" hidden></button>
    <button id="theme" class="icon-btn hide-narrow" type="button"></button>
    <button id="gear" class="icon-btn" type="button" title="Settings"></button>
  </div>
</header>

<main>
  <section class="pane" id="compose" aria-label="Editor">
    <div class="bar">
      <h2>Write or paste</h2><span class="grow"></span>
      <label class="chk"><input id="enterSends" type="checkbox"> Enter sends</label>
      <label class="chk"><input id="wrap" type="checkbox" checked> Wrap long lines</label>
    </div>
    <div id="editing" class="editing" hidden>
      <span data-icon="pencil"></span><span>Editing your message from <b id="editingWhen"></b></span>
      <span class="grow"></span><button id="editCancel" class="btn sm" type="button">Cancel</button>
    </div>
    <textarea id="editor" spellcheck="false" dir="auto" autocomplete="off"></textarea>
    <div id="attach" class="attach" hidden>
      <img id="attThumb" alt="" hidden><span id="attBadge" class="badge" hidden></span>
      <span class="att-name" id="attName"></span><span class="muted" id="attSize"></span>
      <button id="attDel" class="icon-btn" type="button" title="Remove the file">✕</button>
    </div>
    <div class="bar">
      <button id="attachBtn" class="btn" type="button" title="Add a file, up to 5 MB"><span data-icon="clip"></span>File</button>
      <button id="clear" class="btn" type="button">Clear</button>
      <button id="copyEd" class="btn" type="button">Copy</button>
      <input id="fileInput" type="file" hidden>
      <span id="count" class="muted"></span><span class="grow send-grow"></span>
      <span class="break"></span>
      <div class="to-wrap">
        <button id="to" class="btn" type="button" aria-haspopup="true" aria-expanded="false" title="Who should see it">To: <span id="toLabel">Everyone</span> ▾</button>
        <div id="toPop" class="pop" hidden>
          <div class="pop-head">Who should see it?</div>
          <label class="pop-row"><input type="checkbox" id="toAll"><span class="nm">Everyone</span><small>every device, now and later</small></label>
          <div id="toList"></div>
          <p class="pop-note" id="toNote"></p>
        </div>
      </div>
      <button id="share" class="btn primary" type="button"><span id="shareLabel">Share</span><kbd id="shareKey"></kbd></button>
    </div>
  </section>

  <section class="pane" aria-label="History">
    <div class="bar">
      <h2 id="histTitle">History</h2><span id="total" class="muted"></span>
      <button id="archBack" class="btn sm" type="button" hidden>Back to the history</button>
      <span class="grow"></span>
      <input id="search" type="search" placeholder="Search the history" autocomplete="off">
    </div>
    <div class="scroll">
      <div id="list"></div>
      <p id="empty" class="empty" hidden></p>
      <p id="limit" class="note" hidden></p>
    </div>
  </section>
</main>

<footer>
  <span>The history is kept on <b id="where">the server</b>. No internet needed: only this network.</span>
  <span id="others" title="Phones, tablets and computers without the app: open this in a browser"></span>
  <span class="grow"></span>
  <span>Instant Share __VERSION__ · SCAFFApps</span>
  <a class="get-app" href="/download" title="Download Instant Share, to run it on another computer">Get the app</a>
  <a class="change-setup" href="#" hidden title="Which computer keeps the history">Change setup</a>
</footer>

<div id="menu" class="menu" role="menu" hidden></div>

<div id="sheet" class="sheet-back" hidden>
  <div class="sheet" role="dialog" aria-label="Settings">
    <div class="sheet-head"><h2>Settings</h2><button id="sheetClose" class="icon-btn" type="button" title="Close">✕</button></div>
    <section class="sec"><h3>This device</h3>
      <div class="line"><input id="setName" type="text" maxlength="40" autocomplete="off" spellcheck="false"><button id="setNameSave" class="btn" type="button">Save</button></div>
      <span class="sub">Shown next to everything it shares, and in the other devices' lists.</span>
    </section>
    <section class="sec"><h3>Appearance</h3>
      <div class="line"><span class="label">Look</span><div class="seg" id="setTheme"><button type="button" data-v="dark">Dark</button><button type="button" data-v="light">Light</button></div></div>
    </section>
    <section class="sec"><h3>Writing</h3>
      <label class="line"><span class="label">Enter sends<span class="sub">Shift+Enter starts a new line</span></span><input type="checkbox" class="switch" id="setEnter"></label>
      <label class="line"><span class="label">Wrap long lines</span><input type="checkbox" class="switch" id="setWrap"></label>
    </section>
    <section class="sec" id="secApp" hidden><h3>This computer</h3>
      <label class="line"><span class="label">Keep the window on top<span class="sub">Over the other programs, until you turn it off</span></span><input type="checkbox" class="switch" id="setTop"></label>
      <label class="line"><span class="label">Copy what arrives<span class="sub">Text and photos others share go straight to the clipboard</span></span><input type="checkbox" class="switch" id="setAutoCopy"></label>
    </section>
    <section class="sec" id="secSave" hidden><h3>Saving files</h3>
      <div class="line"><span class="label">Save files to<span class="sub" id="savePath"></span></span><button id="saveChange" class="btn sm" type="button">Change…</button></div>
      <div class="line" id="saveResetLine" hidden><span class="label"><span class="sub">Back to the usual Downloads folder</span></span><button id="saveReset" class="btn sm" type="button">Use Downloads</button></div>
    </section>
    <section class="sec"><h3>History</h3>
      <div class="line"><span class="label">Kept on <b id="setHost"></b><span class="sub">No internet needed: only this network</span></span><button id="setSetup" class="btn sm" type="button" hidden>Change setup</button></div>
      <div class="line"><span class="label">Archived messages<span class="sub" id="setArchCount"></span></span><button id="setArch" class="btn sm" type="button">Show</button></div>
      <div class="line"><span class="label">Phones and browsers<span class="sub" id="setAddr"></span></span></div>
    </section>
    <section class="sec viz"><h3>Storage</h3>
      <div id="storage"><span class="sub">Looking…</span></div>
      <div class="line" id="folderLine" hidden><span class="label"><span class="sub" id="folderPath"></span></span><button id="setFolder" class="btn sm" type="button">Show the history folder</button></div>
    </section>
    <section class="sec"><h3>About</h3>
      <div class="line"><span class="label">Instant Share __VERSION__ · SCAFFApps</span><a class="get-app" href="/download">Get the app</a></div>
    </section>
  </div>
</div>

<div id="dialog" class="dialog-back" hidden>
  <div class="dialog" role="alertdialog" aria-labelledby="dlgTitle">
    <h3 id="dlgTitle"></h3><p id="dlgText"></p>
    <div class="btns"><button id="dlgNo" class="btn" type="button">Cancel</button><button id="dlgYes" class="btn primary" type="button">OK</button></div>
  </div>
</div>
<div id="lightbox" class="lightbox" hidden><img alt=""></div>
<div id="toast" role="status" aria-live="polite"><span id="toastText"></span><button id="toastAct" type="button" hidden></button></div>

<script>
"use strict";
const $ = (s) => document.querySelector(s);
const editor = $("#editor"), listEl = $("#list"), search = $("#search"), shareBtn = $("#share");
const saved = {
  get(k, d) { try { const v = localStorage.getItem("textshare." + k); return v === null ? d : v; } catch { return d; } },
  set(k, v) { try { localStorage.setItem("textshare." + k, v); } catch {} },
};
const params = new URLSearchParams(location.search);
const KIND = params.get("app") === "1" ? "app" : "browser";       // the Windows and Android apps say so in the address
const FILE_MAX = 5 * 1024 * 1024;
const TYPES = {   // what can be shared (the computer that keeps the history checks the contents too)
  photo: [".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".heic"],
  document: [".pdf", ".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt", ".odt", ".ods", ".odp", ".rtf", ".txt", ".csv", ".md"],
  audio: [".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".flac", ".amr"],
  video: [".mp4", ".mov", ".3gp"], archive: [".zip"],
};
const ALLOWED = Object.values(TYPES).flat();
$("#fileInput").accept = ALLOWED.join(",");

const ICONS = {
  sun: '<circle cx="12" cy="12" r="3.8"/><path d="M12 3v2.2M12 18.8V21M3 12h2.2M18.8 12H21M5.6 5.6l1.6 1.6M16.8 16.8l1.6 1.6M5.6 18.4l1.6-1.6M16.8 7.2l1.6-1.6"/>',
  moon: '<path d="M19.5 14.2A8 8 0 0 1 9.8 4.5a7.6 7.6 0 1 0 9.7 9.7z"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9a1.6 1.6 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z"/>',
  pin: '<path d="M9 4h6l-1 5 3 3v2H7v-2l3-3-1-5z"/><path d="M12 14v6"/>',
  clip: '<path d="M20.5 11.5l-8.4 8.4a5 5 0 0 1-7.1-7.1l8.8-8.8a3.4 3.4 0 0 1 4.8 4.8l-8.8 8.8a1.7 1.7 0 0 1-2.4-2.4l8.1-8.1"/>',
  pencil: '<path d="M4 20h4L18.5 9.5a2.8 2.8 0 0 0-4-4L4 16v4z"/><path d="M13.5 6.5l4 4"/>',
  archive: '<rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9"/><path d="M10 13h4"/>',
  unarchive: '<rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9"/><path d="M12 17v-5M9.5 14.5 12 12l2.5 2.5"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-12M9 7V4h6v3"/>',
};
const icon = (name) => '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" '
  + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + ICONS[name] + "</svg>";
document.querySelectorAll("[data-icon]").forEach((el) => { el.outerHTML = icon(el.dataset.icon); });
$("#gear").innerHTML = icon("gear");
$("#onTop").innerHTML = icon("pin");

/* Inside the Android app, its own side answers through window.TextShareAndroid: quick calls answer at once,
   the ones that fetch something answer later through textShareDone(call, answer). */
const android = window.TextShareAndroid || null;
let androidCalls = 0;
const androidWaiting = {};
window.textShareDone = (call, answer) => { const r = androidWaiting[call]; delete androidWaiting[call]; if (r) r(answer); };
const androidAsk = (method, ...args) => new Promise((resolve) => {
  const call = ++androidCalls;
  androidWaiting[call] = resolve;
  android[method](call, ...args);
});
const androidApp = android && {
  copy_image: (url) => androidAsk("copyImage", url),
  save_file: (url, name) => androidAsk("saveFile", url, name),
  set_theme: (theme) => Promise.resolve(android.setTheme(theme)),
  set_name: (name) => Promise.resolve(android.setName(name)),
  open_setup: () => Promise.resolve(android.openSetup()),
  get_options: () => Promise.resolve(JSON.parse(android.getOptions ? android.getOptions() : '{"android":true}')),
  choose_save_folder: () => androidAsk("chooseSaveFolder"),
  reset_save_folder: () => Promise.resolve(JSON.parse(android.resetSaveFolder())),
  open_file: (url, name) => Promise.resolve(android.openFile(url, name)),
};
const app = () => (window.pywebview && window.pywebview.api) || androidApp || null;

function newId() {
  const a = new Uint8Array(12);
  crypto.getRandomValues(a);
  return Array.from(a, (b) => b.toString(16).padStart(2, "0")).join("");
}
let deviceId = params.get("id") || saved.get("id", "");
if (!/^[A-Za-z0-9_-]{8,64}$/.test(deviceId)) deviceId = newId();
saved.set("id", deviceId);
let device = saved.get("device", "");
let serverName = "the server", baseTitle = "Instant Share", addresses = [];
let rev = "", revQuery = null, newest = null, skew = 0, unseen = 0;
let pollTimer = 0, pollSeq = 0, isOnline = null, statusMsg = "";
let devices = [];                 // every device the server knows, for "Who should see it"
let targets = new Set();          // the devices chosen; none = everyone
try { targets = new Set(JSON.parse(saved.get("to", "[]"))); } catch {}
let attachment = null;            // {name, type, size, data (base64), url}
let editing = null;               // {id, at}: changing one of this device's own items, in place
let view = "list";                // "list", or "archived": what this device put away
let archivedCount = 0;
let options = {};                 // the app's own settings (always on top, copy what arrives…)
let enterSends = saved.get("enter", "1") === "1";
const rows = new Map();           // key -> its row in the history (a card is rebuilt only when its item changes)

/* ---------------- talking to the server ---------------- */

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", cache: "no-store", headers: {
    "X-TextShare": "1", "X-TextShare-Device": deviceId, "X-TextShare-Kind": KIND,
    "X-TextShare-Name": encodeURIComponent(device || "") } };
  if (opts.body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
  const res = await fetch(path, init);           // throws when the server cannot be reached
  let data = {};
  try { data = await res.json(); } catch {}
  if (!res.ok) { const e = new Error(data.error || "the server answered " + res.status); e.http = res.status; throw e; }
  return data;
}
const unreachable = (e) => !e.http;
const problem = (e) => unreachable(e) ? "can't reach the computer that keeps the history." : e.message;

async function hello() {
  try {
    const h = await api("/api/hello");
    serverName = h.server;
    addresses = (h.addresses || []).slice(0, 2).map((u) => u.replace(/\/$/, ""));
    baseTitle = document.title = "Instant Share · " + h.server;
    $("#where").textContent = $("#setHost").textContent = h.server;
    $("#others").textContent = addresses.length ? "Phones and browsers: " + addresses.join("  or  ") : "";
    $("#setAddr").textContent = addresses.join("  or  ");
    document.querySelectorAll("a.get-app").forEach((get) => {
      if (android) get.hidden = true;                    // in the Android app already
      else if (h.android && /Android/i.test(navigator.userAgent)) { get.href = "/download/android"; get.textContent = "Get the Android app"; }
    });
    if (!device) {    // the app says which device this is; a browser goes by the network's name for it
      device = params.get("me") || h.name || h.you || "this device";
      saved.set("device", device);
    }
  } catch { setTimeout(hello, 3000); }
  showMe();
}

async function poll() {
  clearTimeout(pollTimer);
  const my = ++pollSeq, q = search.value.trim(), key = view + "|" + q;
  const query = new URLSearchParams();
  if (q) query.set("q", q);
  if (view === "archived") query.set("archived", "1");
  if (rev && revQuery === key) query.set("rev", rev);
  try {
    const data = await api("/api/items?" + query);
    if (my !== pollSeq) return;
    setOnline(true);
    skew = data.now - Date.now();
    if (!data.same) { rev = data.rev; revQuery = key; take(data); }
  } catch (e) {
    if (my !== pollSeq) return;
    setOnline(false, unreachable(e) ? "" : e.message);
  }
  pollTimer = setTimeout(poll, document.hidden ? 5000 : 1500);
}
const refresh = () => { rev = ""; poll(); };

function setOnline(on, message) { isOnline = on; statusMsg = message || ""; drawStatus(); }
function drawStatus() {
  const s = $("#status");
  s.className = "status " + (isOnline ? "on" : isOnline === false ? "off" : "");
  const others = devices.filter((d) => d.online && d.id !== deviceId).length;
  s.querySelector("span").textContent = isOnline
    ? "Connected to " + serverName + (others ? ` · ${others} other device${others === 1 ? "" : "s"} online` : "")
    : statusMsg || "Can't reach " + serverName + ". Is Instant Share running there? Retrying…";
}

/* ---------------- devices: who should see it ---------------- */

async function loadDevices() {
  try {
    const d = await api("/api/devices");
    devices = d.devices;
    skew = d.now - Date.now();
  } catch { return; }
  let changed = false;
  for (const id of [...targets]) if (!devices.some((x) => x.id === id)) { targets.delete(id); changed = true; }
  if (changed) saved.set("to", JSON.stringify([...targets]));
  showTargets();
  drawStatus();
  if (!$("#toPop").hidden) renderDevices();
}

function renderDevices() {
  const others = devices.filter((d) => d.id !== deviceId);
  $("#toAll").checked = targets.size === 0;
  const list = $("#toList");
  list.textContent = "";
  for (const d of others) {
    const row = document.createElement("label");
    row.className = "pop-row";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = targets.has(d.id);
    box.addEventListener("change", () => { box.checked ? targets.add(d.id) : targets.delete(d.id); keepTargets(); });
    const dot = document.createElement("i");
    dot.className = "dot" + (d.online ? " on" : "");
    const nm = document.createElement("span");
    nm.className = "nm";
    nm.textContent = d.name;
    const sm = document.createElement("small");
    sm.textContent = d.online ? "online · " + (d.kind === "app" ? "app" : "browser") : "seen " + ago(d.seen);
    row.append(box, dot, nm, sm);
    list.append(row);
  }
  $("#toNote").textContent = others.length
    ? "Tick devices to share with them only: the others will not see it."
    : "No other devices yet. They appear here while Instant Share runs on them.";
}

function keepTargets() { saved.set("to", JSON.stringify([...targets])); showTargets(); renderDevices(); }
function targetNames() { return [...targets].map((id) => (devices.find((d) => d.id === id) || {}).name || "a device"); }
function showTargets() {
  const names = targetNames();
  $("#toLabel").textContent = !names.length ? "Everyone" : names[0] + (names.length > 1 ? " +" + (names.length - 1) : "");
  $("#to").classList.toggle("targeted", names.length > 0);
}
function closePop() { $("#toPop").hidden = true; $("#to").setAttribute("aria-expanded", "false"); }
$("#toAll").addEventListener("change", () => {
  if ($("#toAll").checked) targets.clear(); else if (!targets.size) $("#toAll").checked = true;
  keepTargets();
});
$("#to").addEventListener("click", (e) => {
  e.stopPropagation();
  const pop = $("#toPop");
  pop.hidden = !pop.hidden;
  $("#to").setAttribute("aria-expanded", String(!pop.hidden));
  if (!pop.hidden) { renderDevices(); loadDevices(); }
});
document.addEventListener("click", (e) => {
  if (!e.target.closest(".to-wrap")) closePop();
  if (!e.target.closest("#menu") && !e.target.closest(".more-btn")) closeMenu();
});

/* ---------------- the history ---------------- */

function take(data) {
  const fresh = new Set();
  let fromOthers = 0;
  if (newest !== null && view === "list") for (const it of data.items) if (it.id > newest) {
    fresh.add(it.id);
    if (it.from !== deviceId) fromOthers++;
  }
  if (view === "list") newest = data.newest;
  if (fromOthers && (document.hidden || !document.hasFocus())) { unseen += fromOthers; showTitle(); }
  archivedCount = data.archived;
  $("#setArchCount").textContent = archivedCount ? plural(archivedCount, "message") + " put away on this device" : "None";
  render(data.items, fresh);
  const q = search.value.trim();
  $("#total").textContent = view === "archived" ? `${data.matches} archived`
    : q ? `${data.matches} of ${data.total} match` : data.total ? `${data.total} shared` : "";
  const empty = $("#empty");
  empty.hidden = data.items.length > 0;
  empty.textContent = view === "archived" ? "Nothing archived. Swipe a message left (or use its ⋯ menu) to put it away here."
    : q ? "Nothing in the history matches."
    : "Nothing shared yet. Write or paste something on the left, or add a file, and press Share.";
  const limit = $("#limit");
  limit.hidden = data.matches <= data.items.length;
  limit.textContent = `Showing the newest ${data.items.length}. Search to find older ones.`;
}

const keyOf = (it) => [it.id, it.edited || 0, it.pinned ? 1 : 0, it.archived ? 1 : 0, view].join(":");

function groupLabel(text) {
  const el = document.createElement("div");
  el.className = "group-label";
  el.textContent = text;
  return el;
}

function render(items, fresh) {
  const want = [];
  const pinned = items.filter((it) => it.pinned), rest = items.filter((it) => !it.pinned);
  if (pinned.length && view === "list") {
    want.push(["label:pinned", () => groupLabel("Pinned")]);
    pinned.forEach((it) => want.push([keyOf(it), () => row(it), it]));
    if (rest.length) want.push(["label:recent", () => groupLabel("Recent")]);
    rest.forEach((it) => want.push([keyOf(it), () => row(it), it]));
  } else items.forEach((it) => want.push([keyOf(it), () => row(it), it]));
  const keep = new Set(want.map((w) => w[0]));
  for (const [k, el] of rows) if (!keep.has(k)) { el.remove(); rows.delete(k); }
  let prev = null;
  const added = [];
  for (const [k, make, it] of want) {
    let el = rows.get(k);
    if (!el) {
      el = make(); rows.set(k, el);
      if (it) { added.push(el.querySelector(".item")); if (fresh.has(it.id)) el.querySelector(".item").classList.add("fresh"); }
    }
    const next = prev ? prev.nextElementSibling : listEl.firstElementChild;
    if (el !== next) listEl.insertBefore(el, next);
    prev = el;
  }
  requestAnimationFrame(() => added.forEach(checkMore));
}

function button(cls, label, title) {
  const b = document.createElement("button");
  b.className = "btn sm " + cls;
  b.type = "button";
  b.textContent = label;
  if (title) b.title = title;
  return b;
}

const extOf = (name) => { const m = /\.[^.]+$/.exec(name || ""); return m ? m[0].toLowerCase() : ""; };
function badgeFor(name) {
  const ext = extOf(name), b = document.createElement("span");
  b.className = "badge " + ({ ".pdf": "b-pdf", ".doc": "b-doc", ".docx": "b-doc", ".odt": "b-doc", ".rtf": "b-doc",
    ".xls": "b-xls", ".xlsx": "b-xls", ".ods": "b-xls", ".csv": "b-xls", ".ppt": "b-ppt", ".pptx": "b-ppt",
    ".odp": "b-ppt", ".zip": "b-zip" }[ext] || (TYPES.audio.includes(ext) || TYPES.video.includes(ext) ? "b-media" : ""));
  b.textContent = (ext.slice(1) || "FILE").toUpperCase().slice(0, 4);
  return b;
}

function row(it) {
  const el = document.createElement("div");
  el.className = "row";
  el.dataset.swipe = view === "archived" ? "Back to the history" : "Archive";
  el.append(card(it));
  return el;
}

function card(it) {
  const node = document.createElement("article");
  node.className = "item";
  node.innerHTML = '<div class="head"><span class="who"></span><span class="to" hidden></span><time></time>'
    + '<span class="edited" hidden></span><span class="meta"></span><span class="pinned-mark" hidden title="Pinned on this device"></span>'
    + '<span class="acts"></span></div><div class="file" hidden></div>'
    + '<pre dir="auto"></pre><button class="more" type="button" hidden>Show all</button>';
  node.querySelector(".who").textContent = node.querySelector(".who").title = it.device;
  if (it.to) {
    const to = node.querySelector(".to");
    to.hidden = false;
    to.textContent = "→ " + it.to.map((d) => (d.id === deviceId ? "you" : d.name)).join(", ");
    to.title = "Only for " + to.textContent.slice(2) + (it.from === deviceId ? "" : " and the sender");
  }
  const t = node.querySelector("time");
  t.dataset.at = it.at;
  t.title = new Date(it.at).toLocaleString();
  t.textContent = ago(it.at);
  if (it.edited) {
    const e = node.querySelector(".edited");
    e.hidden = false;
    e.textContent = "· edited " + when(it.edited);
    e.title = "Edited " + new Date(it.edited).toLocaleString();
  }
  if (it.pinned) { const p = node.querySelector(".pinned-mark"); p.hidden = false; p.innerHTML = icon("pin"); }
  const meta = [];
  if (it.size) meta.push(plural(it.lines, "line"), plural(it.size, "character"));
  if (it.file) meta.push(size(it.file.size));
  node.querySelector(".meta").textContent = "· " + meta.join(" · ");

  const acts = node.querySelector(".acts");
  if (it.size) acts.append(button("copy", "Copy"));
  if (it.file) {
    const f = it.file, ext = extOf(f.name), box = node.querySelector(".file");
    box.hidden = false;
    if (f.shown && f.kind === "photo") {
      const img = document.createElement("img");
      img.className = "thumb"; img.loading = "lazy"; img.src = f.url; img.alt = f.name; img.title = f.name;
      box.append(img);
      if (KIND === "app") acts.append(button("copyimg", it.size ? "Copy photo" : "Copy", "Copy the photo, ready to paste"));
    } else if (f.shown && f.kind === "video") {
      const v = document.createElement("video");
      v.src = f.url; v.controls = true; v.preload = "metadata"; v.title = f.name;
      box.append(v);
    } else {
      const chip = document.createElement(f.shown && f.kind === "audio" ? "div" : "a");
      chip.className = "chip";
      if (chip.tagName === "A") {
        chip.href = f.url; chip.title = (ext === ".pdf" ? "Open " : "Download ") + f.name;
        if (ext === ".pdf") { chip.target = "_blank"; chip.rel = "noopener"; } else chip.download = f.name;
      }
      const nm = document.createElement("span"), sm = document.createElement("small");
      nm.className = "nm"; nm.textContent = f.name; sm.textContent = size(f.size);
      chip.append(badgeFor(f.name), nm, sm);
      box.append(chip);
      if (f.shown && f.kind === "audio") {
        const a = document.createElement("audio");
        a.src = f.url; a.controls = true; a.preload = "none";
        box.append(a);
      }
    }
    if (KIND === "app") acts.append(button("save", "Save", "Save it in the folder chosen in Settings (Downloads unless changed)"));
    else {
      const a = document.createElement("a");
      a.className = "btn sm"; a.textContent = "Save"; a.href = f.url + "?download=1"; a.download = f.name; a.title = "Download it";
      acts.append(a);
    }
  }
  acts.append(button("more-btn", "⋯", "More: pin, archive" + (it.mine ? ", edit, delete" : "")));
  const pre = node.querySelector("pre");
  if (it.size) pre.textContent = it.big ? it.text + "\n…" : it.text; else pre.hidden = true;
  node._item = it;
  swipeable(node);
  return node;
}

function checkMore(node) {
  if (!node) return;
  const pre = node.querySelector("pre");
  node.querySelector(".more").hidden = pre.hidden ||
    !(node._item.big || node.classList.contains("open") || pre.scrollHeight > pre.clientHeight + 1);
}

async function fullText(node) {
  const it = node._item;
  if (!it.big) return it.text;
  if (node._full == null) node._full = (await api("/api/items/" + it.id)).text;
  return node._full;
}

listEl.addEventListener("click", async (e) => {
  const thumb = e.target.closest("img.thumb");
  if (thumb) return openLightbox(thumb.src, thumb.alt);
  const chip = e.target.closest("a.chip");
  if (chip && android) {                                                // Android app: the phone's own viewer
    e.preventDefault();
    const it = chip.closest(".item")._item;
    app().open_file(location.origin + it.file.url, it.file.name);
    return;
  }
  if (chip && KIND === "app" && window.pywebview && !chip.target) {     // Windows app: a document goes to Downloads
    e.preventDefault();
    const it = chip.closest(".item")._item;
    const r = await app().save_file(location.origin + it.file.url, it.file.name);
    toast(r && r.ok ? "Saved: " + r.name : (r && r.error) || "Could not save it.", !(r && r.ok));
    return;
  }
  const btn = e.target.closest("button");
  if (!btn) return;
  const node = btn.closest(".item"), it = node._item;
  try {
    if (btn.classList.contains("copy")) {
      const text = await fullText(node);
      if (await copyText(text)) done(btn, "Copied ✓");
      else if (await putInEditor(text)) { editor.select(); toast("The browser blocked the copy. It is in the editor, selected: press Ctrl+C.", true); }
    } else if (btn.classList.contains("copyimg")) {
      const r = await app().copy_image(location.origin + it.file.url);
      if (r && r.ok) done(btn, "Copied ✓"); else toast((r && r.error) || "Could not copy the photo.", true);
    } else if (btn.classList.contains("save")) {
      const r = await app().save_file(location.origin + it.file.url, it.file.name);
      if (r && r.ok) { done(btn, "Saved ✓"); toast("Saved: " + r.name); } else toast((r && r.error) || "Could not save it.", true);
    } else if (btn.classList.contains("more-btn")) {
      openMenu(node, btn);
    } else if (btn.classList.contains("more")) {
      const open = node.classList.toggle("open");
      if (open && it.big) node.querySelector("pre").textContent = await fullText(node);
      btn.textContent = open ? "Show less" : "Show all";
      if (!open) node.scrollIntoView({ block: "nearest" });
    }
  } catch (err) {
    toast("Could not get it: " + problem(err), true);
  }
});

/* ---------------- an item's ⋯ menu: pin, archive, and for its sender edit and delete ---------------- */

function closeMenu() { $("#menu").hidden = true; }
function openMenu(node, anchor) {
  const it = node._item, menu = $("#menu");
  menu.textContent = "";
  const add = (ico, label, run, danger) => {
    const b = document.createElement("button");
    b.type = "button"; b.setAttribute("role", "menuitem");
    if (danger) b.className = "danger";
    b.innerHTML = icon(ico);
    b.append(label);
    b.addEventListener("click", () => { closeMenu(); run(); });
    menu.append(b);
  };
  if (view === "list") add("pin", it.pinned ? "Unpin" : "Pin to the top", () => setMark(it, { pinned: !it.pinned }));
  if (view === "list") add("archive", "Archive", () => archive(node));
  else add("unarchive", "Back to the history", () => archive(node));
  if (it.mine && view === "list") add("pencil", "Edit", () => startEdit(node));
  if (it.mine || !it.from) add("trash", "Delete for everyone", () => remove(node), true);
  menu.hidden = false;
  const r = anchor.getBoundingClientRect(), w = menu.offsetWidth, h = menu.offsetHeight;
  menu.style.left = Math.max(8, Math.min(r.right - w, innerWidth - w - 8)) + "px";
  menu.style.top = (r.bottom + h + 8 > innerHeight ? Math.max(8, r.top - h - 4) : r.bottom + 4) + "px";
  menu.querySelector("button").focus();
}

async function setMark(it, change) {
  try { await api("/api/items/" + it.id + "/mark", { method: "POST", body: change }); refresh(); }
  catch (err) { toast("Not done: " + problem(err), true); }
}

/** Archive (or, in the archive, bring back): the card slides away, with Undo for a few seconds. */
async function archive(node) {
  const it = node._item, putAway = view === "list";
  node.classList.add("gone");
  try {
    await api("/api/items/" + it.id + "/mark", { method: "POST", body: { archived: putAway } });
    toast(putAway ? "Archived on this device." : "Back in the history.", false, {
      label: "Undo", run: () => setMark(it, { archived: !putAway }) });
  } catch (err) {
    node.classList.remove("gone");
    toast("Not done: " + problem(err), true);
  }
  setTimeout(refresh, 230);
}

async function remove(node) {
  const it = node._item;
  if (!await ask("Delete it for everyone?", "It disappears from every device's history. To put it away on this device only, archive it instead.", "Delete")) return;
  try {
    await api("/api/items/" + it.id, { method: "DELETE" });
    node.classList.add("gone");
    setTimeout(refresh, 230);
  } catch (err) {
    if (err.http === 404) refresh(); else toast("Not deleted: " + problem(err), true);
  }
}

/** Swipe a card left (finger or pen) to archive it; the mouse uses the ⋯ menu, so selecting text still works. */
function swipeable(node) {
  let start = null, dx = 0, sliding = false;
  node.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "mouse" || e.target.closest("button, a, audio, video, input")) return;
    start = { x: e.clientX, y: e.clientY, id: e.pointerId }; dx = 0; sliding = false;
  });
  node.addEventListener("pointermove", (e) => {
    if (!start || e.pointerId !== start.id) return;
    const x = e.clientX - start.x, y = e.clientY - start.y;
    if (!sliding) {
      if (Math.abs(y) > 12 && Math.abs(y) > Math.abs(x)) { start = null; return; }     // scrolling: not for us
      if (Math.abs(x) < 12) return;
      sliding = true;
      try { node.setPointerCapture(e.pointerId); } catch {}   // (a pointer the browser already let go of)
      node.classList.remove("back");
    }
    dx = Math.min(0, x);
    node.style.transform = `translateX(${dx}px)`;
    node.parentElement.classList.toggle("swiping", dx < -24);
  });
  const end = () => {
    if (!start) return;
    start = null;
    if (!sliding) return;
    node.parentElement.classList.remove("swiping");
    if (dx < -node.offsetWidth * 0.35) archive(node);
    else { node.classList.add("back"); node.style.transform = ""; }
  };
  node.addEventListener("pointerup", end);
  node.addEventListener("pointercancel", end);
}

function openLightbox(src, alt) {
  const lb = $("#lightbox"), img = lb.querySelector("img");
  img.src = src; img.alt = alt; lb.hidden = false;
}
$("#lightbox").addEventListener("click", () => { $("#lightbox").hidden = true; });

function showView(v) {
  view = v;
  $("#histTitle").textContent = v === "archived" ? "Archived" : "History";
  $("#archBack").hidden = v !== "archived";
  for (const [, el] of rows) el.remove();
  rows.clear();
  refresh();
}
$("#archBack").addEventListener("click", () => showView("list"));

/* ---------------- the editor: writing, a file, editing one's own message ---------------- */

function setEditor(text) { editor.value = text; if (!editing) saved.set("draft", text); updateCount(); }

async function putInEditor(text) {
  if (editor.value.trim() && editor.value !== text && !await ask("Replace what is in the editor?", "What you wrote there is not shared yet.", "Replace")) return false;
  setEditor(text);
  return true;
}

function updateCount() {
  const v = editor.value;
  $("#count").textContent = v ? plural(v.split("\n").length, "line") + " · " + plural(v.length, "character") : "";
}

function applyEnter() {
  $("#enterSends").checked = $("#setEnter").checked = enterSends;
  $("#shareKey").textContent = enterSends ? "Enter" : "Ctrl+Enter";
  editor.placeholder = "Write or paste text here, or drop a file, then press Share ("
    + (enterSends ? "Enter; Shift+Enter for a new line" : "Ctrl+Enter") + ").";
  saved.set("enter", enterSends ? "1" : "0");
}
$("#enterSends").addEventListener("change", (e) => { enterSends = e.target.checked; applyEnter(); });

async function startEdit(node) {
  const it = node._item, text = await fullText(node);
  if (editor.value.trim() && editor.value !== text
      && !await ask("Replace what is in the editor?", "What you wrote there stays saved: Cancel brings it back.", "Edit")) return;
  editing = { id: it.id, at: it.at };            // from here the editor holds the message, not the draft
  setEditor(text);
  attachment = null; showAttachment();
  $("#editingWhen").textContent = when(it.at);
  $("#editing").hidden = false;
  $("#shareLabel").textContent = "Save";
  ["#to", "#attachBtn"].forEach((s) => { $(s).disabled = true; });
  editor.focus();
}
function endEdit() {
  editing = null;
  $("#editing").hidden = true;
  $("#shareLabel").textContent = "Share";
  ["#to", "#attachBtn"].forEach((s) => { $(s).disabled = false; });
}
$("#editCancel").addEventListener("click", () => { endEdit(); setEditor(saved.get("draft", "")); editor.focus(); });

function attachFile(file) {
  if (!file || editing) return;
  const ext = extOf(file.name) || ({ "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp" }[file.type] || "");
  if (!ALLOWED.includes(ext)) return toast("That kind of file can't be shared. Photos, PDFs, Office documents, text, audio, short videos and ZIP files can.", true);
  if (file.size > FILE_MAX) return toast(`That file is ${size(file.size)}. Files can be up to 5 MB.`, true);
  const reader = new FileReader();
  reader.onload = () => {
    const url = String(reader.result);
    attachment = { name: extOf(file.name) ? file.name : "pasted image" + ext, type: file.type, size: file.size,
                   data: url.slice(url.indexOf(",") + 1), url, ext };
    showAttachment();
  };
  reader.onerror = () => toast("Could not read that file.", true);
  reader.readAsDataURL(file);
}
function showAttachment() {
  $("#attach").hidden = !attachment;
  if (!attachment) return;
  const image = [".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"].includes(attachment.ext);
  $("#attThumb").hidden = !image;
  if (image) $("#attThumb").src = attachment.url;
  const badge = $("#attBadge");
  badge.hidden = image;
  if (!image) { const b = badgeFor(attachment.name); badge.className = b.className; badge.textContent = b.textContent; }
  $("#attName").textContent = attachment.name;
  $("#attSize").textContent = size(attachment.size);
}
$("#attachBtn").addEventListener("click", () => $("#fileInput").click());
$("#fileInput").addEventListener("change", (e) => { attachFile(e.target.files[0]); e.target.value = ""; });
$("#attDel").addEventListener("click", () => { attachment = null; showAttachment(); });
editor.addEventListener("paste", (e) => {
  const f = e.clipboardData && e.clipboardData.files && e.clipboardData.files[0];
  if (f) { e.preventDefault(); attachFile(f); }
});
const compose = $("#compose");
const hasFiles = (e) => e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files");
compose.addEventListener("dragover", (e) => { if (hasFiles(e)) { e.preventDefault(); compose.classList.add("drop"); } });
compose.addEventListener("dragleave", (e) => { if (!compose.contains(e.relatedTarget)) compose.classList.remove("drop"); });
compose.addEventListener("drop", (e) => {
  compose.classList.remove("drop");
  if (hasFiles(e)) { e.preventDefault(); attachFile(e.dataTransfer.files[0]); }
});
window.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
window.addEventListener("drop", (e) => { if (hasFiles(e)) e.preventDefault(); });   // never open the file instead

async function share() {
  const text = editor.value;
  if (editing) {
    shareBtn.disabled = true;
    try {
      await api("/api/items/" + editing.id, { method: "PUT", body: { text } });
      endEdit();
      setEditor(saved.get("draft", ""));               // what was being written before, if anything
      toast("Changed. Everyone sees the new text, marked as edited.");
      refresh();
    } catch (e) {
      toast("Not changed: " + problem(e), true);
    } finally { shareBtn.disabled = false; editor.focus(); }
    return;
  }
  if (!text.trim() && !attachment) { toast("Nothing to share yet: write or paste something, or add a file."); editor.focus(); return; }
  shareBtn.disabled = true;
  try {
    const body = { text, device, to: [...targets] };
    if (attachment) body.file = { name: attachment.name, data: attachment.data };
    const r = await api("/api/items", { method: "POST", body });
    setEditor("");
    attachment = null;
    showAttachment();
    toast(r.duplicate ? "That is already the newest in the history."
      : targets.size ? "Shared with " + targetNames().join(", ") + " only." : "Shared. Every device has it now.");
    poll();
  } catch (e) {
    toast("Not shared: " + (unreachable(e) ? "can't reach the computer that keeps the history. Your text is still here." : e.message), true);
  } finally {
    shareBtn.disabled = false;
    editor.focus();
  }
}

let draftTimer = 0;
editor.addEventListener("input", () => {
  updateCount();
  if (editing) return;
  clearTimeout(draftTimer);
  draftTimer = setTimeout(() => saved.set("draft", editor.value), 300);
});
editor.addEventListener("keydown", (e) => {
  if (enterSends && e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey && !e.isComposing) {
    e.preventDefault(); share();
  }
});
shareBtn.addEventListener("click", share);
$("#copyEd").addEventListener("click", async (e) => {
  if (!editor.value) return toast("The editor is empty.");
  if (await copyText(editor.value)) done(e.currentTarget, "Copied ✓");
  else { editor.select(); toast("The browser blocked the copy: the text is selected, press Ctrl+C.", true); }
});
$("#clear").addEventListener("click", async () => {
  if (!editor.value && !attachment) return;
  if (await ask("Clear the editor?", "What you wrote, and the file, if any, go.", "Clear")) {
    if (editing) endEdit();
    setEditor(""); attachment = null; showAttachment(); editor.focus();
  }
});
const wrap = $("#wrap");
function applyWrap() {
  editor.wrap = wrap.checked ? "soft" : "off"; editor.setAttribute("wrap", editor.wrap);
  $("#setWrap").checked = wrap.checked; saved.set("wrap", wrap.checked ? "1" : "0");
}
wrap.checked = saved.get("wrap", "1") === "1";
wrap.addEventListener("change", applyWrap);

document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && !e.altKey && (e.key === "Enter" || e.key === "s" || e.key === "S")) { e.preventDefault(); share(); }
  if (e.key === "Escape") back();
});

/* ---------------- settings ---------------- */

async function openSettings(focusName) {
  $("#setName").value = device;
  $("#sheet").hidden = false;
  drawThemeSeg();
  applyEnter(); applyWrap();
  loadStorage();
  try { options = (app() && app().get_options ? await app().get_options() : {}) || {}; } catch { options = {}; }
  drawOptions();
  if (focusName) $("#setName").select();
}
function closeSettings() { $("#sheet").hidden = true; }
function drawOptions() {
  $("#secApp").hidden = !options.windows;
  $("#setTop").checked = !!options.on_top;
  $("#setAutoCopy").checked = !!options.auto_copy;
  $("#onTop").hidden = !options.windows;
  $("#onTop").classList.toggle("on", !!options.on_top);
  $("#onTop").title = options.on_top ? "On top of other windows: click to stop" : "Keep the window on top of other windows";
  $("#folderLine").hidden = !options.host;
  $("#folderPath").textContent = options.folder || "";
  $("#setSetup").hidden = !app();
  $("#secSave").hidden = options.save_folder === undefined;
  $("#savePath").textContent = options.save_folder || "";
  $("#saveResetLine").hidden = !options.save_chosen;
}
$("#gear").addEventListener("click", () => openSettings(false));
$("#me").addEventListener("click", () => openSettings(true));
$("#sheetClose").addEventListener("click", closeSettings);
$("#sheet").addEventListener("click", (e) => { if (e.target.id === "sheet") closeSettings(); });
function saveName() {
  const v = $("#setName").value.trim().slice(0, 40);
  if (!v) return;
  device = v; saved.set("device", device); showMe();
  if (app() && app().set_name) app().set_name(device);
  toast("This device is now called " + device + ".");
}
$("#setNameSave").addEventListener("click", saveName);
$("#setName").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); saveName(); } });
$("#setTheme").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) { applyTheme(b.dataset.v); tellApp(); } });
$("#setEnter").addEventListener("change", (e) => { enterSends = e.target.checked; applyEnter(); });
$("#setWrap").addEventListener("change", (e) => { wrap.checked = e.target.checked; applyWrap(); });
async function setOption(name, value) {
  try { await app().set_option(name, value); options[name] = value; } catch {}
  drawOptions();
}
$("#setTop").addEventListener("change", (e) => setOption("on_top", e.target.checked));
$("#setAutoCopy").addEventListener("change", (e) => setOption("auto_copy", e.target.checked));
$("#onTop").addEventListener("click", () => setOption("on_top", !options.on_top));
$("#setArch").addEventListener("click", () => { closeSettings(); showView("archived"); });
$("#saveChange").addEventListener("click", async () => {
  const before = options.save_folder;
  try { options = (await app().choose_save_folder()) || options; } catch {}
  drawOptions();
  if (options.save_folder !== before) toast("Files will be saved in " + options.save_folder + ".");
});
$("#saveReset").addEventListener("click", async () => {
  try { options = (await app().reset_save_folder()) || options; } catch {}
  drawOptions();
  toast("Files will be saved in Downloads.");
});
$("#setFolder").addEventListener("click", () => app() && app().open_folder && app().open_folder());
$("#setSetup").addEventListener("click", () => { closeSettings(); app().open_setup(); });
document.querySelectorAll("a.change-setup").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); app().open_setup(); }));

async function loadStorage() {
  const box = $("#storage");
  let s;
  try { s = await api("/api/storage"); } catch (e) { box.innerHTML = '<span class="sub"></span>'; box.firstChild.textContent = "Not available: " + problem(e); return; }
  const pct = s.used / s.limit * 100;
  const parts = s.kinds.filter((k) => k.bytes > 0);
  box.textContent = "";
  const hero = document.createElement("div");
  hero.className = "hero";
  hero.textContent = (s.used && pct < 1 ? "<1" : Math.round(pct)) + "%";
  const small = document.createElement("small");
  small.textContent = "used · " + size(s.used) + " of " + size(s.limit);
  hero.append(small);
  const meter = document.createElement("div");
  meter.className = "meter";
  meter.setAttribute("role", "meter"); meter.setAttribute("aria-valuenow", pct.toFixed(1));
  meter.setAttribute("aria-label", "History storage used");
  const fill = document.createElement("span");
  fill.style.width = Math.min(100, pct) + "%";
  meter.append(fill);
  box.append(hero, meter);
  if (!parts.length) { const p = document.createElement("span"); p.className = "sub"; p.textContent = "Nothing stored yet."; box.append(p); return; }
  const bar = document.createElement("div");
  bar.className = "segbar";
  bar.setAttribute("aria-label", "What the history holds: " + parts.map((k) => `${k.label} ${size(k.bytes)}`).join(", "));
  const legend = document.createElement("div");
  legend.className = "legend";
  s.kinds.forEach((k, n) => {
    const colour = `var(--c${n + 1})`;
    if (k.bytes > 0) {
      const part = document.createElement("span");
      part.style.flex = String(k.bytes);
      part.style.background = colour;
      part.title = `${k.label}: ${size(k.bytes)} (${Math.round(k.bytes / s.used * 100)}% of what is stored)`;
      bar.append(part);
    }
    const dot = document.createElement("i"), name = document.createElement("span"), num = document.createElement("span"), sz = document.createElement("span");
    dot.style.background = colour;
    name.textContent = k.label;
    num.className = "num"; num.textContent = k.count ? plural(k.count, k.kind === "text" ? "message" : "file") : "";
    sz.className = "size"; sz.textContent = k.bytes ? size(k.bytes) : "-";
    legend.append(dot, name, num, sz);
  });
  box.append(bar, legend);
  const note = document.createElement("span");
  note.className = "sub";
  note.textContent = "Up to " + size(s.limit) + " in all; one file up to 5 MB. When it is full, delete old files to make room.";
  box.append(note);
}

/* ---------------- a question (instead of the browser's own boxes) ---------------- */

let dialogDone = null;
function ask(title, text, yes) {
  $("#dlgTitle").textContent = title;
  $("#dlgText").textContent = text;
  $("#dlgYes").textContent = yes || "OK";
  $("#dialog").hidden = false;
  $("#dlgYes").focus();
  return new Promise((resolve) => { dialogDone = resolve; });
}
function answer(v) { $("#dialog").hidden = true; const r = dialogDone; dialogDone = null; if (r) r(v); }
$("#dlgYes").addEventListener("click", () => answer(true));
$("#dlgNo").addEventListener("click", () => answer(false));
$("#dialog").addEventListener("click", (e) => { if (e.target.id === "dialog") answer(false); });

/** Back (Esc, or the phone's Back button): closes what is open; true when something was. */
function back() {
  if (!$("#dialog").hidden) { answer(false); return true; }
  if (!$("#menu").hidden) { closeMenu(); return true; }
  if (!$("#lightbox").hidden) { $("#lightbox").hidden = true; return true; }
  if (!$("#toPop").hidden) { closePop(); return true; }
  if (!$("#sheet").hidden) { closeSettings(); return true; }
  if (view === "archived") { showView("list"); return true; }
  return false;
}
window.textShareBack = back;

/* ---------------- small things ---------------- */

function copyText(text) {
  if (window.isSecureContext && navigator.clipboard) return navigator.clipboard.writeText(text).then(() => true, () => oldCopy(text));
  return Promise.resolve(oldCopy(text));   // plain http on the network: the older way, which browsers still allow on a click
}
function oldCopy(text) {
  const back = document.activeElement, ta = document.createElement("textarea");
  ta.value = text; ta.setAttribute("readonly", ""); ta.style.cssText = "position:fixed;top:0;left:0;opacity:0";
  document.body.appendChild(ta); ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch {}
  ta.remove();
  if (back && back.focus) back.focus();
  return ok;
}

function done(btn, label) {
  clearTimeout(btn._d);
  if (btn._was == null) btn._was = btn.innerHTML;
  btn.textContent = label; btn.classList.add("done");
  btn._d = setTimeout(() => { btn.innerHTML = btn._was; btn._was = null; btn.classList.remove("done"); }, 1300);
}

let toastTimer = 0;
function toast(message, bad, action) {
  const t = $("#toast"), act = $("#toastAct");
  $("#toastText").textContent = message;
  t.classList.toggle("bad", !!bad);
  t.classList.toggle("act", !!action);
  act.hidden = !action;
  act.onclick = null;
  if (action) { act.textContent = action.label; act.onclick = () => { t.classList.remove("show", "act"); action.run(); }; }
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show", "act"), action ? 6000 : bad ? 5000 : 2600);
}

function plural(n, word) { return n.toLocaleString() + " " + word + (n === 1 ? "" : "s"); }
function size(bytes) {
  if (bytes < 1024) return bytes + " bytes";
  if (bytes < 1048576) return Math.round(bytes / 1024) + " KB";
  if (bytes < 1073741824) return (bytes / 1048576).toFixed(bytes < 10485760 ? 2 : 1) + " MB";
  return (bytes / 1073741824).toFixed(bytes % 1073741824 ? 2 : 0) + " GB";
}
function when(at) {
  const d = new Date(at), today = new Date(Date.now() + skew);
  const time = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === today.toDateString()) return time;
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short" }) + " " + time;
}

function ago(at) {
  const s = Math.round((Date.now() + skew - at) / 1000);
  if (s < 45) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return m + " min ago";
  const h = Math.round(m / 60);
  const d = new Date(at), time = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  if (h < 12) return h + " h ago";
  const today = new Date(Date.now() + skew), yesterday = new Date(today - 864e5);
  if (d.toDateString() === today.toDateString()) return "today " + time;
  if (d.toDateString() === yesterday.toDateString()) return "yesterday " + time;
  const opts = { day: "numeric", month: "short" };
  if (d.getFullYear() !== today.getFullYear()) opts.year = "numeric";
  return d.toLocaleDateString(undefined, opts) + " " + time;
}

function showMe() { $("#meName").textContent = device || "…"; }
function showTitle() { document.title = (unseen ? "(" + unseen + ") " : "") + baseTitle; }

let searchTimer = 0;
search.addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(poll, 250); });
document.addEventListener("visibilitychange", () => { if (!document.hidden) { unseen = 0; showTitle(); poll(); } });
window.addEventListener("focus", () => { unseen = 0; showTitle(); });
let resizeTimer = 0;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => rows.forEach((el) => checkMore(el.querySelector && el.querySelector(".item"))), 200);
});
setInterval(() => document.querySelectorAll("#list time").forEach((t) => { t.textContent = ago(+t.dataset.at); }), 30000);

/* ---------------- inside the apps ---------------- */

async function inApp() {
  if (!app()) return;
  document.querySelectorAll("a.change-setup").forEach((a) => { a.hidden = false; });
  if (app().set_name) app().set_name(device);
  tellApp();
  try { options = (app().get_options ? await app().get_options() : {}) || {}; } catch { options = {}; }
  drawOptions();
}
window.addEventListener("pywebviewready", inApp);

/* ---------------- appearance: dark (the default) or light, as in Multi Screens ---------------- */

function tellApp() {               // the app's title bar (or the phone's status bar) follows the page
  try { if (app() && app().set_theme) app().set_theme(document.documentElement.dataset.theme); } catch {}
}
function drawThemeSeg() {
  document.querySelectorAll("#setTheme button").forEach((b) => b.classList.toggle("on", b.dataset.v === document.documentElement.dataset.theme));
}
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const b = $("#theme");
  b.innerHTML = icon(theme === "dark" ? "sun" : "moon");
  b.title = theme === "dark" ? "Switch to light appearance" : "Switch to dark appearance";
  saved.set("theme", theme);
  drawThemeSeg();
}
applyTheme(document.documentElement.dataset.theme);
$("#theme").addEventListener("click", () => {
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  tellApp();
});

/* Shared from another app on the phone (Android's Share menu): into the editor, ready to share. */
function sharedIn(s) {
  if (!s) return;
  if (editing) { endEdit(); setEditor(""); }
  if (s.text) setEditor(editor.value.trim() ? editor.value.replace(/\s*$/, "") + "\n" + s.text : s.text);
  if (s.file) {
    const ext = extOf(s.file.name);
    if (!ALLOWED.includes(ext)) toast("That kind of file can't be shared. Photos, PDFs, Office documents, text, audio, short videos and ZIP files can.", true);
    else if (s.file.size > FILE_MAX) toast(`That file is ${size(s.file.size)}. Files can be up to 5 MB.`, true);
    else {
      attachment = { name: s.file.name, type: s.file.type, size: s.file.size, data: s.file.data, ext,
                     url: "data:" + s.file.type + ";base64," + s.file.data };
      showAttachment();
    }
  }
  editor.focus();
  toast("Ready to share: choose who should see it, then press Share.");
}
window.textShareIncoming = sharedIn;

editor.value = saved.get("draft", "");
applyWrap();
applyEnter();
updateCount();
showMe();
showTargets();
hello().finally(() => { poll(); loadDevices(); });
setInterval(loadDevices, 5000);
if (android) { try { sharedIn(JSON.parse(android.takeShared() || "null")); } catch {} }
inApp();
</script>
</body>
</html>
"""

PAGE = PAGE.replace("__VERSION__", VERSION)

if __name__ == "__main__":
    sys.exit(run())
