# Release notes

## 1.3.3 — 16 November 2026

"Published by SCAFFApps · www.scaffapps.com" wherever Instant Share names its publisher, and no browser-blue links.

### Downloads

| File | Size | SHA-256 |
|---|---|---|
| `Instant-Share-Setup-1.3.3.exe` | 13.8 MB | `F846A1C7A15A31CC5E9D8E3C1E83840BF8709DB9C9602FA366C82D477074137E` |
| `Instant-Share-1.3.3.apk` | 70 KB | `A3B4A9FA880F472260DBF6A7EB6554EF48FD8D6F64011A4953F9423F090346D8` |
| `instantshare.py` | 167 KB | `4CD8A815837578039602853F8D51F3176B98748A430D6D0E4ABA4F0D6C63C4E8` |
| `THIRD-PARTY-NOTICES.txt` | 143 KB | `85C2B99ABA6B09F31876B24AE64FFEE4373D764563F0C5580F69320341EEF269` |

For 64-bit Windows 10 and 11. Not code-signed, so Windows SmartScreen may ask for confirmation on first run.

### Changed

- **"Published by SCAFFApps · www.scaffapps.com"**, with SCAFFApps in bold, at the end of the welcome page, at the foot of the setup page, in the page's footer and in Settings > About (where it said "· SCAFFApps"), as in every SCAFFApps app.
- **Links in the SCAFFApps colours.** **Get the app** in Settings was in the browser's own blue; links are now gold in the dark appearance and purple in the light one.
- Nothing else changed. The Android app and `instantshare.py` are 1.3.3 only to keep the versions together (the page they show has the same changes).

### Updating

- **From 1.3.0 to 1.3.2:** Settings > Updates > **Update now**, or **Update** in the SCAFFApps Suite.
- **Android:** update when convenient.
- From 1.2 or earlier: run the installer once (see 1.3.0).

### Tested

- The SCAFFApps Suite 1.0.0 updated Instant Share 1.3.2 to 1.3.3 with its own button, against a stand-in for GitHub offering this installer: downloaded, checked against both checksums, Windows' permission, installed in 11 seconds; Windows lists 1.3.3. Microsoft Defender finds nothing in the installer.
- The new program, isolated: the line on the setup page, in the footer and in Settings > About, SCAFFApps in bold; **Get the app** in gold.

## 1.3.2 — 16 November 2026

A shorter welcome page, and the program's folder of supporting files is called `resources`.

### Downloads

| File | Size | SHA-256 |
|---|---|---|
| `Instant-Share-Setup-1.3.2.exe` | 13.8 MB | `5ED97307B34C3760FF9E9B8D56A2103D916F0E16C4504FC65017F5C1AB17F6DA` |
| `Instant-Share-1.3.2.apk` | 70 KB | `83FDF9D02F53B9B89F7633A50DF90F7CB1CF2F11281FFBFC54FE7080B07A8EA1` |
| `instantshare.py` | 167 KB | `254A1D5327958F41E35AE03D3D071644668817F9845867A471AD3C8B3200289E` |
| `THIRD-PARTY-NOTICES.txt` | 143 KB | `4A1BE7F24972C63F485C7CD217D91A5662B405CF50BCD2195773BF020F0DA386` |

For 64-bit Windows 10 and 11. Not code-signed, so Windows SmartScreen may ask for confirmation on first run.

### Changed

- **The welcome page** (your name and e-mail, asked once) ends with just "Published by SCAFFApps · www.scaffapps.com", without the paragraph above it.
- **`resources`, not `_internal`.** `C:\Program Files\SCAFFApps\Instant Share` holds `Instant Share.exe`, its uninstaller and one folder, `resources` (Python, the libraries, the icons), named like the other SCAFFApps apps' folder. Updating from 1.3.1 removes its `_internal` folder.
- Nothing else changed. The Android app and `instantshare.py` are 1.3.2 only to keep the versions together.

### Updating

