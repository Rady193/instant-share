"""SCAFFApps registration: who uses the SCAFFApps apps. The Python twin of registration.js in the SCAFFApps Electron
apps (the SCAFFApps Suite, Multi Screens, Smart Snap): the same file, the same address, the same rules.

The first time any SCAFFApps app starts on a Windows account it asks for the user's name and e-mail address and keeps
them in %APPDATA%\\SCAFFApps\\registration.json, with a random install id. Every SCAFFApps app then sends these details
once per version of itself to www.scaffapps.com/api/register, which keeps them until the SCAFFApps console collects
them (Tool Users). Another app finding the file asks nothing. A send that fails (no internet) is tried again every
hour while the app runs, and at the next start.

Tests: SCAFFAPPS_REGISTRATION_FILE (where the file is) and SCAFFAPPS_REGISTER_URL (where it is sent).
"""
import json
import os
import platform
import re
import sys
import threading
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

FILE = Path(os.environ.get("SCAFFAPPS_REGISTRATION_FILE")
            or Path(os.environ.get("APPDATA") or Path.home()) / "SCAFFApps" / "registration.json")
URL = os.environ.get("SCAFFAPPS_REGISTER_URL") or "https://www.scaffapps.com/api/register"
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
RETRY = 3600
_lock = threading.Lock()


def read():
    """The registration on this Windows account, or None."""
    try:
        r = json.loads(FILE.read_text(encoding="utf-8"))
        return r if r.get("install_id") and r.get("name") and EMAIL.match(r.get("email") or "") else None
    except (OSError, ValueError, AttributeError):
        return None


def _write(r):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_name(FILE.name + ".tmp")
    tmp.write_text(json.dumps(r, indent=2), encoding="utf-8")
    os.replace(tmp, FILE)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def windows_name():
    if sys.platform != "win32":
        return f"{platform.system()} {platform.release()}"
    build = sys.getwindowsversion().build
    return f"Windows {11 if build >= 22000 else 10} ({platform.version()})"


def save(name, email):
    """Keeps the user's details. Returns (registration, None) or (None, what is wrong)."""
    name = " ".join(str(name or "").split())
    email = str(email or "").strip()
    if not name:
        return None, "Please enter your name."
    if len(name) > 150:
        return None, "That name is too long."
    if not EMAIL.match(email) or len(email) > 150:
        return None, "Please enter a valid e-mail address."
    with _lock:
        r = read() or {"install_id": str(uuid.uuid4()), "created": _now(), "reported": {}}
        r["name"], r["email"] = name, email.lower()
        try:
            _write(r)
        except OSError as e:
            return None, f"Could not save your details: {e}"
    return r, None


def report(app_id, version):
    """Sends this app's registration if this version of it has not been sent yet. True once it has been."""
    r = read()
    if not r:
        return False
    key = f"{app_id}@{version}"
    if (r.get("reported") or {}).get(key):
        return True
    body = json.dumps({"name": r["name"], "email": r["email"], "install": r["install_id"], "app": app_id,
                       "version": version, "windows": windows_name()}).encode()
    request = urllib.request.Request(URL, data=body, method="POST", headers={
        "Content-Type": "application/json", "User-Agent": f"SCAFFApps-{app_id}/{version}"})
    try:
        with urllib.request.urlopen(request, timeout=15) as res:
            if res.status != 200:
                return False
    except (OSError, ValueError):
        return False
    with _lock:
        now = read() or r                                # another app may have written the file meanwhile: keep its marks
        now["reported"] = {**(now.get("reported") or {}), key: _now()}
        try:
            _write(now)
        except OSError:
            pass
    return True


def report_in_background(app_id, version):
    """report(), and again every hour until it has gone."""
    def loop():
        while not report(app_id, version):
            time.sleep(RETRY)
    threading.Thread(target=loop, daemon=True, name="registration").start()
