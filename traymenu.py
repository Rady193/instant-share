"""The SCAFFApps menu: the right-click menu of a SCAFFApps app's tray icon, drawn the way Electron draws the tray menus
of Smart Snap and SCAFFApps Suite on Windows 11, so every app's menu looks the same. Windows' own functions through
ctypes; no third-party library. The master copy is SCAFFApps Brand/apps/python/traymenu.py; apps carry an exact copy.

The look, measured from Electron's menu (sizes in DIP, i.e. at 100 %; colours follow Windows' dark or light app mode,
as Electron's do):

    Segoe UI 12, ClearType · rows 28 high · padding 12 above the first row, 13 below the last · label 20 from the left (48
    while an item is ticked, to make room for the tick: Material's check, 16 square, at 20. Electron keeps that room for
    any item that could be ticked; the owner found the empty gap odd, 16 Nov 2026) · shortcut text right-
    aligned, 22 from the right edge, at least 13 after the label · separator: 8, a 1 px line, 8 · the hover fills the
    whole row · corners rounded (8) with the shadow Windows 11 gives them; no border.

               dark     light
    menu       1f1f1f   ffffff
    text       e3e3e3   1f1f1f
    shortcut   c7c7c7   474747
    tick       a9a9aa   626262
    hover      363635   f2f2f2
    separator  5e5e5e   d3e3fd

popup(items) shows the menu at the mouse pointer (above it when the taskbar is below) and returns the index of the item
chosen, or None. items: None for a separator, or a dict: label, and optionally checked (True or False: the item can be
ticked), keys (shortcut text on the right), enabled (default True). Like Windows' TrackPopupMenu it runs its own message
loop on the calling thread, so call it from a thread that has windows, such as the tray icon's.
"""

import ctypes
import winreg
from ctypes import wintypes

__all__ = ["popup"]

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
try:
    dwmapi = ctypes.WinDLL("dwmapi")
except OSError:
    dwmapi = None
try:
    gdiplus = ctypes.WinDLL("gdiplus")