- **From 1.3.0 or 1.3.1:** Settings > Updates > **Update now**, or **Update** in the SCAFFApps Suite.
- **Android:** nothing changed for phones; update when convenient.
- From 1.2 or earlier: run the installer once (see 1.3.0).

### Tested

- 1.3.1 updated to 1.3.2 by its installer without pages, as Update now runs it, with Windows' permission: `_internal` gone, `resources` in its place, the installed files identical to the build, Windows lists 1.3.2, Instant Share opened again. (1.3.0's own **Update now** was tested with 1.3.1.)
- The welcome page at 600, 1200 and its own window width: everything centred, the shorter ending. The setup page, keeping the history, handing the Android app to phones.
- Microsoft Defender finds nothing in the installer or the installed program.

### Not tested

- Uninstalling, and the Android app on a real phone.

## 1.3.1 — 16 November 2026

Instant Share for Windows is installed as a folder instead of one self-unpacking file: it opens faster and is less likely to alarm antivirus programs. The welcome page's form is centred.

### Downloads

| File | Size | SHA-256 |
|---|---|---|
| `Instant-Share-Setup-1.3.1.exe` | 13.8 MB | `4A798095B542CDA2BA20EA0A95866BBE2F4F343B2D5EB676B84C489E0057CF0E` |
| `Instant-Share-1.3.1.apk` | 70 KB | `D395A92A1548EAB0C7672E2EAE038026EF284B2DCEAB9AD179910F302F31EE88` |
| `instantshare.py` | 167 KB | `C14B68CB3B784A628FB69886146065B8A456558EB8431763CD3258AA238227D7` |
| `THIRD-PARTY-NOTICES.txt` | 143 KB | `E22C83BA5CED2E695E88319754B1ECA9C274B035DEAFF1E368C54C24D9F9987A` |

For 64-bit Windows 10 and 11. Not code-signed, so Windows SmartScreen may ask for confirmation on first run.

### Changed

- **The program is a folder.** `C:\Program Files\SCAFFApps\Instant Share` now holds `Instant Share.exe` and an `_internal` folder with Python and the libraries it uses (34 MB in all), like the other SCAFFApps apps, instead of one 17 MB file that unpacked itself into a temporary folder every time it started. It opens sooner (its window in about 0.65 s instead of about 1 s on the test laptop), leaves nothing in the temporary folder, and self-unpacking programs are a common reason for antivirus false alarms. The installer is smaller: 13.8 MB instead of 16.8 MB. Updating clears the version before's libraries first, and uninstalling removes the folder.
- **The welcome page's form is centred** under its heading (it sat to the left).
- Nothing else changed: the same pages, settings and history. The Android app and `instantshare.py` are 1.3.1 only to keep the versions together.

### Updating from 1.3.0

- **Windows:** Settings > Updates > **Update now**, or **Update** in the SCAFFApps Suite. Windows asks for permission; Instant Share closes, is updated in the same folder and opens again.
- **Android:** nothing changed for phones; update when convenient.
- From 1.2 or earlier: run the installer once (see 1.3.0).

### Tested

- **Update now** in the installed 1.3.0, against a stand-in for GitHub offering a 1.3.1 build (the same as this one except for the centring): downloaded, checked against its SHA-256, Windows' permission, 1.3.0 closed, 1.3.1 installed in the same folder and opened, in 8 seconds. Windows lists 1.3.1. This release's installer was then run over it without pages, as Update now runs it: the installed files are identical to the build.
- Microsoft Defender finds nothing in the installer or the installed program.
- The new program, isolated (its own data folder): the welcome page (centred at 600, 1200 and its own window width), the setup page, keeping the history (the page it serves), handing the Android app to phones.
- Start to window, three runs each: 1.3.0 0.92 to 1.38 s, 1.3.1 0.60 to 0.78 s.

### Not tested

- Uninstalling 1.3.1, and the Android app on a real phone.

## 1.3.0 — 16 November 2026

An installer instead of the single file, the SCAFFApps colours, and your name and e-mail asked once.

### Downloads

