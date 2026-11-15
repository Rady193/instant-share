# Release notes

## 1.1.0 — 15 November 2026

Text Share is now **Instant Share**, with what its first users asked for.

### Downloads

| File | Size | SHA-256 |
|---|---|---|
| `Instant-Share-1.1.0-portable.exe` | 16.5 MB | `145C56151F48831B6D0D0F284632EFF398A77AC087F98C3D3EB3CC9B68EC4BFA` |
| `Instant-Share-1.1.0.apk` | 62 KB | `AC170DC5A33EEB4E947DEFE33FD845A98A0E0F85D98BEA3BF8927FCE2390BC54` |
| `Instant-Share-Handbook-1.1.0.pdf` | 988 KB | `51B4BCC5BF4E0B1B904A26C3AB46F51DE7149A68F988FFC226504CDE852F21A2` |
| `instantshare.py` | 126 KB | `EE41A9C136561027D0584A11A7BF1256B610DA9CC96D7AA68C89BA6F2AEC5373` |

### New

- **The new name, Instant Share**, everywhere. Coming from Text Share 1.0: quit it, start Instant Share; it takes over
  the same history, settings and "start with Windows". The Android app installs over the old one, keeping its setup.
- **More kinds of files, up to 5 MB**: Word, Excel, PowerPoint (also older and OpenDocument formats), RTF, text, CSV,
  audio and voice notes (MP3, M4A, AAC, WAV, OGG, Opus, FLAC, AMR), short videos (MP4, MOV, 3GP), ZIP, BMP and HEIC
  photos, besides PNG, JPEG, GIF, WebP and PDF. Audio and video play in the history; documents download (never open
  as a page); programs and scripts are refused, even renamed.
- **Edit in place, by the sender only**: the item changes for everyone, marked "edited" with the time.
  **Delete is the sender's too.**
- **Pin** an item to the top, and **archive** one (swipe left on phones and touch screens, or the ⋯ menu), each
  device for itself; Undo; the archive is in Settings.
- **Settings** (the gear): name, appearance, **Enter sends** (on: Shift+Enter for a new line), wrap, **keep the
  window on top** (also the pin in the top bar), **copy what arrives** (straight to the clipboard), **where Save puts
  files** (a folder of your choice, on Windows and Android), the archive, and **storage**: the history's use of its
  2 GB, by kind, and its folder.
- **2 GB for the whole history**: when it is full, new shares are refused with a message.
- **Phones**: a gear instead of the name and appearance buttons, with the page's details in Settings; File, Clear and
  Copy side by side; the device list as a sheet that fits the screen; the Back button closes what is open, then puts
  the app away, and never returns to the setup; documents open in the phone's own apps.
- **Questions in the page's own style** instead of the browser's boxes.

### Fixed

- **The pop-up by the clock was blank** on Windows (shown without being told it was visible, so its page was never
  drawn), and appeared in the taskbar. It now draws, and stays out of the taskbar and Alt+Tab.
- **Start with Windows could be taken over** by another copy of the program started with another history folder (a
  test copy, say). Each copy now changes only its own entry.
