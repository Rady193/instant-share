# Instant Share

Instant Share passes text, photos, documents and voice notes between the computers and Android phones on your own
network. Share on one, and the others have it at once, with a notification and a Copy button. Choose who sees each
share. Everything stays in a history you can search, copy from and delete from. It is encrypted, only devices that
joined with a pairing code get in. Sharing needs no internet: nothing shared ever leaves your network.

Version 1.4.0 · Windows 10 and 11 (64-bit) · Android 10 and newer · Published by SCAFFApps

The **[handbook](https://github.com/Rady193/instant-share/releases/latest)** (`Instant-Share-Handbook-<version>.pdf`, on the Releases page) explains everything, with pictures.

## Contents

- [How it works](#how-it-works)
- [Install](#install)
- [Quick start](#quick-start)
- [Features](#features)
- [Keyboard shortcuts](#keyboard-shortcuts)
- [Options](#options)
- [Where things are saved](#where-things-are-saved)
- [Security](#security)
- [Updates](#updates)
- [What it does not do](#what-it-does-not-do)
- [Building from source](#building-from-source)
- [Project layout](#project-layout)
- [Licence](#licence)

## How it works

**One** computer keeps the history. The other computers run the same app, and Android phones run Instant Share for
Android; they find it on the network by themselves. iPhones, tablets and computers without the app open its address
in a browser.

```
  laptop          (app) ──┐
  Android phone   (app) ──┼──▶  the computer that keeps the history  (app)
  iPhone      (browser) ──┘
```

Every device joins once with a 6-digit pairing code shown on a device that has joined already. Once set up, the app
waits in the tray when its window is closed, so it can show what others share.

## Install

Download from the [Releases page](https://github.com/Rady193/instant-share/releases/latest).

| File | What it is |
|---|---|
| `Instant-Share-Setup-1.4.0.exe` | The app for Windows: its installer. It installs for everyone in `C:\Program Files\SCAFFApps\Instant Share` (Windows asks for administrator permission once), with shortcuts in the Start menu's SCAFFApps folder and on the desktop, and can be removed from Windows Settings. Install it on every computer; **Get the app** on Instant Share's page leads to it. The SCAFFApps Suite installs it too. |
| `Instant-Share-1.4.0.apk` | Instant Share for Android 10 and newer. Phones on the same Wi-Fi can also get it from the computer that keeps the history: **Get the Android app** on its page, in the phone's browser. |
| `Instant-Share-Handbook-1.4.0.pdf` | The handbook: a PDF with pictures, in the SCAFFApps style. The SCAFFApps Suite opens it too. |
| `instantshare.py` | Instant Share as a plain server, for Mac and Linux: `python3 instantshare.py` (Python 3.10 or newer, nothing else) keeps the history there, and every device uses a browser. |
| `THIRD-PARTY-NOTICES.txt` | The licences of the parts the Windows app carries inside it. |

The release notes list each file's SHA-256 checksum, so you can check a download with `Get-FileHash <file>` in PowerShell.

The app is not code-signed, so the first time you run it Windows SmartScreen may show "Windows protected your PC".
Choose **More info**, then **Run anyway**. To remove that warning for other people you would need to buy a
code-signing certificate and sign the file.

The window is drawn by Microsoft Edge WebView2, which Windows 10 and 11 include. Nothing else is needed.

**Coming from 1.2 or earlier** (one portable file): run the installer. It closes the old copy; then open Instant Share
from the Start menu. The history, settings and pairing stay (they are in `%LOCALAPPDATA%\InstantShare`), start with
Windows moves to the installed program, and the old file can be deleted.

### The first start

The first SCAFFApps app to start on a Windows account asks for your **name** and **e-mail address**, once; the other
SCAFFApps apps on that account (the SCAFFApps Suite, Multi Screens, Smart Snap) use the same details and do not ask.
Both are required. What is sent is under [Security](#security).

## Quick start

1. **On one computer**, one that stays on while you work, start Instant Share and choose **Use this computer**. Windows
   asks whether to allow it on networks: allow it on **Private networks**.
2. **On every other computer**, start Instant Share and choose the first computer from the list (or type its name or
   address). It asks for a pairing code: on the first computer, open **Settings > Devices > Add a device** (or **Add a
   device…** in the tray menu) and type the 6-digit code it shows. Each code is good for 10 minutes and one device.
3. Leave **Start Instant Share when I sign in to Windows** ticked.
4. Phones: install `Instant-Share-1.4.0.apk`, choose the computer, and type a code the same way. iPhones and other
   browsers open the address in Settings > Phones and browsers (for example `https://OFFICE-PC:8765`), continue past
   the browser's certificate warning the first time, and type a code.

Coming from 1.1? Update every device: 1.2 talks only to 1.2. The history, settings and start-with-Windows stay; the
computer that keeps the history is joined already, and each other device joins once with a code and keeps its own
items.

If the computers cannot find each other, check that the network is set to **Private** on the computer that keeps the
history (Settings > Network & internet > Wi-Fi or Ethernet > Network profile type). If Windows never asked to allow
Instant Share, add the rules yourself in an **administrator** PowerShell there:

```
netsh advfirewall firewall add rule name="Instant Share" dir=in action=allow protocol=TCP localport=8765 profile=private
netsh advfirewall firewall add rule name="Instant Share search" dir=in action=allow protocol=UDP localport=8765 profile=private
```

## Features

- **Share text** of any length (up to 20 MB): press **Enter** (Shift+Enter for a new line; the "Enter sends" switch
  turns that off), or Ctrl+Enter, or **Share**. It reaches the other devices at once.
- **Share files** up to 5 MB each: photos (PNG, JPEG, GIF, WebP, BMP, HEIC), PDF, Word, Excel, PowerPoint (also the
  older and OpenDocument formats), RTF, text, CSV, audio (MP3, M4A, AAC, WAV, OGG, Opus, FLAC, AMR), short videos (MP4,
  MOV, 3GP) and ZIP. The **File** button, drag and drop, or paste a picture. Each file is checked by its contents:
  programs and scripts are refused, even renamed; documents only ever download, never open as a page.
- **Choose who sees it**: the **To** button lists the devices, with who is online now. Everyone, or only the devices
  you tick: the others cannot see it or open its file.
- **Send later**: away from the office network, or with the computer that keeps the history asleep, share as usual:
  Share says **Send later**, and what you share waits on your device under **Waiting to send** (with **Edit** and
  **Delete**). It goes by itself, oldest first, the moment that computer answers again, even with the window closed,
  and a notification says so; the others see it marked "written 09:14". Started while away, the app's "Can't reach…"
  page has a **Write for later** box of its own. Each share carries its own id, so one sent again after a lost answer
  is still kept once; one the history refuses stays, with why, for **Try again**, Edit or Delete.
- **Join with a code**: Settings > **Devices** > **Add a device** shows a 6-digit pairing code for the next device;
  the same list shows every device that joined, with **Remove**.
- **Windows notifications** when another device shares something with you: who, a preview (the photo itself for a
  photo), and **Copy** (text or photo) or **Save** (other files), and **Open**. They come even when the window is
  closed, stay in Windows' Notification Centre, and follow Do Not Disturb. With **Copy what arrives** on, it is on the
  clipboard already.
- **History**: Copy, Save, and a ⋯ menu to **pin** an item to the top, **archive** it (or swipe left) and, for its
  sender only, **edit** it in place (marked "edited" with the time) or **delete** it for everyone. Pins and the
  archive are each device's own. Search, photos full size, audio and video players.
- **Settings** (the gear): this device's name, Dark or Light, Enter sends, wrap, **keep the window on top**, **copy
  what arrives**, the folder **Save** uses, the devices, the archive, **storage** (how much of the history's 2 GB is
  used, by kind, with the history folder one click away on the computer that keeps it), **updates**, and **Quit**.
- **Tray icon**: click to open; right-click for Add a device, Start with Windows, Change setup and Quit.
- **Finds the other computers by itself**, and follows the computer that keeps the history if its address changes.
- **Arabic and other right-to-left text** reads right to left, line by line.
- **Instant Share for Android**: the same history and settings on the phone; notifications with Copy, Save and Open
  while the app is closed (no Google services: one encrypted connection kept open to the computer that keeps the
  history); Android's **Share** menu (links, text, photos, documents, voice notes from any app); **Send later**, also
  from its own "Can't reach…" screen and from the Share menu while away, sent when the phone is back on the Wi-Fi;
  documents open in the phone's own apps; the Back button closes what is open, then puts the app away.
- **iPhones and other browsers** use the same address (https), without notifications.
- **Updates**: Settings > About says when a new version is out (a daily check, which can be turned off) and updates in
  a click.

## Keyboard shortcuts

| Keys | Does |
|---|---|
| Ctrl+Enter or Ctrl+S | Share what is in the editor, and its file |
| Ctrl+V in the editor | Paste text, or add a copied picture as the file |
| Esc | Close the device list or a full-size photo |

## Options

```
"Instant Share.exe" [--hidden] [--data FOLDER] [--port PORT]
```

| Option | Does |
|---|---|
| `--port 9000` | On the computer that keeps the history: another port than 8765. (If 8765 is taken, it picks the next free one by itself.) |
| `--data D:\InstantShare` | Keeps the history and settings in another folder, for example on another drive. |
| `--hidden` | Starts in the tray, without the window. Windows starts it this way at sign-in. |

Starting the program while it is already running brings its window to the front.

## Where things are saved

| What | Where |
|---|---|
| The program | `C:\Program Files\SCAFFApps\Instant Share`: `Instant Share.exe` and its `resources` folder (Python and the libraries it uses) |
| Your registration (name and e-mail, shared by every SCAFFApps app) | `%APPDATA%\SCAFFApps\registration.json` |
| The history, files included | `%LOCALAPPDATA%\InstantShare\history.db` on the computer that keeps it. To back it up, quit Instant Share and copy it. |
| Settings (setup, name, appearance) | `%LOCALAPPDATA%\InstantShare\settings.json` on each computer |
| A log, for troubleshooting | `%LOCALAPPDATA%\InstantShare\textshare.log` on each computer |
| The encryption key and certificate | `tls-key.pem` and `tls-cert.pem` in the same folder, on the computer that keeps the history. Keep them with `history.db` when moving it; with a new key, every device must join again. |
| Joined devices' keys | Each device keeps its own in its settings; the computer that keeps the history keeps only a hash of each. |
| Waiting to send (Send later) | `%LOCALAPPDATA%\InstantShare\outbox`, one file each, on the device that wrote it, until it is shared; the Android app keeps its own, a browser keeps them in its own storage for the page |
| Saved files | The folder chosen in Settings; Downloads unless you change it |
| Start with Windows | The usual list of programs that start at sign-in (Settings > Apps > Startup) |

## Security

- **Only devices that joined.** A device joins once with a 6-digit pairing code from a device that has joined, and gets
  its own secret key; every request must carry it. Without one there is nothing to see: not the history, the devices,
  a file or a share. **Remove** takes a device off at once.
- **The code never travels.** It is checked with SPAKE2 (a password-authenticated key exchange): listening reveals
  nothing about it, and something pretending to be the computer gets one guess, not a million. A code lasts 10 minutes
  and 5 tries.
- **Encrypted.** Everything goes over https (TLS 1.2 or newer) with a key the computer that keeps the history makes for
  itself (RSA 2048, made with Python's standard library).
- **The apps pin the computer's key.** The Windows and Android apps bind the pairing to the key they saw, and from then
  on accept only that key, so a computer in the middle is refused.
- **Locks that hold.** "Who should see it", "only the sender edits and deletes", pins and the archive are enforced by
  the computer that keeps the history, by each device's key.
- **Only your own network.** It refuses connections from outside the local network.
- **Your registration.** The Windows app asks for your name and e-mail address once and sends them to SCAFFApps
  (`www.scaffapps.com`), with the app's name and version, the Windows version and a random number made for your
  Windows account, so SCAFFApps knows who uses its apps. Nothing shared, and nothing else about you or the computer,
  is sent.

## Updates

Settings > About > **Updates** shows this version and whether a newer one is out on the
[Releases page](https://github.com/Rady193/instant-share/releases/latest). With **Check for updates once a day** on
(the default), the apps ask once a day (the SCAFFApps website, or GitHub if it does not answer) and say so in a
notification. **Update now** downloads the new version,
checks it against the SHA-256 published in the release notes, then:

- **Windows**: the new version's installer is checked the same way and run with Windows' permission: Instant Share
  closes, is updated in the same folder and opens again (the history, settings and start-with-Windows stay). The
  SCAFFApps Suite can do the same.
- **Android**: opens Android's installer (allow Instant Share to install apps, once); the update installs over the app.

That question and your registration are all that go to the internet; nothing shared ever does.

## What it does not do

- **A browser cannot check the computer's key.** It warns about the certificate (made by the computer itself), and a
  fake page would look the same: prefer the apps.
- **The history is not encrypted on the disk** of the computer that keeps it. Protect that computer and its Windows
  account; delete passwords or keys from the history once you have pasted them where they belong.
- **Files up to 5 MB each, of the kinds listed above, and 2 GB in all.** When the history is full, new shares
  are refused: delete old files to make room.
- **The computer that keeps the history must be on.** When it sleeps, nothing new reaches the others: what they
  share waits on their own devices (Send later) and goes when it wakes.
- **A browser sends what waits only while Instant Share's page is open** in it (the apps send it themselves, also
  with their window closed), and a browser that cannot reach the computer cannot open the page to write in.

## Building from source

You need Windows and Python 3.10 or newer (the release is built with Python 3.14).

- Run the app from the source: `pythonw app.py` (needs `pywebview`, `pillow` and the Windows notification packages:
  `pip install pywebview pillow winrt-runtime winrt-Windows.UI.Notifications winrt-Windows.Data.Xml.Dom`).
- Run the plain server: `py instantshare.py`, or double-click `Instant Share.cmd`.
- Produce the installer (`Instant-Share-Setup-<version>.exe`, made with NSIS: the `makensis.exe` that electron-builder
  keeps for the other SCAFFApps apps, or the one `MAKENSIS` names), the Android app, `instantshare.py` and
  `THIRD-PARTY-NOTICES.txt` in the `release` folder: `py build.py`.
  The first run makes a build environment in `C:\venvs\textshare-build` with the versions it needs. The build prints
  each file's SHA-256 checksum and saves the list in `release\SHA256.txt`.
- The Android app is built by the same `py build.py`, when the phone-app tools are on the PC (a JDK 17 and the
  Android SDK with `platforms;android-34` and `build-tools;34.0.0`, in `%LOCALAPPDATA%\ScaffERP-android-tools`): it
  writes `android\assets\setup.html` (`android\make_setup.py`), runs `android\build.ps1` (no Gradle), packs the APK
  into the program and copies it to `release`. The first build makes the signing key,
  `%LOCALAPPDATA%\ScaffERP-android-tools\textshare-release.keystore`: **keep it and back it up**, since Android
  installs an update over the app only when it is signed with the same key.
- Produce the handbook (`release\Instant-Share-Handbook-<version>.pdf`, its only copy):
  `C:\venvs\textshare-build\Scripts\python.exe handbook\build_handbook.py`. It retakes the pictures from the real
  pages with a made-up history; `--no-shots` keeps the pictures as they are.

## Project layout

| Path | What it is |
|---|---|
| `instantshare.py` | The heart: the server that keeps the history (SQLite), the page every device shows, finding computers on the network, and the security (its own certificate, pairing with SPAKE2, device keys). Python standard library only. |
| `app.py` | The Windows app around it: the window, the setup and pairing, the tray icon, Windows notifications, copying and saving, updates. |
| `build.py` | Packs the app into one program and makes its installer (`build/installer.nsi`). |
| `registration.py` | The name and e-mail registration, the Python twin of `registration.js` in the other SCAFFApps apps. |
| `build/` | The icon (`icon.svg`, and `make_icon.py`, which draws `icon.png` and `icon.ico`), and `installer.nsi`. |
| `android/` | Instant Share for Android: Java sources (`src/`), resources, the setup page (`assets/`, made by `make_setup.py`) and `build.ps1`. |
| `handbook/` | The handbook: `handbook.html`, its pictures, and `build_handbook.py`. |
| `Instant Share.cmd` | Runs the plain server from the source. |
| `RELEASE-NOTES.md` | What changed in each version. |

## Licence

Copyright © 2026 SCAFFApps. All rights reserved. The released program is free to download and use, for personal or
business use. The source code is published for reference only. See [LICENSE](LICENSE).
