"""Instant Share for Windows: its own window, tray icon and pop-ups around the server in instantshare.py.

One computer keeps the history: the app runs the server there. The other computers run the same app and connect to
it; they find it on the network by themselves. On every computer the app waits in the tray when its window is closed,
and when another device shares something with it, a pop-up by the tray shows who, a preview, and a Copy button.
Phones, and computers without the app, use a browser.

    pythonw app.py              (build.py packs this into the portable .exe)
        --hidden                start in the tray without the window (how Windows starts it at sign-in)
        --data FOLDER           keep the history and the settings in another folder
        --port 9000             the port when this computer keeps the history (default 8765)

Copyright (c) 2026 SCAFFApps. All rights reserved. See LICENSE.
"""

import argparse
import ctypes
import hashlib
import html
import io
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import winreg
import winsound
from ctypes import wintypes
from pathlib import Path
from urllib.parse import quote, urlsplit

import instantshare as ts

APP = "Instant Share"
TOAST_TITLE = "Instant Share notification"
RES = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "build"     # icon.png, icon.ico
# the programs that start with Windows; automated checks point INSTANTSHARE_RUN_KEY elsewhere so they never touch it
RUN_KEY = os.environ.get("INSTANTSHARE_RUN_KEY") or r"Software\Microsoft\Windows\CurrentVersion\Run"
OLD_RUN_NAME = "Text Share"                  # how 1.0 was listed in the programs that start with Windows
HOST_PORTS = 10                  # when 8765 is taken by another program, the next free one of 8765..8774
EVERY = 3                        # seconds between the app's questions to the computer that keeps the history

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
TITLE_BAR = {"dark": ("#191a1b", "#cccccc"), "light": ("#ffffff", "#171a21")}
BACKGROUND = {"dark": "#121314", "light": "#eef0f4"}


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
    """{"main": hwnd, "toast": hwnd} of this program's windows. (The main one takes the page's title, such as
    "Instant Share · OFFICE-PC", so it is told apart from the pop-up by that one's fixed title.)"""
    found = {}
    pid = os.getpid()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if buf.value == TOAST_TITLE:
                found.setdefault("toast", hwnd)
            elif buf.value.startswith(APP):
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
    """The icon by the clock and its menu, made with Windows' own functions (no third-party library).

    items: [(label, action, checked)] where checked is None or a function saying whether to tick it; None in the
    list is a separator. The first item is the default (bold), and a left click on the icon does it."""

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
        u.CreatePopupMenu.restype = wintypes.HMENU
        u.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
        u.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                     wintypes.HWND, wintypes.LPVOID]
        u.SetMenuDefaultItem.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.UINT]
        u.DestroyMenu.argtypes = [wintypes.HMENU]
        u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.SetForegroundWindow.argtypes = [wintypes.HWND]
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
        try:                                             # menus in Windows' dark mode when it is on
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
        menu = user32.CreatePopupMenu()
        for n, item in enumerate(self.items, 1):
            if item is None:
                user32.AppendMenuW(menu, 0x0800, 0, None)             # MF_SEPARATOR
            else:
                label, _, checked = item
                user32.AppendMenuW(menu, 0x0008 if checked and checked() else 0, n, label)   # MF_CHECKED
        user32.SetMenuDefaultItem(menu, 1, 0)
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        user32.SetForegroundWindow(self.hwnd)            # so the menu closes when clicking elsewhere
        chosen = user32.TrackPopupMenu(menu, 0x0100 | 0x0080 | 0x0002 | 0x0020, point.x, point.y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, 0, 0, 0)          # WM_NULL, as Windows asks after a tray menu
        user32.DestroyMenu(menu)
        if chosen:
            self.do(chosen - 1)

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
        self.window = self.toast = self.tray = self.server = self.beacon = self.store = None
        self.mode = None                 # what runs now: "host", "join", or None while the setup page shows
        self.port = None
        self.base = None                 # http://host:port of the computer that keeps the history, once reached
        self.quitting = False
        self.told_tray = False
        self.lock = threading.RLock()
        self.attempt = 0                 # each new connection attempt makes the older ones give up
        self.wake = threading.Event()    # "Try again" on the can't-reach page
        self.listening = 0               # each new connection makes the older questioner stop
        self.toast_item = None
        self.toast_hidden_at = 0

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
                "save_chosen": bool(self.settings.get("save_folder"))}

    def set_option(self, name, value):
        if name not in ("on_top", "auto_copy"):
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
        hidden = self.args.hidden and self.settings.get("mode") in ("host", "join")
        self.window = webview.create_window(APP, html=self.local_page("starting"), js_api=Api(self), width=1220,
                                            height=820, min_size=(420, 520), hidden=hidden,
                                            on_top=bool(self.settings.get("on_top")),
                                            background_color=BACKGROUND[self.theme], text_select=True)
        self.window.events.closing += self.on_closing
        self.window.events.closed += self.on_closed
        self.window.events.shown += lambda: self.set_theme(self.theme)
        self.toast = webview.create_window(TOAST_TITLE, html=toast_page(self.theme), js_api=ToastApi(self),
                                           width=380, height=176, frameless=True, on_top=True, hidden=True,
                                           focus=False, resizable=False, shadow=True,
                                           background_color=BACKGROUND[self.theme])
        webview.start(self.begin, gui="edgechromium", private_mode=False, icon=str(RES / "icon.ico"),
                      storage_path=str(self.data / "webview"))
        self.listening += 1
        self.stop_host()

    def begin(self):
        self.set_theme(self.theme)
        self.instance.listen(self.show)
        mode = self.settings.get("mode")
        if mode in ("host", "join"):
            self.start_tray()
            command = autostart_get()
            # this copy's own entry follows it when the program moved or was renamed; another copy's is not touched
            if autostart_moved(self.data) or (mine(command, self.data) and command != autostart_command(self.data)):
                autostart_set(autostart_command(self.data))
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
        if self.toast:
            try:
                self.toast.evaluate_js(f"setTheme({json.dumps(self.theme)})")
            except Exception:
                pass

    def set_name(self, name):
        name = str(name).strip()[:40]
        if name and name != self.settings.get("name"):
            self.settings["name"] = name
            self.settings.save()

    def local_page(self, kind, info=None):
        return local_page(kind, info, self.theme)

    def page_url(self, host, port):
        where = f"[{host}]" if ":" in host else host
        return (f"http://{where}:{port}/?app=1&id={self.settings['device_id']}&me={quote(self.name)}"
                f"&theme={self.theme}")

    def show(self):
        if self.window and not self.quitting:
            self.window.show()
            self.window.restore()

    def on_closing(self):
        """Closing the window: once set up, Instant Share waits in the tray, so the others can go on sharing with
        this computer and its pop-ups still come; Quit in the tray menu ends it."""
        if self.quitting or self.mode is None:
            self.quitting = True
            self.hide_toast()
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
        """The main window is gone: the pop-up's hidden window goes too, so the program ends."""
        self.quitting = True
        try:
            self.toast.destroy()
        except Exception:
            pass

    def quit(self):
        self.quitting = True
        self.attempt += 1
        self.listening += 1
        self.hide_toast()
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

    # ---- the tray

    def start_tray(self):
        if self.tray:
            self.tray_title()
            return

        def toggle_autostart():
            autostart_set(None if mine(autostart_get(), self.data) else autostart_command(self.data))

        self.tray = Tray(RES / "icon.ico", APP, [
            ("Open Instant Share", self.show, None),
            ("Start with Windows", toggle_autostart, lambda: mine(autostart_get(), self.data)),
            ("Change setup…", self.open_setup, None),
            None,
            ("Quit Instant Share", self.quit, None)])
        self.tray.start()
        self.tray_title()

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
        self.listen(f"http://127.0.0.1:{self.port}")
        self.load_host_page()

    def load_host_page(self):
        self.window.load_url(self.page_url("localhost", self.port))

    def start_server(self):
        self.store = ts.Store(self.data / "history.db")
        first = self.args.port or int(self.settings.get("host_port", ts.DEFAULT_PORT))
        for port in range(first, first + HOST_PORTS):
            server = ts.make_server(port, self.store)
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
        self.window.load_html(self.local_page("connecting", {"name": self.settings["host"]}))
        threading.Thread(target=self.connect, args=(self.attempt,), daemon=True, name="connect").start()

    def connect(self, attempt):
        shown_problem = False
        while attempt == self.attempt and not self.quitting:
            name = self.settings["host"]
            port = int(self.settings.get("port", ts.DEFAULT_PORT))
            for host in dict.fromkeys(filter(None, [self.settings.get("address"), name])):
                answer = hello(host, port)
                if answer and answer["server"].lower() == name.lower() and attempt == self.attempt:
                    where = f"[{host}]" if ":" in host else host
                    self.listen(f"http://{where}:{port}")
                    self.window.load_url(self.page_url(host, port))
                    return
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
                    self.window.load_html(self.local_page("unreachable", {"name": name}))
                self.wake.wait(5)
                self.wake.clear()

    def choose_join(self, name, address, port):
        self.settings.update(mode="join", host=name, address=address, port=port)
        self.settings.save()
        self.join()

    # ---- what the others share: asked every few seconds, shown in a pop-up

    def ask(self, path, base=None, timeout=6):
        """A question to the computer that keeps the history, as this device."""
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request((base or self.base) + path, headers={
            "X-TextShare": "1", "X-TextShare-Device": self.settings["device_id"],
            "X-TextShare-Name": quote(self.name), "X-TextShare-Kind": "app"})
        with opener.open(request, timeout=timeout) as r:
            body = r.read()
            return json.loads(body) if r.headers.get_content_type() == "application/json" else body

    def fetch(self, url):
        """The bytes of a file in the history; only from the computer that keeps it."""
        if not self.base or not url.startswith(self.base + "/files/"):
            raise OSError("not from the computer that keeps the history")
        return self.ask(url[len(self.base):], timeout=15)

    def listen(self, base):
        self.base = base
        self.listening += 1
        threading.Thread(target=self.questions, args=(base, self.listening), daemon=True, name="questions").start()

    def questions(self, base, turn):
        """Anything new for this device? One question kept open (the computer that keeps the history answers the
        moment something arrives, or after 25 s), asked again at once. It also keeps this device on the others'
        lists as online."""
        after = None
        while turn == self.listening and not self.quitting:
            started = time.monotonic()
            try:
                answer = self.ask("/api/events" + (f"?after={after}&wait=25" if after is not None else ""), base,
                                  timeout=45)
                if after is not None and answer["items"] and turn == self.listening:
                    self.pop_up(base, answer["items"])
                after = answer["newest"] if after is None else max(after, answer["newest"])
                if answer["items"] or time.monotonic() - started > 1:
                    continue                     # straight back to waiting
            except (OSError, ValueError, KeyError):
                pass                             # away for now: the page says so; asked again shortly
            threading.Event().wait(EVERY)

    def pop_up(self, base, items):
        main = my_windows().get("main")
        if main and user32.GetForegroundWindow() == main:
            return                               # the page is in front: it shows the new items itself
        newest = items[0]
        file = dict(newest["file"], url=base + newest["file"]["url"]) if newest.get("file") else None
        self.toast_item = dict(newest, file=file, base=base)
        data = {"id": newest["id"], "who": newest["device"], "text": newest["text"], "size": newest["size"],
                "file": file, "more": len(items) - 1, "to_me": bool(newest.get("to"))}
        try:
            self.toast.evaluate_js(f"show({json.dumps(data)})")
        except Exception:
            return
        self.place_toast()
        if self.settings.get("auto_copy") and (newest["size"] or (file and file["type"].startswith("image/"))):
            def copy_now():
                answer = self.toast_action("copy")
                if answer.get("ok"):
                    try:
                        self.toast.evaluate_js(f"copied({json.dumps('Copied ✓')})")
                    except Exception:
                        pass
            threading.Thread(target=copy_now, daemon=True, name="auto-copy").start()
        try:
            winsound.PlaySound("SystemNotification", winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except RuntimeError:
            pass

    def place_toast(self):
        hwnd = my_windows().get("toast")
        if not hwnd:
            return
        box, work = wintypes.RECT(), wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(box))
        user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work), 0)          # SPI_GETWORKAREA: above the taskbar
        w, h = box.right - box.left, box.bottom - box.top
        margin = 12
        # a tool window: never in the taskbar or Alt+Tab
        user32.SetWindowLongW(hwnd, -20, (user32.GetWindowLongW(hwnd, -20) | 0x80) & ~0x40000)   # TOOLWINDOW, not APPWINDOW
        # on top of everything (HWND_TOPMOST), in the corner above the taskbar
        user32.SetWindowPos(hwnd, wintypes.HWND(-1), work.right - w - margin, work.bottom - h - margin, 0, 0,
                            0x0001 | 0x0010)                # SWP_NOSIZE | SWP_NOACTIVATE
        # shown the standard way (SW_SHOWNOACTIVATE): the window learns it is visible, so its page is drawn - without
        # taking the focus from what the user is doing
        user32.ShowWindow(hwnd, 4)

    def hide_toast(self):
        hwnd = my_windows().get("toast")
        if hwnd:
            user32.ShowWindow(hwnd, 0)               # SW_HIDE

    def toast_action(self, what):
        """Copy (text or photo) or Save (a PDF) the item in the pop-up."""
        item = self.toast_item
        if not item:
            return {"error": "Nothing to copy."}
        try:
            if what == "copy" and item["size"]:
                full = self.ask(f"/api/items/{item['id']}", item["base"])
                copy_text(full["text"])
                return {"ok": True, "label": "Copied ✓"}
            if item.get("file"):
                data = self.ask(item["file"]["url"][len(item["base"]):], item["base"], timeout=15)
                if what == "copy" and item["file"]["type"].startswith("image/"):
                    copy_image(data)
                    return {"ok": True, "label": "Copied ✓"}
                saved = save_in_downloads(item["file"]["name"], data, self.save_folder)
                return {"ok": True, "label": "Saved ✓", "name": saved.name}
        except Exception as e:
            log(f"pop-up {what}: {e}")
            return {"error": "Could not get it: " + str(e)}
        return {"error": "Nothing to copy."}


