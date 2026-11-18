"""Instant Share for Windows: its own window, tray icon and pop-ups around the server in instantshare.py.

One computer keeps the history: the app runs the server there. The other computers run the same app and connect to
it; they find it on the network by themselves, and join it once with a pairing code. Everything between them is
encrypted. On every computer the app waits in the tray when its window is closed, and when another device shares
something with it, a Windows notification shows who, a preview, and Copy or Save (kept in the Notification Centre).
Phones use the Android app or a browser.

    pythonw app.py              (build.py packs this into the installed program)
        --hidden                start in the tray without the window (how Windows starts it at sign-in)
        --data FOLDER           keep the history and the settings in another folder
        --port 9000             the port when this computer keeps the history (default 8765)

Copyright (c) 2026 SCAFFApps. All rights reserved. See LICENSE.
"""

import argparse
import ctypes
import hashlib
import hmac
import html
import io
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import winreg
from ctypes import wintypes
from pathlib import Path
from urllib.parse import quote, urlsplit

import instantshare as ts
import registration as reg
import traymenu

APP = "Instant Share"
# how Windows knows Instant Share's notifications (its name and icon in them and in the Notification Centre);
# automated checks use their own
AUMID = os.environ.get("INSTANTSHARE_AUMID") or "SCAFFApps.InstantShare"
RES = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "build"     # icon.png, icon.ico
# the programs that start with Windows; automated checks point INSTANTSHARE_RUN_KEY elsewhere so they never touch it
RUN_KEY = os.environ.get("INSTANTSHARE_RUN_KEY") or r"Software\Microsoft\Windows\CurrentVersion\Run"
OLD_RUN_NAME = "Text Share"                  # how 1.0 was listed in the programs that start with Windows
REPO = "Rady193/instant-share"   # where new versions are published (GitHub releases)
UPDATE_EVERY = 24 * 3600         # the daily check for a new version (when on)
HOST_PORTS = 10                  # when 8765 is taken by another program, the next free one of 8765..8774
EVERY = 3                        # seconds between the app's questions to the computer that keeps the history
OUTBOX_EVERY = 15                # seconds between tries to share what waits ("Send later"), while anything does

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                wintypes.UINT]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, wintypes.LPVOID, wintypes.UINT]

# The window's title bar in the page's appearance, as Multi Screens does (its --surface and --text colours)
TITLE_BAR = {"dark": ("#191a1b", "#cccccc"), "light": ("#ffffff", "#17141c")}
BACKGROUND = {"dark": "#121314", "light": "#fbf6ea"}


def message(text, error=False):
    user32.MessageBoxW(None, text, APP, 0x10 if error else 0x40)


def log(text):
    ts.log(text)


# ---------------------------------------------------------------- Windows odds and ends

def paint_title_bar(hwnd, theme):
    """Windows 11 takes the exact colours; Windows 10 at least a dark or a light frame."""
    try:
        dwm = ctypes.WinDLL("dwmapi")
        caption, text = TITLE_BAR.get(theme, TITLE_BAR["dark"])

        def colorref(hex_colour):
            r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
            return ctypes.c_uint(r | g << 8 | b << 16)

        dark = ctypes.c_int(1 if theme == "dark" else 0)
        for attribute, value in ((20, dark), (19, dark), (35, colorref(caption)), (36, colorref(text))):
            dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), attribute, ctypes.byref(value), ctypes.sizeof(value))
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)
    except (OSError, AttributeError, ValueError):
        pass


def my_windows():
    """{"main": hwnd} of this program's windows (its title is the page's, such as "Instant Share · OFFICE-PC")."""
    found = {}
    pid = os.getpid()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if buf.value.startswith(APP):
                found.setdefault("main", hwnd)
        return True

    user32.EnumWindows(each, 0)
    return found


def put_on_clipboard(fmt, data):
    handle = kernel32.GlobalAlloc(0x0002, len(data))              # GMEM_MOVEABLE
    if not handle:
        raise OSError("no memory for the clipboard")
    ctypes.memmove(kernel32.GlobalLock(handle), data, len(data))
    kernel32.GlobalUnlock(handle)
    for _ in range(20):                                            # another program may hold it for a moment
        if user32.OpenClipboard(None):
            break
        threading.Event().wait(0.05)
    else:
        kernel32.GlobalFree(handle)
        raise OSError("another program is using the clipboard")
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(fmt, handle):
            kernel32.GlobalFree(handle)
            raise OSError("the clipboard did not take it")
    finally:
        user32.CloseClipboard()


def copy_text(text):
    text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    put_on_clipboard(13, text.encode("utf-16-le") + b"\0\0")       # CF_UNICODETEXT


def copy_image(data):
    from PIL import Image
    image = Image.open(io.BytesIO(data))
    image.seek(0)
    if image.mode in ("RGBA", "LA", "P"):                          # transparent parts become white, as when pasted
        image = image.convert("RGBA")
        white = Image.new("RGB", image.size, (255, 255, 255))
        white.paste(image, mask=image.getchannel("A"))
        image = white
    out = io.BytesIO()
    image.convert("RGB").save(out, "BMP")
    put_on_clipboard(8, out.getvalue()[14:])                        # CF_DIB: the BMP without its file header


def downloads_folder():
    try:
        guid = (ctypes.c_byte * 16).from_buffer_copy(
            bytes.fromhex("90e24d373f126545916439c4925e467b"))     # FOLDERID_Downloads
        path = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(path)) == 0:
            folder = Path(path.value)
            ctypes.windll.ole32.CoTaskMemFree(path)
            return folder
    except (OSError, AttributeError):
        pass
    return Path.home() / "Downloads"


def save_in_downloads(name, data, folder=None):
    """Saves a file from the history in the chosen folder (Downloads unless another was chosen) and shows it there."""
    folder = Path(folder) if folder else downloads_folder()
    folder.mkdir(parents=True, exist_ok=True)
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    target, n = folder / name, 1
    while target.exists():
        n += 1
        target = folder / f"{stem} ({n}){'.' + ext if ext else ''}"
    target.write_bytes(data)
    subprocess.Popen(f'explorer /select,"{target}"')
    return target


class Tray:
    """The icon by the clock and its menu, made with Windows' own functions (no third-party library). The menu is the
    SCAFFApps menu (traymenu.py), drawn like the tray menus of the Electron apps (Smart Snap, SCAFFApps Suite).

    items: [(label, action, checked)] where checked is None or a function saying whether to tick it; None in the
    list is a separator. A left click on the icon does the first item."""

    WM_TRAY = 0x8000 + 1                                  # WM_APP + 1: what the icon sends its window
    CLASS = "SCAFFApps.TextShare.Tray"

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                    ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                    ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                    ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
                    ("dwInfoFlags", wintypes.DWORD), ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wintypes.HICON)]

    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    class WNDCLASSW(ctypes.Structure):
        pass

    WNDCLASSW._fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                          ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                          ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                          ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

    def __init__(self, icon_path, tip, items):
        self.icon_path, self.tip, self.items = str(icon_path), tip, items
        self.hwnd = None
        self.taskbar_created = None                      # set once the window exists; it gets messages before
        self.icon = None
        self.ready = threading.Event()
        u, k, s = user32, kernel32, ctypes.WinDLL("shell32")
        self.shell = s
        u.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.DefWindowProcW.restype = self.LRESULT
        u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                      wintypes.HINSTANCE, wintypes.LPVOID]
        u.CreateWindowExW.restype = wintypes.HWND
        u.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                 wintypes.UINT]
        u.LoadImageW.restype = wintypes.HANDLE
        u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.DestroyWindow.argtypes = [wintypes.HWND]
        u.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
        k.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        k.GetModuleHandleW.restype = wintypes.HMODULE
        s.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.c_void_p]
        self._proc = self.WNDPROC(self.on_message)       # kept, so Windows always has something to call

    def start(self):
        threading.Thread(target=self.run, daemon=True, name="tray").start()
        self.ready.wait(5)

    def run(self):
        try:                                             # Windows' own menus (the title bar's) dark when Windows is
            uxtheme = ctypes.WinDLL("uxtheme")
            uxtheme[135](1)                              # SetPreferredAppMode(AllowDark)
            uxtheme[136]()                               # FlushMenuThemes
        except (OSError, AttributeError, ValueError):
            pass
        instance = kernel32.GetModuleHandleW(None)
        wc = self.WNDCLASSW(lpfnWndProc=self._proc, hInstance=instance, lpszClassName=self.CLASS)
        user32.RegisterClassW(ctypes.byref(wc))         # fails harmlessly when already registered
        self.hwnd = user32.CreateWindowExW(0, self.CLASS, "Instant Share tray", 0, 0, 0, 0, 0, None, None, instance, None)
        self.taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")     # Explorer restarted: add it again
        size = user32.GetSystemMetrics(49)               # SM_CXSMICON
        self.icon = user32.LoadImageW(None, self.icon_path, 1, size, size, 0x10)   # IMAGE_ICON, LR_LOADFROMFILE
        self.notify_icon(0)                              # NIM_ADD
        self.ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def data(self, flags):
        nid = self.NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(nid)
        nid.hWnd, nid.uID, nid.uFlags = self.hwnd, 1, flags
        nid.uCallbackMessage, nid.hIcon = self.WM_TRAY, self.icon
        nid.szTip = self.tip[:127]
        return nid

    def notify_icon(self, what, flags=0x1 | 0x2 | 0x4, **extra):     # NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid = self.data(flags)
        for key, value in extra.items():
            setattr(nid, key, value)
        return self.shell.Shell_NotifyIconW(what, ctypes.byref(nid))

    def on_message(self, hwnd, msg, wparam, lparam):
        try:
            return self.handle(hwnd, msg, wparam, lparam)
        except Exception:                                # never let a mistake here break the icon's window
            traceback.print_exc()
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def handle(self, hwnd, msg, wparam, lparam):
        if msg == self.WM_TRAY:
            event = lparam & 0xFFFF
            if event == 0x0202:                          # WM_LBUTTONUP: the default action
                self.do(0)
            elif event in (0x0205, 0x007B):              # WM_RBUTTONUP, WM_CONTEXTMENU: the menu
                self.menu()
            return 0
        if self.taskbar_created and msg == self.taskbar_created:
            self.notify_icon(0)
            return 0
        if msg == 0x0010:                                # WM_CLOSE: away with the icon, then the window
            self.notify_icon(2)                          # NIM_DELETE
            user32.DestroyWindow(hwnd)
            return 0
        if msg == 0x0002:                                # WM_DESTROY
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def menu(self):
        chosen = traymenu.popup([None if item is None else
                                 dict(label=item[0], checked=None if item[2] is None else bool(item[2]()))
                                 for item in self.items])
        if chosen is not None:
            self.do(chosen)

    def do(self, index):
        item = self.items[index]
        if item:
            threading.Thread(target=item[1], daemon=True).start()     # never hold up the icon

    def set_tip(self, tip):
        self.tip = tip
        if self.hwnd:
            self.notify_icon(1, 0x4)                     # NIM_MODIFY, NIF_TIP

    def balloon(self, text, title):
        if self.hwnd:
            self.notify_icon(1, 0x10, szInfo=text[:255], szInfoTitle=title[:63], dwInfoFlags=0x1)   # NIF_INFO

    def stop(self):
        if self.hwnd:
            user32.PostMessageW(self.hwnd, 0x0010, 0, 0)


class Settings(dict):
    def __init__(self, folder):
        self.path = folder / "settings.json"
        try:
            super().__init__(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            super().__init__()

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self, indent=2), encoding="utf-8")
        tmp.replace(self.path)


