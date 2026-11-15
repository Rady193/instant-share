"""Text Share: pass text between the computers on your own network.

This file is the heart of Text Share: the server that keeps the history, and the page every computer shows. On Windows
the app (app.py, packed into the portable .exe by build.py) runs it behind its own window and tray icon. On its own,
with nothing but Python's standard library, it runs as a plain server for Mac and Linux, or for anyone who prefers a
browser:

    python3 textshare.py               start it and open it in the browser; close the window (Ctrl+C) to stop it
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

VERSION = "1.0.0"
FROZEN = getattr(sys, "frozen", False)                       # running as the packed .exe
APP_FILE = Path(sys.executable if FROZEN else __file__).resolve()
DEFAULT_PORT = 8765
MAX_BODY = 22 * 1024 * 1024      # biggest share accepted, in bytes (20 MB of text, or a file with some)
LIST_LIMIT = 300                 # the history shows the newest this many (search reaches older ones)
FULL_LIMIT = 64 * 1024           # items longer than this come as a preview in the list, the rest on demand
PREVIEW = 4000                   # characters of a long item's preview


def now_ms():
    return int(time.time() * 1000)


def default_data_dir():
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "TextShare" if base else Path.home() / ".textshare"


def log(message):
    """A line in the window. It never stops a share: a name the window cannot show comes out as "?"."""
    try:
        print(f"  {time.strftime('%H:%M')}  {message}", flush=True)
    except (AttributeError, OSError, UnicodeError):
        pass


# ---------------------------------------------------------------- the history (one SQLite file)

LINES = "length(text) - length(replace(text, char(10), '')) + 1"
COLUMNS = (f"id, at, device, length(text), {LINES}, "
           f"CASE WHEN length(text) <= {FULL_LIMIT} THEN text ELSE substr(text, 1, {PREVIEW}) END, "
           "sender, recipients, file_name, file_type, file_size, file_key")
VISIBLE = "(recipients IS NULL OR sender = ? OR instr(recipients, ?) > 0)"     # for everyone, from me, or to me
DEVICE_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")
ONLINE_MS = 15_000               # a device is online when it was heard from in the last 15 seconds
FORGET_MS = 30 * 86_400_000      # devices not heard from for 30 days leave the list

FILE_MAX = 1024 * 1024           # photos and PDFs, up to 1 MB
FILE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp",
              "application/pdf": ".pdf"}


def sniff(data):
    """What a file really is, by its first bytes (never by its name): one of FILE_TYPES, or None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    return None


