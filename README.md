# Text Share

Text Share passes text, photos and PDFs between the computers and Android phones on your own network. Share on
one, and the others have it at once, with a pop-up (or a phone notification) and a Copy button. Choose who sees each share. Everything stays
in a history you can search, copy from and delete from. It needs no internet and no accounts.

Version 1.0.0 · Windows 10 and 11 (64-bit) · Android 10 and newer · Published by SCAFFApps

The **[handbook](Text%20Share%20Handbook.pdf)** explains everything, with pictures.

## Contents

- [How it works](#how-it-works)
- [Install](#install)
- [Quick start](#quick-start)
- [Features](#features)
- [Keyboard shortcuts](#keyboard-shortcuts)
- [Options](#options)
- [Where things are saved](#where-things-are-saved)
- [What it does not do](#what-it-does-not-do)
- [Building from source](#building-from-source)
- [Project layout](#project-layout)
- [Licence](#licence)

## How it works

**One** computer keeps the history. The other computers run the same app, and Android phones run Text Share for
Android; they find it on the network by themselves. iPhones, tablets and computers without the app open its address
in a browser.

```
  laptop          (app) ──┐
  Android phone   (app) ──┼──▶  the computer that keeps the history  (app)
  iPhone      (browser) ──┘
```

Once set up, the app waits in the tray when its window is closed, so it can show what others share.

## Install

Download from the [Releases page](https://github.com/Rady193/text-share/releases/latest).

| File | What it is |
|---|---|
| `Text-Share-1.0.0-portable.exe` | The app for Windows, in one file. Nothing is installed and no administrator permission is needed: copy it anywhere (a USB stick works too) and double-click it. Use the same file on every computer. |
| `Text-Share-1.0.0.apk` | Text Share for Android 10 and newer. Phones on the same Wi-Fi can also get it from the computer that keeps the history: **Get the Android app** at the bottom of its page in the phone's browser. |
| `Text-Share-Handbook-1.0.0.pdf` | The handbook. |
| `textshare.py` | Text Share as a plain server, for Mac and Linux: `python3 textshare.py` (Python 3.10 or newer, nothing else) keeps the history there, and every device uses a browser. |
| `THIRD-PARTY-NOTICES.txt` | The licences of the parts the Windows app carries inside it. |

The release notes list each file's SHA-256 checksum, so you can check a download with `Get-FileHash <file>` in PowerShell.

The app is not code-signed, so the first time you run it Windows SmartScreen may show "Windows protected your PC".
Choose **More info**, then **Run anyway**. To remove that warning for other people you would need to buy a
code-signing certificate and sign the file.

The window is drawn by Microsoft Edge WebView2, which Windows 10 and 11 include. Nothing else is needed.

## Quick start

1. **On one computer**, one that stays on while you work, start Text Share and choose **Use this computer**. Windows
   asks whether to allow it on networks: allow it on **Private networks**.
2. **On every other computer**, start Text Share and choose the first computer from the list (or type its name or
   address).
3. Leave **Start Text Share when I sign in to Windows** ticked.
4. Phones: open the address shown at the bottom of the Text Share window (for example `http://OFFICE-PC:8765`) in the
   browser, and bookmark it.

If the computers cannot find each other, check that the network is set to **Private** on the computer that keeps the
history (Settings > Network & internet > Wi-Fi or Ethernet > Network profile type). If Windows never asked to allow
Text Share, add the rules yourself in an **administrator** PowerShell there:

```
netsh advfirewall firewall add rule name="Text Share" dir=in action=allow protocol=TCP localport=8765 profile=private
netsh advfirewall firewall add rule name="Text Share search" dir=in action=allow protocol=UDP localport=8765 profile=private
```

## Features

- **Share text** of any length (up to 20 MB): press **Share** or Ctrl+Enter. It reaches the other devices within a few
  seconds.
- **Share photos and PDFs** up to 1 MB: the **File** button, drag and drop, or paste a picture. PNG, JPEG, GIF, WebP
  and PDF, checked by their contents, not their names.
- **Choose who sees it**: the **To** button lists the devices, with who is online now. Everyone, or only the devices
  you tick.
- **Pop-ups by the clock** when another device shares something with you: who, a preview, and **Copy** (text or
  photo) or **Save** (PDF). They come even when the window is closed, and never take the keyboard away from you.
- **History** of everything shared with you: Copy, Save, Edit, Delete (for everyone), search, photos full size.
- **Dark and light appearance** (the sun or moon button), the dark one by default; the window's title bar follows it.
- **Tray icon**: click to open; right-click for Start with Windows, Change setup and Quit.
- **Finds the other computers by itself**, and follows the computer that keeps the history if its address changes.
- **Arabic and other right-to-left text** reads right to left, line by line.
- **Text Share for Android**: the same history on the phone, notifications with Copy and Save while the app is
  closed (no Google services, no internet: one connection kept open to the computer that keeps the history), and
  Android's **Share** menu: share a link, a text, a photo or a PDF from any app to Text Share.
- **iPhones and other browsers** use the same address, without notifications.

## Keyboard shortcuts

| Keys | Does |
|---|---|
| Ctrl+Enter or Ctrl+S | Share what is in the editor, and its file |
| Ctrl+V in the editor | Paste text, or add a copied picture as the file |
| Esc | Close the device list or a full-size photo |

## Options

```
Text-Share-1.0.0-portable.exe [--hidden] [--data FOLDER] [--port PORT]
```

| Option | Does |
|---|---|
| `--port 9000` | On the computer that keeps the history: another port than 8765. (If 8765 is taken, it picks the next free one by itself.) |
| `--data D:\TextShare` | Keeps the history and settings in another folder, for example next to the program on a USB stick. |
| `--hidden` | Starts in the tray, without the window. Windows starts it this way at sign-in. |

Starting the program while it is already running brings its window to the front.

## Where things are saved

| What | Where |
|---|---|
| The history, files included | `%LOCALAPPDATA%\TextShare\history.db` on the computer that keeps it. To back it up, quit Text Share and copy it. |
| Settings (setup, name, appearance) | `%LOCALAPPDATA%\TextShare\settings.json` on each computer |
| A log, for troubleshooting | `%LOCALAPPDATA%\TextShare\textshare.log` on each computer |
| Saved photos and PDFs | Your Downloads folder |
| Start with Windows | The usual list of programs that start at sign-in (Settings > Apps > Startup) |

## What it does not do

- **No password and no encryption.** Anyone on the same network who opens the address can use Text Share. Use it on
  networks you trust, such as home or your own office, never on public Wi-Fi. Delete passwords or keys once you have
  pasted them where they belong.
- **"Who should see it" is not a lock.** Items shared with chosen devices are kept out of the others' history and
  pop-ups, but someone with technical knowledge on your network could still read them.
- **Only your own network.** It refuses connections from outside the local network, and it doesn't reach the internet
  for anything.
- **Files up to 1 MB, photos and PDFs only.**
- **The computer that keeps the history must be on.** When it sleeps, the others can't share.

## Building from source

You need Windows and Python 3.10 or newer (the release is built with Python 3.14).

- Run the app from the source: `pythonw app.py` (needs `pywebview` and `pillow`: `pip install pywebview pillow`).
- Run the plain server: `py textshare.py`, or double-click `Text Share.cmd`.
- Produce the portable app, `textshare.py` and `THIRD-PARTY-NOTICES.txt` in the `release` folder: `py build.py`.
  The first run makes a build environment in `C:\venvs\textshare-build` with the versions it needs. The build prints
  each file's SHA-256 checksum and saves the list in `release\SHA256.txt`.
- The Android app is built by the same `py build.py`, when the phone-app tools are on the PC (a JDK 17 and the
  Android SDK with `platforms;android-34` and `build-tools;34.0.0`, in `%LOCALAPPDATA%\ScaffERP-android-tools`): it
  writes `android\assets\setup.html` (`android\make_setup.py`), runs `android\build.ps1` (no Gradle), packs the APK
  into the `.exe` and copies it to `release`. The first build makes the signing key,
  `%LOCALAPPDATA%\ScaffERP-android-tools\textshare-release.keystore`: **keep it and back it up**, since Android
  installs an update over the app only when it is signed with the same key.
- Produce the handbook (`Text Share Handbook.pdf`, and a copy in `release`):
  `C:\venvs\textshare-build\Scripts\python.exe handbook\build_handbook.py`. It retakes the pictures from the real
  pages with a made-up history; `--no-shots` keeps the pictures as they are.

## Project layout

| Path | What it is |
|---|---|
| `textshare.py` | The heart: the server that keeps the history (SQLite), the page every device shows, and finding computers on the network. Python standard library only. |
| `app.py` | The Windows app around it: the window, the setup, the tray icon, the pop-ups, copying and saving. |
| `build.py` | Packs the app into the portable `.exe`. |
| `build/` | The icon (`icon.svg`, and `make_icon.py`, which draws `icon.png` and `icon.ico`). |
| `android/` | Text Share for Android: Java sources (`src/`), resources, the setup page (`assets/`, made by `make_setup.py`) and `build.ps1`. |
| `handbook/` | The handbook: `handbook.html`, its pictures, and `build_handbook.py`. |
| `Text Share.cmd` | Runs the plain server from the source. |
| `RELEASE-NOTES.md` | What changed in each version. |

## Licence

Copyright © 2026 SCAFFApps. All rights reserved. The released program is free to download and use, for personal or
business use. The source code is published for reference only. See [LICENSE](LICENSE).
