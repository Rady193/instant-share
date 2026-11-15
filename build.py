"""Packs Text Share into one portable Windows program: release/Text-Share-<version>-portable.exe

    py build.py

The first run makes the build environment, C:\\venvs\\textshare-build (outside OneDrive: thousands of small files),
with the versions below. The program is app.py (its window, tray and pop-ups, drawn by Windows' own Edge WebView2)
around textshare.py, and carries its own Python, so the computers it runs on need nothing. release/ also gets
textshare.py itself, for Mac and Linux (python3 textshare.py), and the files' SHA-256 checksums.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

PYINSTALLER = "6.22.3"
NEEDS = [f"pyinstaller=={PYINSTALLER}", "pywebview==6.2.1", "pillow", "playwright"]
BUNDLED = ["pywebview", "bottle", "proxy_tools", "typing_extensions", "pythonnet", "clr_loader", "cffi", "pycparser",
           "pillow"]                      # what the .exe carries besides Python: their licences go in the notices
HERE = Path(__file__).resolve().parent
VENV = Path(r"C:\venvs\textshare-build")
WORK = VENV / "work"
PY = VENV / "Scripts" / "python.exe"


def run(*cmd):
    print(">", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True)


def version():
    m = re.search(r'^VERSION = "([\d.]+)"', (HERE / "textshare.py").read_text(encoding="utf-8"), re.M)
    return m[1]


def version_file(v, exe_name):
    nums = tuple(int(n) for n in (v.split(".") + ["0", "0", "0"])[:4])
    strings = {
        "CompanyName": "SCAFFApps",
        "FileDescription": "Text Share",
        "FileVersion": v,
        "InternalName": "Text Share",
        "LegalCopyright": "Copyright \u00a9 2026 SCAFFApps",
        "OriginalFilename": exe_name,
        "ProductName": "Text Share",
        "ProductVersion": v,
    }
    table = ",\n        ".join(f"StringStruct({k!r}, {s!r})" for k, s in strings.items())
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1,
                    subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
        {table}])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    path = WORK / "version.txt"
    path.write_text(text, encoding="utf-8")
    return path


LICENCES = r"""
import json, sys, importlib.metadata as md
out = []
for name in sys.argv[1:]:
    d = md.distribution(name)
    meta = d.metadata
    licence = (meta.get("License-Expression") or (meta.get("License") or "").strip().split("\n")[0]).strip()
    if not licence or len(licence) > 60:
        licence = next((c.split("::")[-1].strip() for c in meta.get_all("Classifier") or []
                        if c.startswith("License ::")), "see below")
    texts = []
    for f in d.files or []:
        if any(k in f.name.upper() for k in ("LICENSE", "LICENCE", "COPYING", "NOTICE")) and not f.name.endswith((".py", ".pyc")):
            try:
                texts.append(f.read_text(encoding="utf-8").strip())
            except Exception:
                pass
    home = meta.get("Home-page") or next((u.split(",", 1)[-1].strip() for u in meta.get_all("Project-URL") or []), "")
    out.append({"name": meta["Name"], "version": d.version, "licence": licence, "texts": texts, "home": home})