class Outbox:
    """What this computer wrote while it could not reach the computer that keeps the history ("Send later"): one
    file each in the outbox folder, shared oldest first as soon as that computer answers, also with the window
    closed. Each carries the id it was given when written, so the history never keeps one twice, even when an answer
    is lost on the way and it is sent again. One the history refuses (too big, not allowed, history full) stays,
    with why, until it is tried again, taken back into the editor or deleted."""

    TEXT_MAX = 20 * 1024 * 1024

    def __init__(self, folder):
        self.folder = folder / "outbox"
        self.lock = threading.Lock()

    def _files(self):
        try:
            return sorted(self.folder.glob("*.json"))
        except OSError:
            return []

    @staticmethod
    def _read(path):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            return entry if isinstance(entry, dict) and ts.DEVICE_ID.fullmatch(str(entry.get("uid", ""))) else None
        except (OSError, ValueError):
            return None

    def _write(self, entry):
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / f"{int(entry['written']):013d}-{entry['uid']}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entry), encoding="utf-8")
        tmp.replace(path)

    def _find(self, uid):
        return next((p for p in self._files() if p.stem.partition("-")[2] == str(uid)), None)    # <written>-<uid>.json

    def entries(self):
        with self.lock:
            return [e for e in map(self._read, self._files()) if e]

    def summary(self):
        """For the pages: oldest first, without the files' contents, the start of the text only."""
        out = []
        for e in self.entries():
            text, file = e.get("text") or "", e.get("file")
            out.append({"uid": e["uid"], "text": text[:4000], "size": len(text),
                        "lines": text.count("\n") + 1 if text else 0, "to": e.get("to") or [],
                        "to_names": e.get("to_names") or [], "written": e["written"], "error": e.get("error") or "",
                        "file": {"name": file["name"], "type": file.get("type", ""), "size": file["size"]}
                        if file else None})
        return out

    def sendable(self):
        return any(not e.get("error") for e in self.entries())

    def add(self, entry):
        """A share to keep, as the page made it: {uid, text, to, to_names, file: {name, type, size, data}, written}."""
        if not isinstance(entry, dict) or not ts.DEVICE_ID.fullmatch(str(entry.get("uid", ""))):
            return {"error": "That was not a proper share."}
        text = entry.get("text") or ""
        if not isinstance(text, str) or len(text.encode("utf-8")) > self.TEXT_MAX:
            return {"error": "That is too long to share (more than 20 MB)."}
        file = entry.get("file") or None
        if file:
            if not isinstance(file, dict) or not isinstance(file.get("data"), str) or not file.get("name"):
                return {"error": "The file could not be read."}
            size = len(file["data"]) * 3 // 4 - file["data"][-2:].count("=")
            if size > ts.FILE_MAX:
                return {"error": f"That file is {size / 1048576:.2f} MB. Files can be up to 5 MB."}
            file = {"name": str(file["name"])[:255], "type": str(file.get("type") or "")[:100], "size": size,
                    "data": file["data"]}
        if not text.strip() and not file:
            return {"error": "There is nothing to share."}
        to = [d for d in entry.get("to") or [] if isinstance(d, str) and ts.DEVICE_ID.fullmatch(d)][:50]
        names = [str(n)[:60] for n in entry.get("to_names") or [] if isinstance(n, str)][:50] if to else []
        now = int(time.time() * 1000)
        written = entry.get("written")
        written = int(written) if isinstance(written, (int, float)) and 0 < written <= now else now
        with self.lock:
            try:
                self._write({"uid": entry["uid"], "text": text, "to": to, "to_names": names, "file": file,
                             "written": written})
            except OSError as e:
                return {"error": f"Could not keep it on this computer: {e}"}
        return {"ok": True}

    def get(self, uid):
        with self.lock:
            path = self._find(uid)
            return self._read(path) if path else None

    def remove(self, uid):
        with self.lock:
            path = self._find(uid)
            if path:
                path.unlink(missing_ok=True)
        return {"ok": True}

    def set_error(self, uid, error):
        """Refused by the history (error), or to be tried again ("")."""
        with self.lock:
            path = self._find(uid)
            entry = self._read(path) if path else None
            if entry:
                if error:
                    entry["error"] = str(error)[:300]
                else:
                    entry.pop("error", None)
                self._write(entry)


class Instance:
    """One app per data folder. The first one listens, on this computer only, for a later one asking it to come to
    the front (that is what double-clicking the program again does)."""

    def __init__(self, folder):
        key = hashlib.sha1(str(folder.resolve()).lower().encode()).hexdigest()[:16]
        self.mutex = kernel32.CreateMutexW(None, False, f"Local\\SCAFFApps.TextShare.{key}")
        self.first = ctypes.get_last_error() != 183          # ERROR_ALREADY_EXISTS
        self.file = folder / "instance.json"

    def listen(self, on_show):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(4)
        token = secrets.token_hex(16)
        self.file.write_text(json.dumps({"port": srv.getsockname()[1], "token": token}), encoding="utf-8")

        def serve():
            while True:
                conn, _ = srv.accept()
                with conn:
                    conn.settimeout(2)
                    try:
                        if conn.recv(64) == f"show {token}".encode():
                            on_show()
                    except OSError:
                        pass
        threading.Thread(target=serve, daemon=True, name="instance").start()

    def wake_first(self):
        user32.AllowSetForegroundWindow(-1)                  # ASFW_ANY: the running one may come to the front
        try:
            info = json.loads(self.file.read_text(encoding="utf-8"))
            with socket.create_connection(("127.0.0.1", info["port"]), timeout=3) as s:
                s.sendall(f"show {info['token']}".encode())
            return True
        except (OSError, ValueError, KeyError):
            return False


# ---------------------------------------------------------------- start with Windows

def autostart_command(data):
    if ts.FROZEN:
        cmd = f'"{sys.executable}" --hidden'
    else:
        pyw = Path(sys.executable).with_name("pythonw.exe")
        cmd = f'"{pyw if pyw.exists() else sys.executable}" "{Path(__file__).resolve()}" --hidden'
    if data.resolve() != ts.default_data_dir().resolve():
        cmd += f' --data "{data.resolve()}"'
    return cmd


def autostart_get():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, APP)[0]
    except OSError:
        return None


def command_data(command):
    """The history folder a start-with-Windows command uses (its --data, else the usual one). The 1.0 folder
    (TextShare) counts as the usual one: it moves to InstantShare."""
    m = re.search(r'--data "([^"]+)"', command or "")
    if not m:
        return ts.default_data_dir().resolve()
    folder = Path(m[1]).resolve()
    old_usual = Path(os.environ.get("LOCALAPPDATA", "")) / "TextShare"
    return ts.default_data_dir().resolve() if folder == old_usual.resolve() else folder


def mine(command, data):
    """Is this start-with-Windows command this copy's (the same history folder)? Another copy's is left alone."""
    return bool(command) and command_data(command) == data.resolve()


