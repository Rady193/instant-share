"""Text Share: pass text between the computers on your own network.

Run it on a computer that stays on and open its address in a browser on any computer on the same network. Write or
paste text, press Share, and every other computer has it at once, kept in a history you can search, copy from and
delete from. No internet, no accounts. Python standard library only; build.py packs it into one portable .exe.

    Text-Share.exe  (or  py textshare.py)   start it and open it in the browser; close the window to stop it
        --no-open                           start it without opening the browser
        --port 9000                         use another port (default 8765)
        --autostart on                      start it whenever you sign in to Windows ("off" undoes it)

Copyright (c) 2026 SCAFFApps. All rights reserved. See LICENSE.
"""

import argparse
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
from urllib.parse import parse_qs, urlsplit

VERSION = "1.0.0"
FROZEN = getattr(sys, "frozen", False)                       # running as the packed .exe
APP_FILE = Path(sys.executable if FROZEN else __file__).resolve()
DEFAULT_PORT = 8765
MAX_BODY = 20 * 1024 * 1024      # biggest share accepted, in bytes
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


class Store:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS items (
            id     INTEGER PRIMARY KEY AUTOINCREMENT,   -- AUTOINCREMENT: a deleted id is never handed out again
            at     INTEGER NOT NULL,                    -- when it was shared, ms since 1970 (UTC)
            device TEXT NOT NULL,                       -- the name of the computer that shared it
            text   TEXT NOT NULL)""")
        self.db.commit()
        self.lock = threading.Lock()
        self.boot = secrets.token_hex(4)    # a restart changes the revision, so every page reloads its list
        self.changes = 0

    @property
    def rev(self):
        return f"{self.boot}.{self.changes}"

    @staticmethod
    def _item(r):
        item_id, at, device, size, lines, text = r
        return {"id": item_id, "at": at, "device": device, "size": size, "lines": lines, "text": text,
                "big": size > len(text)}

    def list(self, q=""):
        where, params = "", []
        if q:
            where = "WHERE instr(lower(text), lower(?)) > 0 OR instr(lower(device), lower(?)) > 0"
            params = [q, q]
        with self.lock:
            total, newest = self.db.execute("SELECT COUNT(*), COALESCE(MAX(id), 0) FROM items").fetchone()
            matches = self.db.execute(f"SELECT COUNT(*) FROM items {where}", params).fetchone()[0] if q else total
            rows = self.db.execute(
                f"SELECT id, at, device, length(text), {LINES}, "
                f"CASE WHEN length(text) <= ? THEN text ELSE substr(text, 1, ?) END "
                f"FROM items {where} ORDER BY id DESC LIMIT ?",
                [FULL_LIMIT, PREVIEW, *params, LIST_LIMIT]).fetchall()
            rev = self.rev
        return {"rev": rev, "now": now_ms(), "total": total, "matches": matches, "newest": newest,
                "items": [self._item(r) for r in rows]}

    def get(self, item_id, full=True):
        with self.lock:
            return self._get(item_id, full)

    def _get(self, item_id, full=True):
        r = self.db.execute(f"SELECT id, at, device, length(text), {LINES}, text FROM items WHERE id = ?",
                            (item_id,)).fetchone()
        if not r:
            return None
        item = self._item(r)
        if not full:
            del item["text"], item["big"]
        return item

    def add(self, text, device):
        """Keeps a new item; if it is the same text as the newest one, keeps nothing and says so."""
        with self.lock:
            same = self.db.execute("SELECT id FROM items WHERE id = (SELECT MAX(id) FROM items) AND text = ?",
                                   (text,)).fetchone()
            if same:
                return self._get(same[0], full=False), True
            cur = self.db.execute("INSERT INTO items (at, device, text) VALUES (?, ?, ?)", (now_ms(), device, text))
            self.db.commit()
            self.changes += 1
            return self._get(cur.lastrowid, full=False), False

    def delete(self, item_id):
        with self.lock:
            gone = self.db.execute("DELETE FROM items WHERE id = ?", (item_id,)).rowcount
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

    @guarded
    def do_GET(self):
        url = urlsplit(self.path)
        path, api = url.path, url.path.startswith("/api/")
        if not self.gate(api):
            return
        store = self.server.store
        if path == "/":
            self.reply(200, PAGE, "text/html; charset=utf-8", PAGE_HEADERS)
        elif path == "/download":     # this very program, to run Text Share on another computer
            ctype = "application/vnd.microsoft.portable-executable" if FROZEN else "text/x-python; charset=utf-8"
            self.reply(200, APP_FILE.read_bytes(), ctype,
                       [("Content-Disposition", f'attachment; filename="{APP_FILE.name}"')])
        elif path == "/api/hello":
            ip = ipaddress.ip_address(self.client_address[0])
            if ip.version == 6 and ip.ipv4_mapped:
                ip = ip.ipv4_mapped
            ip = str(ip).split("%")[0]
            name = socket.gethostname() if ipaddress.ip_address(ip).is_loopback else guess_name(ip)
            self.reply(200, {"server": socket.gethostname(), "version": VERSION, "you": ip, "name": name})
        elif path == "/api/items":
            qs = parse_qs(url.query)
            q = qs.get("q", [""])[0].strip()[:200]
            rev = qs.get("rev", [""])[0]
            if rev and rev == store.rev:
                self.reply(200, {"same": True, "rev": rev, "now": now_ms()})
            else:
                self.reply(200, store.list(q))
        elif m := re.fullmatch(r"/api/items/(\d+)", path):
            item = store.get(int(m[1]))
            if item:
                self.reply(200, item)
            else:
                self.fail(404, "That is no longer in the history.")
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
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            text, device = data.get("text"), data.get("device")
        except (ValueError, AttributeError):
            return self.fail(400, "That was not a proper share.")
        if not isinstance(text, str) or not text.strip():
            return self.fail(400, "There is no text to share.")
        text = text.encode("utf-8", "replace").decode("utf-8")
        device = (device if isinstance(device, str) else "").encode("utf-8", "replace").decode("utf-8")
        device = device.strip()[:60] or self.client_address[0]
        item, duplicate = self.server.store.add(text, device)
        if not duplicate:
            lines = item["lines"]
            log(f"{device} shared {lines} line{'' if lines == 1 else 's'} ({item['size']:,} characters)")
        self.reply(200 if duplicate else 201, {"item": item, "duplicate": duplicate})

    @guarded
    def do_DELETE(self):
        if not self.gate(True):
            return
        m = re.fullmatch(r"/api/items/(\d+)", urlsplit(self.path).path)
        if not m:
            return self.fail(404, "Not found.")
        if self.server.store.delete(int(m[1])):
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


def autostart(args):
    if os.name != "nt":
        print("--autostart works on Windows only.")
        return 1
    target = (Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
              / "Text Share.cmd")
    if args.autostart == "off":
        if target.exists():
            target.unlink()
            print("Text Share will no longer start with Windows.")
        else:
            print("Text Share was not set to start with Windows.")
        return 0
    program = f'"{APP_FILE}"' if FROZEN else f'"{sys.executable}" "{APP_FILE}"'
    extra = " --no-open"
    if args.port != DEFAULT_PORT:
        extra += f" --port {args.port}"
    if args.data != default_data_dir():
        extra += f' --data "{args.data.resolve()}"'
    target.write_text(f'@start "Text Share" /min {program}{extra}\r\n', encoding="oem")
    undo = f'"{APP_FILE.name}" --autostart off' if FROZEN else "py textshare.py --autostart off"
    print(f"Text Share will start (minimised) whenever you sign in to Windows.\n  {target}\n"
          f"If you move or rename {APP_FILE.name}, run this again.  To undo:  {undo}")
    return 0


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
    ap = argparse.ArgumentParser(prog=APP_FILE.name, description=f"Text Share {VERSION}: pass text between the "
                                 "computers on your network. Starts it and opens it in the browser.")
    ap.add_argument("--no-open", action="store_true", help="start it without opening the browser")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port to listen on (default {DEFAULT_PORT})")
    ap.add_argument("--data", type=Path, default=default_data_dir(), help="folder for the history (default %(default)s)")
    ap.add_argument("--autostart", choices=("on", "off"), help="start it whenever you sign in to Windows, or stop that")
    ap.add_argument("--open", action="store_true", help=argparse.SUPPRESS)      # 1.0 spelling; opening is the default
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")    # Arabic names etc. in a window or log that cannot show them
        except (AttributeError, ValueError):
            pass
    if args.autostart:
        return autostart(args)

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
                                "connect-src 'self'; img-src data:; base-uri 'none'; form-action 'none'; "
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
<style>
:root {
  --bg: #150c20; --panel: #1f1430; --panel-2: #2a1b40; --line: rgba(183, 155, 214, .16); --line-2: rgba(183, 155, 214, .34);
  --text: #f1ecf7; --muted: #a596b8; --gold: #f0c33c; --ok: #3ec27f; --bad: #ff6b5e; --ink: #1a1222;
  --mono: "Cascadia Mono", "Cascadia Code", Consolas, ui-monospace, monospace;
  --sans: "Segoe UI", system-ui, -apple-system, sans-serif;
  color-scheme: dark;
}
* { box-sizing: border-box }
[hidden] { display: none !important }
html, body { height: 100% }
body { margin: 0; display: flex; flex-direction: column; background: var(--bg); color: var(--text); font: 14px/1.5 var(--sans) }
button, input, textarea { font: inherit; color: inherit }
:focus-visible { outline: 2px solid var(--gold); outline-offset: 2px }
::selection { background: var(--gold); color: var(--ink) }

.top { display: flex; align-items: center; gap: 18px; padding: 10px 18px; border-bottom: 1px solid var(--line); background: rgba(0, 0, 0, .2) }
.brand { display: flex; align-items: center; gap: 9px; font: 700 15px var(--mono); letter-spacing: -.02em; white-space: nowrap }
.status { display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 13px; min-width: 0 }
.status span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap }
.status i { flex: none; width: 9px; height: 9px; border-radius: 50%; background: var(--muted) }
.status.on i { background: var(--ok); box-shadow: 0 0 0 3px rgba(62, 194, 127, .18) }
.status.off i { background: var(--bad); box-shadow: 0 0 0 3px rgba(255, 107, 94, .18) }
.status.off span { color: var(--bad) }
.me { margin-inline-start: auto; background: none; border: 1px solid transparent; border-radius: 8px; padding: 4px 10px; color: var(--muted); cursor: pointer; font-size: 13px; white-space: nowrap }
.me:hover { border-color: var(--line-2); color: var(--text) }
.me b { color: var(--text); font-weight: 600 }

main { flex: 1; min-height: 0; display: grid; grid-template-columns: minmax(0, 1.15fr) minmax(0, 1fr); gap: 20px; padding: 16px 18px }
.pane { display: flex; flex-direction: column; min-height: 0; gap: 10px }
.bar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; min-height: 34px }
.bar h2 { margin: 0; font: 700 12px var(--mono); text-transform: uppercase; letter-spacing: .1em; color: var(--muted) }
.grow { flex: 1 }
.muted { color: var(--muted); font-size: 13px }
.chk { display: flex; align-items: center; gap: 6px; color: var(--muted); font-size: 13px; cursor: pointer; user-select: none }
#editor { flex: 1; min-height: 220px; width: 100%; resize: none; padding: 14px 16px; background: var(--panel); border: 1px solid var(--line); border-radius: 12px; font: 14px/1.6 var(--mono); tab-size: 4; outline: none }
#editor:focus { border-color: var(--line-2); box-shadow: 0 0 0 3px rgba(240, 195, 60, .12) }
#editor[wrap="off"] { white-space: pre; overflow-x: auto }
#search { width: min(240px, 100%); padding: 6px 10px; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; outline: none }
#search:focus { border-color: var(--line-2) }

.btn { background: var(--panel-2); border: 1px solid var(--line); border-radius: 8px; padding: 6px 12px; cursor: pointer; white-space: nowrap }
.btn:hover { border-color: var(--line-2) }
.btn:disabled { opacity: .55; cursor: default }
.btn.primary { background: var(--gold); border-color: var(--gold); color: var(--ink); font-weight: 700; padding: 7px 16px }
.btn.primary:hover { filter: brightness(1.07) }
.btn.danger { background: var(--bad); border-color: var(--bad); color: var(--ink); font-weight: 700 }
.btn.done { border-color: var(--ok); color: var(--ok) }
.btn.sm { padding: 2px 9px; font-size: 12px; border-radius: 6px }
kbd { font: 11px var(--mono); opacity: .65; margin-inline-start: 6px }

.scroll { flex: 1; min-height: 0; overflow-y: auto; padding-inline-end: 4px }
#list { display: flex; flex-direction: column; gap: 10px }
.item { background: var(--panel); border: 1px solid var(--line); border-radius: 12px; overflow: hidden }
.item.fresh { animation: fresh 2.6s ease-out }
@keyframes fresh { 0%, 35% { border-color: var(--gold); box-shadow: 0 0 0 3px rgba(240, 195, 60, .25) } }
.head { display: flex; align-items: center; gap: 10px; padding: 8px 10px 0 14px; font-size: 12px; color: var(--muted) }
.who { color: var(--text); font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 14em; min-width: 3em }
.head time { white-space: nowrap }
.meta { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0 }
.acts { margin-inline-start: auto; display: flex; gap: 5px; flex: none }
.item pre { margin: 0; padding: 6px 14px 12px; font: 13px/1.55 var(--mono); white-space: pre-wrap; overflow-wrap: anywhere; max-height: calc(1.55em * 8 + 18px); overflow: hidden }
.item.open pre { max-height: none }
.more { display: block; width: 100%; padding: 5px; background: var(--panel-2); border: 0; border-top: 1px solid var(--line); color: var(--muted); font-size: 12px; cursor: pointer }
.more:hover { color: var(--text) }
.empty { color: var(--muted); text-align: center; padding: 40px 20px; border: 1px dashed var(--line-2); border-radius: 12px; margin: 0 }
.note { color: var(--muted); font-size: 12px; text-align: center; margin: 14px 0 4px }

footer { display: flex; gap: 16px; flex-wrap: wrap; padding: 8px 18px 12px; border-top: 1px solid var(--line); color: var(--muted); font-size: 12px }
footer a { color: var(--muted) }
footer a:hover { color: var(--gold) }
#toast { position: fixed; left: 50%; bottom: 26px; transform: translate(-50%, 16px); opacity: 0; transition: opacity .2s, transform .2s; background: #2f2145; border: 1px solid var(--line-2); padding: 10px 16px; border-radius: 10px; box-shadow: 0 10px 30px rgba(0, 0, 0, .45); pointer-events: none; max-width: min(560px, 90vw); text-align: center }
#toast.show { opacity: 1; transform: translate(-50%, 0) }
#toast.bad { border-color: var(--bad); color: #ffd6d1 }

@media (max-width: 860px) {
  html, body { height: auto }
  main { grid-template-columns: 1fr }
  #editor { min-height: 38vh }
  .scroll { overflow: visible }
  .top { flex-wrap: wrap; row-gap: 4px }
  .status { order: 3; width: 100% }
  .me .lbl, .meta { display: none }
}
@media (prefers-reduced-motion: reduce) { .item.fresh { animation: none } #toast { transition: none } }
</style>
</head>
<body>
<header class="top">
  <div class="brand">
    <svg width="22" height="22" viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="8" fill="#5b2c83"/><path d="M7 12h17l-4-4M25 20H8l4 4" fill="none" stroke="#f0c33c" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></svg>
    Text Share
  </div>
  <div id="status" class="status"><i></i><span>Connecting…</span></div>
  <button id="me" class="me" type="button" title="Rename this computer"><span class="lbl">This computer: </span><b id="meName">…</b> ✎</button>
</header>

<main>
  <section class="pane" aria-label="Editor">
    <div class="bar">
      <h2>Write or paste</h2><span class="grow"></span>
      <label class="chk"><input id="wrap" type="checkbox" checked> Wrap long lines</label>
    </div>
    <textarea id="editor" spellcheck="false" dir="auto" autocomplete="off"
      placeholder="Write or paste text here, then press Share (Ctrl+Enter).&#10;Every computer with this page open gets it at once."></textarea>
    <div class="bar">
      <span id="count" class="muted"></span><span class="grow"></span>
      <button id="clear" class="btn" type="button">Clear</button>
      <button id="copyEd" class="btn" type="button">Copy</button>
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
  <span class="grow"></span>
  <span>Text Share __VERSION__ · SCAFFApps</span>
  <a href="/download" title="Download Text Share, to run it on another computer">Get the app file</a>
</footer>
<div id="toast" role="status" aria-live="polite"></div>

<script>
"use strict";
const $ = (s) => document.querySelector(s);
const editor = $("#editor"), listEl = $("#list"), search = $("#search"), shareBtn = $("#share");
const saved = {
  get(k, d) { try { const v = localStorage.getItem("textshare." + k); return v === null ? d : v; } catch { return d; } },
  set(k, v) { try { localStorage.setItem("textshare." + k, v); } catch {} },
};

let device = saved.get("device", "");
let serverName = "the server", baseTitle = "Text Share";
let rev = "", revQuery = null, newest = null, skew = 0, unseen = 0;
let pollTimer = 0, pollSeq = 0;
const nodes = new Map();          // item id -> its card (items never change, so a card is made once)

/* ---------------- talking to the server ---------------- */

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: { "X-TextShare": "1" }, cache: "no-store" };
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
    if (!device) { device = h.name || h.you || "this computer"; saved.set("device", device); }
  } catch { setTimeout(hello, 3000); }
  showMe();
}

async function poll() {
  clearTimeout(pollTimer);
  const my = ++pollSeq, q = search.value.trim();
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (rev && revQuery === q) params.set("rev", rev);
  try {
    const data = await api("/api/items?" + params);
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

function setOnline(on, message) {
  const s = $("#status");
  s.className = "status " + (on ? "on" : "off");
  s.querySelector("span").textContent = on ? "Connected to " + serverName
    : message || "Can't reach " + serverName + ". Is Text Share running there? Retrying…";
}

/* ---------------- the history ---------------- */

function take(data) {
  const fresh = new Set();
  let fromOthers = 0;
  if (newest !== null) for (const it of data.items) if (it.id > newest) {
    fresh.add(it.id);
    if (it.device !== device) fromOthers++;
  }
  newest = data.newest;
  if (fromOthers && (document.hidden || !document.hasFocus())) { unseen += fromOthers; showTitle(); }
  render(data.items, fresh);

  $("#total").textContent = revQuery ? `${data.matches} of ${data.total} match` : data.total ? `${data.total} shared` : "";
  const empty = $("#empty");
  empty.hidden = data.items.length > 0;
  empty.textContent = revQuery ? "Nothing in the history matches."
    : "Nothing shared yet. Write or paste something on the left and press Share.";
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

function card(it) {
  const node = document.createElement("article");
  node.className = "item";
  node.innerHTML = '<div class="head"><span class="who"></span><time></time><span class="meta"></span>'
    + '<span class="acts"><button class="btn sm copy" type="button">Copy</button>'
    + '<button class="btn sm edit" type="button" title="Open it in the editor">Edit</button>'
    + '<button class="btn sm del" type="button" title="Delete it from the history">✕</button></span></div>'
    + '<pre dir="auto"></pre><button class="more" type="button" hidden>Show all</button>';
  node.querySelector(".who").textContent = it.device;
  node.querySelector(".who").title = it.device;
  const t = node.querySelector("time");
  t.dataset.at = it.at;
  t.title = new Date(it.at).toLocaleString();
  t.textContent = ago(it.at);
  node.querySelector(".meta").textContent = "· " + plural(it.lines, "line") + " · " + plural(it.size, "character");
  node.querySelector("pre").textContent = it.big ? it.text + "\n…" : it.text;
  node._item = it;
  return node;
}

function checkMore(node) {
  const pre = node.querySelector("pre");
  node.querySelector(".more").hidden =
    !(node._item.big || node.classList.contains("open") || pre.scrollHeight > pre.clientHeight + 1);
}

async function fullText(node) {
  const it = node._item;
  if (!it.big) return it.text;
  if (node._full == null) node._full = (await api("/api/items/" + it.id)).text;
  return node._full;
}

listEl.addEventListener("click", async (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  const node = btn.closest(".item"), it = node._item;
  try {
    if (btn.classList.contains("copy")) {
      const text = await fullText(node);
      if (await copyText(text)) done(btn, "Copied ✓");
      else if (putInEditor(text)) { editor.select(); toast("The browser blocked the copy. It is in the editor, selected: press Ctrl+C.", true); }
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

/* ---------------- the editor ---------------- */

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

async function share() {
  const text = editor.value;
  if (!text.trim()) { toast("Nothing to share yet: write or paste something first."); editor.focus(); return; }
  shareBtn.disabled = true;
  try {
    const r = await api("/api/items", { method: "POST", body: { text, device } });
    setEditor("");
    toast(r.duplicate ? "That is already the newest in the history." : "Shared. Every computer has it now.");
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
  if (editor.value) arm(e.currentTarget, "Clear?", () => { setEditor(""); editor.focus(); });
});
const wrap = $("#wrap");
function applyWrap() { editor.wrap = wrap.checked ? "soft" : "off"; editor.setAttribute("wrap", editor.wrap); saved.set("wrap", wrap.checked ? "1" : "0"); }
wrap.checked = saved.get("wrap", "1") === "1";
wrap.addEventListener("change", applyWrap);

document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && !e.altKey && (e.key === "Enter" || e.key === "s" || e.key === "S")) { e.preventDefault(); share(); }
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
  const v = prompt("Name this computer. The name shows next to everything it shares.", device);
  if (v === null || !v.trim()) return;
  device = v.trim().slice(0, 40); saved.set("device", device); showMe();
});

let searchTimer = 0;
search.addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(poll, 250); });
document.addEventListener("visibilitychange", () => { if (!document.hidden) { unseen = 0; showTitle(); poll(); } });
window.addEventListener("focus", () => { unseen = 0; showTitle(); });
let resizeTimer = 0;
window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => nodes.forEach(checkMore), 200); });
setInterval(() => document.querySelectorAll("#list time").forEach((t) => { t.textContent = ago(+t.dataset.at); }), 30000);

editor.value = saved.get("draft", "");
applyWrap();
updateCount();
showMe();
hello().finally(poll);
</script>
</body>
</html>
"""

PAGE = PAGE.replace("__VERSION__", VERSION)

if __name__ == "__main__":
    sys.exit(run())