sys.stdout.reconfigure(encoding="utf-8")
print(json.dumps(out))
"""


def notices(v, target):
    """The licences of what the .exe carries besides Text Share: Python (with its Windows build's libraries),
    PyInstaller's bootloader and the Python packages in BUNDLED. Taken from the build environment, so they match."""
    base = subprocess.check_output([str(PY), "-c", "import sys; print(sys.base_prefix)"], text=True).strip()
    python_license = (Path(base) / "LICENSE.txt").read_text(encoding="utf-8")
    packages = json.loads(subprocess.check_output([str(PY), "-c", LICENCES, *BUNDLED], text=True, encoding="utf-8"))
    rule = "\n\n" + "=" * 78 + "\n\n"
    target.write_text(
        f"Text Share {v} for Windows (the .exe) carries these third-party components, each under its own licence:\n\n"
        "1. Python, under the Python Software Foundation License, with the libraries its Windows build includes\n"
        "   (among them OpenSSL, SQLite, libffi and zlib). Their licence texts follow below, as Python publishes them.\n\n"
        "2. The PyInstaller bootloader, which starts the program: GPL 2.0 or later with the PyInstaller bootloader\n"
        "   exception, which allows programs made with it to be distributed under any licence.\n"
        "   https://pyinstaller.org/en/stable/license.html\n\n"
        "3. These Python packages (the window, and copying photos), each with its licence text below:\n"
        + "".join(f"   {p['name']} {p['version']}: {p['licence']}\n" for p in packages)
        + rule + python_license
        + "".join(rule + f"{p['name']} {p['version']} ({p['licence']})\n\n"
                  + ("\n\n".join(p["texts"]) or f"No licence text is shipped with this package; see {p['home']}")
                  for p in packages), encoding="utf-8")


def android(v, release):
    """Text Share for Android (android/build.ps1), when the phone-app tools are on this PC; else the build goes on
    without it, and says so."""
    tools = Path(os.environ.get("LOCALAPPDATA", "")) / "ScaffERP-android-tools"
    if not (tools / "sdk").is_dir():
        print(f"!! No Android tools in {tools}: building without the Android app.")
        return None
    run(PY, HERE / "android" / "make_setup.py")
    run("powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", HERE / "android" / "build.ps1", "-Tools", tools)
    apk = HERE / "android" / "build" / "Text-Share.apk"
    shutil.copy2(apk, release / f"Text-Share-{v}.apk")
    return apk


def main():
    if sys.platform != "win32":
        sys.exit("build.py makes the Windows program; on Mac or Linux just run:  python3 textshare.py")
    v = version()
    exe_name = f"Text-Share-{v}-portable.exe"

    if not PY.exists():
        run(sys.executable, "-m", "venv", VENV)
    run(PY, "-m", "pip", "install", "-q", "--disable-pip-version-check", *NEEDS)
    WORK.mkdir(parents=True, exist_ok=True)
    if not (HERE / "build" / "icon.ico").exists():
        run(PY, HERE / "build" / "make_icon.py")

    release = HERE / "release"
    release.mkdir(exist_ok=True)
    apk = android(v, release)                                  # the phone app first: the .exe hands it to phones
    run(PY, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
        "--onefile", "--windowed", "--name", exe_name[:-4],
        "--icon", HERE / "build" / "icon.ico",
        "--add-data", f"{HERE / 'build' / 'icon.png'};build", "--add-data", f"{HERE / 'build' / 'icon.ico'};build",
        "--exclude-module", "tkinter", "--exclude-module", "playwright", "--exclude-module", "pystray",
        "--exclude-module", "numpy", "--exclude-module", "PIL._avif", "--exclude-module", "PIL.AvifImagePlugin",   # not used; numpy alone is 24 MB
        *(["--add-data", f"{apk};android"] if apk else []),
        "--version-file", version_file(v, exe_name),
        "--distpath", release, "--workpath", WORK, "--specpath", WORK,
        HERE / "app.py")
    shutil.copy2(HERE / "textshare.py", release / "textshare.py")
    notices(v, release / "THIRD-PARTY-NOTICES.txt")

    print(f"\nText Share {v}:")
    lines = []
    for name in [exe_name] + ([f"Text-Share-{v}.apk"] if apk else []) + ["textshare.py"]:
        data = (release / name).read_bytes()
        digest = hashlib.sha256(data).hexdigest().upper()
        size = f"{len(data) / 1048576:.1f} MB" if len(data) > 1048576 else f"{len(data) / 1024:.0f} KB"
        lines.append(f"| `{name}` | {size} | `{digest}` |")
    print("\n".join(lines))
    (release / "SHA256.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