def autostart_moved(data):
    """True when Text Share 1.0 was set to start with this same history; that entry goes (the new one replaces it)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_ALL_ACCESS) as key:
            old = winreg.QueryValueEx(key, OLD_RUN_NAME)[0]
            if not mine(old, data):
                return False
            winreg.DeleteValue(key, OLD_RUN_NAME)
            return True
    except OSError:
        return False


def autostart_set(command):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if command:
            winreg.SetValueEx(key, APP, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, APP)
            except FileNotFoundError:
                pass


# ---------------------------------------------------------------- the app

class App:
    def __init__(self, args, instance):
        self.args, self.instance = args, instance
        self.data = args.data
        self.settings = Settings(self.data)
        if not ts.DEVICE_ID.fullmatch(self.settings.get("device_id", "")):
            self.settings["device_id"] = secrets.token_hex(12)      # who this app is, to the others
            self.settings.save()
        self.me = socket.gethostname()
        self.window = self.tray = self.server = self.beacon = self.store = None
        self.key = self.pin = None       # this device's key, and the history computer's (checked on every connection)
        self.notes = None                # Windows notifications
        self.update, self.release = {}, None     # what the last check for a new version found
        self.mode = None                 # what runs now: "host", "join", or None while the setup page shows
        self.port = None
        self.base = None                 # http://host:port of the computer that keeps the history, once reached
        self.quitting = False
        self.told_tray = False
        self.lock = threading.RLock()
        self.attempt = 0                 # each new connection attempt makes the older ones give up
        self.wake = threading.Event()    # "Try again" on the can't-reach page
        self.listening = 0               # each new connection makes the older questioner stop
        self.outbox = Outbox(self.data)  # written while the computer that keeps the history could not be reached
        self.outbox_wake = threading.Event()
        self.held = None                 # (host, port): it answers again, but the can't-reach page has a draft

    @property
    def name(self):
        return self.settings.get("name") or self.me

    @property
    def theme(self):
        return "light" if self.settings.get("theme") == "light" else "dark"

    @property
    def save_folder(self):
        """Where Save puts files: the folder chosen in Settings, while it exists; else Downloads."""
        chosen = self.settings.get("save_folder")
        return Path(chosen) if chosen and Path(chosen).is_dir() else downloads_folder()

    def options(self):
        return {"windows": True, "on_top": bool(self.settings.get("on_top")), "auto_copy": bool(self.settings.get("auto_copy")),
                "host": self.mode == "host", "folder": str(self.data), "save_folder": str(self.save_folder),
                "save_chosen": bool(self.settings.get("save_folder")),
                "update_check": self.settings.get("update_check", True),
                "update": dict(self.update, current=ts.VERSION)}

    def set_option(self, name, value):
        if name not in ("on_top", "auto_copy", "update_check"):
            return self.options()
        self.settings[name] = bool(value)
        self.settings.save()
        if name == "on_top" and self.window:
            self.window.on_top = bool(value)
        return self.options()

    def run(self):
        import webview
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        if os.environ.get("TEXTSHARE_DEBUG_PORT", "").isdigit():      # for automated checks of the app's windows
            webview.settings["REMOTE_DEBUGGING_PORT"] = int(os.environ["TEXTSHARE_DEBUG_PORT"])
        pin_window_certificates(self)
        self.notes = Notifier(self)
        hidden = self.args.hidden and self.settings.get("mode") in ("host", "join") and reg.read() is not None
        self.window = webview.create_window(APP, html=self.local_page("starting"), js_api=Api(self), width=1220,
                                            height=820, min_size=(420, 520), hidden=hidden,
                                            on_top=bool(self.settings.get("on_top")),
                                            background_color=BACKGROUND[self.theme], text_select=True)
        self.window.events.closing += self.on_closing
        self.window.events.closed += self.on_closed
        self.window.events.shown += lambda: self.set_theme(self.theme)
        webview.start(self.begin, gui="edgechromium", private_mode=False, icon=str(RES / "icon.ico"),
                      storage_path=str(self.data / "webview"))
        self.listening += 1
        self.stop_host()
        self.notes.close()

    def begin(self):
        try:
            self._begin()
        except Exception:                # pywebview would swallow it: written to the log, and said in the window
            traceback.print_exc()
            self.window.load_html(self.local_page("problem", {
                "title": "Instant Share could not start", "text": "Details are in textshare.log in " + str(self.data)}))

    def _begin(self):
        self.window.events.loaded.wait(15)   # the first page ("Starting…") in place, so the next one is not lost under it
        self.set_theme(self.theme)
        self.instance.listen(self.show)
        if reg.read() is None:               # the user's name and e-mail first, asked once for every SCAFFApps app
            self.registering = True
            self.window.load_html(self.local_page("register"))
            return
        self._carry_on()

    def after_register(self):
        self.registering = False
        self.window.load_html(self.local_page("starting"))
        self._carry_on()

    def _carry_on(self):
        reg.report_in_background("instant-share", ts.VERSION)
        mode = self.settings.get("mode")
        if mode in ("host", "join"):
            self.start_tray()
            command = autostart_get()
            # this copy's own entry follows it when the program moved or was renamed; another copy's is not touched
            if autostart_moved(self.data) or (mine(command, self.data) and command != autostart_command(self.data)):
                autostart_set(autostart_command(self.data))
        threading.Thread(target=self.update_checks, daemon=True, name="updates").start()
        threading.Thread(target=self.outbox_loop, daemon=True, name="outbox").start()
        if self.args.after_update:
            threading.Thread(target=finish_update, args=(self, Path(self.args.after_update)), daemon=True).start()
        if mode == "host":
            self.become_host()
        elif mode == "join" and self.settings.get("host"):
            self.join()
        else:
            self.open_setup()

    # ---- the window

    def set_theme(self, theme):
        if theme in ("dark", "light") and theme != self.settings.get("theme"):
            self.settings["theme"] = theme
            self.settings.save()
        hwnd = my_windows().get("main")
        if hwnd:
            paint_title_bar(hwnd, self.theme)

    def set_name(self, name):
        name = str(name).strip()[:40]
        if name and name != self.settings.get("name"):
            self.settings["name"] = name
            self.settings.save()

    def local_page(self, kind, info=None):
        return local_page(kind, info, self.theme)

    def page_url(self, host, port, scheme="https"):
        """The page, opened through /login with this device's key (it becomes a cookie for this window)."""
        where = f"[{host}]" if ":" in host else host
        page = f"/?app=1&me={quote(self.name)}&theme={self.theme}"
        return f"{scheme}://{where}:{port}/login?key={quote(self.key or '')}&next={quote(page, safe='')}"

    def show(self):
        if self.window and not self.quitting:
            self.window.show()
            self.window.restore()

    def on_closing(self):
        """Closing the window: once set up, Instant Share waits in the tray, so the others can go on sharing with
        this computer and its pop-ups still come; Quit in the tray menu ends it."""
        if self.quitting or self.mode is None or getattr(self, "registering", False):
            self.quitting = True
            self.stop_tray()
            return True
        self.window.hide()
        if not self.told_tray and self.tray:
            self.told_tray = True
            try:
                self.tray.balloon("Instant Share is still running here, so it can show what others share. "
                                 "To stop it, right-click its icon and choose Quit.", APP)
            except Exception:
                pass
        return False

    def on_closed(self):
        self.quitting = True

    def quit(self):
        self.quitting = True
        self.attempt += 1
        self.listening += 1
        self.stop_tray()
        self.stop_host()
        self.window.destroy()

    def open_setup(self):
        found = {"mode": self.mode, "me": self.me, "version": ts.VERSION, "host": self.settings.get("host", ""),
                 "autostart": bool(autostart_get()) if self.mode else True}
        self.window.load_html(self.local_page("setup", found))
        self.show()

    def resume(self):
        """Back from the setup page to what was running."""
        if self.mode == "host":
            self.load_host_page()
        elif self.mode == "join":
            self.join()
        else:
            self.open_setup()

    # ---- new versions (GitHub releases)

    def update_checks(self):
        """Once a day, if allowed: is there a newer version? Said once per version, in a notification."""
        threading.Event().wait(45)                       # not while starting
        while not self.quitting:
            if self.settings.get("update_check", True) and time.time() - self.settings.get("update_checked", 0) > UPDATE_EVERY:
                self.check_update(tell=True)
            threading.Event().wait(3600)

    def check_update(self, tell=False):
        try:
            rel = latest_release()
        except Exception as e:
            self.update = {"error": str(e) if isinstance(e, RuntimeError) else
                           f"Could not ask GitHub ({e}). Is this computer on the internet?",
                           "checked": int(time.time() * 1000)}
            return dict(self.update, current=ts.VERSION)
        self.release = rel
        newer = version_tuple(rel["version"]) > version_tuple(ts.VERSION)
        setup = f"Instant-Share-Setup-{rel['version']}.exe"
        self.update = {"latest": rel["version"], "available": newer, "page": rel["page"],
                       "checked": int(time.time() * 1000),
                       "can_install": installed() and setup in rel["assets"] and setup in rel["hashes"]}
        self.settings["update_checked"] = time.time()
        if newer and tell and self.settings.get("update_told") != rel["version"]:
            self.settings["update_told"] = rel["version"]
            self.notes.update_available(rel["version"])
        self.settings.save()
        return dict(self.update, current=ts.VERSION)

    def update_now(self):
        """Download the new version's installer, check it against the release's SHA-256, and start it with Windows'
        permission: it closes this copy, installs the new version in the same folder and opens it (/run)."""
        if not installed():
            return {"error": "This copy is not installed (it runs from Python source or a copied folder): get the "
                             "installer from the Releases page.", "page": f"https://github.com/{REPO}/releases/latest"}
        try:
            rel = self.release or latest_release()
            v = rel["version"]
            if version_tuple(v) <= version_tuple(ts.VERSION):
                return {"error": f"{ts.VERSION} is the newest version already."}
            name = f"Instant-Share-Setup-{v}.exe"
            url, expected = rel["assets"].get(name), rel["hashes"].get(name)
            if not url or not expected:
                return {"error": "That release has no installer with a checksum. Get it from the Releases page.",
                        "page": rel["page"]}
            folder = Path(os.environ.get("TEMP") or self.data) / "Instant Share update"
            folder.mkdir(parents=True, exist_ok=True)
            setup, part = folder / name, folder / (name + ".download")
            digest = hashlib.sha256()
            request = urllib.request.Request(url, headers={"User-Agent": "InstantShare-update"})
            with urllib.request.urlopen(request, timeout=60) as r, open(part, "wb") as out:
                while chunk := r.read(1 << 16):
                    out.write(chunk)
                    digest.update(chunk)
            if digest.hexdigest().lower() != expected.lower():
                part.unlink(missing_ok=True)
                return {"error": "The download did not match its published checksum, so nothing was changed. "
                                 "Try again later."}
            os.replace(part, setup)
        except OSError as e:
            return {"error": f"Could not download it: {e}"}
        log(f"Updating to {v} with {setup}")
        # administrator rights: Windows asks first. More than 32 means it started; 32 or less, it did not (refused)
        started = ctypes.windll.shell32.ShellExecuteW(None, "runas", str(setup), "/S /run", None, 1)
        if started <= 32:
            return {"error": "Windows did not get permission to install it. Choose Update now again and answer Yes."}
        threading.Thread(target=self.quit, daemon=True).start()
        return {"ok": True, "version": v}

    # ---- the tray

    def start_tray(self):
        if self.tray:
            self.tray_title()
            return

        def toggle_autostart():
            autostart_set(None if mine(autostart_get(), self.data) else autostart_command(self.data))

        self.tray = Tray(RES / "icon.ico", APP, [
            ("Open Instant Share", self.show, None),
            ("Add a device…", self.add_device, None),
            ("Start with Windows", toggle_autostart, lambda: mine(autostart_get(), self.data)),
            ("Change setup…", self.open_setup, None),
            None,
            ("Quit Instant Share", self.quit, None)])
        self.tray.start()
        self.tray_title()

    def add_device(self):
        """The tray's "Add a device…": Settings, with a new pairing code."""
        self.show()
        try:
            self.window.evaluate_js("openSettings(false).then(makeCode)")
        except Exception:
            pass

    def tray_title(self):
        if self.tray:
            where = (f"keeping the history on {self.me}" if self.mode == "host"
                     else f"connected to {self.settings.get('host', '')}" if self.mode == "join" else "setting up")
            self.tray.set_tip(f"Instant Share: {where}")

    def stop_tray(self):
        if self.tray:
            try:
                self.tray.stop()
            except Exception:
                pass
            self.tray = None

    # ---- this computer keeps the history

    def become_host(self):
        with self.lock:
            if self.mode != "host":
                self.attempt += 1
                self.mode = None
                if not self.start_server():
                    return
                self.mode = "host"
                self.start_tray()
        self.listen(f"https://127.0.0.1:{self.port}")
        self.load_host_page()

    def load_host_page(self):
        self.window.load_url(self.page_url("localhost", self.port, "http"))     # this computer itself: no detour

    def start_server(self):
        self.store = ts.Store(self.data / "history.db")
        tls = ts.server_tls(self.data)
        self.pin = tls[1]
        # this computer's own window and notifications: paired by the history itself (a new key each start)
        self.key = self.store.issue_key(self.settings["device_id"], self.name, "app", take_over=True)[1]
        first = self.args.port or int(self.settings.get("host_port", ts.DEFAULT_PORT))
        for port in range(first, first + HOST_PORTS):
            server = ts.make_server(port, self.store, tls)
            if server:
                break
        else:
            self.store.close()
            self.store = None
            self.window.load_html(self.local_page("problem", {
                "title": "Instant Share cannot start here",
                "text": f"Ports {first} to {first + HOST_PORTS - 1} are all used by other programs. "
                        "Start Instant Share with --port and another number, for example --port 9000."}))
            return False
        self.server, self.port = server, port
        self.settings["host_port"] = port
        self.settings.save()
        threading.Thread(target=server.serve_forever, daemon=True, name="server").start()
        try:
            self.beacon = ts.Beacon(port)
            self.beacon.start()
        except OSError:
            self.beacon = None
            log(f"Another program uses UDP port {ts.DISCOVERY_PORT}: the other computers must be given the address")
        log(f"Keeping the history on {self.me}, port {port}: {self.store.path}")
        return True

    def stop_host(self):
        with self.lock:
            if self.beacon:
                self.beacon.close()
                self.beacon = None
            if self.server:
                self.server.shutdown()
                self.server.server_close()
                self.server = None
            if self.store:
                self.store.close()
                self.store = None
            if self.mode == "host":
                self.mode = None

    # ---- connecting to the computer that keeps the history

    def join(self):
        if self.mode == "host":
            self.stop_host()
        self.mode = "join"
        self.base = None
        self.listening += 1
        self.attempt += 1
        self.start_tray()
        self.key, self.pin = self.settings.get("key"), self.settings.get("pin")
        if not (self.key and self.pin):
            return self.ask_code()
        self.window.load_html(self.local_page("connecting", {"name": self.settings["host"]}))
        threading.Thread(target=self.connect, args=(self.attempt,), daemon=True, name="connect").start()

    def ask_code(self, why=""):
        """The pairing page: this computer joins the one that keeps the history with a code shown there."""
        self.attempt += 1
        self.listening += 1
        self.base = None
        self.window.load_html(self.local_page("pair", {"name": self.settings.get("host", ""), "why": why}))
        self.show()

    def pair(self, code):
        """The code typed on the pairing page: {ok} or {error}."""
        name = self.settings.get("host", "")
        port = int(self.settings.get("port", ts.DEFAULT_PORT))
        last = None
        for host in dict.fromkeys(filter(None, [self.settings.get("address"), name])):
            where = f"[{host}]" if ":" in host else host
            try:
                got = ts.pair(f"https://{where}:{port}", code, self.name, "app", self.settings["device_id"])
            except ts.Refused as e:
                return {"error": str(e)}
            except OSError as e:
                last = e
                if "not right" in str(e):
                    return {"error": str(e)}
                continue
            self.settings.update(key=got["key"], pin=got["pin"], device_id=got["id"])
            self.settings.save()
            log(f"Joined Instant Share on {name}")
            threading.Thread(target=self.join, daemon=True).start()
            return {"ok": True}
        return {"error": f"Can't reach {name}: {last}" if last else f"Can't reach {name}."}

    def connect(self, attempt):
        shown_problem = False
        while attempt == self.attempt and not self.quitting:
            name = self.settings["host"]
            port = int(self.settings.get("port", ts.DEFAULT_PORT))
            for host in dict.fromkeys(filter(None, [self.settings.get("address"), name])):
                try:
                    answer = hello(host, port, self.pin, self.key)
                except ts.WrongComputer:
                    if attempt == self.attempt:
                        self.window.load_html(self.local_page("problem", {
                            "title": f"This is not the {name} this computer joined",
                            "text": f"The computer answering at {host} has another key than {name} had when this "
                                    "computer joined it. If Instant Share was reinstalled there, or its folder "
                                    "replaced, join it again (Setup). Otherwise someone may be pretending to be it."}))
                    return
                if answer and answer["server"].lower() == name.lower() and attempt == self.attempt:
                    if not answer.get("paired"):
                        self.settings.pop("key", None)
                        self.settings.save()
                        return self.ask_code(f"{name} no longer knows this computer (it was removed there, or its "
                                             "history was replaced). Join it again with a new code.")
                    where = f"[{host}]" if ":" in host else host
                    self.listen(f"https://{where}:{port}")
                    if shown_problem and self.holding_draft():
                        self.held = (host, port)     # the page says it answers again; the user opens the history
                        return
                    self.window.load_url(self.page_url(host, port))
                    return
                old = old_version(host, port)
                if old and attempt == self.attempt:
                    self.window.load_html(self.local_page("problem", {
                        "title": f"{name} runs Instant Share {old}",
                        "text": f"This computer has Instant Share {ts.VERSION}, which encrypts everything and lets "
                                f"only devices that joined in. Update Instant Share on {name} to {ts.VERSION} too."}))
                    self.wake.wait(20)
                    self.wake.clear()
                    shown_problem = False
                    break
            else:
                for found in ts.discover():          # its address may have changed: ask the network by its name
                    if found["name"].lower() == name.lower():
                        self.settings.update(address=found["address"], port=found["port"])
                        self.settings.save()
                        break
                else:
                    if attempt != self.attempt:
                        return
                    if not shown_problem:
                        shown_problem = True
                        self.window.load_html(self.local_page("unreachable", {
                            "name": name, "devices": self.settings.get("devices") or []}))
                    self.wake.wait(5)
                    self.wake.clear()

    def holding_draft(self):
        """The can't-reach page has something written that is not kept yet: it is not swept away by the history's
        page; the page says the computer answers again instead (and opens the history when told to)."""
        try:
            return bool(self.window.evaluate_js("window.holdForDraft ? window.holdForDraft() : false"))
        except Exception:
            return False

    def open_history(self):
        """From the can't-reach page, once the computer answers again (if it went away meanwhile: connecting again,
        which shows the can't-reach page as long as it does not answer)."""
        held, self.held = self.held, None
        if held and self.mode == "join" and self.base and hello(held[0], held[1], self.pin, self.key):
            self.window.load_url(self.page_url(*held))
        else:
            self.resume()

    def choose_join(self, name, address, port):
        if (self.settings.get("host", "").lower(), int(self.settings.get("port", 0) or 0)) != (name.lower(), port):
            for k in ("key", "pin"):            # another computer: join it with a code of its own
                self.settings.pop(k, None)
        self.settings.update(mode="join", host=name, address=address, port=port)
        self.settings.save()
        self.join()

    # ---- what the others share: one question kept open, shown in Windows notifications

    def ask(self, path, base=None, timeout=6):
        """A question to the computer that keeps the history, as this device: encrypted, only to the computer with
        the key remembered, with this device's own key."""
        status, ctype, data, _ = ts.call(base or self.base, path, pin=self.pin, key=self.key, timeout=timeout,
                                         headers={"X-TextShare-Name": quote(self.name), "X-TextShare-Kind": "app"})
        if status == 401 and self.mode == "join" and not self.quitting:
            self.settings.pop("key", None)
            self.settings.save()
            threading.Thread(target=self.ask_code, args=(f"{self.settings.get('host', '')} no longer knows this "
                             "computer (it was removed there). Join it again with a new code.",), daemon=True).start()
        if status >= 400:
            raise OSError(f"the computer answered {status}")
        return json.loads(data) if ctype == "application/json" else data

    def fetch(self, url):
        """The bytes of a file in the history (the page gives its address; only /files/ is fetched, and only from
        the computer that keeps the history)."""
        u = urlsplit(str(url))
        if not self.base or not u.path.startswith("/files/"):
            raise OSError("not a file in the history")
        return self.ask(u.path + (f"?{u.query}" if u.query else ""), timeout=15)

    def listen(self, base):
        self.base = base
        self.listening += 1
        threading.Thread(target=self.questions, args=(base, self.listening), daemon=True, name="questions").start()
        self.outbox_wake.set()           # in reach: what waits goes now

    def questions(self, base, turn):
        """Anything new for this device? One question kept open (the computer that keeps the history answers the
        moment something arrives, or after 25 s), asked again at once. It also keeps this device on the others'
        lists as online."""
        after = None
        away = True
        while turn == self.listening and not self.quitting:
            started = time.monotonic()
            try:
                answer = self.ask("/api/events" + (f"?after={after}&wait=25" if after is not None else ""), base,
                                  timeout=45)
                if away:                         # (back) in reach: the device list for writing while away, and
                    away = False                 # whatever waits to be sent
                    self.remember_devices(base)
                    self.outbox_wake.set()
                if after is not None and answer["items"] and turn == self.listening:
                    self.pop_up(base, answer["items"])
                after = answer["newest"] if after is None else max(after, answer["newest"])
                if answer["items"] or time.monotonic() - started > 1:
                    continue                     # straight back to waiting
            except (OSError, ValueError, KeyError):
                away = True                      # away for now: the page says so; asked again shortly
            threading.Event().wait(EVERY)

    def pop_up(self, base, items):
        main = my_windows().get("main")
        if main and user32.GetForegroundWindow() == main:
            return                               # the page is in front: it shows the new items itself
        newest = dict(items[0], base=base, more=len(items) - 1)
        photo = None
        file = newest.get("file")
        if file and file["type"].startswith("image/") and file["size"] <= 5 * 1024 * 1024:
            try:
                photo = self.notes.picture(newest["id"], file["name"], self.ask(file["url"], base, timeout=15))
            except Exception:
                photo = None
        self.notes.show(newest, photo)
        if self.settings.get("auto_copy") and (newest["size"] or (file and file["type"].startswith("image/"))):
            threading.Thread(target=self.act, args=("copy", newest), daemon=True, name="auto-copy").start()

    def act(self, what, item):
        """Copy (text or photo) or Save (a file) an item from a notification."""
        try:
            if what == "copy" and item["size"]:
                copy_text(self.ask(f"/api/items/{item['id']}", item["base"])["text"])
                return {"ok": True}
            if item.get("file"):
                data = self.ask(item["file"]["url"], item["base"], timeout=15)
                if what == "copy" and item["file"]["type"].startswith("image/"):
                    copy_image(data)
                    return {"ok": True}
                saved = save_in_downloads(item["file"]["name"], data, self.save_folder)
                self.notes.saved(saved)
                return {"ok": True, "name": saved.name}
        except Exception as e:
            log(f"notification {what}: {e}")
            self.notes.trouble(f"Could not get it from the computer that keeps the history: {e}")
            return {"error": str(e)}
        return {"error": "Nothing to copy."}

    # ---- waiting to send: written while the computer that keeps the history could not be reached

    def remember_devices(self, base):
        """The other devices, kept for the can't-reach page's "To" (who should see what is written there)."""
        try:
            found = self.ask("/api/devices", base)["devices"]
        except Exception:
            return
        devices = [{"id": d["id"], "name": d["name"]} for d in found if not d.get("me")]
        if devices != self.settings.get("devices"):
            self.settings["devices"] = devices
            self.settings.save()

    def outbox_loop(self):
        """Shares what waits, oldest first: as soon as the computer that keeps the history answers (the questioner
        and the page say so), when something is added, and every few seconds while anything waits."""
        while not self.quitting:
            self.outbox_wake.wait(OUTBOX_EVERY)
            self.outbox_wake.clear()
            if self.quitting:
                return
            if self.base and self.mode in ("host", "join") and self.outbox.sendable():
                try:
                    self.send_waiting(self.base)
                except Exception:
                    traceback.print_exc()

    def send_waiting(self, base):
        sent = changed = 0
        for entry in self.outbox.entries():
            if entry.get("error"):
                continue
            body = {"text": entry.get("text") or "", "device": self.name, "to": entry.get("to") or [],
                    "uid": entry["uid"], "waited": max(0, int(time.time() * 1000) - int(entry["written"]))}
            if entry.get("file"):
                body["file"] = {"name": entry["file"]["name"], "data": entry["file"]["data"]}
            try:
                status, ctype, data, _ = ts.call(base, "/api/items", pin=self.pin, key=self.key, method="POST",
                                                 body=body, timeout=60,
                                                 headers={"X-TextShare-Name": quote(self.name), "X-TextShare-Kind": "app"})
            except Exception:
                break                            # out of reach again: later
            if status in (200, 201):
                self.outbox.remove(entry["uid"])
                sent += 1
                changed += 1
                continue
            if status == 401 or (status >= 500 and status != 507):
                break                            # not joined any more (the questioner says so), or trouble there
            try:
                why = json.loads(data).get("error") if ctype == "application/json" else ""
            except ValueError:
                why = ""
            self.outbox.set_error(entry["uid"], why or f"the computer answered {status}")
            changed += 1
        if sent:
            log(f"shared {sent} item{'s' if sent > 1 else ''} written while away")
        if changed:
            self.tell_pages({"sent": sent})
            main = my_windows().get("main")
            if sent and not (main and user32.GetForegroundWindow() == main and user32.IsWindowVisible(main)):
                self.notes.sent_later(sent, self.settings.get("host", "") if self.mode == "join" else self.me)

    def tell_pages(self, news=None):
        """The page in the window (Instant Share's, or the can't-reach page) shows the outbox again."""
        try:
            self.window.evaluate_js(f"window.textShareOutbox && window.textShareOutbox({json.dumps(news or {})})")
        except Exception:
            pass


