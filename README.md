# Homecloud

Move your iCloud Photos or Google Photos library to a computer you already own. Keep a photo app on your iPhone or Android phone. Stop paying for cloud storage.

You paste one prompt into Claude Code, and Claude Code does the setup for you on your Mac or Windows PC:

1. It checks your computer by itself, without changing anything.
2. It explains the ways to move your photos, asks you at most two questions, and shows you one plan. It changes nothing before your yes.
3. After that one yes, it runs the plan to the end. Your photos come down in the way you picked, with every original that the cloud keeps, and it checks every photo and video: "N of N".
4. It installs [Immich](https://immich.app), a free photo app that runs on your computer, adds your library to it, and checks again.
5. It sets up [Tailscale](https://tailscale.com), so the Immich app on your phone works away from home, and opens a page with QR codes for your iPhone or Android phone.
6. At the end, it gives you the steps to stop the old cloud's photo sync and change your plan. You do those steps yourself.

Homecloud never deletes or changes anything in iCloud or in Google Photos. You can also run every step yourself, without Claude Code. See [Do it by hand](#do-it-by-hand).

If you are Claude Code or another AI agent: read [PROMPT.md](PROMPT.md) and follow it. Explain the ways, show one plan, and wait for the person's yes before you run anything that changes the computer.

## Ways to move your photos

Every way ends the same: your photos in Immich on your own computer. For iCloud Photos, there are two ways, and [docs/two-paths.md](docs/two-paths.md) compares them. For Google Photos, there is one main way, with Google's own export as the other choice, and [docs/google-photos.md](docs/google-photos.md) explains both.

### iCloud Photos

- **Path A: sign in to iCloud.com.** `scripts/icloud.py` downloads every original from iCloud's servers, on a Mac, Windows, or Linux, also straight to an external drive. It counts your library before it starts and checks every file against iCloud's own list. You type your Apple Account password in your own Terminal window, and it goes only to Apple. Until you log out, a session key on the computer opens what iCloud.com opens. The script is unofficial, so a change at Apple can break it.
- **Path B: no sign-in.** Apple's own apps download the originals with the sign-in you already have: the Photos app on a Mac, the Immich app on your iPhone, iCloud for Windows, or Apple's data copy at privacy.apple.com. Nobody asks for your Apple password, and no key stays on the computer. It is slower for a big library.

Path B fits best if your Apple account holds a lot of sensitive data, or if you don't want any key on the computer. Path A fits best for a big library, for Windows or Linux, or for a copy straight to an external drive.

### Google Photos

- **Path G: Google Photos in your browser.** You paste a script from the kit into the console of your own signed-in photos.google.com tab, in Chrome, Edge, Brave, Arc, Vivaldi, or Opera. It repeats the web app's internal requests: it lists your library, compares its size with the free space, and downloads every item into a folder that you pick. It only reads. It never sees your Google password or reads your cookies, and it saves nothing that could open your account. At the end, the kit checks every file against the list that the script read from Google: "N of N".
- **Google Takeout, Google's own export.** It carries no risk to your Google Account. Google prepares the zip archives first, which can take hours or days, you download them within 7 days, and the check is weaker.

Path G has risks, and you should know them before you choose:

- Google's terms of service forbid reverse engineering its services and some kinds of automated access. Path G repeats Google's internal requests, so Google could see it as a breach.
- Google can suspend or close a whole Google Account, with Gmail, Google Drive, and YouTube, not only Photos. No case of this was found for read-only tools that run in your own tab, but nobody can rule it out.
- Google can change the internal requests any day. Then the script stops, and Takeout still works.
- Browsers warn before you paste into the console, because scammers use it. Paste only the script that the kit copies for you.
- Items that you backed up in Storage saver have no original at Google. You get the smaller copy that Google kept.
- Locked Folder and Trash are not included.

If you want no risk to your Google Account, pick Takeout.

## Four ways to run it

Claude Code asks you one question: will this computer be at home and switched on most of the day? Yes means Always on, and no means Home archive. Ask for Hybrid or Offline vault if you want one of those. Hybrid is for iCloud Photos, and Offline vault is for an iPhone. Each mode gives something up. The prices are what you pay for cloud storage each month, when what stays in the cloud fits in the free plan: 5 GB at Apple, or 15 GB at Google, shared with Gmail and Google Drive.

### Always on, $0 a month for storage

A computer stays on at home all the time. An old laptop on its charger works, or a small PC you buy once. New photos upload from your phone by themselves, whenever iOS or Android lets the Immich app run. Your whole library is in the Immich app wherever you are.

What you give up:

- A computer that stays on and awake, and the electricity it uses. A MacBook sleeps when you close the lid, so you keep the lid open.
- Your phone decides when the Immich app uploads in the background. The sure way is to open the app on its Backup page and keep it open until the upload ends.
- After a power cut or a restart, Immich stays down until someone signs in to the computer.

### Home archive, $0 a month for storage

The computer is on only some of the time. When it is on, you open the Immich app on your phone, and new photos go home. You keep about the last year of photos on your phone.

What you give up:

- Older photos open only while the computer is on.
- New photos exist only on your phone until the computer is on and the app has uploaded them.

### Hybrid, $0.99 a month for storage

For iCloud Photos. You keep Apple's 50 GB plan for recent photos. Once a month, older photos move to your computer.

What you give up:

- You still pay $0.99 a month. That is the US price, and it differs by country.
- Older photos open only while the computer is on.
- Each month you delete older photos from iCloud yourself, in Apple's Photos app or with iCloud for Windows. No script does that step for you.

### Offline vault, $0 a month for storage, no apps on your phone

For an iPhone. Once a month you plug your iPhone into the computer. A script copies the new photos to a drive and checks them. The script runs on the computer with Python and pymobiledevice3. On Windows it also needs Apple's device driver, which comes with the Apple Devices app or iTunes.

The cable sees only the originals stored on your iPhone. With **Optimize iPhone Storage** on, many originals live only in iCloud. That is why the first full copy of your library comes through Path A, Path B, or Path G.

What you give up:

- You have to remember to do it, and you have to be there to unlock the iPhone and type its passcode.
- There is no phone app.

### In every mode, you own the backups

Keep a second copy of your photos on another drive. If the only drive with your photos dies, the photos are gone.

Read [docs/modes.md](docs/modes.md) for how each mode works day to day.

## What you need

- A Mac or a Windows PC. A Mac needs one of the three newest versions of macOS. A PC needs 64-bit Windows 10 22H2, Windows 11 23H2, or newer. The Path A download also runs on Linux.
- At least 8 GB of memory (RAM). Immich needs 6 GB for itself. 16 GB is more comfortable.
- For Path A, Python 3.10 or newer. For Path B, Apple's app that downloads your photos: the Photos app on a Mac, set to sync iCloud Photos, or iCloud for Windows from the Microsoft Store on a PC. Without either, the Immich app on your iPhone can copy your library, more slowly. For Path G, Chrome, Edge, Brave, Arc, Vivaldi, or Opera, where you can sign in to Google Photos. Safari and Firefox do not work for it.
- Free disk space. Path A, Path G, and the Photos app on a Mac need about 2.2 times the size of your library, because the files land in a download folder and are then copied into Immich. With the Photos app, the Photos library comes on top. The Immich app needs about 1.1 times. [docs/two-paths.md](docs/two-paths.md#side-by-side) lists each way for iCloud. An external drive works for the download folder. Claude Code works out the numbers after it counts your library.
- A second drive for the backup copy.
- Claude Code, for the guided setup. It needs a paid Claude plan, such as Pro at $20 a month, or a Claude Console account. The free Claude plan does not include Claude Code. Immich and Tailscale keep running without it.
- Your phone, an iPhone or an Android phone, to run the Immich app. For Path A, your iPhone gets Apple's sign-in code. For Path B, you may read your library's size and your photo counts on it.
- Time. Downloading every original can take many hours.

Claude Code installs what is missing after your one yes to its plan: Docker Desktop, Immich, and Tailscale, Python on Windows, Apple's Command Line Tools on a Mac that lacks them, and the osxphotos tool for the Photos app on a Mac. Path A needs Python 3.10 or newer. The other scripts run on Python 3.9 or newer. Docker Desktop is free for personal use.

## Quick start

Do this on the computer that will keep your photos.

### What you do

1. Open Claude Code and switch it to **Auto** mode. Install the Claude desktop app with [Anthropic's desktop quickstart](https://code.claude.com/docs/en/desktop-quickstart), sign in, and open the **Code** tab. Under the prompt box, choose **Local**, pick a new, empty folder, and choose **Auto** in the mode menu. Cmd+Shift+M on a Mac or Ctrl+Shift+M on Windows opens that menu. In Auto mode, Claude Code runs commands without asking you each time.
2. Paste the prompt. Open [PROMPT.md](PROMPT.md), copy the text in the box, and paste it into the prompt box.
3. Answer at most two questions, and say yes to the plan that Claude Code shows you. That one yes covers the whole setup.
4. When your computer asks to allow an installer, allow it. On a Mac, you type your Mac password in the system's window. On Windows, you click **Yes**.
5. Choose an email and a password for Immich, the photo app, in the window that opens. Never type that password in the chat.
6. Sign in to Tailscale, or create a free account, in the browser window that opens. On a Mac, allow Tailscale's system extension and VPN configuration when macOS asks.
7. On your phone, when Claude Code says so: follow the part of the page it opens for your phone, iPhone or Android. Scan the two QR codes, install Tailscale and Immich, sign in to both, and turn on **Enable Backup** in Immich. On Android, the page also shows how to let Immich run in the background.

Your path and your mode add a few steps of your own, and the plan lists them:

- Path A: you type your Apple Account email, password, and code once, in your own Terminal or PowerShell window.
- Path B: you change a setting in Apple's own app, such as **Download Originals to this Mac** in Photos, or you install iCloud for Windows. Before the download, you may read your library's size on your iPhone and type it in the chat. With the Immich app, iCloud for Windows, or Apple's data copy, you also read the number of photos and videos there.
- Path G: in the Google Photos tab that opens, you open the console, paste the script, and press Enter. The first time, the browser asks you to type `allow pasting` and paste again. That switches the paste warning off in this browser profile, so when the move is done, you switch it back on: in DevTools, click the gear icon at the top right, and at the bottom of **Preferences** click **Restore defaults and reload**. This also resets any other DevTools setting you changed. Then you click **Start** in the Homecloud panel, pick the folder, and allow the site to edit files in it. If the browser asks whether photos.google.com may download multiple files, you click **Allow**. You keep the tab open until the panel says **Done**. In Brave, you first switch on one setting, and the steps show how.
- On a Mac without Apple's Command Line Tools, a window offers to install them. Click **Install**, then **Agree**.
- On Windows, the setup may need one restart to finish WSL 2, which Docker Desktop uses. Restart the PC, open the same session in the Claude app, and type **continue**.
- In Tailscale's admin console, you turn off key expiry for this computer, so Tailscale does not sign it out after 180 days. The setup prints the link.
- To keep Immich running after a restart, you turn on **Start Docker Desktop when you sign in to your computer** in Docker Desktop's settings. On a Mac, you also check that Tailscale opens at sign-in, as [scripts/tailscale.md](scripts/tailscale.md#mac) shows. In Always on mode on a Mac, you run one command in Terminal, which asks for your Mac password.
- For the backup copy, you connect a second drive.
- At the end, you turn off iCloud Photos or backup in the Google Photos app, and change your plan yourself.

If **Auto** is not in the mode menu, keep the mode you have. Claude Code then asks before each command, and you approve each one.

### What Claude Code does for you

- It checks your computer: the system, the memory, the free space, the drives, and what is installed.
- It picks a folder for your photos with enough room, outside iCloud Drive, OneDrive, and Google Drive.
- It runs one command that installs Docker Desktop, Immich, and Tailscale: `scripts/setup.sh` on a Mac, or `scripts/setup.ps1` on Windows, which also installs Python if it is missing.
- It creates your Immich account and an API key for the import. The key goes into a file that only your account can read, and nothing shows it.
- It copies your photos the way you picked, and checks every one: "N of N".
- It adds them to Immich with your favorites and albums, and checks again.
- It opens a page for your phone with QR codes, your server address, and your Immich email, with one part for the iPhone and one for Android.
- It sets up your mode, copies your photos to the second drive, and checks that copy.
- With Path A, it signs you out of iCloud.com at the end.
- It gives you the steps to turn off the old cloud's photo sync and change your plan.

Pick a folder for Claude Code that does not sync to a cloud service. On a Mac, Desktop and Documents sync to iCloud Drive if you turned that on. On Windows, OneDrive often backs up Desktop, Documents, and Pictures. The Google Drive folder syncs too. A new folder directly in your home folder is a safe choice. Do not choose Cloud. The setup must run on your own computer.

Claude Code never asks for your Apple or Google password in the chat. With Path A, you type your Apple password only in your own Terminal window. With Path B and Path G, nobody asks for it. If Google asks you to sign in, you do it on Google's own page. Your computer may ask for its own password when it installs Docker Desktop or Tailscale. That is your computer's password, and you type it in the system's window.

If you prefer a terminal, install the command-line version with [Anthropic's setup guide](https://code.claude.com/docs/en/setup), run `claude` in the new folder, press Shift+Tab until it shows **auto mode on**, and paste the prompt there.

## Get your photos onto the computer

For iCloud Photos, pick Path A or Path B, as [docs/two-paths.md](docs/two-paths.md) explains. For Google Photos, use [Path G](#path-g-google-photos). In the steps below, `DIR` is the folder for the copy. Pick a folder outside iCloud Drive, OneDrive, and Google Drive, with room for the whole library.

### Path A: sign in to iCloud.com

`scripts/icloud.py` downloads your library from iCloud.com, the way the iCloud website loads it, with no Apple app on the computer. It runs on a Mac, Windows, or Linux, and it only reads from iCloud. It needs Python 3.10 or newer and the packages in `scripts/requirements.txt`. Its sign-in code comes from [pyicloud](https://github.com/timlaing/pyicloud). It is not made or endorsed by Apple.

Know what the sign-in leaves behind before you start. The script keeps a session key in `.homecloud/session` in your home folder. That key opens the same things as a browser signed in to iCloud.com, such as Photos, iCloud Drive, and iCloud Mail, but not your Keychain passwords, Messages, or Health. Any program that runs as you on the computer could read it. [docs/two-paths.md](docs/two-paths.md#what-the-session-key-opens) has the full list. The script never saves your password. You need the key only for the one-time move, so the last step is to log out.

1. Install the packages into a virtual environment. If `python3 --version` shows 3.9, install a newer Python from python.org first. On Windows, use the `venv` commands from [Do it by hand](#do-it-by-hand).

   ```
   python3 -m venv ~/homecloud-venv
   source ~/homecloud-venv/bin/activate
   python3 -m pip install -r scripts/requirements.txt
   ```

2. Sign in, in your own Terminal window. You type your Apple Account email, your password, and the six-digit code there. Nothing shows while you type the password. Never type it into a chat.

   ```
   python3 scripts/icloud.py login
   ```

3. Count, download, and check. `scan` only reads, and shows the count and size per year. To try a few files first, add `--limit 20` to the download. If the download stops, run the same command again. It skips finished files. On Windows, leave out `caffeinate -i` and set sleep to **Never** until the download ends.

   ```
   python3 scripts/icloud.py scan
   caffeinate -i python3 scripts/icloud.py download --to DIR
   python3 scripts/icloud.py verify --to DIR
   ```

   Go on only if verify reports N of N. Then import `DIR` into Immich with the `import_library.py` commands in step 4 of [Do it by hand](#do-it-by-hand). They work on Windows for this path too.

4. Log out. When every file is there, `verify` asks whether to sign out. When Claude Code runs it, it signs out by itself. Add `--keep-session` to `verify` only if you will download again soon, for example in Hybrid mode. You can sign out at any time:

   ```
   python3 scripts/icloud.py logout
   ```

   `logout` ends this computer's session at Apple and deletes `.homecloud/session`. It does not touch your photos.

With Advanced Data Protection on, this path works only while web access is on. On iOS 26.4 or later, open **Settings**, tap your name, tap **iCloud**, tap **iCloud.com**, and turn on **Allow Data Access**. On earlier iOS, open **Settings**, tap your name, tap **iCloud**, and turn on **Access iCloud Data on the Web**. You approve the sign-in on a trusted device, and during a long download you may need to approve it again.

The download copies your originals, the video part of each Live Photo, and your edited versions, into `DIR/YYYY/MM`. Shared Albums and Recently Deleted are not included.

Only Hybrid mode needs this path again: a fresh sign-in each month. The Photos app on a Mac does the monthly step with no sign-in. If Apple changes how iCloud.com works, this path can stop until the script is fixed.

[docs/safety.md](docs/safety.md#path-a-the-icloudcom-sign-in) explains how the script handles your password, and [docs/troubleshooting.md](docs/troubleshooting.md#path-a-the-icloudcom-download) covers its errors.

### Path B: no sign-in

Apple's own apps download the originals with the sign-in you already have, and the kit copies and checks them. Pick one of four options:

- **B1, the Photos app on a Mac.** Use it if Photos on your Mac syncs iCloud Photos. It is the only Path B option that checks every file against its original, byte for byte.
- **B2, the Immich app on your iPhone, with any computer.** It needs no Apple app on the computer. For a large library it is the slowest way, because the iPhone downloads each original from iCloud before it uploads it.
- **B3, iCloud for Windows on a PC.** Use it if drive C: has room for your whole library.
- **B4, Apple's data copy.** Ask for it at privacy.apple.com. It can take up to 7 days.

#### On a Mac: the Photos app

The Photos app is already signed in to your Apple Account, so nothing asks for your password.

1. Open **Photos** and choose **Photos** > **Settings**. On older macOS versions, the menu item is **Preferences**.
2. Click **iCloud** and select **Download Originals to this Mac**.
3. Keep Photos open, and keep the Mac plugged in, awake, and online until the download ends.

If the Mac lacks space, move the Photos library to an external drive before step 2. Apple's page [Move your Photos library to save space on your Mac](https://support.apple.com/108345) says the drive must use APFS or Mac OS Extended (Journaled) and must not be your Time Machine drive. Quit Photos, drag **Photos Library** from your **Pictures** folder to the drive, and double-click it there to open it. Then, in **Photos** > **Settings** > **General**, make it the System Photo Library, so iCloud Photos keeps working with it.

If Photos on this Mac does not sync iCloud Photos yet, do not turn it on in a library that already holds photos. Photos would add those photos to iCloud. Use the Immich app way instead.

The kit then copies every photo and video out of the Photos library into `DIR`. It uses [osxphotos](https://github.com/RhetTbull/osxphotos), and the top of `scripts/mac/export_photos.py` shows how to install it. `check` only reads. It prints how many photos and videos the library holds, and how many originals are still only in iCloud. Wait until that second number is 0.

```
python3 scripts/mac/export_photos.py check --to DIR
caffeinate -i python3 scripts/mac/export_photos.py export --to DIR
```

`caffeinate -i` keeps the Mac awake. `export` copies each file, then compares its size and SHA-256 checksum with the original in the Photos library. It prints "Files verified: N of N" and ends with "Result: all N files are copied and verified." If it stops, run it again. It copies only what is not done yet. Exit code 2 means some originals are still only in iCloud, so wait for Photos and run it again.

The copy holds your originals, your edited versions with `_edited` in the name, the video part of each Live Photo, and next to each photo a `.xmp` file with its favorite mark and album names. Hidden photos are included. Photos in shared albums and in Recently Deleted are left out.

#### On Windows: iCloud for Windows

iCloud for Windows is Apple's own app. If it is new on your PC, it asks you to sign in once, as it would without Homecloud. Homecloud never sees that sign-in. Apple's guide [Set up Photos in iCloud for Windows](https://support.apple.com/guide/icloud-windows/set-up-photos-icw9f0ba665f/icloud) has the current screens.

1. Install **iCloud for Windows** from the Microsoft Store, open it, and sign in if it asks.
2. Turn on **Photos**. In its options, select **Download High-Efficiency format photos and videos** and **Download HDR videos**. Without them, iCloud for Windows turns HEIF photos into JPEG before it downloads them, and you do not get your originals. Apple lists these options for iCloud for Windows 14.2 or later.
3. Open File Explorer. Right-click **iCloud Photos** in the navigation pane, and choose **Always keep on this device**. If it is not in the navigation pane, open **Pictures** and right-click the **iCloud Photos** folder there. Apple describes this in [Keep photos and videos downloaded in iCloud for Windows](https://support.apple.com/guide/icloud-windows/icw524278130/icloud).
4. Set sleep to **Never** while the PC is plugged in. Keep it on until every photo shows a solid green check. A cloud icon means that the file is still only in iCloud. The iCloud icon in the notification area shows the download status.

The photos land in `C:\Users\<your name>\Pictures\iCloud Photos\Photos`. Users in Apple's support forums report that iCloud for Windows cannot move this folder to another drive, so drive C: needs room for your whole library.

Do not delete, move, or rename anything in the iCloud Photos folder. Apple's guide [Delete and recover photos and videos in iCloud for Windows](https://support.apple.com/guide/icloud-windows/delete-and-recover-photos-and-videos-icw5c1e2d1a7/icloud) says a delete there deletes the photo from iCloud and from all your devices.

Large downloads can stall. [docs/troubleshooting.md](docs/troubleshooting.md#downloading-with-icloud-for-windows) lists the problems that users report, such as a download stuck at "Pending sync" or Live Photos without their short video, and what helped them.

Then Claude Code copies the iCloud Photos folder into `DIR` with robocopy, the copy tool that comes with Windows. It never uses the robocopy options that delete or move files: `/MIR`, `/MOV`, `/MOVE`, and `/PURGE`. It checks that `DIR` has the same files with the same sizes, and shows the result as N of N. Then `scripts/immich/make_manifest.py` lists the files in `DIR`, and the import copies them into Immich, as for the other ways.

#### With any computer: the Immich app on your iPhone

Your iPhone already shows your whole library. The Immich app uploads it to Immich on your computer. When an original is only in iCloud, the app pulls it from iCloud into its own cache and empties the cache after the upload, as [Immich's backup docs](https://docs.immich.app/features/mobile-backup) describe.

1. Set up the computer with `scripts/setup.sh` on a Mac or `scripts/setup.ps1` on Windows. It installs Immich and Tailscale, creates your Immich account, and opens a page for your phone. Claude Code runs it for you.
2. On the iPhone, follow the first four steps in the iPhone part of that page: install **Tailscale** and **Immich** from the App Store, and sign in to Immich with the server address and email that the page shows.
3. Tap the cloud icon at the top right to open the **Backup** screen. On the **Backup Albums** card, tap **Select** and pick **Recents**, which holds every photo on the iPhone except hidden ones. Then turn on **Enable Backup**.
4. Keep the app open on its backup screen, with the iPhone on Wi-Fi and charging. The iPhone screen stays on while that screen is open.
5. The backup screen shows **Total**, **Backup**, and **Remainder**. The upload is done when **Remainder** is 0.

The iPhone needs free space for the app's cache. Users report that the cache can fill the iPhone while the uploads stall ([Immich issue #22850](https://github.com/immich-app/immich/issues/22850)).

#### Apple's data copy at privacy.apple.com

Sign in at [privacy.apple.com](https://privacy.apple.com) yourself, on Apple's own site, and ask for a copy of your data with iCloud Photos selected. Apple says the copy can take up to 7 days. It arrives as zip archives that you download and unpack into `DIR`. Then `scripts/immich/make_manifest.py` lists the photos and videos, and Claude Code compares the counts with your iPhone.

#### Read your counts on the iPhone

With iCloud for Windows, the Immich app, and Apple's data copy, no list gives the kit a count. You read the counts on your iPhone and type them into Claude Code. They are not secret.

- In **Photos** on iOS 26 or later, open the **Collections** tab and tap your profile picture at the top right. The count is under your name. On iOS 18, tap the profile picture at the top right of Photos. On iOS 17 and earlier, scroll to the very bottom of your library.
- Or open **Settings** > **General** > **About**, which lists **Photos** and **Videos**. Users report that this count includes hidden photos, and the Photos app leaves them out.

On a Mac, `export_photos.py check` prints the count from the Photos library: "Photos and videos to copy: N".

The numbers can differ a little, and Claude Code explains each difference. [docs/troubleshooting.md](docs/troubleshooting.md#the-counts-do-not-match) lists the usual reasons.

### Path G: Google Photos

`scripts/google/google_photos.py` prepares a browser script that runs in your own signed-in photos.google.com tab and downloads your library from there. It only reads from Google Photos. It needs Python 3.9 or newer, with no extra packages, and Chrome, Edge, Brave, Arc, Vivaldi, or Opera. It is not made or endorsed by Google. [docs/google-photos.md](docs/google-photos.md) explains how it works, what it includes, its risks, and the Takeout route.

Use a new folder for `DIR`, apart from any iCloud copy. The browser does not let a web page save into your home folder, Desktop, Documents, or Downloads themselves, or into system folders. A new folder directly in your home folder works.

1. Prepare. This creates `DIR`, copies the browser script to the clipboard, opens <https://photos.google.com> in the first of these browsers that it finds, and prints the steps. To pick the browser, add `--browser` with its name, such as `--browser edge`.

   ```
   python3 scripts/google/google_photos.py prepare --to DIR
   ```

   In Brave, first open `brave://flags/#file-system-access-api`, set it to **Enabled**, and click **Relaunch**. Brave keeps the folder window that the script needs switched off until then. When the move is done, set it back to **Default**.

2. In the Google Photos tab, sign in if Google asks, on Google's own page. If you use more than one Google account, check that the picture at the top right is the account with your photos. Open the console with Cmd+Option+J on a Mac or Ctrl+Shift+J on Windows. If nothing opens, press Cmd+Option+I on a Mac or F12 on Windows, and click **Console**. Click in the console, paste the script, and press Enter. The first time, the browser blocks the paste and asks you to type `allow pasting`, in the browser's language. Type exactly what it shows, press Enter, then paste again and press Enter. This switches the warning off in this browser profile until you reset it in step 4.
3. In the Homecloud panel on the page, click **Start** and choose `DIR` in the folder window. When the browser asks "Allow this site to edit files?", click **Allow**. If the browser then says that photos.google.com wants to download multiple files, click **Allow**. Files that the page cannot save directly arrive as zip files in your Downloads folder. `status` and `verify` unpack them into `DIR`, check the sizes, and delete each zip file once all its files are in `DIR`. Keep the tab open, ideally in front, and the computer awake and plugged in, until the panel says **Done**.
4. Follow the progress, then check every file. `status` exits with 0 when the download is done, 4 while it still runs or has not started, and 1 when it failed or was stopped. On Windows, leave out `caffeinate -i`.

   ```
   caffeinate -i python3 scripts/google/google_photos.py status --to DIR --wait 100
   python3 scripts/google/google_photos.py verify --to DIR
   ```

   Go on only if `verify` exits with code 0. It writes the list that the Immich import reads. Then import `DIR` into Immich with the `import_library.py` commands in step 4 of [Do it by hand](#do-it-by-hand). Last, switch the console's paste warning back on: in DevTools, click the gear icon at the top right, and at the bottom of **Preferences** click **Restore defaults and reload**. This also resets any other DevTools setting you changed. In Brave, also set `brave://flags/#file-system-access-api` back to **Default** and click **Relaunch**.

If the tab closes or the computer sleeps, run `prepare` again with the same `DIR`, paste the script again, and click **Start**. It skips every item that is already there. If the panel shows a problem with Google's requests, Google may have changed them. Then the script cannot work until it is updated, and [Google Takeout](docs/google-photos.md#google-takeout-the-official-route) still works.

Trash, Locked Folder, and albums that other people shared with you are not included. Items in Storage saver come down as the smaller copy that Google kept.

## Do it by hand

You can run the kit without Claude Code. You need a terminal: Terminal on a Mac, or PowerShell on Windows. Run every command from the kit folder. The commands are for a Mac. On Windows, type `py` instead of `python3`.

1. Get the kit:

   ```
   git clone https://github.com/Bodila51/iCloud-reverse-engineered homecloud
   cd homecloud
   ```

   For Path A or Offline vault mode, also install their Python packages into a virtual environment in your home folder. Path A uses `scripts/requirements.txt`, and Offline vault uses `scripts/vault/requirements.txt`. Leave out the line you don't need. Path G needs no extra packages.

   ```
   python3 -m venv ~/homecloud-venv
   source ~/homecloud-venv/bin/activate
   python3 -m pip install -r scripts/requirements.txt
   python3 -m pip install -r scripts/vault/requirements.txt
   ```

   On Windows, create it with `py -m venv "$HOME/homecloud-venv"` and run its Python directly instead of activating it: `"$HOME/homecloud-venv/Scripts/python.exe" -m pip install -r scripts/requirements.txt`, with `&` in front in PowerShell. Use that Python for every Path A and Offline vault command.

2. Set up the computer. One command installs Docker Desktop if it is missing, then Immich and Tailscale. It creates your Immich account and an API key for the import, and opens a page for your phone. It checks the computer first, shows its plan, and asks once before it changes anything.

   ```
   bash scripts/setup.sh
   ```

   On Windows:

   ```
   powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
   ```

   Your Mac asks for its password, or Windows asks for permission, to install apps. A window asks you to choose an email and a password for Immich. The API key goes into `.homecloud/immich-api-key` in your home folder, which only your account can read, and nothing shows it. A browser window opens for the Tailscale sign-in. The setup ends with `Result: OK` and prints the address and email for your phone. If it stops, its last line says what to do. A second run skips what is done. On Windows, exit code 3 means that Windows must restart to finish WSL 2: restart, then run the same command again. In Offline vault mode, add `--no-tailscale` on a Mac or `-NoTailscale` on Windows. [scripts/immich/README.md](scripts/immich/README.md) and [scripts/tailscale.md](scripts/tailscale.md) show each part on its own, with the steps by hand.

3. Get your photos onto the computer with [Path A, Path B, or Path G](#get-your-photos-onto-the-computer). For the Immich app, iCloud for Windows, and Apple's data copy, read your counts on the iPhone.
4. Add `DIR` to Immich. With the Immich iPhone app, the app's backup already did this, so go on to step 5. With Google Takeout, `python3 scripts/google/google_photos.py takeout --to DIR --import` does this step with immich-go, as [docs/google-photos.md](docs/google-photos.md#google-takeout-the-official-route) shows.

   If `DIR` has a manifest, from Path A, the Photos app on a Mac, or Path G's `verify`, import it. The setup already saved the API key.

   ```
   python3 scripts/immich/import_library.py check --dir DIR
   python3 scripts/immich/import_library.py all --dir DIR
   ```

   `check` only reads and shows the plan. `all` asks first, then copies every file into Immich. It marks your favorites, fills your albums, and archives the photos you hid in Apple Photos or archived in Google Photos. Last, it checks that Immich holds every file. Go on only if it ends with "Result: OK".

   For iCloud for Windows and Apple's data copy, first list the files with `python3 scripts/immich/make_manifest.py --dir DIR`. It changes none of them. Then run the same two commands. Without a list from one of the kit's scripts, `apply` sets no favorites or albums, because those copies don't carry them.

   If you installed Immich without the setup, save a key first with `python3 scripts/immich/bootstrap.py`, or by hand with `save-key`, as [scripts/immich/README.md](scripts/immich/README.md) shows.

5. Compare the photos and videos in Immich with the count from `scan`, `export_photos.py check`, or Path G's `verify`, or with the counts from your iPhone. In Immich, open the menu under your profile picture, then **Administration** > **Server Stats**.
6. On your phone, follow the steps on the page that the setup opened, in the part for your iPhone or Android phone, and tap **Enable Backup** last. To open the page again, use the address and email that the setup printed:

   ```
   python3 scripts/phone_card.py --url http://100.x.y.z:2283 --email you@example.com --open
   ```

   [scripts/tailscale.md](scripts/tailscale.md) explains each step. Skip this in Offline vault mode.
7. Set up your mode with [docs/modes.md](docs/modes.md).
8. Copy `DIR`, with its hidden `.homecloud` folder, to your second drive, and check the copy. `COPY` is the folder on the second drive. For Path A, run `python3 scripts/icloud.py verify --to COPY`. For the Photos app on a Mac, run `python3 scripts/mac/export_photos.py verify --to COPY`. For Path G, run `python3 scripts/google/google_photos.py verify --to COPY`. For iCloud for Windows and Apple's data copy, run `python3 scripts/immich/make_manifest.py --dir COPY` and compare its counts and total size with those for `DIR`. With the Immich iPhone app or Google Takeout, copy Immich's library folder instead.
9. Last, follow [docs/downgrade.md](docs/downgrade.md) for iCloud Photos, or [After the move](docs/google-photos.md#after-the-move) for Google Photos.

## Safety promises

- Your Apple password never goes into the chat. With Path B, nobody asks for it. With Path A, you type it only in your own Terminal window, it goes only to Apple, and the session key is deleted when you log out.
- Your Google password never goes into the chat either. With Path G, the script runs in your own signed-in tab, and never sees your password or reads your cookies. It uses the page's own request key inside the tab, as the page does, and never saves it. Nothing that opens your Google Account stays on the computer.
- Homecloud never deletes, moves, or changes anything in iCloud, in Google Photos, in the Photos library on your Mac, or in the iCloud Photos folder on your PC.
- Claude Code shows you its whole plan before it installs or changes anything, and starts only after your yes. It asks again before anything that would delete data or cannot be undone.
- Your Immich password goes only into a window on your computer, never into the chat. The import's API key goes into a file that only your account can read, and nothing shows it.
- Every copy is checked, and any mismatch stops the setup. Path A checks every file against iCloud's own list. Path G checks every file against the list that the script read from Google Photos. The Photos app way compares each file with its original in the Photos library. The other Path B options compare the counts with the counts on your iPhone, as N of N.
- Software comes only from each tool's maker. On a Mac, the setup downloads Docker Desktop and Tailscale from their makers, as their docs describe. On Windows, it installs Docker Desktop, Python, and Tailscale with winget, Microsoft's package manager. The phone apps come from the App Store or Google Play.
- Immich is never opened to the internet. Away from home, only your own devices reach it, through Tailscale.
- You turn off iCloud Photos, or backup in the Google Photos app, yourself, as the last step, after the checks pass and a second copy exists.

[docs/safety.md](docs/safety.md) explains how each promise works.

## Honest limits

- Homecloud is not an Apple or Google product. Path A uses the web interface of iCloud.com, which Apple can change at any time, and then Path A stops until the script is fixed. Path G repeats internal requests of photos.google.com that Google does not publish and can change at any time, and then Path G stops until the script is fixed. Path B depends on Apple's own apps. Users report that iCloud for Windows can stall on a large library, as [docs/troubleshooting.md](docs/troubleshooting.md#downloading-with-icloud-for-windows) shows. Apple's [Data and Privacy page](https://privacy.apple.com) can always send you a copy of your photos, which can take up to seven days, and Google Takeout can always send you a copy of your Google Photos.
- Google's terms of service forbid reverse engineering and some kinds of automated access, and Google can suspend or close a whole Google Account. If you want no risk to your Google Account, use Google Takeout.
- Path A checks every file against iCloud's own list, and the Photos app way checks every file against its original. Path G checks every file against the list that the script read from Google. If that list came back short, the check could not see it, so compare a few years and albums before you delete anything in Google Photos. With iCloud for Windows, the Immich app, and Apple's data copy, the check compares the number of photos and videos with your iPhone. With Google Takeout, there is no N of N check.
- Immich's own FAQ says Immich will probably not save you money once you count hardware, backup drives, and your time. Homecloud uses a computer you already have, which helps. The second drive and the electricity still cost money. A small mini PC idles at about 6 to 12 watts, and an old laptop at about 10 to 15.
- Your phone decides when the Immich app uploads in the background. On an iPhone, the app has no setting to upload only while charging. When you open the app, it uploads on screen instead, and that upload stops when you leave the app. On Android, battery settings can stop background uploads unless you set Immich's battery use to **Unrestricted**. For a sure upload, keep the app open on its Backup page until it ends.
- **Free Up Space** in the Immich app removes a photo from your phone only when Immich holds a copy with exactly the same bytes. Files that Homecloud imported from your computer or from Google Photos can differ, and then it does not offer them. Read its preview before you confirm.
- The first upload from the Immich app checks every photo on your phone. If **Optimize iPhone Storage** is on, the iPhone app downloads each full-size photo from iCloud to check it. Run that first upload on Wi-Fi with the phone charging.
- Immich's docs recommend Linux and strongly discourage macOS and Windows, because Docker works less well there. The Immich team says it can help less with those setups. Homecloud runs Immich on a Mac or a PC anyway, inside Docker Desktop, which also uses more memory.
- Homecloud moves photos and videos only. Your iPhone's iCloud Backup and other iCloud data still need room in Apple's free 5 GB. At Google, Gmail and Google Drive still share the free 15 GB.

## FAQ

### Is this made by Apple or Google?

No. Homecloud is not affiliated with Apple or Google, and neither of them endorses it.

### Do I need my Apple password?

Only for Path A. You type it in your own Terminal window, never in the chat, and the script sends it only to Apple. Path B never asks for it: Apple's own apps download your photos with the sign-in you already have. If iCloud for Windows is new on your PC, Apple's app asks you to sign in once, as it would without Homecloud. If you ever type your Apple password into the chat, change it. On your iPhone, open **Settings**, tap your name, then **Sign-In & Security**, then **Change Password**.

### Do I need my Google password?

No. Path G runs in the browser tab where you are already signed in to Google Photos. If Google asks you to sign in, you do it on Google's own page, as always. The script never sees your password or reads your cookies, and nothing that could open your account is saved. If you ever type your Google password into the chat, change it.

### Is Auto mode safe to use?

In Auto mode, Claude Code runs commands without asking you each time. Before each action runs, a separate model checks it and blocks actions that go beyond what you asked for. Anthropic's docs say that Auto mode reduces prompts but does not guarantee safety. The kit's own rules apply in every mode: your Apple and Google passwords never go into the chat, nothing in iCloud or Google Photos is deleted or changed, and Claude Code stops and asks before anything that would delete data. To approve each command yourself, choose **Manual** in the same mode menu.

### Does Claude see my photos?

Claude Code runs on your computer and reads what the scripts print: counts, sizes, file names, and error messages. That text is part of your chat with Claude. The prompt tells Claude Code not to open your photos.

### What if the download stops halfway?

With Path A, run the same `download` command again. It skips the files it already has. With the Photos app on a Mac, keep Photos open until `check` reports 0 originals still only in iCloud, then run `export` again. It continues with the files that are not done yet. With Path G, run `prepare` again with the same folder and paste the script again. It skips the items it already has. On Windows, see the known problems under [iCloud for Windows](#on-windows-icloud-for-windows). The Immich app continues where it stopped when you open it on its backup screen.

### I use Advanced Data Protection. Does it work?

Yes. With Path B, nothing changes, because Apple's own apps work with Advanced Data Protection. On a PC, Apple's page [How to turn on Advanced Data Protection for iCloud](https://support.apple.com/108756) lists iCloud for Windows 14.1 or later. With Path A, turn on web access first, as [Path A](#path-a-sign-in-to-icloudcom) describes.

### What happens to my photos in iCloud?

Nothing, until you decide. Turning off iCloud Photos on your devices does not delete the copy in iCloud. [docs/downgrade.md](docs/downgrade.md) explains what happens next and what you can choose.

### What happens to my photos in Google Photos?

Nothing, until you decide. Turning off backup in the Google Photos app does not delete anything. Before you delete anything in Google Photos, turn off backup on every phone: Google says a delete in Google Photos also deletes the same items on every device where backup is on. [After the move](docs/google-photos.md#after-the-move) explains the steps and your choices.

### I share iCloud+ with my family, or I pay for Apple One.

Everyone in your family group uses the shared storage, so a smaller plan leaves less room for all of them. Every Apple One plan includes iCloud+ storage, so you reach the free 5 GB only by cancelling Apple One. You do that under **Settings** > your name > **Subscriptions** > **Apple One**.

### Can my family use it too?

Yes. Immich gives each person their own account and library. Each person can back up their own phone with the Immich app, signed in to their own Immich account, so nobody shares a password. For access away from home, share the computer with each person in Tailscale instead of inviting them to your Tailscale account. Each person makes a free Tailscale account, and you share the Immich computer with them. A shared computer does not use one of your 6 user places, and they can reach only that computer. [docs/modes.md](docs/modes.md#share-it-with-your-family) has the steps.

Whoever runs the computer can open everyone's files on its disk, and everyone's photos depend on the same second drive.

### Can I go back to iCloud later?

Yes. Buy iCloud storage again and turn iCloud Photos back on. Your devices upload the photos they have. To put older photos back, upload them at iCloud.com, or import them into the Photos app on a Mac that has iCloud Photos on.

### Does it work with Android or Linux?

Android, yes. The phone page has a part for Android, and the Immich and Tailscale apps come from Google Play. If your photos are in Google Photos, Path G moves them. Hybrid mode is for iCloud Photos, and Offline vault mode is for an iPhone.

Linux only in part. The Path A download runs on Linux, and so does Immich, but the kit's setup and guides cover macOS and Windows.

### What does it cost?

- Homecloud is free, under the MIT license.
- Immich is free. You can buy an optional product key to support its developers.
- Tailscale's Personal plan is free for non-commercial use, for up to 6 users and unlimited personal devices.
- Docker Desktop is free for personal use.
- Claude Code needs a paid Claude plan while you set things up, from $20 a month for Pro. You do not need it if you follow [Do it by hand](#do-it-by-hand).
- The second drive costs money once.

## Credits

Homecloud uses these projects:

- [Immich](https://github.com/immich-app/immich), the photo app.
- [Tailscale](https://tailscale.com), for private access away from home.
- [pyicloud](https://github.com/timlaing/pyicloud), for the iCloud.com sign-in and download in Path A.
- [osxphotos](https://github.com/RhetTbull/osxphotos), for copying photos out of the Photos library on a Mac.
- [pymobiledevice3](https://github.com/doronz88/pymobiledevice3), for copying photos from an iPhone over USB. It is under the GPL-3.0 license, and the kit installs it from PyPI as a separate package.
- [Google Photos Toolkit](https://github.com/xob0t/Google-Photos-Toolkit) by xob0t, for the Google Photos web requests in Path G. The browser script copies its request formats and response parsing, under its MIT license.
- [immich-go](https://github.com/simulot/immich-go), for importing Google Takeout archives into Immich in the Takeout route.
- [Docker](https://www.docker.com), which runs Immich.
- [QR Code generator library](https://www.nayuki.io/page/qr-code-generator-library) by Project Nayuki, for the QR codes on the phone page. It is under the MIT license, in `scripts/vendor/`.

## License

MIT. See [LICENSE](LICENSE).

Not affiliated with Apple or Google. iCloud and iPhone are trademarks of Apple Inc. Google Photos, Google One, and Android are trademarks of Google LLC.
