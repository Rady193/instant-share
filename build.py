"""Packs Text Share into one portable Windows program: release/Text-Share-<version>-portable.exe

    py build.py

The first run makes the build environment, C:\\venvs\\textshare-build (outside OneDrive: thousands of small files),
with the PyInstaller version below. The program carries its own Python, so the computers it runs on need nothing.
release/ also gets textshare.py itself, for Mac and Linux (python3 textshare.py), and the files' SHA-256 checksums.
"""

import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

PYINSTALLER = "6.22.3"
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


def notices(v, target):
    """The licences of what the .exe carries besides Text Share: Python (with its Windows build's libraries) and
    PyInstaller's bootloader. Taken from the Python the build used, so they always match."""
    base = subprocess.check_output([str(PY), "-c", "import sys; print(sys.base_prefix)"], text=True).strip()
    python_license = (Path(base) / "LICENSE.txt").read_text(encoding="utf-8")
    target.write_text(
        f"Text Share {v} for Windows (the .exe) carries these third-party components, each under its own licence:\n\n"
        "1. Python, under the Python Software Foundation License, with the libraries its Windows build includes\n"
        "   (among them OpenSSL, SQLite, libffi and zlib). Their licence texts follow below, as Python publishes them.\n\n"
        "2. The PyInstaller bootloader, which starts the program: GPL 2.0 or later with the PyInstaller bootloader\n"
        "   exception, which allows programs made with it to be distributed under any licence.\n"
        "   https://pyinstaller.org/en/stable/license.html\n\n"
        + "=" * 78 + "\n\n" + python_license, encoding="utf-8")


def main():
    if sys.platform != "win32":
        sys.exit("build.py makes the Windows program; on Mac or Linux just run:  python3 textshare.py")
    v = version()
    exe_name = f"Text-Share-{v}-portable.exe"

    if not PY.exists():
        run(sys.executable, "-m", "venv", VENV)
    run(PY, "-m", "pip", "install", "-q", "--disable-pip-version-check", f"pyinstaller=={PYINSTALLER}", "pillow")
    WORK.mkdir(parents=True, exist_ok=True)
    if not (HERE / "build" / "icon.ico").exists():
        run(PY, HERE / "build" / "make_icon.py")

    release = HERE / "release"
    release.mkdir(exist_ok=True)
    run(PY, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
        "--onefile", "--console", "--name", exe_name[:-4],
        "--icon", HERE / "build" / "icon.ico",
        "--version-file", version_file(v, exe_name),
        "--distpath", release, "--workpath", WORK, "--specpath", WORK,
        HERE / "textshare.py")
    shutil.copy2(HERE / "textshare.py", release / "textshare.py")
    notices(v, release / "THIRD-PARTY-NOTICES.txt")

    print(f"\nText Share {v}:")
    lines = []
    for name in (exe_name, "textshare.py"):
        data = (release / name).read_bytes()
        digest = hashlib.sha256(data).hexdigest().upper()
        size = f"{len(data) / 1048576:.1f} MB" if len(data) > 1048576 else f"{len(data) / 1024:.0f} KB"
        lines.append(f"| `{name}` | {size} | `{digest}` |")
    print("\n".join(lines))
    (release / "SHA256.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