- On phones, the device list ran off the left of the screen.
- Audio and video could not play (the page's security policy did not allow them yet).

### Tested

- On Windows 11, with the `.exe`: the 24 checks of 1.0 (setup, discovery, tray, pop-ups with Copy, private shares,
  photos, refusals, appearance, second start), now also the pop-up's own pixels (drawn, not blank) and its staying out
  of the taskbar; 9 more for the new settings: always on top on and off (the window's real topmost state),
  remembered; copy what arrives (on the clipboard with no click); the history folder and storage on the computer that
  keeps it; where Save puts files. The real start-with-Windows entries were left as they were.
- The server: 53 checks (edit and delete by the sender only, pins and archive per device, Word and voice notes
  accepted, programs refused even renamed, documents sandboxed downloads, storage, the 2 GB limit, the 1.0 history
  upgraded in place).
- The page: 15 checks (Enter sends, edit in place, menus by owner, pin, archive with Undo, storage, the phone layout)
  and swiping left on a touch screen; the Android app's pages with a stand-in for its Java side (13 checks: save
  folder, documents to the phone's viewer, Back, settings); the APK built and signed.

### Not tested

- The Android app on a real phone: the folder picker, Back, documents in other apps, the new file types from the
  Share menu, and installing 1.1 over 1.0.
- Choosing a folder in Windows' folder dialog (the setting itself was checked).
- Two separate computers on a real network; Windows 10; Safari on an iPhone; `instantshare.py` on Mac or Linux.

## 1.0.0 — 15 November 2026

The first release.

### Downloads

| File | Size | SHA-256 |
|---|---|---|
| `Text-Share-1.0.0-portable.exe` | 16.5 MB | `6E082AE6EDBB2F2F59C53B42DC957D0CA845AB700B3F39E1BF7AF7551D5509CF` |
| `Text-Share-1.0.0.apk` | 58 KB | `0A5C1EE02B73A8AD40B87EAD142C291280064E693876E5AA2EAB7398B214D89F` |
| `Text-Share-Handbook-1.0.0.pdf` | 915 KB | `30642B79DAE05591A8EFC6C24FFD89FE11303DCD51E03CFD4B729FC047AD70B8` |
| `textshare.py` | 80 KB | `3CF5EE3C0ACBCA3D184054C17DCC0162FBC1DF38B7F32D96E079BDB59061C86A` |

The `.exe` is for 64-bit Windows 10 and 11 and is not code-signed, so Windows SmartScreen may ask for confirmation on
first run. The `.apk` is for Android 10 and newer, installed from outside the Play Store (Android asks to allow that
once). `textshare.py` is Text Share as a plain server for Mac and Linux (`python3 textshare.py`), used from a browser.

### Features

- **A Windows app**, one portable file, with its own window and a tray icon. No console window.
- **Setup in one question**: keep the history on this computer, or connect to the one that does. The others are found
  on the network by themselves, and followed if their address changes.
- **Share text, photos and PDFs.** Text up to 20 MB; photos (PNG, JPEG, GIF, WebP) and PDFs up to 1 MB, checked by
  their contents. Add a file with the File button, drag and drop, or paste a picture.
- **Who should see it**: everyone, or only the devices you tick, from a list showing who is online.
- **Pop-ups by the clock** when another device shares with this one: who, a preview, Copy (text or photo) or Save
  (PDF), Open. They come while the window is closed and never take the keyboard.
- **History**: Copy, Copy photo, Save to Downloads, Edit, Delete for everyone, Show all, search (text, file names,
  devices), photos full size.
- **Dark and light appearance**, as in Multi Screens; dark by default; the title bar follows.
- **Tray icon**: click to open; Start with Windows, Change setup, Quit. Closing the window keeps Text Share running
  there; starting it again brings the window back.
- **Text Share for Android**: the same history on the phone; notifications with Copy, Copy photo, Save and Open,
  also while the app is closed; Android's Share menu (links, text, photos, PDFs from any app); the phone's status
  bar in the chosen appearance. Phones on the same Wi-Fi get the app from the computer that keeps the history.
- **Instant**: the apps keep one request open to the computer that keeps the history, which answers the moment
  something is shared with them. No Google services, no internet.
- **iPhones and other browsers** use the same address, without notifications.
- **Offline**: no internet, no accounts; it answers only devices on its own network.

### Tested

- On Windows 11, two copies of the app on one PC (one keeping the history, one connecting) and a browser:
  - the first-start setup on both, the second finding the first by itself;
  - starting hidden in the tray, closing to the tray, opening from the tray icon, a second start bringing the window
    back;
  - the device list showing the other app online;
  - a share for one device only: the pop-up on that device alone, its Copy putting the text on the clipboard, and the
    browser not seeing it;
  - a photo shared from the browser: shown in the history, and copied as a picture from the pop-up;
  - files too big or of the wrong kind refused;
  - the light appearance kept.
- The server on its own (27 checks): an earlier history upgraded in place; private items hidden from others, also by
  number and for deleting; photos and PDFs stored and served unchanged; a web page named `.png`, an SVG, files over
  1 MB and broken uploads refused; search by file name.
- The tray menu, opened once and looked at.
- The Android app: built and signed (minimum Android 10, target 14), its manifest and contents checked. Its pages in
  a phone-sized browser with a stand-in for its Java side (14 checks): the setup finding the computer and connecting;
  the can't-reach page trying again by itself; a link and a photo shared from another app arriving in the editor;
  Copy photo, Save, the appearance, the device name and Change setup reaching the phone's side; an Android browser
  being offered the app and receiving the same APK.
- The open request (4 more server checks): a share reaches a waiting device at once; a wait with nothing ends on
  time; a waiting device counts as online.
- The `.exe`: publisher SCAFFApps and version 1.0.0 in its file properties. Microsoft Defender finds no threats.

### Not tested

- **The Android app on a real phone.** It was built and its pages tested, but not run on Android: notifications and
  their buttons, staying connected in the background, finding the computer over Wi-Fi, the Share menu, saving to
  Downloads and opening PDFs are untried.
- Two separate computers on a real network, and the Windows Firewall question on first start.
- Windows 10.
- Safari on an iPhone.
- Save (to Downloads) from the pop-up and from the history.
- The tray menu's items when clicked by hand, and Start with Windows at a real sign-in.
- `textshare.py` on Mac or Linux.
- SmartScreen on a computer that downloaded the file.