| File | Size | SHA-256 |
|---|---|---|
| `Instant-Share-Setup-1.3.0.exe` | 16.8 MB | `35D2D9D7855DEBB750F3EB63850B4090AF898A4186DA1E027A95B7E3DCB63AB4` |
| `Instant-Share-1.3.0.apk` | 70 KB | `C26AD6099DC8F97C1429BC3DB27F1637F4051BA62EBD844288C12813F13043D9` |
| `instantshare.py` | 167 KB | `FF20EB56E9C47DBA7EF7A579BAD921E02B27035D05F94A7EA902BF1657286DA8` |
| `THIRD-PARTY-NOTICES.txt` | 143 KB | `288A31546C419317E87BF89C92C7E878681F62B89A9C36EA3675602293C845A1` |

### Changed

- **Installed, not portable.** `Instant-Share-Setup-1.3.0.exe` installs for everyone in `C:\Program Files\SCAFFApps\Instant Share`, with shortcuts in the Start menu's **SCAFFApps** folder and on the desktop, and an entry in Windows' list of programs. It closes a running Instant Share (an old single-file copy too) first. The history, settings and pairing stay in `%LOCALAPPDATA%\InstantShare`.
- **Updates through the installer.** **Update now** downloads the new installer, checks its SHA-256, and runs it with Windows' permission: Instant Share closes, is updated in place and opens again.
- **Get the app** leads to this Releases page (the installer) instead of handing out the program.
- **The SCAFFApps colours**, on Windows, in the pages the history computer serves and on Android: buttons and checkboxes in SCAFFApps purple, highlights in gold; the light appearance white, with cream only behind the content.
- **Your name and e-mail, once** (the Windows app). The first SCAFFApps app to start on a Windows account asks for them; the others (the SCAFFApps Suite, Multi Screens, Smart Snap) do not ask again. They are sent to SCAFFApps (`www.scaffapps.com`) with the app's name and version, the Windows version and a random number made for the Windows account. Nothing shared, and nothing else, is sent.
- **The SCAFFApps Suite** installs, opens and updates Instant Share.

### Updating from 1.2

- **Windows:** 1.2 cannot update itself to 1.3 (it looks for a single file, which 1.3 does not have). On each computer, run the installer once: it closes the old copy; then open Instant Share from the Start menu. Start with Windows moves to the installed program; delete the old file.
- **Android:** install the new app over the old one, or use **Update now** in the app.
- 1.3 and 1.2 work together; nothing changed in how devices talk to each other.

### Tested

- Installed without pages, with Windows' permission: in `C:\Program Files\SCAFFApps\Instant Share`, Start menu and desktop shortcuts, Windows lists 1.3.0 by SCAFFApps. Microsoft Defender finds nothing in the installer or the program.
- The installed program, isolated (its own data folder, start-up entry and notification name): the registration page first (empty details refused), then setup; the registration reached a local copy of the website.
- Found and fixed while testing: an installer that closed itself (it closed every "Instant-Share-*" program, its own name included), and an installer that kept a copy of itself in the program's folder, which Microsoft Defender's machine-learning check took for a trojan (a false alarm, gone without that step).

### Not tested

- **Update now** from 1.3 to a later version (needs a later release), and the Android app on a real phone.

## 1.2.0 — 15 November 2026

Instant Share now keeps strangers out: everything is encrypted, and only devices that joined with a pairing code get
in. Notifications are Windows' own, and the apps can update themselves.

### Downloads

| File | Size | SHA-256 |
|---|---|---|
| `Instant-Share-1.2.0-portable.exe` | 16.8 MB | `62A705763AA4D99C616B33665FB8892790563B000AF2708EEE93DE23369B34EC` |
| `Instant-Share-1.2.0.apk` | 70 KB | `9CC80EE32346193A8081DE34F1C061695770F909537BC9AC4ADBF65670A2BFEA` |
| `Instant-Share-Handbook-1.2.0.pdf` | 1.1 MB | `EA40BA64668BF13DE332B24CF588F040421A23463E52C62FB9BA8E745512E636` |
| `instantshare.py` | 170 KB | `1A2ACD516CF7192F80E2FA06543226EDD7F9874D2D3BD8043E70E620AAC0FD4F` |