def clean_file_name(name, mime):
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "", str(name or "").replace("\\", "/").rsplit("/", 1)[-1]).strip(" .")
    stem, dot, ext = name.rpartition(".")
    if not dot or "." + ext.lower() not in (FILE_TYPES[mime], ".jpeg") or not stem:
        name = (name or ("photo" if mime.startswith("image/") else "document")) + FILE_TYPES[mime]
    return name[-120:]


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
                             ("file", "BLOB")):
            if column not in have:
                self.db.execute(f"ALTER TABLE items ADD COLUMN {column} {kind}")
        self.db.execute("""CREATE TABLE IF NOT EXISTS devices (
            id        TEXT PRIMARY KEY,                 -- made up by the device the first time
            name      TEXT NOT NULL,
            kind      TEXT NOT NULL,                    -- "app" or "browser"
            last_seen INTEGER NOT NULL)""")
        self.db.commit()
        self.lock = threading.Lock()
        self.boot = secrets.token_hex(4)    # a restart changes the revision, so every page reloads its list
        self.changes = 0
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
                "online": now - d["seen"] < ONLINE_MS}
               for i, d in list(self.seen.items()) if now - d["seen"] < FORGET_MS]
        out.sort(key=lambda d: (not d["online"], d["name"].lower()))
        return {"now": now, "devices": out}

    def _names(self):
        return {i: d["name"] for i, d in list(self.seen.items())}

    # ---- items

    def _item(self, r, names):
        (item_id, at, device, size, lines, text, sender, recipients, fname, ftype, fsize, fkey) = r
        to = json.loads(recipients) if recipients else None
        return {"id": item_id, "at": at, "device": device, "from": sender or "", "size": size,
                "lines": lines if size else 0, "text": text, "big": size > len(text),
                "to": [{"id": d, "name": names.get(d, "a device")} for d in to] if to else None,
                "file": {"name": fname, "type": ftype, "size": fsize,
                         "url": f"/files/{item_id}/{fkey}/{quote(fname)}"} if fkey else None}

    @staticmethod
    def _me(me):
        me = me if DEVICE_ID.fullmatch(me or "") else "-"            # "-" is nobody's id
        return [me, json.dumps(me)]

    def list(self, q="", me=""):
        params = self._me(me)
        where = VISIBLE
        if q:
            where += (" AND (instr(lower(text), lower(?)) > 0 OR instr(lower(device), lower(?)) > 0"
                      " OR instr(lower(COALESCE(file_name, '')), lower(?)) > 0)")
        with self.lock:
            total, newest = self.db.execute(f"SELECT COUNT(*), COALESCE(MAX(id), 0) FROM items WHERE {VISIBLE}",
                                            params).fetchone()
            matches = (self.db.execute(f"SELECT COUNT(*) FROM items WHERE {where}", params + [q, q, q]).fetchone()[0]
                       if q else total)
            rows = self.db.execute(f"SELECT {COLUMNS} FROM items WHERE {where} ORDER BY id DESC LIMIT ?",
                                   params + ([q, q, q] if q else []) + [LIST_LIMIT]).fetchall()
            rev = self.rev
        names = self._names()
        return {"rev": rev, "now": now_ms(), "total": total, "matches": matches, "newest": newest,
                "items": [self._item(r, names) for r in rows]}

    def get(self, item_id, me=""):
        """One item with all its text, if this device may see it."""
        with self.lock:
            r = self.db.execute(f"SELECT {COLUMNS}, text FROM items WHERE id = ? AND {VISIBLE}",
                                [item_id] + self._me(me)).fetchone()
        if not r:
            return None
        item = self._item(r[:-1], self._names())
        item["text"], item["big"] = r[-1], False
        return item

    def get_file(self, item_id, key):
        with self.lock:
            r = self.db.execute("SELECT file_name, file_type, file FROM items WHERE id = ? AND file_key = ?",
                                (item_id, key)).fetchone()
        return r

    def events(self, me, after):
        """For the app on a device: what others shared for it since `after` (the newest id it has seen)."""
        params = self._me(me)
        with self.lock:
            newest = self.db.execute(f"SELECT COALESCE(MAX(id), 0) FROM items WHERE {VISIBLE}", params).fetchone()[0]
            rows = [] if after is None else self.db.execute(
                f"SELECT {COLUMNS} FROM items WHERE id > ? AND {VISIBLE} AND COALESCE(sender, '') != ? "
                "ORDER BY id DESC LIMIT 20", [after] + params + [params[0]]).fetchall()
        names = self._names()
        items = [self._item(r, names) for r in rows]
        for item in items:
            item["text"] = item["text"][:400]
        return {"newest": newest, "items": items}

    def add(self, text, device, sender="", to=None, file=None):
        """Keeps a new item. The same text again (no file), right after itself, is not kept twice."""
        sender = sender if DEVICE_ID.fullmatch(sender or "") else None
        recipients = json.dumps(sorted(set(to))) if to else None
        with self.lock:
            if not file:
                same = self.db.execute(
                    "SELECT id FROM items WHERE id = (SELECT MAX(id) FROM items) AND text = ? AND file_key IS NULL "
                    "AND recipients IS ? AND sender IS ?", (text, recipients, sender)).fetchone()
                if same:
                    return self._summary(same[0]), True
            fname, ftype, fdata = file if file else (None, None, None)
            cur = self.db.execute(
                "INSERT INTO items (at, device, text, sender, recipients, file_name, file_type, file_size, file_key, "
                "file) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (now_ms(), device, text, sender, recipients, fname, ftype, len(fdata) if file else None,
                 secrets.token_urlsafe(12) if file else None, fdata))
            self.db.commit()
            self.changes += 1
            return self._summary(cur.lastrowid), False

    def _summary(self, item_id):
        r = self.db.execute(f"SELECT id, at, device, length(text), {LINES}, file_name, file_size FROM items "
                            "WHERE id = ?", (item_id,)).fetchone()
        return {"id": r[0], "at": r[1], "device": r[2], "size": r[3], "lines": r[4] if r[3] else 0,
                "file": {"name": r[5], "size": r[6]} if r[5] else None}

    def delete(self, item_id, me=""):
        with self.lock:
            gone = self.db.execute(f"DELETE FROM items WHERE id = ? AND {VISIBLE}",
                                   [item_id] + self._me(me)).rowcount
            self.db.commit()
            if gone:
                self.changes += 1
            return gone > 0

    def close(self):
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
            problem = "Text Share only answers computers on its own network."
        elif not host_ok(self.headers.get("Host", "")):
            problem = "Open Text Share by the server's IP address or computer name, e.g. http://192.168.1.20:8765"
        elif api and self.headers.get("X-TextShare") != "1":
            problem = "This address is for the Text Share page only."
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
        elif path == "/download":     # this very program, to run Text Share on another computer
            ctype = "application/vnd.microsoft.portable-executable" if FROZEN else "text/x-python; charset=utf-8"
            self.reply(200, APP_FILE.read_bytes(), ctype,
                       [("Content-Disposition", f'attachment; filename="{APP_FILE.name}"')])
        elif m := re.fullmatch(r"/files/(\d+)/([A-Za-z0-9_-]+)(?:/[^/]*)?", path):
            found = store.get_file(int(m[1]), m[2])
            if not found:
                return self.fail(404, "That file is no longer in the history.", False)
            name, ctype, data = found
            how = "attachment" if qs.get("download") else "inline"
            self.reply(200, data, ctype, [
                ("Content-Disposition", f"{how}; filename*=UTF-8''{quote(name)}"),
                ("Cache-Control", "private, max-age=31536000, immutable")])
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
                             "addresses": addresses, "id": me})
        elif path == "/api/items":
            me = self.who()
            q = qs.get("q", [""])[0].strip()[:200]
            rev = qs.get("rev", [""])[0]
            if rev and rev == store.rev:
                self.reply(200, {"same": True, "rev": rev, "now": now_ms()})
            else:
                self.reply(200, store.list(q, me))
        elif m := re.fullmatch(r"/api/items/(\d+)", path):
            item = store.get(int(m[1]), self.who())
            if item:
                self.reply(200, item)
            else:
                self.fail(404, "That is no longer in the history.")
        elif path == "/api/devices":
            self.reply(200, store.devices(self.who()))
        elif path == "/api/events":
            me = self.who()
            if not me:
                return self.fail(400, "Say which device is asking.")
            after = qs.get("after", [""])[0]
            self.reply(200, store.events(me, int(after) if after.isdigit() else None))
        else:
            self.fail(404, "Not found.", api)

    @guarded
    def do_POST(self):
        if not self.gate(True):
            return
        if urlsplit(self.path).path != "/api/items":
            self.close_connection = True
            return self.fail(404, "Not found.")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self.close_connection = True
            return self.fail(411, "Length required.")
        if length > MAX_BODY:
            self.close_connection = True
            return self.fail(413, f"That is too big to share (more than {MAX_BODY // 1048576} MB).")
        me = self.who()
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            text, device, to, file = data.get("text") or "", data.get("device"), data.get("to"), data.get("file")
        except (ValueError, AttributeError):
            return self.fail(400, "That was not a proper share.")
        if not isinstance(text, str):
            return self.fail(400, "That was not a proper share.")
        upload = None
        if file:
            try:
                raw = base64.b64decode(str(file["data"]), validate=True)
            except (TypeError, KeyError, ValueError, binascii.Error):
                return self.fail(400, "The file did not arrive whole. Please try again.")
            if len(raw) > FILE_MAX:
                return self.fail(413, f"That file is {len(raw) / 1048576:.2f} MB. Photos and PDFs can be up to 1 MB.")
            mime = sniff(raw)
            if not mime:
                return self.fail(415, "Only photos (PNG, JPEG, GIF, WebP) and PDF files can be shared.")
            upload = (clean_file_name(file.get("name"), mime), mime, raw)
        if not text.strip() and not upload:
            return self.fail(400, "There is no text to share.")
        text = text.encode("utf-8", "replace").decode("utf-8")
        device = (device if isinstance(device, str) else "").encode("utf-8", "replace").decode("utf-8")
        device = device.strip()[:60] or self.client_address[0]
        to = [d for d in to if isinstance(d, str) and DEVICE_ID.fullmatch(d)][:50] if isinstance(to, list) else []
        if to and me:
            to = [d for d in to if d != me]
        item, duplicate = self.server.store.add(text, device, me, to or None, upload)
        if not duplicate:
            what = []
            if item["size"]:
                what.append(f"{item['lines']} line{'' if item['lines'] == 1 else 's'} ({item['size']:,} characters)")
            if upload:
                what.append(("a photo" if upload[1].startswith("image/") else "a PDF") + f" ({len(upload[2]) // 1024} KB)")
            log(f"{device} shared {' and '.join(what)}" + (f", for {len(to)} device{'s' if len(to) > 1 else ''}"
                                                           if to else ""))
        self.reply(200 if duplicate else 201, {"item": item, "duplicate": duplicate})

    @guarded
    def do_DELETE(self):
        if not self.gate(True):
            return
        m = re.fullmatch(r"/api/items/(\d+)", urlsplit(self.path).path)
        if not m:
            return self.fail(404, "Not found.")
        if self.server.store.delete(int(m[1]), self.who()):
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