class Notifier:
    """Windows' own notifications: who shared, a preview (the photo itself for a photo), and Copy or Save, and
    Open. Windows keeps them in the Notification Centre and follows Do Not Disturb. Their buttons work while
    Instant Share runs, so the ones left from an earlier run are cleared when it starts and when it quits."""

    def __init__(self, app):
        self.app, self.ok, self.items, self.live = app, False, {}, {}
        self.pictures = app.data / "notifications"
        try:
            from winrt.windows.data.xml.dom import XmlDocument
            from winrt.windows.ui.notifications import (ToastActivatedEventArgs, ToastNotification,
                                                        ToastNotificationManager)
            self._xml, self._toast, self._args, self._manager = (XmlDocument, ToastNotification,
                                                                 ToastActivatedEventArgs, ToastNotificationManager)
            self.register()
            self.notifier = ToastNotificationManager.create_toast_notifier(AUMID)
            self.clear()
            self.ok = True
        except Exception as e:                  # Windows without them (very old): the tray's own balloons instead
            log(f"Windows notifications are not available ({e}); using the tray's")
        shutil.rmtree(self.pictures, ignore_errors=True)

    def register(self):
        """Instant Share's name and icon for its notifications (under this Windows account only)."""
        icon = self.app.data / "icon.png"
        try:
            if not icon.exists() or icon.read_bytes() != (RES / "icon.png").read_bytes():
                icon.write_bytes((RES / "icon.png").read_bytes())
        except OSError:
            pass
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{AUMID}") as k:
            winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, APP)
            winreg.SetValueEx(k, "IconUri", 0, winreg.REG_SZ, str(icon))
            winreg.SetValueEx(k, "IconBackgroundColor", 0, winreg.REG_SZ, "FF5B2C83")

    def clear(self):
        try:
            self._manager.history.clear(AUMID)
        except Exception:
            pass

    def close(self):
        if self.ok:
            self.clear()
        shutil.rmtree(self.pictures, ignore_errors=True)

    def picture(self, item_id, name, data):
        """A photo, as a file the notification can show."""
        self.pictures.mkdir(parents=True, exist_ok=True)
        old = sorted(self.pictures.iterdir(), key=lambda f: f.stat().st_mtime)
        for f in old[:-20]:
            f.unlink(missing_ok=True)
        path = self.pictures / f"{item_id}{Path(name).suffix.lower() or '.png'}"
        path.write_bytes(data)
        return path

    def _send(self, xml, tag=None, item=None):
        doc = self._xml()
        doc.load_xml(xml)
        toast = self._toast(doc)
        if tag:
            toast.tag, toast.group = tag, "items"
        keep = tag or f"x{id(toast)}"
        toast.add_activated(self._activated)
        toast.add_dismissed(lambda t, a: self.live.pop(keep, None))
        self.live[keep] = toast                           # kept, so its buttons are heard
        self.notifier.show(toast)

    def show(self, item, photo=None):
        file = item.get("file")
        who = item["device"] + (" shared with you" if item.get("to") else " shared")
        if not self.ok:
            if self.app.tray:
                self.app.tray.balloon((item["text"] or (file or {}).get("name", ""))[:200], who)
            return
        self.items[str(item["id"])] = item
        for k in list(self.items)[:-50]:
            del self.items[k]
        esc = lambda t: html.escape(str(t), quote=True)                         # noqa: E731
        text = item["text"][:300] if item["size"] else (file["name"] if file else "")
        image = f'<image src="{esc(photo.as_uri())}"/>' if photo else ""
        more = f'<text placement="attribution">+{item["more"]} more</text>' if item.get("more") else ""
        copyable = item["size"] or (file and file["type"].startswith("image/"))
        main = "Copy" if copyable else "Save"
        n = item["id"]
        self._send(f'''<toast launch="open|{n}" activationType="foreground">
  <visual><binding template="ToastGeneric"><text hint-maxLines="1">{esc(who)}</text><text>{esc(text)}</text>{image}{more}
  </binding></visual>
  <actions><action content="{main}" arguments="{main.lower()}|{n}" activationType="foreground"/>
    <action content="Open" arguments="open|{n}" activationType="foreground"/></actions>
</toast>''', str(n), item)

    def update_available(self, version):
        if self.ok:
            self._send(f'''<toast launch="settings|" activationType="foreground"><visual><binding template="ToastGeneric">
  <text>Instant Share {html.escape(version)} is available</text><text>This computer has {ts.VERSION}. Update in a click: the history and settings stay.</text></binding></visual>
  <actions><action content="Update now" arguments="update|" activationType="foreground"/>
    <action content="Later" arguments="later|" activationType="foreground"/></actions></toast>''')

    def updated(self):
        if self.ok:
            self._send(f'''<toast><visual><binding template="ToastGeneric"><text>Instant Share is up to date</text>
  <text>Now {ts.VERSION}. The history and settings are as they were.</text></binding></visual><audio silent="true"/></toast>''')

    def saved(self, path):
        if self.ok:
            esc = lambda t: html.escape(str(t), quote=True)                     # noqa: E731
            self._send(f'''<toast launch="folder|{esc(path)}" activationType="foreground"><visual><binding template="ToastGeneric">
  <text>Saved</text><text>{esc(path.name)} is in {esc(path.parent)}</text></binding></visual>
  <actions><action content="Show in folder" arguments="folder|{esc(path)}" activationType="foreground"/></actions>
  <audio silent="true"/></toast>''')

    def sent_later(self, n, host):
        """What was written while away is shared now (said when the window is not in front; the page says it there)."""
        what = "The message you wrote while away is" if n == 1 else f"The {n} messages you wrote while away are"
        if not self.ok:
            if self.app.tray:
                self.app.tray.balloon(f"{what} in the history on {host} now.", APP)
            return
        self._send(f'''<toast launch="open|" activationType="foreground"><visual><binding template="ToastGeneric">
  <text>Shared what you wrote while away</text><text>{html.escape(what)} in the history on {html.escape(host)} now.</text>
  </binding></visual><audio silent="true"/></toast>''')

    def trouble(self, message):
        if self.ok:
            self._send(f'''<toast><visual><binding template="ToastGeneric"><text>Instant Share</text>
  <text>{html.escape(message)}</text></binding></visual><audio silent="true"/></toast>''')

    def _activated(self, toast, args):
        try:
            what, _, ref = self._args._from(args).arguments.partition("|")
        except Exception:
            what, ref = "open", ""
        if what == "folder":
            subprocess.Popen(["explorer", "/select,", ref])
            return
        if what == "later":
            return
        if what == "update":
            def go():
                answer = self.app.update_now()
                if answer.get("error"):
                    self.trouble(answer["error"])
            threading.Thread(target=go, daemon=True, name="update").start()
            return
        if what == "settings":
            self.app.show()
            try:
                self.app.window.evaluate_js("openSettings(false)")
            except Exception:
                pass
            return
        item = self.items.get(ref)
        if what in ("copy", "save") and item:
            threading.Thread(target=self.app.act, args=(what, item), daemon=True, name="notification").start()
        else:
            self.app.show()