except OSError:
    gdiplus = None

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR)]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [("hdc", wintypes.HDC), ("fErase", wintypes.BOOL), ("rcPaint", wintypes.RECT),
                ("fRestore", wintypes.BOOL), ("fIncUpdate", wintypes.BOOL), ("rgbReserved", ctypes.c_byte * 32)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


class TRACKMOUSEEVENT(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("dwFlags", wintypes.DWORD), ("hwndTrack", wintypes.HWND),
                ("dwHoverTime", wintypes.DWORD)]


class PointF(ctypes.Structure):
    _fields_ = [("x", ctypes.c_float), ("y", ctypes.c_float)]


class GdiplusStartupInput(ctypes.Structure):
    _fields_ = [("GdiplusVersion", ctypes.c_uint32), ("DebugEventCallback", ctypes.c_void_p),
                ("SuppressBackgroundThread", wintypes.BOOL), ("SuppressExternalCodecs", wintypes.BOOL)]


def _sig(lib, name, res, *args):
    fn = getattr(lib, name)
    fn.restype, fn.argtypes = res, list(args)
    return fn


H, U, I, B, P = wintypes.HANDLE, wintypes.UINT, ctypes.c_int, wintypes.BOOL, ctypes.c_void_p
_sig(user32, "DefWindowProcW", LRESULT, wintypes.HWND, U, wintypes.WPARAM, wintypes.LPARAM)
_sig(user32, "RegisterClassW", wintypes.ATOM, ctypes.POINTER(WNDCLASSW))
_sig(user32, "CreateWindowExW", wintypes.HWND, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
     I, I, I, I, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, P)
_sig(user32, "DestroyWindow", B, wintypes.HWND)
_sig(user32, "ShowWindow", B, wintypes.HWND, I)
_sig(user32, "SetWindowPos", B, wintypes.HWND, wintypes.HWND, I, I, I, I, U)
_sig(user32, "SetForegroundWindow", B, wintypes.HWND)
_sig(user32, "GetForegroundWindow", wintypes.HWND)
_sig(user32, "TrackMouseEvent", B, ctypes.POINTER(TRACKMOUSEEVENT))
_sig(user32, "SetCapture", wintypes.HWND, wintypes.HWND)
_sig(user32, "ReleaseCapture", B)
_sig(user32, "GetCapture", wintypes.HWND)
_sig(user32, "InvalidateRect", B, wintypes.HWND, P, B)
_sig(user32, "BeginPaint", wintypes.HDC, wintypes.HWND, ctypes.POINTER(PAINTSTRUCT))
_sig(user32, "EndPaint", B, wintypes.HWND, ctypes.POINTER(PAINTSTRUCT))
_sig(user32, "GetDC", wintypes.HDC, wintypes.HWND)
_sig(user32, "ReleaseDC", I, wintypes.HWND, wintypes.HDC)
_sig(user32, "FillRect", I, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH)
_sig(user32, "DrawTextW", I, wintypes.HDC, wintypes.LPCWSTR, I, ctypes.POINTER(wintypes.RECT), U)
_sig(user32, "GetCursorPos", B, ctypes.POINTER(wintypes.POINT))
_sig(user32, "MonitorFromPoint", H, wintypes.POINT, wintypes.DWORD)
_sig(user32, "GetMonitorInfoW", B, H, ctypes.POINTER(MONITORINFO))
_sig(user32, "GetMessageW", wintypes.BOOL, ctypes.POINTER(wintypes.MSG), wintypes.HWND, U, U)
_sig(user32, "TranslateMessage", B, ctypes.POINTER(wintypes.MSG))
_sig(user32, "DispatchMessageW", LRESULT, ctypes.POINTER(wintypes.MSG))
_sig(user32, "PostQuitMessage", None, I)
_sig(user32, "LoadCursorW", H, wintypes.HINSTANCE, P)
_sig(kernel32, "GetModuleHandleW", wintypes.HMODULE, wintypes.LPCWSTR)
_sig(gdi32, "CreateCompatibleDC", wintypes.HDC, wintypes.HDC)
_sig(gdi32, "CreateCompatibleBitmap", wintypes.HBITMAP, wintypes.HDC, I, I)
_sig(gdi32, "SelectObject", wintypes.HGDIOBJ, wintypes.HDC, wintypes.HGDIOBJ)
_sig(gdi32, "DeleteObject", B, wintypes.HGDIOBJ)
_sig(gdi32, "DeleteDC", B, wintypes.HDC)
_sig(gdi32, "BitBlt", B, wintypes.HDC, I, I, I, I, wintypes.HDC, I, I, wintypes.DWORD)
_sig(gdi32, "CreateSolidBrush", wintypes.HBRUSH, wintypes.COLORREF)
_sig(gdi32, "SetTextColor", wintypes.COLORREF, wintypes.HDC, wintypes.COLORREF)
_sig(gdi32, "SetBkMode", I, wintypes.HDC, I)
_sig(gdi32, "CreateFontW", wintypes.HFONT, I, I, I, I, I, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
     wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.LPCWSTR)
_sig(gdi32, "GetTextExtentPoint32W", B, wintypes.HDC, wintypes.LPCWSTR, I, ctypes.POINTER(wintypes.SIZE))
_sig(gdi32, "GetDeviceCaps", I, wintypes.HDC, I)
try:
    _sig(user32, "GetDpiForWindow", U, wintypes.HWND)
except AttributeError:                                   # before Windows 10 1607
    pass
if dwmapi:
    _sig(dwmapi, "DwmSetWindowAttribute", ctypes.c_long, wintypes.HWND, wintypes.DWORD, P, wintypes.DWORD)
if gdiplus:
    _sig(gdiplus, "GdiplusStartup", I, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(GdiplusStartupInput), P)
    _sig(gdiplus, "GdipCreateFromHDC", I, wintypes.HDC, ctypes.POINTER(P))
    _sig(gdiplus, "GdipSetSmoothingMode", I, P, I)
    _sig(gdiplus, "GdipSetPixelOffsetMode", I, P, I)
    _sig(gdiplus, "GdipCreateSolidFill", I, ctypes.c_uint32, ctypes.POINTER(P))
    _sig(gdiplus, "GdipFillPolygon", I, P, P, ctypes.POINTER(PointF), I, I)
    _sig(gdiplus, "GdipDeleteBrush", I, P)
    _sig(gdiplus, "GdipDeleteGraphics", I, P)

THEMES = {   # menu, text, shortcut, tick, hover, separator
    "dark": (0x1F1F1F, 0xE3E3E3, 0xC7C7C7, 0xA9A9AA, 0x363635, 0x5E5E5E),
    "light": (0xFFFFFF, 0x1F1F1F, 0x474747, 0x626262, 0xF2F2F2, 0xD3E3FD),
}
FONT, ROW, PAD_TOP, PAD_BOTTOM, LEFT, LEFT_TICKS, TICK, RIGHT, GAP, SEP = 12, 28, 12, 13, 20, 48, 16, 22, 13, 8
CLASS_STYLE = 0x00020000      # CS_DROPSHADOW: Windows 10's shadow (11 gives a rounded window its own, the same either way)
CHECK = [(9, 16.17), (4.83, 12), (3.41, 13.41), (9, 19), (21, 7), (19.59, 5.59)]   # Material "check", 24 units
CLASS = "SCAFFApps.Menu"

_state = None                                             # the open menu (one at a time)
_registered = False
_gdiplus_token = None


def _bgr(rgb):
    return ((rgb & 0xFF) << 16) | (rgb & 0xFF00) | ((rgb >> 16) & 0xFF)


def _mix(a, b, t):
    """a over b at opacity t, for a disabled item's text."""
    return sum(round(((a >> s) & 0xFF) * t + ((b >> s) & 0xFF) * (1 - t)) << s for s in (16, 8, 0))


def windows_theme():
    """'dark' or 'light': Windows' app mode, which Electron's menus follow too."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return "light" if winreg.QueryValueEx(k, "AppsUseLightTheme")[0] else "dark"
    except OSError:
        return "light"


class _Menu:
    def __init__(self, items, theme):
        self.items = [dict(it, enabled=it.get("enabled", True)) if it else None for it in items]
        self.colours = THEMES.get(theme or windows_theme(), THEMES["dark"])
        self.hwnd = None
        self.hover = None
        self.chosen = None
        self.done = False
        self.font = None
        self.tracking = False                                         # WM_MOUSELEAVE asked for
        self.captured = False                                         # the fallback: the menu holds the mouse

    # ---- sizes, at this window's DPI
    def measure(self):
        s = self.scale
        px = lambda dip: int(dip * s + 0.5)
        self.row, self.sep, self.pad_top, self.pad_bottom = px(ROW), 2 * px(SEP) + max(1, int(s)), px(PAD_TOP), px(PAD_BOTTOM)
        ticks = any(it and it.get("checked") for it in self.items)   # room for the tick only while one shows
        self.left, self.tick_x, self.tick, self.right, self.gap = px(LEFT_TICKS if ticks else LEFT), px(LEFT), px(TICK), px(RIGHT), px(GAP)
        self.font = gdi32.CreateFontW(-px(FONT), 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 5, 0, "Segoe UI")   # ClearType, as Chromium
        dc = user32.GetDC(None)
        old = gdi32.SelectObject(dc, self.font)
        size = wintypes.SIZE()
        widest = 0
        for it in self.items:
            if not it:
                continue
            gdi32.GetTextExtentPoint32W(dc, it["label"], len(it["label"]), ctypes.byref(size))
            w = size.cx
            if it.get("keys"):
                gdi32.GetTextExtentPoint32W(dc, it["keys"], len(it["keys"]), ctypes.byref(size))
                w += self.gap + size.cx
            widest = max(widest, w)
        gdi32.SelectObject(dc, old)
        user32.ReleaseDC(None, dc)
        self.width = self.left + widest + self.right
        self.tops = []
        y = self.pad_top
        for it in self.items:
            self.tops.append(y)
            y += self.row if it else self.sep
        self.height = y + self.pad_bottom

    def item_at(self, x, y):
        if not (0 <= x < self.width):
            return None
        for i, it in enumerate(self.items):
            if it and it["enabled"] and self.tops[i] <= y < self.tops[i] + self.row:
                return i
        return None

    # ---- drawing
    def paint(self, hdc):
        bg, text, keys, tick, hover, line = self.colours
        mem = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, self.width, self.height)
        old_bmp = gdi32.SelectObject(mem, bmp)
        old_font = gdi32.SelectObject(mem, self.font)
        self.fill(mem, 0, 0, self.width, self.height, bg)
        gdi32.SetBkMode(mem, 1)                                       # TRANSPARENT
        for i, it in enumerate(self.items):
            top = self.tops[i]
            if not it:
                thick = max(1, int(self.scale))
                mid = top + (self.sep - thick) // 2
                self.fill(mem, 0, mid, self.width, mid + thick, line)
                continue
            if i == self.hover:
                self.fill(mem, 0, top, self.width, top + self.row, hover)
            colour = text if it["enabled"] else _mix(text, bg, .38)
            nudge = int(self.scale * .67)                             # Windows' text sits a pixel higher than Chromium's
            r = wintypes.RECT(self.left, top + nudge, self.width - self.right, top + self.row + nudge)
            gdi32.SetTextColor(mem, _bgr(colour))
            user32.DrawTextW(mem, it["label"], -1, ctypes.byref(r), 0x0 | 0x4 | 0x20 | 0x800)   # LEFT VCENTER SINGLELINE NOPREFIX
            if it.get("keys"):
                gdi32.SetTextColor(mem, _bgr(keys if it["enabled"] else colour))
                user32.DrawTextW(mem, it["keys"], -1, ctypes.byref(r), 0x2 | 0x4 | 0x20 | 0x800)   # RIGHT
            if it.get("checked"):
                self.check(mem, self.tick_x, top + (self.row - self.tick) / 2, self.tick, tick)
        gdi32.BitBlt(hdc, 0, 0, self.width, self.height, mem, 0, 0, 0x00CC0020)   # SRCCOPY
        gdi32.SelectObject(mem, old_font)
        gdi32.SelectObject(mem, old_bmp)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)

    @staticmethod
    def fill(dc, x0, y0, x1, y1, rgb):
        brush = gdi32.CreateSolidBrush(_bgr(rgb))
        user32.FillRect(dc, ctypes.byref(wintypes.RECT(x0, y0, x1, y1)), brush)
        gdi32.DeleteObject(brush)

    @staticmethod
    def check(dc, x, y, size, rgb):
        """Material's check mark, smoothed with GDI+ (a plain tick character if GDI+ is missing)."""
        global _gdiplus_token
        if gdiplus and _gdiplus_token is None:
            token = ctypes.c_size_t()
            if gdiplus.GdiplusStartup(ctypes.byref(token), ctypes.byref(GdiplusStartupInput(1, None, False, False)), None) == 0:
                _gdiplus_token = token
        if not _gdiplus_token:
            r = wintypes.RECT(int(x), int(y), int(x + size), int(y + size))
            gdi32.SetTextColor(dc, _bgr(rgb))
            user32.DrawTextW(dc, "✓", -1, ctypes.byref(r), 0x1 | 0x4 | 0x20 | 0x800)
            return
        g, brush = P(), P()
        gdiplus.GdipCreateFromHDC(dc, ctypes.byref(g))
        gdiplus.GdipSetSmoothingMode(g, 4)                            # anti-alias
        gdiplus.GdipSetPixelOffsetMode(g, 4)                          # half: pixel centres as Skia has them
        gdiplus.GdipCreateSolidFill(0xFF000000 | rgb, ctypes.byref(brush))
        k = size / 24
        pts = (PointF * len(CHECK))(*[PointF(x + px * k, y + py * k) for px, py in CHECK])
        gdiplus.GdipFillPolygon(g, brush, pts, len(CHECK), 0)
        gdiplus.GdipDeleteBrush(brush)
        gdiplus.GdipDeleteGraphics(g)

    # ---- the window
    def finish(self, chosen=None):
        if self.done:
            return
        self.done, self.chosen = True, chosen
        if user32.GetCapture() == self.hwnd:
            user32.ReleaseCapture()
        user32.ShowWindow(self.hwnd, 0)                               # SW_HIDE

    def step(self, direction):
        live = [i for i, it in enumerate(self.items) if it and it["enabled"]]
        if not live:
            return
        if self.hover not in live:
            self.hover = live[0] if direction > 0 else live[-1]
        else:
            self.hover = live[(live.index(self.hover) + direction) % len(live)]
        user32.InvalidateRect(self.hwnd, None, False)

    def handle(self, hwnd, msg, wparam, lparam):
        if msg == 0x000F:                                             # WM_PAINT
            ps = PAINTSTRUCT()
            hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
            self.paint(hdc)
            user32.EndPaint(hwnd, ctypes.byref(ps))
            return 0
        if msg == 0x0014:                                             # WM_ERASEBKGND: painted whole in WM_PAINT
            return 1
        if msg == 0x0200:                                             # WM_MOUSEMOVE
            if not self.tracking:                                     # ask for WM_MOUSELEAVE
                track = TRACKMOUSEEVENT(ctypes.sizeof(TRACKMOUSEEVENT), 0x2, hwnd, 0)   # TME_LEAVE
                self.tracking = bool(user32.TrackMouseEvent(ctypes.byref(track)))
            x, y = ctypes.c_short(lparam & 0xFFFF).value, ctypes.c_short((lparam >> 16) & 0xFFFF).value
            inside = 0 <= x < self.width and 0 <= y < self.height
            at = self.item_at(x, y) if inside else None
            if at != self.hover and (at is not None or not inside):  # over a separator the hover stays
                self.hover = at
                user32.InvalidateRect(hwnd, None, False)
            return 0
        if msg == 0x02A3:                                             # WM_MOUSELEAVE
            self.tracking = False
            if self.hover is not None and not self.captured:
                self.hover = None
                user32.InvalidateRect(hwnd, None, False)
            return 0
        if msg in (0x0201, 0x0204, 0x0207):                           # a button pressed: outside the menu (only seen
            x, y = ctypes.c_short(lparam & 0xFFFF).value, ctypes.c_short((lparam >> 16) & 0xFFFF).value
            if not (0 <= x < self.width and 0 <= y < self.height):  # with the capture fallback) closes it
                self.finish()
            return 0
        if msg in (0x0202, 0x0205):                                   # WM_LBUTTONUP, WM_RBUTTONUP: an item chosen
            x, y = ctypes.c_short(lparam & 0xFFFF).value, ctypes.c_short((lparam >> 16) & 0xFFFF).value
            at = self.item_at(x, y) if 0 <= y < self.height else None
            if at is not None:
                self.finish(at)
            return 0
        if msg == 0x0100:                                             # WM_KEYDOWN
            if wparam == 0x1B:                                        # Esc
                self.finish()
            elif wparam in (0x26, 0x28):                              # Up, Down
                self.step(-1 if wparam == 0x26 else 1)
            elif wparam in (0x24, 0x23):                              # Home, End
                self.hover = None
                self.step(1 if wparam == 0x24 else -1)
            elif wparam in (0x0D, 0x20) and self.hover is not None:   # Enter, Space
                self.finish(self.hover)
            return 0
        if msg == 0x0006 and (wparam & 0xFFFF) == 0:                  # WM_ACTIVATE, WA_INACTIVE: clicked elsewhere
            self.finish()
        elif msg == 0x0215 and lparam != (self.hwnd or 0):            # WM_CAPTURECHANGED to another window
            self.finish()
        elif msg == 0x001F:                                           # WM_CANCELMODE
            self.finish()
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def run(self, at=None):
        instance = kernel32.GetModuleHandleW(None)
        point = wintypes.POINT(*at) if at else wintypes.POINT()
        if not at:
            user32.GetCursorPos(ctypes.byref(point))
        # WS_EX_TOPMOST | WS_EX_TOOLWINDOW, WS_POPUP
        self.hwnd = user32.CreateWindowExW(0x8 | 0x80, CLASS, "", 0x80000000, point.x, point.y, 1, 1, None, None, instance, None)
        if not self.hwnd:
            return None
        try:
            dpi = user32.GetDpiForWindow(self.hwnd)
        except AttributeError:
            dc = user32.GetDC(None)
            dpi = gdi32.GetDeviceCaps(dc, 90)                         # LOGPIXELSY
            user32.ReleaseDC(None, dc)
        self.scale = (dpi or 96) / 96
        self.measure()
        info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        user32.GetMonitorInfoW(user32.MonitorFromPoint(point, 2), ctypes.byref(info))   # MONITOR_DEFAULTTONEAREST
        work = info.rcWork
        x, y = point.x, point.y
        if y + self.height > work.bottom:                             # no room below (the taskbar): above the pointer
            y = point.y - self.height
        if x + self.width > work.right:
            x = point.x - self.width
        x, y = max(work.left, x), max(work.top, y)
        if dwmapi:
            dark = ctypes.c_int(self.colours is THEMES["dark"])
            dwmapi.DwmSetWindowAttribute(self.hwnd, 20, ctypes.byref(dark), 4)                      # dark shadow / frame
            dwmapi.DwmSetWindowAttribute(self.hwnd, 33, ctypes.byref(ctypes.c_int(2)), 4)           # corners: round
            dwmapi.DwmSetWindowAttribute(self.hwnd, 34, ctypes.byref(ctypes.c_uint(0xFFFFFFFE)), 4)  # no border
        user32.SetWindowPos(self.hwnd, wintypes.HWND(-1), x, y, self.width, self.height, 0x0040)   # TOPMOST, SHOWWINDOW
        # In front, it takes the keyboard, and a click anywhere else closes it (the click still reaches what was
        # clicked, as with Electron's menus). Windows lets the app whose tray icon was clicked come in front; if it
        # refuses anyway, the menu holds the mouse instead, so a click elsewhere still closes it.
        user32.SetForegroundWindow(self.hwnd)
        if user32.GetForegroundWindow() != self.hwnd:
            self.captured = True
            user32.SetCapture(self.hwnd)
        msg = wintypes.MSG()
        while not self.done:
            got = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if got <= 0:
                if got == 0:
                    user32.PostQuitMessage(msg.wParam)                # leave the quit for the thread's own loop
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        self.done = True
        user32.DestroyWindow(self.hwnd)
        gdi32.DeleteObject(self.font)
        return self.chosen


def _wndproc(hwnd, msg, wparam, lparam):
    try:
        if _state is not None and _state.hwnd in (None, hwnd):
            return _state.handle(hwnd, msg, wparam, lparam)
    except Exception:                                                 # never let a mistake here break the thread
        import traceback
        traceback.print_exc()
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


_proc = WNDPROC(_wndproc)                                             # kept, so Windows always has something to call


def popup(items, theme=None, at=None):
    """Shows the menu and returns the index of the chosen item, or None. theme: 'dark' / 'light' (default: Windows'
    app mode); at: (x, y) on the screen (default: the mouse pointer)."""
    global _state, _registered
    if _state is not None:                                            # one menu at a time
        return None
    if not _registered:
        wc = WNDCLASSW(style=CLASS_STYLE, lpfnWndProc=_proc, hInstance=kernel32.GetModuleHandleW(None),
                       hCursor=user32.LoadCursorW(None, ctypes.c_void_p(32512)), lpszClassName=CLASS)    # IDC_ARROW
        user32.RegisterClassW(ctypes.byref(wc))                       # fails harmlessly when already registered
        _registered = True
    _state = _Menu(items, theme)
    try:
        return _state.run(at)
    finally:
        _state = None