### Updating from 1.1

Update **every** device: 1.2 talks only to 1.2 (an older app is told to update). Quit 1.1 from its tray menu and start
the 1.2 file: the history, settings and start-with-Windows stay. The computer that keeps the history is joined
already; each other computer and phone joins once with a code (below), and keeps its own items. On Android, install
the new app over the old one.

### New

- **Joining with a pairing code.** Only devices that joined can see the history, the devices, the files or share
  anything. On a device that has joined, Settings > **Devices** > **Add a device** (or **Add a device…** in the tray
  menu) shows a 6-digit code, good for 10 minutes, 5 tries and one device; the new device types it once. The code is
  checked with SPAKE2, so it never crosses the network. Each device gets its own key; the computer that keeps the
  history stores only a hash of it. Settings > Devices lists the joined devices, with **Remove**.
- **Encrypted.** Everything between the devices goes over https (TLS 1.2 or newer), with a certificate the computer
  that keeps the history makes for itself (no extra software). Old `http://` addresses lead to the new ones.
- **The apps check the computer's key.** The Windows and Android apps bind the pairing to the key they saw and accept
  only that key afterwards, so a device pretending to be that computer is refused, with a
  message.
- **Locks that hold.** "Who should see it", "only the sender edits and deletes", pins and the archive are now enforced
  by device keys: a device that was not chosen cannot open a file even with its address.
- **Windows' own notifications** instead of the home-made pop-up: who, a preview (the photo itself for a photo),
  **Copy** or **Save**, and **Open**. They stay in the Notification Centre and follow Do Not Disturb.
- **Updates.** Settings > About > **Updates**: **Check now**, a daily check (on by default, can be turned off; the
  only thing that reaches the internet), and **Update now**, which downloads the new version, checks it against the
  SHA-256 in its release notes, and restarts in it (Windows) or opens Android's installer.
- **Quit Instant Share** in Settings (Windows and Android): closes it completely, notifications included, until it is
  started again.
- **Phones**: "Enter sends" and "Wrap long lines" side by side, without the "Write or paste" label; the search box
  across the whole width; no Copy button under the editor (each item keeps its own).

### Fixed

- A page that the Windows app showed right at start-up (the "connecting" or setup page) could be covered by the
  "Starting…" page that was still loading.

### Tested

- The server, against real encrypted connections: 41 checks (its own certificate, read by OpenSSL; pairing with the
  right code, a wrong code, a used code, 5 tries; the pairing bound to the key seen; keys, the cookie, Remove; files
  and lists only for their devices; old http addresses sent to https; 1.1 apps told to update). The 53 checks of 1.1,
  run again with device keys.
- A browser (Edge, phone size) joining with a code over https: 20 checks, with the phone layout changes. The Android
  pages with a stand-in for the Java side: 14 checks (setup and pairing, updates, Quit, removal). The phone's pairing
  code itself, in Java on a computer against the real server: 5 checks.
- On Windows 11 with the `.exe`: 17 checks (the computer that keeps the history paired by itself; another computer
  joining with a code; its window on the computer's own certificate, accepted by its key; a share arriving as a
  Windows notification, kept in the Notification Centre with Copy and Open; Remove; another key at the same address
  refused; Quit). Updating with the `.exe` from a stand-in for GitHub: 8 checks (found, downloaded, checked, restarted,
  the old file removed, start-with-Windows moved, the history kept); a download not matching its checksum changes
  nothing.

### Not tested

- Pressing **Copy** on a Windows notification (the notification and its buttons were checked; on the test PC Windows
  put notifications straight into the Notification Centre, and the automated press could not reach it).
- The Android app on a real phone: joining with a code, the pinned connection, Quit, updating through Android's
  installer.
- Safari on an iPhone with the computer's own certificate; two separate computers on a real network; Windows 10;
  `instantshare.py` on Mac or Linux.

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