def pin_window_certificates(app):
    """The window opens the page of the computer that keeps the history over https, with that computer's own
    certificate: it is accepted when its key is the one remembered from pairing, and refused otherwise."""
    import webview
    from webview.platforms import edgechromium

    def on_certificate_error(self, _, args):
        action = edgechromium.CoreWebView2ServerCertificateErrorAction
        try:
            seen = ts.pin_of(ts._unpem(args.ServerCertificate.ToPemEncoding()))
            ok = bool(app.pin) and hmac.compare_digest(seen, app.pin)
        except Exception:
            ok = False
        if not ok:
            log("refused a certificate that is not the paired computer's")
        args.set_Action(action.AlwaysAllow if ok else action.Cancel)

    edgechromium.EdgeChrome.on_certificate_error = on_certificate_error
    webview.settings["IGNORE_SSL_ERRORS"] = True            # (handled above: only the paired key gets through)


def installed():
    """This copy was installed by Instant Share's installer (its uninstaller is beside it), so it can update itself."""
    return ts.FROZEN and (Path(sys.executable).parent / "Uninstall Instant Share.exe").exists()


def version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:3])


RELEASES_SITE = "https://www.scaffapps.com/api/releases/"
RELEASES_MIRROR = "https://raw.githubusercontent.com/Rady193/scaffapps-suite/main/releases.json"


def _get_json(url, timeout, accept="application/json"):
    request = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "InstantShare-update-check",
                                                   "Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=timeout) as r:
        return json.loads(r.read())


def _release_data(timeout):
    """GitHub's answer for the newest release, asked first of the SCAFFApps website, then of releases.json in the
    SCAFFApps Suite's repository, and only then of GitHub's API, which answers 60 questions an hour for each internet
    connection (shared by every computer and app behind it). All three answer in the API's shape."""
    test = os.environ.get("INSTANTSHARE_UPDATE_URL")                                           # tests: asked alone
    name = REPO.split("/")[1]
    if not test:
        for ask in (lambda: _get_json(RELEASES_SITE + name, timeout),
                    lambda: (_get_json(RELEASES_MIRROR, timeout).get("repos") or {}).get(name)):
            try:
                data = ask()
                if data and data.get("tag_name"):
                    return data
            except Exception:
                pass                                                                           # the next one
    try:
        return _get_json(test or f"https://api.github.com/repos/{REPO}/releases/latest", timeout,
                         "application/vnd.github+json")
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            raise RuntimeError("GitHub is limiting how often it can be asked. Try again in an hour.") from None
        if e.code == 404:
            raise RuntimeError("No release has been published yet.") from None
        raise


def latest_release(timeout=15):
    """The newest Instant Share: {version, page, assets: {name: url}, hashes: {name: SHA-256}} (the hashes from the
    table in the release's notes)."""
    data = _release_data(timeout)
    hashes = dict(re.findall(r"\|\s*`([^`|]+)`\s*\|[^|\n]*\|\s*`([0-9A-Fa-f]{64})`", data.get("body") or ""))
    return {"version": str(data["tag_name"]).lstrip("v"), "page": data.get("html_url") or "",
            "assets": {a["name"]: a["browser_download_url"] for a in data.get("assets", [])}, "hashes": hashes}


def finish_update(app, old):
    """The new version, started by the old one: once that has quit, its file goes, and the user is told."""
    log(f"Updated to {ts.VERSION} (was {old.name})")
    me = Path(sys.executable).resolve()
    if old.resolve() != me and old.suffix.lower() == ".exe" and old.name.lower().startswith(("instant-share", "text-share")):
        for _ in range(40):
            try:
                old.unlink(missing_ok=True)
                break
            except OSError:
                time.sleep(0.5)
    threading.Event().wait(3)
    app.notes.updated()


def hello(host, port, pin=None, key=None, timeout=2.5):
    """Instant Share's answer at host:port (encrypted; with a pin, only from the computer with that key), or None.
    Raises ts.WrongComputer when a pinned computer shows another key."""
    where = f"[{host}]" if ":" in host else host
    try:
        status, ctype, data, _ = ts.call(f"https://{where}:{port}", "/api/hello", pin=pin, key=key, timeout=timeout)
        answer = json.loads(data) if status == 200 else {}
        return answer if "server" in answer else None
    except ts.WrongComputer:
        raise
    except (OSError, ValueError):
        return None


def old_version(host, port, timeout=2.5):
    """The version of an Instant Share from before 1.2 (plain http) at host:port, or None."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    where = f"[{host}]" if ":" in host else host
    try:
        with opener.open(urllib.request.Request(f"http://{where}:{port}/api/hello", headers={"X-TextShare": "1"}),
                         timeout=timeout) as r:
            return str(json.loads(r.read()).get("version") or "1.0")
    except (OSError, ValueError):
        return None


class Api:
    """What the pages in the app's window may ask of it: its own pages (setup, connecting) and Instant Share's page."""

    def __init__(self, app):
        self._app = app

    def register(self, name, email):
        """The register page: keeps the user's name and e-mail (registration.py), then Instant Share starts."""
        r, error = reg.save(name, email)
        if not r:
            return {"ok": False, "error": error}
        threading.Thread(target=self._app.after_register, daemon=True).start()
        return {"ok": True}

    def find(self):
        app = self._app
        return {"found": ts.discover(), "me": app.me, "mode": app.mode,
                "host": app.settings.get("host", "") if app.mode == "join" else ""}

    def use_this_computer(self, autostart):
        app = self._app
        app.settings["mode"] = "host"
        app.settings.save()
        autostart_set(autostart_command(app.data) if autostart else None)
        threading.Thread(target=app.become_host, daemon=True).start()
        return {"ok": True}

    def connect_to(self, text, autostart):
        """A computer typed in: "OFFICE-PC", "192.168.1.20" or either with ":port"."""
        text = str(text).strip().removeprefix("http://").rstrip("/")
        host, _, port = text.rpartition(":") if text.count(":") == 1 else (text, "", "")
        host = host.strip("[]") or text
        try:
            port = int(port) if port else ts.DEFAULT_PORT
        except ValueError:
            return {"error": "The part after the colon must be a port number, for example OFFICE-PC:8765."}
        if not host:
            return {"error": "Type the name or the address of the computer that keeps the history."}
        answer = hello(host, port, timeout=4)
        if not answer:
            old = old_version(host, port)
            if old:
                return {"error": f"{host} runs Instant Share {old}. Update it to {ts.VERSION} there first."}
            return {"error": f"Instant Share does not answer at {host} (port {port}). Check that it is running there, "
                             "on the same network, and that Windows allowed it on Private networks."}
        return self._join(answer["server"], host, port, autostart)

    def join_found(self, name, address, port, autostart):
        if not hello(address, int(port)):
            old = old_version(address, int(port))
            if old:
                return {"error": f"{name} runs Instant Share {old}. Update it to {ts.VERSION} there first."}
            return {"error": f"{name} stopped answering. Look again in a moment."}
        return self._join(name, address, int(port), autostart)

    def _join(self, name, address, port, autostart):
        app = self._app
        if name.lower() == app.me.lower() and app.mode == "host":
            return {"error": "That is this computer, which keeps the history already."}
        autostart_set(autostart_command(app.data) if autostart else None)
        threading.Thread(target=app.choose_join, args=(name, address, port), daemon=True).start()
        return {"ok": True}

    def open_setup(self):
        self._app.open_setup()

    def pair_with(self, code):
        return self._app.pair(str(code))

    def pair_again(self):
        """The page found this device unknown to the computer that keeps the history."""
        if self._app.mode == "join":
            self._app.settings.pop("key", None)
            self._app.settings.save()
            self._app.ask_code(f"{self._app.settings.get('host', '')} no longer knows this computer. Join it again "
                               "with a new code.")

    def quit(self):
        threading.Thread(target=self._app.quit, daemon=True).start()

    def check_update(self):
        return self._app.check_update()

    def update_now(self):
        return self._app.update_now()

    def back(self):
        self._app.resume()

    def retry(self):
        self._app.wake.set()

    def set_theme(self, theme):
        self._app.set_theme(str(theme))

    def set_name(self, name):
        self._app.set_name(name)

    def get_options(self):
        return self._app.options()

    def set_option(self, name, value):
        return self._app.set_option(str(name), value)

    def open_folder(self):
        """The folder that holds the history (on the computer that keeps it)."""
        if self._app.mode == "host":
            os.startfile(self._app.data)

    def choose_save_folder(self):
        import webview
        chosen = self._app.window.create_file_dialog(webview.FileDialog.FOLDER, directory=str(self._app.save_folder))
        if chosen:
            self._app.settings["save_folder"] = str(chosen[0] if isinstance(chosen, (list, tuple)) else chosen)
            self._app.settings.save()
        return self._app.options()

    def reset_save_folder(self):
        self._app.settings.pop("save_folder", None)
        self._app.settings.save()
        return self._app.options()

    def copy_image(self, url):
        try:
            copy_image(self._app.fetch(str(url)))
            return {"ok": True}
        except Exception as e:
            return {"error": f"Could not copy the photo: {e}"}

    def save_file(self, url, name):
        try:
            name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "", str(name)).strip(" .") or "file"
            saved = save_in_downloads(name, self._app.fetch(str(url)), self._app.save_folder)
            return {"ok": True, "name": saved.name}
        except Exception as e:
            return {"error": f"Could not save it: {e}"}

    # waiting to send (1.4): Instant Share's page and the can't-reach page keep here what is written while the
    # computer that keeps the history is out of reach; the app shares it as soon as it can

    def outbox_list(self):
        return self._app.outbox.summary()

    def outbox_add(self, entry):
        answer = self._app.outbox.add(entry)
        if answer.get("ok"):
            self._app.outbox_wake.set()
        return answer

    def outbox_get(self, uid):
        return self._app.outbox.get(str(uid))

    def outbox_remove(self, uid):
        return self._app.outbox.remove(str(uid))

    def outbox_send(self, uid=""):
        """Share what waits now (and one the history refused before, tried again)."""
        if uid:
            self._app.outbox.set_error(str(uid), "")
        self._app.outbox_wake.set()
        return {"ok": True}

    def open_history(self):
        threading.Thread(target=self._app.open_history, daemon=True).start()