DISCOVERY_PORT = 8765            # UDP: the apps on the other computers ask here where Text Share is
ASK = b"TEXTSHARE?"


class Beacon(threading.Thread):
    """Answers the Text Share apps that look for this computer on the network, with its name and port."""

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
            except OSError:                         # closed: Text Share is stopping
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
    """The computers on this network running Text Share: [{name, address, port, version}], by name."""
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
    """True when Text Share already answers on this port of this computer."""
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
    ap = argparse.ArgumentParser(prog=APP_FILE.name, description=f"Text Share {VERSION} as a plain server: pass "
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
            print(f"Text Share is already running on this computer: {url}")
            if not args.no_open:
                webbrowser.open(url)
            return 0
        print(f"Port {args.port} is used by another program. Start Text Share on another port, e.g.  --port {args.port + 1}")
        return 1

    try:
        beacon = Beacon(args.port)
        beacon.start()
    except OSError:
        beacon = None               # another program has the discovery port: apps must be given the address
    name = socket.gethostname()
    set_title(f"Text Share {VERSION}  -  http://{name}:{args.port}/")
    print(f"\n  Text Share {VERSION} is running on {name}.\n")
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
                                "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; "
                                "frame-ancestors 'none'"),
    ("X-Frame-Options", "DENY"),
]

PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Text Share</title>
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
.btn .icon { width: 16px; height: 16px }

.attach { display: flex; align-items: center; gap: 10px; padding: 6px 6px 6px 6px; background: var(--surface); border: 1px solid var(--line); border-radius: 10px }
.attach img { width: 44px; height: 44px; object-fit: cover; border-radius: 6px; border: 1px solid var(--line); flex: none }
.attach .icon-btn { margin-inline-start: auto; flex: none }
.att-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0 }
.pdf-badge { display: inline-grid; place-items: center; width: 44px; height: 44px; flex: none; border-radius: 6px; background: var(--danger-fill); color: #fff; font-size: 11px; font-weight: 700; letter-spacing: .04em }
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

.to { color: var(--accent); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; max-width: 16em }
.file { padding: 8px 12px 4px }
.file + pre { padding-top: 4px }
.file img.thumb { display: block; max-width: 100%; max-height: 200px; border-radius: 8px; border: 1px solid var(--line); background: var(--surface-2); cursor: zoom-in }
.file a.pdf { display: inline-flex; align-items: center; gap: 10px; max-width: 100%; padding: 5px 12px 5px 5px; color: var(--text); text-decoration: none; background: var(--surface-2); border: 1px solid var(--line); border-radius: 8px }
.file a.pdf:hover { border-color: var(--accent) }
.file a.pdf .pdf-badge { width: 34px; height: 34px; font-size: 10px }
.file a.pdf .nm { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0 }
.file a.pdf small { color: var(--dim); white-space: nowrap }
.file:not([hidden]) ~ .more { margin-top: 6px }
.lightbox { position: fixed; inset: 0; z-index: 50; display: grid; place-items: center; background: rgba(0, 0, 0, .82); cursor: zoom-out }
.lightbox img { max-width: 94vw; max-height: 92vh; border-radius: 6px; box-shadow: 0 20px 60px rgba(0, 0, 0, .6) }

@media (max-width: 860px) {
  html, body { height: auto }
  main { grid-template-columns: 1fr }
  #editor { min-height: 38vh }
  .scroll { overflow: visible }
  .top { flex-wrap: wrap; row-gap: 2px }
  .status { order: 3; width: 100% }
  .me .lbl, .meta { display: none }
}
@media (prefers-reduced-motion: reduce) { .item.fresh { animation: none } #toast, .btn, .item { transition: none } }
</style>
</head>
<body>
<header class="top">
  <div class="brand">
    <svg width="20" height="20" viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="8" fill="#5b2c83"/><path d="M7 12h17l-4-4M25 20H8l4 4" fill="none" stroke="#f0c33c" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></svg>
    Text Share
  </div>
  <div id="status" class="status"><i></i><span>Connecting…</span></div>
  <button id="me" class="me" type="button" title="Rename this device"><span class="lbl">This device: </span><b id="meName">…</b> ✎</button>
  <button id="theme" class="icon-btn" type="button"></button>
</header>

<main>
  <section class="pane" id="compose" aria-label="Editor">
    <div class="bar">
      <h2>Write or paste</h2><span class="grow"></span>
      <label class="chk"><input id="wrap" type="checkbox" checked> Wrap long lines</label>
    </div>
    <textarea id="editor" spellcheck="false" dir="auto" autocomplete="off"
      placeholder="Write or paste text here, or drop a photo or a PDF, then press Share (Ctrl+Enter)."></textarea>
    <div id="attach" class="attach" hidden>
      <img id="attThumb" alt="" hidden><span id="attPdf" class="pdf-badge" hidden>PDF</span>
      <span class="att-name" id="attName"></span><span class="muted" id="attSize"></span>
      <button id="attDel" class="icon-btn" type="button" title="Remove the file">✕</button>
    </div>
    <div class="bar">
      <button id="attachBtn" class="btn" type="button" title="Add a photo or a PDF, up to 1 MB">
        <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20.5 11.5l-8.4 8.4a5 5 0 0 1-7.1-7.1l8.8-8.8a3.4 3.4 0 0 1 4.8 4.8l-8.8 8.8a1.7 1.7 0 0 1-2.4-2.4l8.1-8.1"/></svg>File</button>
      <input id="fileInput" type="file" accept="image/png,image/jpeg,image/gif,image/webp,application/pdf" hidden>
      <span id="count" class="muted"></span><span class="grow"></span>
      <button id="clear" class="btn" type="button">Clear</button>
      <button id="copyEd" class="btn" type="button">Copy</button>
      <div class="to-wrap">
        <button id="to" class="btn" type="button" aria-haspopup="true" aria-expanded="false" title="Who should see it">To: <span id="toLabel">Everyone</span> ▾</button>
        <div id="toPop" class="pop" hidden>
          <div class="pop-head">Who should see it?</div>
          <label class="pop-row"><input type="checkbox" id="toAll"><span class="nm">Everyone</span><small>every device, now and later</small></label>
          <div id="toList"></div>
          <p class="pop-note" id="toNote"></p>
        </div>
      </div>
      <button id="share" class="btn primary" type="button">Share<kbd>Ctrl+Enter</kbd></button>
    </div>
  </section>

  <section class="pane" aria-label="History">
    <div class="bar">
      <h2>History</h2><span id="total" class="muted"></span><span class="grow"></span>
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
  <span>Text Share __VERSION__ · SCAFFApps</span>
  <a href="/download" title="Download Text Share, to run it on another computer">Get the app</a>
  <a id="setup" href="#" hidden title="Which computer keeps the history">Change setup</a>
</footer>
<div id="lightbox" class="lightbox" hidden><img alt=""></div>
<div id="toast" role="status" aria-live="polite"></div>

<script>
"use strict";
const $ = (s) => document.querySelector(s);
const editor = $("#editor"), listEl = $("#list"), search = $("#search"), shareBtn = $("#share");
const saved = {
  get(k, d) { try { const v = localStorage.getItem("textshare." + k); return v === null ? d : v; } catch { return d; } },
  set(k, v) { try { localStorage.setItem("textshare." + k, v); } catch {} },
};
const params = new URLSearchParams(location.search);
const KIND = params.get("app") === "1" ? "app" : "browser";       // the Windows app says so in the address
const ACCEPT = ["image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf"];
const FILE_MAX = 1024 * 1024;

function newId() {
  const a = new Uint8Array(12);
  crypto.getRandomValues(a);
  return Array.from(a, (b) => b.toString(16).padStart(2, "0")).join("");
}
let deviceId = params.get("id") || saved.get("id", "");
if (!/^[A-Za-z0-9_-]{8,64}$/.test(deviceId)) deviceId = newId();
saved.set("id", deviceId);
let device = saved.get("device", "");
let serverName = "the server", baseTitle = "Text Share";
let rev = "", revQuery = null, newest = null, skew = 0, unseen = 0;
let pollTimer = 0, pollSeq = 0, isOnline = null, statusMsg = "";
let devices = [];                 // every device the server knows, for "Who should see it"
let targets = new Set();          // the devices chosen; none = everyone
try { targets = new Set(JSON.parse(saved.get("to", "[]"))); } catch {}
let attachment = null;            // {name, type, size, data (base64), url}
const nodes = new Map();          // item id -> its card (items never change, so a card is made once)
const app = () => (window.pywebview && window.pywebview.api) || null;

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

async function hello() {
  try {
    const h = await api("/api/hello");
    serverName = h.server;
    baseTitle = document.title = "Text Share · " + h.server;
    $("#where").textContent = h.server;
    const a = h.addresses || [];
    $("#others").textContent = a.length ? "Phones and browsers: " + a.slice(0, 2).map((u) => u.replace(/\/$/, "")).join("  or  ") : "";
    if (!device) {    // the app says which computer this is; a browser goes by the network's name for it
      device = params.get("me") || h.name || h.you || "this device";
      saved.set("device", device);
    }
  } catch { setTimeout(hello, 3000); }
  showMe();
}

async function poll() {
  clearTimeout(pollTimer);
  const my = ++pollSeq, q = search.value.trim();
  const query = new URLSearchParams();
  if (q) query.set("q", q);
  if (rev && revQuery === q) query.set("rev", rev);
  try {
    const data = await api("/api/items?" + query);
    if (my !== pollSeq) return;
    setOnline(true);
    skew = data.now - Date.now();
    if (!data.same) { rev = data.rev; revQuery = q; take(data); }
  } catch (e) {
    if (my !== pollSeq) return;
    setOnline(false, unreachable(e) ? "" : e.message);
  }
  pollTimer = setTimeout(poll, document.hidden ? 5000 : 1500);
}

function setOnline(on, message) { isOnline = on; statusMsg = message || ""; drawStatus(); }
function drawStatus() {
  const s = $("#status");
  s.className = "status " + (isOnline ? "on" : isOnline === false ? "off" : "");
  const others = devices.filter((d) => d.online && d.id !== deviceId).length;
  s.querySelector("span").textContent = isOnline
    ? "Connected to " + serverName + (others ? ` · ${others} other device${others === 1 ? "" : "s"} online` : "")
    : statusMsg || "Can't reach " + serverName + ". Is Text Share running there? Retrying…";
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
    : "No other devices yet. They appear here while Text Share runs on them.";
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
document.addEventListener("click", (e) => { if (!e.target.closest(".to-wrap")) closePop(); });

/* ---------------- the history ---------------- */

function take(data) {
  const fresh = new Set();
  let fromOthers = 0;
  if (newest !== null) for (const it of data.items) if (it.id > newest) {
    fresh.add(it.id);
    if (it.from !== deviceId) fromOthers++;
  }
  newest = data.newest;
  if (fromOthers && (document.hidden || !document.hasFocus())) { unseen += fromOthers; showTitle(); }
  render(data.items, fresh);

  $("#total").textContent = revQuery ? `${data.matches} of ${data.total} match` : data.total ? `${data.total} shared` : "";
  const empty = $("#empty");
  empty.hidden = data.items.length > 0;
  empty.textContent = revQuery ? "Nothing in the history matches."
    : "Nothing shared yet. Write or paste something on the left, or add a photo or a PDF, and press Share.";
  const limit = $("#limit");
  limit.hidden = data.matches <= data.items.length;
  limit.textContent = `Showing the newest ${data.items.length}. Search to find older ones.`;
}

function render(items, fresh) {
  const keep = new Set(items.map((it) => it.id));
  for (const [id, node] of nodes) if (!keep.has(id)) { node.remove(); nodes.delete(id); }
  let prev = null;
  const added = [];
  for (const it of items) {
    let node = nodes.get(it.id);
    if (!node) {
      node = card(it); nodes.set(it.id, node); added.push(node);
      if (fresh.has(it.id)) node.classList.add("fresh");
    }
    const want = prev ? prev.nextElementSibling : listEl.firstElementChild;
    if (node !== want) listEl.insertBefore(node, want);
    prev = node;
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

function card(it) {
  const node = document.createElement("article");
  node.className = "item";
  node.innerHTML = '<div class="head"><span class="who"></span><span class="to" hidden></span><time></time>'
    + '<span class="meta"></span><span class="acts"></span></div><div class="file" hidden></div>'
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
  const meta = [];
  if (it.size) meta.push(plural(it.lines, "line"), plural(it.size, "character"));
  if (it.file) meta.push(size(it.file.size));
  node.querySelector(".meta").textContent = "· " + meta.join(" · ");

  const acts = node.querySelector(".acts");
  if (it.size) acts.append(button("copy", "Copy"));
  if (it.file) {
    const image = it.file.type.startsWith("image/");
    const box = node.querySelector(".file");
    box.hidden = false;
    if (image) {
      const img = document.createElement("img");
      img.className = "thumb"; img.loading = "lazy"; img.src = it.file.url; img.alt = it.file.name; img.title = it.file.name;
      box.append(img);
      if (KIND === "app") acts.append(button("copyimg", it.size ? "Copy photo" : "Copy", "Copy the photo, ready to paste"));
    } else {
      const a = document.createElement("a");
      a.className = "pdf"; a.href = it.file.url; a.target = "_blank"; a.rel = "noopener"; a.title = "Open " + it.file.name;
      a.innerHTML = '<span class="pdf-badge">PDF</span><span class="nm"></span><small></small>';
      a.querySelector(".nm").textContent = it.file.name;
      a.querySelector("small").textContent = size(it.file.size);
      box.append(a);
    }
    if (KIND === "app") acts.append(button("save", "Save", "Save it in Downloads"));
    else {
      const a = document.createElement("a");
      a.className = "btn sm"; a.textContent = "Save"; a.href = it.file.url + "?download=1"; a.download = it.file.name;
      a.title = "Download it";
      acts.append(a);
    }
  }
  if (it.size) acts.append(button("edit", "Edit", "Open it in the editor"));
  acts.append(button("del", "✕", "Delete it from the history"));
  const pre = node.querySelector("pre");
  if (it.size) pre.textContent = it.big ? it.text + "\n…" : it.text; else pre.hidden = true;
  node._item = it;
  return node;
}

function checkMore(node) {
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
  const btn = e.target.closest("button");
  if (!btn) return;
  const node = btn.closest(".item"), it = node._item;
  try {
    if (btn.classList.contains("copy")) {
      const text = await fullText(node);
      if (await copyText(text)) done(btn, "Copied ✓");
      else if (putInEditor(text)) { editor.select(); toast("The browser blocked the copy. It is in the editor, selected: press Ctrl+C.", true); }
    } else if (btn.classList.contains("copyimg")) {
      const r = await app().copy_image(location.origin + it.file.url);
      if (r && r.ok) done(btn, "Copied ✓"); else toast((r && r.error) || "Could not copy the photo.", true);
    } else if (btn.classList.contains("save")) {
      const r = await app().save_file(location.origin + it.file.url, it.file.name);
      if (r && r.ok) { done(btn, "Saved ✓"); toast("Saved in Downloads: " + r.name); } else toast((r && r.error) || "Could not save it.", true);
    } else if (btn.classList.contains("edit")) {
      if (putInEditor(await fullText(node))) { editor.focus(); editor.setSelectionRange(0, 0); editor.scrollTop = 0; toast("It is in the editor. Change it and Share it again."); }
    } else if (btn.classList.contains("del")) {
      arm(btn, "Delete?", async () => {
        try {
          await api("/api/items/" + it.id, { method: "DELETE" });
        } catch (err) {
          if (err.http !== 404) return toast("Not deleted: " + (unreachable(err) ? "can't reach the server." : err.message), true);
        }
        node.remove(); nodes.delete(it.id); poll();
      });
    } else if (btn.classList.contains("more")) {
      const open = node.classList.toggle("open");
      if (open && it.big) node.querySelector("pre").textContent = await fullText(node);
      btn.textContent = open ? "Show less" : "Show all";
      if (!open) node.scrollIntoView({ block: "nearest" });
    }
  } catch (err) {
    toast("Could not get it from the server: " + (unreachable(err) ? "can't reach it." : err.message), true);
  }
});

function openLightbox(src, alt) {
  const lb = $("#lightbox"), img = lb.querySelector("img");
  img.src = src; img.alt = alt; lb.hidden = false;
}
$("#lightbox").addEventListener("click", () => { $("#lightbox").hidden = true; });

/* ---------------- the editor and its file ---------------- */

function setEditor(text) { editor.value = text; saved.set("draft", text); updateCount(); }

function putInEditor(text) {
  if (editor.value.trim() && editor.value !== text && !confirm("Replace what is in the editor?")) return false;
  setEditor(text);
  return true;
}

function updateCount() {
  const v = editor.value;
  $("#count").textContent = v ? plural(v.split("\n").length, "line") + " · " + plural(v.length, "character") : "";
}

function attachFile(file) {
  if (!file) return;
  if (!ACCEPT.includes(file.type)) return toast("Only photos (PNG, JPEG, GIF, WebP) and PDF files can be shared.", true);
  if (file.size > FILE_MAX) return toast(`That file is ${size(file.size)}. Photos and PDFs can be up to 1 MB.`, true);
  const reader = new FileReader();
  reader.onload = () => {
    const url = String(reader.result);
    attachment = { name: file.name || (file.type === "application/pdf" ? "document.pdf" : "pasted image.png"),
                   type: file.type, size: file.size, data: url.slice(url.indexOf(",") + 1), url };
    showAttachment();
  };
  reader.onerror = () => toast("Could not read that file.", true);
  reader.readAsDataURL(file);
}
function showAttachment() {
  $("#attach").hidden = !attachment;
  if (!attachment) return;
  const image = attachment.type.startsWith("image/");
  $("#attThumb").hidden = !image;
  if (image) $("#attThumb").src = attachment.url;
  $("#attPdf").hidden = image;
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
    toast("Not shared: " + (unreachable(e) ? "can't reach the server. Your text is still here." : e.message), true);
  } finally {
    shareBtn.disabled = false;
    editor.focus();
  }
}

let draftTimer = 0;
editor.addEventListener("input", () => {
  updateCount();
  clearTimeout(draftTimer);
  draftTimer = setTimeout(() => saved.set("draft", editor.value), 300);
});
shareBtn.addEventListener("click", share);
$("#copyEd").addEventListener("click", async (e) => {
  if (!editor.value) return toast("The editor is empty.");
  if (await copyText(editor.value)) done(e.currentTarget, "Copied ✓");
  else { editor.select(); toast("The browser blocked the copy: the text is selected, press Ctrl+C.", true); }
});
$("#clear").addEventListener("click", (e) => {
  if (editor.value || attachment) arm(e.currentTarget, "Clear?", () => { setEditor(""); attachment = null; showAttachment(); editor.focus(); });
});
const wrap = $("#wrap");
function applyWrap() { editor.wrap = wrap.checked ? "soft" : "off"; editor.setAttribute("wrap", editor.wrap); saved.set("wrap", wrap.checked ? "1" : "0"); }
wrap.checked = saved.get("wrap", "1") === "1";
wrap.addEventListener("change", applyWrap);

document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && !e.altKey && (e.key === "Enter" || e.key === "s" || e.key === "S")) { e.preventDefault(); share(); }
  if (e.key === "Escape") { closePop(); $("#lightbox").hidden = true; }
});

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

