# Release notes

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