# ---------------------------------------------------------------- the app's own pages

PAGE_STYLE = """
:root { --bg: #121314; --surface: #191a1b; --surface-2: #202122; --surface-3: #2a2b2c; --line: #2a2b2c;
  --line-strong: #333536; --text: #cccccc; --dim: #8c8c8c; --accent: #f0c33c; --accent-strong: #5b2c83;
  --accent-hover: #45215f; --accent-soft: rgba(91,44,131,.3); --inset: #121314; --danger: #f48771; --danger-fill: #c74e39;
  --ok: #73c991; color-scheme: dark }
:root[data-theme="light"] { --bg: #fbf6ea; --surface: #ffffff; --surface-2: #f6f5f8; --surface-3: #eceaf0;
  --line: #e7e4ec; --line-strong: #d4d0dc; --text: #17141c; --dim: #6a6175; --accent: #5b2c83;
  --accent-strong: #5b2c83; --accent-hover: #45215f; --accent-soft: rgba(91,44,131,.1); --inset: #f6f5f8; --danger: #e5484d;
  --danger-fill: #e5484d; --ok: #1a7f37; color-scheme: light }
* { box-sizing: border-box }
[hidden] { display: none !important }
/* SCAFFApps scrollbars, as the SCAFFERP system and www.scaffapps.com: a slim thumb on no track, wider under the
   pointer; never the grey Windows bar with its arrows. Purple, stronger in the dark appearance to show on the dark grey.
   Touch screens keep their own. */
@media (pointer: fine) {
  :root { --sb-thumb: rgba(91, 44, 131, .85); --sb-thumb-on: #5b2c83 }
  :root[data-theme="light"] { --sb-thumb: rgba(91, 44, 131, .55); --sb-thumb-on: rgba(91, 44, 131, .85) }
  ::-webkit-scrollbar { width: 12px; height: 12px; background: transparent }
  ::-webkit-scrollbar-track, ::-webkit-scrollbar-corner { background: transparent }
  ::-webkit-scrollbar-button { display: none; width: 0; height: 0 }
  ::-webkit-scrollbar-thumb { background-color: var(--sb-thumb); border: 3px solid transparent; border-radius: 12px;
    background-clip: padding-box; min-height: 40px; min-width: 40px }
  ::-webkit-scrollbar-thumb:hover, ::-webkit-scrollbar-thumb:active { background-color: var(--sb-thumb-on); border-width: 1.5px }
}
body { margin: 0; min-height: 100vh; background: var(--bg); color: var(--text);
  font: 13px/1.5 "Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif; -webkit-font-smoothing: antialiased;
  display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 32px 24px }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px }
input[type="checkbox"] { accent-color: var(--accent-strong) }
.wrap { width: min(860px, 100%) }
.brand { display: flex; align-items: center; gap: 9px; font-weight: 600; font-size: 14px; margin-bottom: 24px }
h1 { margin: 0 0 8px; font-size: 24px; font-weight: 600; letter-spacing: -.01em }
.lead { margin: 0 0 24px; color: var(--dim); font-size: 14px; max-width: 640px }
.cards { display: grid; grid-template-columns: 1fr 1fr; gap: 16px }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 20px }
.card.now { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft) }
.card h2 { margin: 0 0 8px; font-size: 15px; font-weight: 600 }
.card p { margin: 0 0 14px; color: var(--dim) }
.tag { display: inline-block; margin-left: 8px; padding: 1px 8px; border-radius: 99px; background: var(--accent-strong);
  color: #fff; font-size: 11px; font-weight: 600; vertical-align: 1px }
button { font: inherit; color: inherit; cursor: pointer }
.btn { display: inline-flex; align-items: center; justify-content: center; height: 32px; padding: 0 12px;
  background: var(--surface-2); border: 1px solid var(--line); border-radius: 8px; font-weight: 500;
  transition: background .12s, border-color .12s }
.btn:hover { background: var(--surface-3); border-color: var(--line-strong) }
.btn:disabled { opacity: .55; cursor: default }
.btn.primary { padding: 0 16px; color: #fff; background: var(--accent-strong); border-color: transparent; font-weight: 600 }
.btn.primary:hover { background: var(--accent-hover) }
.link { background: none; border: 0; padding: 0; color: var(--dim); text-decoration: underline; font-size: 12px }
.link:hover { color: var(--accent) }
.chk { display: flex; gap: 8px; align-items: center; margin: 16px 0 0; color: var(--dim); cursor: pointer }
.found { display: grid; gap: 6px; margin: 0 0 10px }
.found button { display: flex; justify-content: space-between; align-items: center; width: 100%; height: 36px; padding: 0 12px;
  text-align: left; background: var(--surface-2); border: 1px solid var(--line); border-radius: 8px }
.found button:hover { border-color: var(--accent); background: var(--surface-3) }
.found small { color: var(--dim) }
.status { color: var(--dim); margin: 0 0 10px }
label.small { display: block; margin: 14px 0 6px; color: var(--dim) }
.row { display: flex; gap: 8px }
input:not([type]) { flex: 1; min-width: 0; height: 32px; padding: 0 10px; background: var(--inset);
  border: 1px solid var(--line); border-radius: 8px; color: var(--text); font: inherit; outline: none;
  transition: border-color .12s, box-shadow .12s }
input:not([type]):focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft) }
.err { color: var(--danger); margin: 10px 0 0; min-height: 1em }
.foot { margin-top: 22px; color: var(--dim); font-size: 12px; display: flex; gap: 14px; align-items: center }
.pub { color: var(--text); font-weight: 600 }
.spin { display: inline-block; width: 12px; height: 12px; border: 2px solid var(--line-strong); border-top-color: var(--accent);
  border-radius: 50%; animation: spin .8s linear infinite; vertical-align: -1px; margin-right: 6px }
@keyframes spin { to { transform: rotate(1turn) } }
.center { text-align: center; max-width: 520px }
.center .brand { justify-content: center }
.center .row { justify-content: center; margin-top: 20px }
@media (max-width: 760px) { .cards { grid-template-columns: 1fr } }
@media (prefers-reduced-motion: reduce) { .spin { animation: none } }
"""

MARK = ('<svg width="24" height="24" viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="8" '
        'fill="#5b2c83"/><path d="M7 12h17l-4-4M25 20H8l4 4" fill="none" stroke="#f0c33c" stroke-width="2.6" '
        'stroke-linecap="round" stroke-linejoin="round"/></svg>Instant Share')

READY = """
function ready(fn) { if (window.pywebview && window.pywebview.api) fn(); else window.addEventListener("pywebviewready", fn); }
const $ = (s) => document.querySelector(s);
"""

SETUP = """
<div class="wrap">
  <div class="brand">{mark}</div>
  <h1>Set up Instant Share on this computer</h1>
  <p class="lead">One computer keeps the history and the others connect to it, each joining once with a code shown
  there. Phones use the Instant Share app for Android, or a browser.</p>
  <div class="cards">
    <section class="card" id="hostCard">
      <h2>Keep the history here<span class="tag" id="hostNow" hidden>NOW</span></h2>
      <p>Choose this on <b>one</b> computer only, one that stays on while you work. Windows will ask to allow Instant
      Share on networks: allow it on <b>Private networks</b>.</p>
      <button class="btn primary" id="useThis">Use this computer</button>
    </section>
    <section class="card" id="joinCard">
      <h2>Connect to the computer that keeps it<span class="tag" id="joinNow" hidden>NOW</span></h2>
      <p class="status" id="status"><span class="spin"></span>Looking for Instant Share on your network…</p>
      <div class="found" id="found"></div>
      <button class="link" id="again" hidden>Look again</button>
      <label class="small" for="addr">Not listed? Its name or address:</label>
      <div class="row"><input id="addr" placeholder="e.g. OFFICE-PC or 192.168.1.20" autocomplete="off" spellcheck="false">
        <button class="btn" id="go">Connect</button></div>
      <p class="err" id="err"></p>
    </section>
  </div>
  <label class="chk"><input type="checkbox" id="autostart"> Start Instant Share when I sign in to Windows. It waits in
    the tray, ready to show what others share.</label>
  <div class="foot"><span>This computer: <b id="me"></b></span><span>Instant Share {version}</span><span>Published by <b class="pub">SCAFFApps</b> - www.scaffapps.com</span>
    <span style="flex:1"></span><button class="btn" id="back" hidden>Back</button></div>
</div>
<script>
{ready}
const info = {info};
$("#me").textContent = info.me;
$("#autostart").checked = info.autostart;
if (info.mode) $("#back").hidden = false;
if (info.mode === "host") { $("#hostNow").hidden = false; $("#hostCard").classList.add("now"); $("#useThis").textContent = "Keep it here"; }
if (info.mode === "join") { $("#joinNow").hidden = false; $("#joinCard").classList.add("now"); }
const busy = (on) => document.querySelectorAll("button, input").forEach((b) => { b.disabled = on; });
const fail = (r) => { busy(false); $("#err").textContent = (r && r.error) || "Something went wrong."; };
const auto = () => $("#autostart").checked;

async function find() {
  $("#again").hidden = true; $("#found").textContent = "";
  $("#status").innerHTML = '<span class="spin"></span>Looking for Instant Share on your network…';
  const r = await window.pywebview.api.find();
  const others = r.found.filter((f) => !(f.name.toLowerCase() === r.me.toLowerCase() && r.mode === "host"));
  $("#status").textContent = others.length ? "Found on your network:"
    : "None found. Is Instant Share running on the computer that keeps the history, on this network?";
  for (const f of others) {
    const b = document.createElement("button");
    const left = document.createElement("span"), right = document.createElement("small");
    left.textContent = f.name + (f.name.toLowerCase() === r.me.toLowerCase() ? "  (this computer)" : "");
    right.textContent = (r.host && r.host.toLowerCase() === f.name.toLowerCase() ? "connected · " : "") + f.address;
    b.append(left, right);
    b.addEventListener("click", async () => {
      busy(true); $("#err").textContent = "";
      const res = await window.pywebview.api.join_found(f.name, f.address, f.port, auto());
      if (!res.ok) fail(res);
    });
    $("#found").append(b);
  }
  $("#again").hidden = false;
}
ready(() => {
  find();
  $("#again").addEventListener("click", find);
  $("#useThis").addEventListener("click", async () => {
    busy(true);
    const res = await window.pywebview.api.use_this_computer(auto());
    if (!res.ok) fail(res);
  });
  const go = async () => {
    if (!$("#addr").value.trim()) { $("#addr").focus(); return; }
    busy(true); $("#err").textContent = "";
    const res = await window.pywebview.api.connect_to($("#addr").value, auto());
    if (!res.ok) { fail(res); $("#addr").focus(); }
  };
  $("#go").addEventListener("click", go);
  $("#addr").addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  $("#back").addEventListener("click", () => window.pywebview.api.back());
});
</script>
"""

NOTICE = """
<div class="center">
  <div class="brand">{mark}</div>
  <h1>{title}</h1>
  <p class="lead">{text}</p>
  {buttons}
</div>
<script>
{ready}
ready(() => {
  const r = $("#retry"), s = $("#setup");
  if (r) r.addEventListener("click", () => { r.disabled = true; r.textContent = "Trying…"; window.pywebview.api.retry(); });
  if (s) s.addEventListener("click", () => window.pywebview.api.open_setup());
});
</script>
"""