function arm(btn, label, action) {
  if (btn.dataset.armed) { clearTimeout(btn._t); disarm(btn); action(); return; }
  btn.dataset.armed = "1"; btn._label = btn.innerHTML; btn.textContent = label; btn.classList.add("danger");
  btn._t = setTimeout(() => disarm(btn), 3000);
}
function disarm(btn) {
  if (!btn.dataset.armed) return;
  delete btn.dataset.armed; btn.innerHTML = btn._label; btn.classList.remove("danger");
}
function done(btn, label) {
  clearTimeout(btn._d);
  if (btn._was == null) btn._was = btn.innerHTML;
  btn.textContent = label; btn.classList.add("done");
  btn._d = setTimeout(() => { btn.innerHTML = btn._was; btn._was = null; btn.classList.remove("done"); }, 1300);
}

let toastTimer = 0;
function toast(message, bad) {
  const t = $("#toast");
  t.textContent = message; t.classList.toggle("bad", !!bad); t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), bad ? 5000 : 2600);
}

function plural(n, word) { return n.toLocaleString() + " " + word + (n === 1 ? "" : "s"); }
function size(bytes) { return bytes < 1024 ? bytes + " bytes" : bytes < 1048576 ? Math.round(bytes / 1024) + " KB" : (bytes / 1048576).toFixed(bytes < 10485760 ? 2 : 1) + " MB"; }

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