def hello(host, port, timeout=2.5):
    """Instant Share's answer at host:port, or None."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))      # never through a proxy
    where = f"[{host}]" if ":" in host else host
    request = urllib.request.Request(f"http://{where}:{port}/api/hello", headers={"X-TextShare": "1"})
    try:
        with opener.open(request, timeout=timeout) as r:
            answer = json.loads(r.read())
            return answer if "server" in answer else None
    except (OSError, ValueError):
        return None


class Api:
    """What the pages in the app's window may ask of it: its own pages (setup, connecting) and Instant Share's page."""

    def __init__(self, app):
        self._app = app

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
            return {"error": f"Instant Share does not answer at {host} (port {port}). Check that it is running there, "
                             "on the same network, and that Windows allowed it on Private networks."}
        return self._join(answer["server"], host, port, autostart)

    def join_found(self, name, address, port, autostart):
        if not hello(address, int(port)):
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


class ToastApi:
    """What the pop-up's buttons ask."""

    def __init__(self, app):
        self._app = app

    def act(self, what):
        return self._app.toast_action(str(what))

    def open(self):
        self._app.hide_toast()
        self._app.show()

    def dismiss(self):
        self._app.hide_toast()


# ---------------------------------------------------------------- the app's own pages

PAGE_STYLE = """
:root { --bg: #121314; --surface: #191a1b; --surface-2: #202122; --surface-3: #2a2b2c; --line: #2a2b2c;
  --line-strong: #333536; --text: #cccccc; --dim: #8c8c8c; --accent: #48a0c7; --accent-strong: #297aa0;
  --accent-hover: #2b7da3; --accent-soft: rgba(57,148,188,.16); --danger: #f48771; --danger-fill: #c74e39;
  --ok: #73c991; color-scheme: dark }
:root[data-theme="light"] { --bg: #eef0f4; --surface: #ffffff; --surface-2: #f4f5f8; --surface-3: #e7e9ef;
  --line: #dfe2e9; --line-strong: #c9cdd8; --text: #171a21; --dim: #667085; --accent: #3f63f0;
  --accent-strong: #4a6cf5; --accent-hover: #3f63f0; --accent-soft: rgba(63,99,240,.1); --danger: #e5484d;
  --danger-fill: #e5484d; --ok: #1a7f37; color-scheme: light }
* { box-sizing: border-box }
[hidden] { display: none !important }
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
input:not([type]) { flex: 1; min-width: 0; height: 32px; padding: 0 10px; background: var(--bg);
  border: 1px solid var(--line); border-radius: 8px; color: var(--text); font: inherit; outline: none;
  transition: border-color .12s, box-shadow .12s }
input:not([type]):focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft) }
.err { color: var(--danger); margin: 10px 0 0; min-height: 1em }
.foot { margin-top: 22px; color: var(--dim); font-size: 12px; display: flex; gap: 14px; align-items: center }
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
  <p class="lead">One computer keeps the history and the others connect to it. Phones need nothing: they open the
  address shown at the bottom of the Instant Share page in their browser.</p>
  <div class="cards">
    <section class="card" id="hostCard">
      <h2>Keep the history here<span class="tag" id="hostNow" hidden>NOW</span></h2>
      <p>Choose this on <b>one</b> computer only, one that stays on while you work. Windows will ask to allow Text
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
  <div class="foot"><span>This computer: <b id="me"></b></span><span>Instant Share {version} · SCAFFApps</span>
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

TOAST = """<!DOCTYPE html><html lang="en" data-theme="{theme}"><head><meta charset="utf-8"><title>Instant Share notification</title>
<style>{style}
body { display: block; min-height: 0; height: 100vh; padding: 0; overflow: hidden; background: var(--surface); user-select: none }
.toast { display: flex; flex-direction: column; height: 100vh; border: 1px solid var(--line-strong); border-radius: 0; padding: 10px 10px 10px 12px; gap: 8px }
.head { display: flex; align-items: center; gap: 8px; color: var(--dim); font-size: 12px }
.head svg { flex: none }
.head b { color: var(--text); font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0 }
.head .x { margin-left: auto; width: 26px; height: 26px; padding: 0; background: transparent; border: 0; border-radius: 6px; color: var(--dim) }
.head .x:hover { background: var(--surface-3); color: var(--text) }
.body { flex: 1; min-height: 0; display: flex; gap: 10px; cursor: pointer; overflow: hidden }
.body img { width: 64px; height: 64px; object-fit: cover; border-radius: 6px; border: 1px solid var(--line); flex: none }
.pdf { display: inline-grid; place-items: center; width: 46px; height: 46px; flex: none; border-radius: 6px; background: var(--danger-fill); color: #fff; font-size: 11px; font-weight: 700 }
.text { flex: 1; min-width: 0; margin: 0; font: 12.5px/1.45 "Cascadia Mono", Consolas, monospace; white-space: pre-wrap; overflow-wrap: anywhere;
  display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden }
.text.file { font-family: inherit; font-weight: 600 }
.foot { display: flex; align-items: center; gap: 6px; margin: 0; color: var(--text); font-size: 13px }
.foot .more { color: var(--dim); font-size: 12px; margin-right: auto }
.foot .btn { height: 28px }
.btn.done { color: var(--ok); border-color: var(--ok); background: var(--surface-2) }
</style></head><body>
<div class="toast">
  <div class="head"><svg width="16" height="16" viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="8" fill="#5b2c83"/><path d="M7 12h17l-4-4M25 20H8l4 4" fill="none" stroke="#f0c33c" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></svg>
    <b id="who"></b><span id="what">shared</span><button class="x" id="x" title="Close">✕</button></div>
  <div class="body" id="body" title="Open Instant Share"><img id="thumb" alt="" hidden><span class="pdf" id="pdf" hidden>PDF</span><p class="text" id="text" dir="auto"></p></div>
  <div class="foot"><span class="more" id="more"></span><button class="btn" id="open">Open</button><button class="btn primary" id="act">Copy</button></div>
</div>
<script>
const $ = (s) => document.querySelector(s);
const api = () => window.pywebview.api;
let timer = 0, hovered = false;
function setTheme(t) { document.documentElement.dataset.theme = t; }
function copied(label) { const b = $("#act"); b.textContent = label; b.classList.add("done"); }
function later() { clearTimeout(timer); timer = setTimeout(() => { if (!hovered) api().dismiss(); }, 9000); }
function show(d) {
  $("#who").textContent = d.who;
  $("#what").textContent = d.to_me ? "shared with you" : "shared";
  const image = d.file && d.file.type.startsWith("image/");
  $("#thumb").hidden = !image;
  if (image) $("#thumb").src = d.file.url;
  $("#pdf").hidden = !(d.file && !image);
  const t = $("#text");
  t.classList.toggle("file", !d.size && !!d.file);
  t.textContent = d.size ? d.text : d.file ? d.file.name : "";
  $("#more").textContent = d.more ? "+" + d.more + " more" : "";
  const act = $("#act");
  act.disabled = false; act.classList.remove("done");
  act.textContent = d.size || image ? "Copy" : "Save";
  act.title = d.size ? "Copy the text" : image ? "Copy the photo" : "Save it in Downloads";
  later();
}
document.addEventListener("mouseenter", () => { hovered = true; });
document.addEventListener("mouseleave", () => { hovered = false; later(); });
$("#x").addEventListener("click", () => api().dismiss());
$("#open").addEventListener("click", () => api().open());
$("#body").addEventListener("click", () => api().open());
$("#act").addEventListener("click", async () => {
  const b = $("#act");
  b.disabled = true;
  const r = await api().act("copy");
  b.disabled = false;
  if (r && r.ok) { b.textContent = r.label; b.classList.add("done"); clearTimeout(timer); timer = setTimeout(() => api().dismiss(), 1500); }
  else { b.textContent = "Try again"; b.title = (r && r.error) || ""; }
});
</script></body></html>"""


def toast_page(theme):
    return TOAST.replace("{theme}", theme).replace("{style}", PAGE_STYLE)


def local_page(kind, info=None, theme="dark"):
    info = info or {}
    if kind == "setup":
        body = (SETUP.replace("{mark}", MARK).replace("{version}", ts.VERSION).replace("{ready}", READY)
                .replace("{info}", json.dumps(info).replace("</", "<\\/")))
    else:
        name = html.escape(info.get("name", ""))
        title, text, buttons = {
            "starting": ("Starting…", "", ""),
            "connecting": (f"Connecting to {name}…", "", ""),
            "unreachable": (f"Can't reach {name}",
                            f"Instant Share must be running on {name}, and both computers must be on the same network. "
                            "Trying again every few seconds…",
                            '<div class="row"><button class="btn" id="retry">Try again now</button>'
                            '<button class="btn" id="setup">Choose another computer</button></div>'),
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
    args = ap.parse_args()
    args.data.mkdir(parents=True, exist_ok=True)
    log_to_file(args.data)
    quiet_late_answers()

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