PAIR = """
<div class="center">
  <div class="brand">{mark}</div>
  <h1>Join Instant Share on {name}</h1>
  <p class="lead" id="why"></p>
  <p class="lead">Each device joins once, with a code. On a device that has joined already ({name} itself, for
  example), open Settings (the gear) and choose <b>Add a device</b>. Type the code it shows here:</p>
  <div class="row"><input id="code" inputmode="numeric" maxlength="7" placeholder="000 000" autocomplete="off"
    style="font: 600 22px/1 Consolas, monospace; letter-spacing: .2em; text-align: center; width: 190px">
    <button class="btn primary" id="join">Join</button></div>
  <p class="err" id="err"></p>
  <div class="row"><button class="btn" id="setup">Choose another computer</button></div>
</div>
<script>
{ready}
const info = {info};
$("#why").textContent = info.why || "";
$("#why").hidden = !info.why;
const code = $("#code");
code.addEventListener("input", () => { const d = code.value.replace(/\\D/g, "").slice(0, 6); code.value = d.length > 3 ? d.slice(0, 3) + " " + d.slice(3) : d; });
ready(() => {
  code.focus();
  const go = async () => {
    const c = code.value.replace(/\\D/g, "");
    if (c.length !== 6) { $("#err").textContent = "The code has 6 digits."; code.focus(); return; }
    $("#join").disabled = true; $("#join").textContent = "Checking…"; $("#err").textContent = "";
    const r = await window.pywebview.api.pair_with(c);
    if (!r.ok) { $("#err").textContent = r.error || "Could not join."; $("#join").disabled = false; $("#join").textContent = "Join"; code.select(); }
  };
  $("#join").addEventListener("click", go);
  code.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  $("#setup").addEventListener("click", () => window.pywebview.api.open_setup());
});
</script>
"""

REGISTER = """
<div class="center">
  <div class="brand">{mark}</div>
  <h1>Welcome to Instant Share</h1>
  <p class="lead">Before you start, please tell us who you are. You are asked only once.</p>
  <div style="width: min(380px, 100%); margin: 0 auto; text-align: left">
    <label style="display: block; margin-bottom: 12px; font-weight: 600">Your name
      <input id="name" autocomplete="name" maxlength="150" style="display: block; width: 100%; height: 36px; margin-top: 6px; padding: 0 10px; background: var(--inset); border: 1px solid var(--line-strong); border-radius: 8px; color: var(--text); font: inherit; font-weight: 400"></label>
    <label style="display: block; margin-bottom: 12px; font-weight: 600">E-mail address
      <input id="email" type="email" autocomplete="email" maxlength="150" style="display: block; width: 100%; height: 36px; margin-top: 6px; padding: 0 10px; background: var(--inset); border: 1px solid var(--line-strong); border-radius: 8px; color: var(--text); font: inherit; font-weight: 400"></label>
    <p class="err" id="err"></p>
    <button class="btn primary" id="go" style="width: 100%; height: 38px">Continue</button>
  </div>
  <p class="lead" style="font-size: 12px; margin: 22px auto 0">Published by <b class="pub">SCAFFApps</b> - www.scaffapps.com</p>
</div>
<script>
{ready}
ready(() => {
  $("#name").focus();
  const say = (t, f) => { $("#err").textContent = t; if (f) $(f).focus(); };
  const go = async () => {
    const name = $("#name").value.trim(), email = $("#email").value.trim();
    if (!name) return say("Please enter your name.", "#name");
    if (!/^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$/.test(email)) return say("Please enter a valid e-mail address.", "#email");
    $("#go").disabled = true; say("");
    const r = await window.pywebview.api.register(name, email);
    if (!r.ok) { $("#go").disabled = false; say(r.error || "Could not save your details."); }
  };
  $("#go").addEventListener("click", go);
  $("#email").addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
});
</script>
"""


# "Write for later" on the can't-reach page (Windows) and the Android app's (android/make_setup.py takes it from here):
# what is written there waits on the device, kept by the app, and is shared oldest first as soon as the computer that
# keeps the history answers. later(io) runs it; io is the app's side: list, add, get, remove, send (the outbox), the
# devices last seen, the computer's name, and what to do when it answers again (answered, open).

LATER_STYLE = """
:root { --accent-line: rgba(240, 195, 60, .5) }
:root[data-theme="light"] { --accent-line: rgba(91, 44, 131, .4) }
.later { margin-top: 26px; text-align: left }
.later .note { margin: 0 0 12px }
.later textarea { display: block; width: 100%; min-height: 130px; resize: vertical; padding: 10px 12px; background: var(--inset);
  border: 1px solid var(--line); border-radius: 8px; color: var(--text); outline: none;
  font: 13px/1.55 "Cascadia Mono", "Cascadia Code", Consolas, ui-monospace, monospace;
  transition: border-color .12s, box-shadow .12s }
.later textarea:focus, .later.drop textarea { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft) }
.later textarea::placeholder { color: var(--dim); opacity: .8 }
.later .bar { display: flex; align-items: center; gap: 8px; margin-top: 10px; flex-wrap: wrap }
.later .err { margin: 8px 0 0 }
.later .err:empty { display: none }
#laterTo { gap: 5px }
.grow { flex: 1 }
.dim { color: var(--dim) }
.att { display: flex; align-items: center; gap: 10px; margin-top: 8px; padding: 6px; background: var(--surface-2);
  border: 1px solid var(--line); border-radius: 8px }
.att img { width: 40px; height: 40px; object-fit: cover; border-radius: 6px; flex: none }
.att-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0 }
.x { margin-left: auto; flex: none; width: 28px; height: 28px; background: none; border: 0; border-radius: 6px; color: var(--dim) }
.x:hover { background: var(--surface-3); color: var(--text) }
.badge { display: inline-grid; place-items: center; width: 36px; height: 36px; flex: none; border-radius: 6px; color: #fff;
  font-size: 10px; font-weight: 700; letter-spacing: .03em; background: #5b2c83 }
.to-wrap { position: relative }
.pop { position: absolute; right: 0; bottom: calc(100% + 6px); z-index: 10; width: 280px; max-height: 320px; overflow-y: auto;
  padding: 6px; background: var(--surface); border: 1px solid var(--line-strong); border-radius: 10px;
  box-shadow: 0 12px 36px rgba(0, 0, 0, .45) }
.pop-row { display: flex; align-items: center; gap: 8px; padding: 6px 8px; border-radius: 6px; cursor: pointer }
.pop-row:hover { background: var(--surface-3) }
.pop-row .nm { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0 }
.pop-note { margin: 6px 8px 4px; color: var(--dim); font-size: 11px }
.btn.sm { height: 26px; padding: 0 9px; font-size: 12px; border-radius: 6px }
.btn.danger, .btn.danger:hover { color: #fff; background: var(--danger-fill); border-color: transparent }
.waiting { margin-top: 22px; text-align: left }
.waiting h3 { margin: 0 0 8px; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .06em; color: var(--dim) }
.wlist { display: flex; flex-direction: column; gap: 8px }
.wcard { background: var(--surface); border: 1px dashed var(--accent-line); border-radius: 10px; padding: 7px 8px 10px 12px }
.wcard.failed { border-color: var(--danger) }
.whead { display: flex; align-items: center; gap: 10px; font-size: 12px; color: var(--dim) }
.wcard.failed .when { color: var(--danger) }
.whead .to { color: var(--accent); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0 }
.whead .acts { margin-left: auto; display: flex; gap: 4px; flex: none }
.wfile { margin-top: 6px; font-weight: 600; overflow-wrap: anywhere }
.wcard pre { margin: 6px 0 0; font: 12.5px/1.55 "Cascadia Mono", Consolas, ui-monospace, monospace; white-space: pre-wrap;
  overflow-wrap: anywhere; max-height: calc(1.55em * 5); overflow: hidden }
.wrap.away { width: min(720px, 100%) }
.away .row { justify-content: flex-start; margin-top: 0 }
"""

LATER_HTML = """
<section class="card later" id="later">
  <h2>Write for later</h2>
  <p class="note dim" id="laterNote"></p>
  <textarea id="laterText" dir="auto" spellcheck="false" placeholder="Write or paste what to share, or add a file"></textarea>
  <div id="laterFile" class="att" hidden><img id="laterThumb" alt="" hidden><span id="laterBadge" class="badge" hidden></span>
    <span class="att-name" id="laterName"></span><span class="dim" id="laterSize"></span>
    <button class="x" id="laterDrop" type="button" title="Remove the file">✕</button></div>
  <div class="bar">
    <button class="btn" id="laterAttach" type="button" title="Add a file, up to 5 MB">File</button>
    <input id="laterInput" type="file" hidden>
    <span class="grow"></span>
    <div class="to-wrap"><button class="btn" id="laterTo" type="button" title="Who should see it">To: <span id="laterToLabel">Everyone</span> ▾</button>
      <div class="pop" id="laterPop" hidden>
        <label class="pop-row"><input type="checkbox" id="laterAll"><span class="nm">Everyone</span></label>
        <div id="laterList"></div><p class="pop-note" id="laterToNote"></p>
      </div></div>
    <button class="btn primary" id="laterGo" type="button">Send later</button>
  </div>
  <p class="err" id="laterErr"></p>
</section>
<section class="waiting" id="waiting" hidden><h3 id="waitTitle"></h3><div class="wlist" id="waitList"></div></section>
"""