$("#me").addEventListener("click", () => {
  const v = prompt("Name this device. The name shows next to everything it shares, and in other devices' lists.", device);
  if (v === null || !v.trim()) return;
  device = v.trim().slice(0, 40); saved.set("device", device); showMe();
  if (app()) app().set_name(device);
});

let searchTimer = 0;
search.addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(poll, 250); });
document.addEventListener("visibilitychange", () => { if (!document.hidden) { unseen = 0; showTitle(); poll(); } });
window.addEventListener("focus", () => { unseen = 0; showTitle(); });
let resizeTimer = 0;
window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => nodes.forEach(checkMore), 200); });
setInterval(() => document.querySelectorAll("#list time").forEach((t) => { t.textContent = ago(+t.dataset.at); }), 30000);

/* ---------------- inside the Windows app ---------------- */

function inApp() {
  const link = $("#setup");
  if (!app() || !link.hidden) return;
  link.hidden = false;
  link.addEventListener("click", (e) => { e.preventDefault(); app().open_setup(); });
  app().set_name(device);
  tellApp();
}
window.addEventListener("pywebviewready", inApp);

/* ---------------- appearance: dark (the default) or light, as in Multi Screens ---------------- */

const ICONS = {
  sun: '<circle cx="12" cy="12" r="3.8"/><path d="M12 3v2.2M12 18.8V21M3 12h2.2M18.8 12H21M5.6 5.6l1.6 1.6M16.8 16.8l1.6 1.6M5.6 18.4l1.6-1.6M16.8 7.2l1.6-1.6"/>',
  moon: '<path d="M19.5 14.2A8 8 0 0 1 9.8 4.5a7.6 7.6 0 1 0 9.7 9.7z"/>',
};
const icon = (name) => '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" '
  + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + ICONS[name] + "</svg>";
function tellApp() {               // the app's title bar and its own pages follow the page
  try { if (app()) app().set_theme(document.documentElement.dataset.theme); } catch {}
}
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const b = $("#theme");
  b.innerHTML = icon(theme === "dark" ? "sun" : "moon");
  b.title = theme === "dark" ? "Switch to light appearance" : "Switch to dark appearance";
  saved.set("theme", theme);
}
applyTheme(document.documentElement.dataset.theme);
$("#theme").addEventListener("click", () => {
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  tellApp();
});

editor.value = saved.get("draft", "");
applyWrap();
updateCount();
showMe();
showTargets();
hello().finally(() => { poll(); loadDevices(); });
setInterval(loadDevices, 5000);
inApp();
</script>
</body>
</html>
"""

PAGE = PAGE.replace("__VERSION__", VERSION)

if __name__ == "__main__":
    sys.exit(run())