LATER_SCRIPT = """
const LATER_ALLOWED = {allowed};          // the endings the history takes (it checks the contents too)
const LATER_MAX = 5 * 1024 * 1024;
function later(io) {
  const text = $("#laterText"), go = $("#laterGo"), card = $("#later");
  const devices = (io.devices || []).filter((d) => d && d.id && d.name);
  const targets = new Set();
  let file = null, answered = false;
  const say = (t) => { $("#laterErr").textContent = t || ""; };
  const size = (b) => b < 1024 ? b + " bytes" : b < 1048576 ? Math.round(b / 1024) + " KB" : (b / 1048576).toFixed(2) + " MB";
  const plural = (n, w) => n.toLocaleString() + " " + w + (n === 1 ? "" : "s");
  const when = (at) => {
    const d = new Date(at), t = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    return d.toDateString() === new Date().toDateString() ? t : d.toLocaleDateString(undefined, { day: "numeric", month: "short" }) + " " + t;
  };
  const extOf = (n) => { const m = /\\.[^.]+$/.exec(n || ""); return m ? m[0].toLowerCase() : ""; };
  const newId = () => Array.from(crypto.getRandomValues(new Uint8Array(12)), (b) => b.toString(16).padStart(2, "0")).join("");
  $("#laterNote").textContent = "What you write here waits on " + io.where + ", and is shared as soon as " + io.host
    + " answers, even with " + io.closed + " closed.";

  /* a file: the File button, pasting a picture, or dropping it here */
  function attach(f, data) {
    if (!f) return;
    const ext = extOf(f.name) || ({ "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp" }[f.type] || "");
    if (!LATER_ALLOWED.includes(ext)) return say("That kind of file can't be shared. Photos, PDFs, Office documents, text, audio, short videos and ZIP files can.");
    if (f.size > LATER_MAX) return say("That file is " + size(f.size) + ". Files can be up to 5 MB.");
    const take = (b64) => {
      file = { name: extOf(f.name) ? f.name : "pasted image" + ext, type: f.type || "", size: f.size, data: b64, ext,
               url: "data:" + (f.type || "application/octet-stream") + ";base64," + b64 };
      drawFile(); say("");
    };
    if (data != null) return take(data);
    const r = new FileReader();
    r.onload = () => { const url = String(r.result); take(url.slice(url.indexOf(",") + 1)); };
    r.onerror = () => say("Could not read that file.");
    r.readAsDataURL(f);
  }
  function drawFile() {
    $("#laterFile").hidden = !file;
    if (!file) return;
    const image = [".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"].includes(file.ext);
    $("#laterThumb").hidden = !image;
    if (image) $("#laterThumb").src = file.url;
    $("#laterBadge").hidden = image;
    $("#laterBadge").textContent = (file.ext.slice(1) || "FILE").toUpperCase().slice(0, 4);
    $("#laterName").textContent = file.name;
    $("#laterSize").textContent = size(file.size);
  }
  $("#laterInput").accept = LATER_ALLOWED.join(",");
  $("#laterAttach").addEventListener("click", () => $("#laterInput").click());
  $("#laterInput").addEventListener("change", (e) => { attach(e.target.files[0]); e.target.value = ""; });
  $("#laterDrop").addEventListener("click", () => { file = null; drawFile(); });
  text.addEventListener("paste", (e) => {
    const f = e.clipboardData && e.clipboardData.files && e.clipboardData.files[0];
    if (f) { e.preventDefault(); attach(f); }
  });
  const hasFiles = (e) => e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files");
  card.addEventListener("dragover", (e) => { if (hasFiles(e)) { e.preventDefault(); card.classList.add("drop"); } });
  card.addEventListener("dragleave", (e) => { if (!card.contains(e.relatedTarget)) card.classList.remove("drop"); });
  card.addEventListener("drop", (e) => { card.classList.remove("drop"); if (hasFiles(e)) { e.preventDefault(); attach(e.dataTransfer.files[0]); } });
  window.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
  window.addEventListener("drop", (e) => { if (hasFiles(e)) e.preventDefault(); });     // never open the file instead

  /* who should see it: the devices as last seen while in reach */
  const names = () => [...targets].map((id) => (devices.find((d) => d.id === id) || {}).name || "a device");
  function drawTo() {
    const n = names();
    $("#laterToLabel").textContent = !n.length ? "Everyone" : n[0] + (n.length > 1 ? " +" + (n.length - 1) : "");
    $("#laterAll").checked = !targets.size;
    const list = $("#laterList");
    list.textContent = "";
    for (const d of devices) {
      const row = document.createElement("label"), box = document.createElement("input"), nm = document.createElement("span");
      row.className = "pop-row"; box.type = "checkbox"; box.checked = targets.has(d.id);
      box.addEventListener("change", () => { box.checked ? targets.add(d.id) : targets.delete(d.id); drawTo(); });
      nm.className = "nm"; nm.textContent = d.name;
      row.append(box, nm);
      list.append(row);
    }
    $("#laterToNote").textContent = devices.length ? "Tick devices to share with them only: the others will not see it."
      : "The other devices are listed here once " + io.where + " has reached " + io.host + ".";
  }
  $("#laterAll").addEventListener("change", () => { if ($("#laterAll").checked) targets.clear(); drawTo(); });
  $("#laterTo").addEventListener("click", (e) => { e.stopPropagation(); $("#laterPop").hidden = !$("#laterPop").hidden; });
  document.addEventListener("click", (e) => { if (!e.target.closest(".to-wrap")) $("#laterPop").hidden = true; });
  drawTo();

  /* Send later: kept by the app (or, once it answers again, Share: kept, sent at once, and on to the history) */
  async function keep() {
    say("");
    if (!text.value.trim() && !file) { say("Write or paste something first, or add a file."); text.focus(); return; }
    go.disabled = true;
    let r;
    try {
      r = await io.add({ uid: newId(), text: text.value, to: [...targets], to_names: names(), written: Date.now(),
                         file: file && { name: file.name, type: file.type, size: file.size, data: file.data } });
    } catch (e) { r = { error: String((e && e.message) || e) }; }
    go.disabled = false;
    if (!r || !r.ok) { say((r && r.error) || "Could not keep it."); return; }
    text.value = ""; file = null; drawFile();
    if (answered) { io.open(); return; }
    await refresh();
  }
  go.addEventListener("click", keep);
  text.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); keep(); } });

  /* what waits, oldest first: Edit takes it back into the box above, Delete (twice) drops it */
  async function refresh() {
    let list = [];
    try { const got = await io.list(); if (Array.isArray(got)) list = got; } catch {}
    $("#waiting").hidden = !list.length;
    $("#waitTitle").textContent = "Waiting to send · " + list.length;
    const box = $("#waitList");
    box.textContent = "";
    for (const w of list) box.append(waitingRow(w));
  }
  function waitingRow(w) {
    const el = document.createElement("div");
    el.className = "wcard" + (w.error ? " failed" : "");
    el.innerHTML = '<div class="whead"><span class="when"></span><span class="to"></span><span class="acts"></span></div>'
      + '<div class="wfile" hidden></div><pre dir="auto"></pre>';
    el.querySelector(".when").textContent = w.error ? "Not shared: " + w.error
      : "Written " + when(w.written) + (w.size ? " · " + plural(w.size, "character") : "");
    if (w.to_names && w.to_names.length) el.querySelector(".to").textContent = "→ " + w.to_names.join(", ");
    if (w.file) { const f = el.querySelector(".wfile"); f.hidden = false; f.textContent = w.file.name + " · " + size(w.file.size); }
    const pre = el.querySelector("pre");
    if (w.size) pre.textContent = w.text + (w.size > w.text.length ? "\\n…" : ""); else pre.hidden = true;
    const acts = el.querySelector(".acts");
    const add = (label, run) => {
      const b = document.createElement("button");
      b.type = "button"; b.className = "btn sm"; b.textContent = label;
      b.addEventListener("click", run);
      acts.append(b);
      return b;
    };
    if (w.error) add("Try again", async () => { await io.send(w.uid); setTimeout(refresh, 1500); });
    add("Edit", async () => {
      if (text.value.trim() || file) { say("Send or clear what is in the box first, then edit this one."); return; }
      const full = await io.get(w.uid);
      if (!full) return refresh();
      await io.remove(w.uid);
      text.value = full.text || "";
      if (full.file && full.file.data) attach({ name: full.file.name, type: full.file.type || "", size: full.file.size }, full.file.data);
      else { file = null; drawFile(); }
      targets.clear();
      (full.to || []).forEach((id) => targets.add(id));
      drawTo();
      refresh();
      text.focus();
    });
    const del = add("Delete", async () => {
      if (!del.classList.contains("danger")) {                   // asked twice: it has not been shared
        del.classList.add("danger"); del.textContent = "Delete it";
        setTimeout(() => { del.classList.remove("danger"); del.textContent = "Delete"; }, 4000);
        return;
      }
      await io.remove(w.uid);
      refresh();
    });
    return el;
  }
  window.textShareOutbox = () => refresh();       // the app shared some (or kept one): the list again

  /* it answers again while something is written here: the page says so instead of making way for the history */
  function hold() {
    if (!text.value.trim() && !file) return false;
    if (!answered) { answered = true; go.textContent = "Share"; io.answered(); }
    return true;
  }
  window.holdForDraft = hold;
  /* shared from another app (Android's Share menu) */
  function take(s) {
    if (!s) return;
    if (s.text) text.value = text.value.trim() ? text.value.replace(/\\s*$/, "") + "\\n" + s.text : s.text;
    if (s.file) attach({ name: s.file.name, type: s.file.type || "", size: s.file.size }, s.file.data);
  }
  refresh();
  return { hold, take, has: () => !!(text.value.trim() || file) };
}
"""

UNREACHABLE = """
<div class="wrap away">
  <div class="brand">{mark}</div>
  <h1 id="awayTitle">Can't reach {name}</h1>
  <p class="lead" id="awayText">Instant Share must be running on {name}, and both computers must be on the same network.
  Trying again every few seconds…</p>
  <div class="row"><button class="btn" id="retry">Try again now</button><button class="btn primary" id="openHistory" hidden>Open the history</button>
    <button class="btn" id="setup">Choose another computer</button></div>
  {later}
</div>
<script>
{ready}
const info = {info};
{later_script}
ready(() => {
  const api = window.pywebview.api, r = $("#retry"), open = $("#openHistory");
  r.addEventListener("click", () => {
    r.disabled = true; r.textContent = "Trying…"; api.retry();
    setTimeout(() => { r.disabled = false; r.textContent = "Try again now"; }, 6000);
  });
  $("#setup").addEventListener("click", () => api.open_setup());
  const box = later({
    list: () => api.outbox_list(), add: (e) => api.outbox_add(e), get: (uid) => api.outbox_get(uid),
    remove: (uid) => api.outbox_remove(uid), send: (uid) => api.outbox_send(uid),
    devices: info.devices, host: info.name, where: "this computer", closed: "the window",
    open: () => api.open_history(),
    answered: () => {
      $("#awayTitle").textContent = info.name + " answers again";
      $("#awayText").textContent = "Share what you wrote below, or open the history without it.";
      r.hidden = true; open.hidden = false;
    },
  });
  open.addEventListener("click", () => {
    if (box.has() && !open.classList.contains("danger")) {     // asked twice: what is written here is not kept
      open.classList.add("danger"); open.textContent = "Open it, leaving what you wrote";
      setTimeout(() => { open.classList.remove("danger"); open.textContent = "Open the history"; }, 4000);
      return;
    }
    open.disabled = true;
    api.open_history();
  });
  $("#laterText").focus();
});
</script>
"""


def later_parts():
    """The "Write for later" box, its style and its script, for the can't-reach pages (here and on Android)."""
    script = LATER_SCRIPT.replace("{allowed}", json.dumps(sorted(ts.FILE_TYPES)))
    return LATER_STYLE, LATER_HTML, script


def local_page(kind, info=None, theme="dark"):
    info = info or {}
    style, later_html, later_script = later_parts()
    if kind == "unreachable":
        body = (UNREACHABLE.replace("{later_script}", later_script).replace("{later}", later_html)
                .replace("{mark}", MARK).replace("{name}", html.escape(info.get("name", ""))).replace("{ready}", READY)
                .replace("{info}", json.dumps(info).replace("</", "<\\/")))
        return (f'<!DOCTYPE html><html lang="en" data-theme="{theme}"><head><meta charset="utf-8"><title>{APP}</title>'
                f"<style>{PAGE_STYLE}{style}</style></head><body>{body}</body></html>")
    if kind == "setup":
        body = (SETUP.replace("{mark}", MARK).replace("{version}", ts.VERSION).replace("{ready}", READY)
                .replace("{info}", json.dumps(info).replace("</", "<\\/")))
    elif kind == "register":
        body = REGISTER.replace("{mark}", MARK).replace("{ready}", READY)
    elif kind == "pair":
        body = (PAIR.replace("{mark}", MARK).replace("{name}", html.escape(info.get("name", "")))
                .replace("{ready}", READY).replace("{info}", json.dumps(info).replace("</", "<\\/")))
    else:
        name = html.escape(info.get("name", ""))
        title, text, buttons = {
            "starting": ("Starting…", "", ""),
            "connecting": (f"Connecting to {name}…", "", ""),
            "problem": (html.escape(info.get("title", "")), html.escape(info.get("text", "")),
                        '<div class="row"><button class="btn" id="setup">Setup</button></div>'),
        }[kind]
        body = (NOTICE.replace("{mark}", MARK).replace("{title}", title).replace("{text}", text)
                .replace("{buttons}", buttons).replace("{ready}", READY))
    return (f'<!DOCTYPE html><html lang="en" data-theme="{theme}"><head><meta charset="utf-8"><title>{APP}</title>'
            f"<style>{PAGE_STYLE}</style></head><body>{body}</body></html>")


# ---------------------------------------------------------------- start

def quiet_late_answers():
    """A page that moved on (to the history, or to setup) before the app answered one of its calls makes pywebview
    report a missing JavaScript callback. Nothing is lost; it is left out of the log."""
    default = threading.excepthook

    def hook(args):
        if type(args.exc_value).__name__ == "JavascriptException":
            tb = args.exc_traceback
            while tb:
                if tb.tb_frame.f_code.co_name == "_call" and "webview" in tb.tb_frame.f_code.co_filename:
                    return                       # an answer to a page that has moved on
                tb = tb.tb_next
        default(args)
    threading.excepthook = hook


def log_to_file(folder):
    """No console window, so what Instant Share would print goes to textshare.log in its folder."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    path = folder / "textshare.log"
    try:
        if path.exists() and path.stat().st_size > 1_000_000:
            path.replace(folder / "textshare.old.log")
        sys.stdout = sys.stderr = open(path, "a", encoding="utf-8", errors="replace", buffering=1)
    except OSError:
        pass


def main():
    ap = argparse.ArgumentParser(prog=APP)
    ap.add_argument("--hidden", action="store_true", help="start in the tray, without the window")
    ap.add_argument("--data", type=Path, default=ts.default_data_dir(), help="folder for the history and settings")
    ap.add_argument("--port", type=int, help="port when this computer keeps the history (default 8765)")
    ap.add_argument("--after-update", help=argparse.SUPPRESS)      # started by the version before, which is quitting
    args = ap.parse_args()
    args.data.mkdir(parents=True, exist_ok=True)
    log_to_file(args.data)
    quiet_late_answers()

    instance = Instance(args.data)
    for _ in range(120 if args.after_update else 0):          # the version before is still quitting: wait for it
        if instance.first:
            break
        kernel32.CloseHandle(instance.mutex)
        time.sleep(0.5)
        instance = Instance(args.data)
    if not instance.first:              # already running: bring it to the front instead
        if not instance.wake_first():
            message("Instant Share is already running on this computer.")
        return 0
    log(f"Instant Share {ts.VERSION} starting on {socket.gethostname()}")
    try:
        App(args, instance).run()
    except Exception:
        traceback.print_exc()
        message("Instant Share could not open its window.\n\nIt needs Microsoft Edge WebView2, which Windows 10 and 11 "
                "normally include. Details are in textshare.log in:\n" + str(args.data), error=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
