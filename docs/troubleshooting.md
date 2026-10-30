# Fix common problems

Find the step where you got stuck, then the symptom. The commands use `python3`. On Windows, type `py` instead. Run them from the kit folder. The Path A script and the Offline vault script also need the kit's virtual environment, as [README.md](../README.md#do-it-by-hand) shows.

## Downloading with Photos on a Mac

### `check` still shows originals that are only in iCloud

```
python3 scripts/mac/export_photos.py check --to DIR
```

Photos downloads the originals only while it is open. Keep Photos open, and keep the Mac plugged in, awake, and online. Then check that **Download Originals to this Mac** is still selected in **Photos** > **Settings** > **iCloud**. The status line at the bottom of the library in Photos shows what Photos is doing.

Apple gives no time for this download. Users report that it can sit at "Downloading" for many hours without visible progress, for example in the MacRumors thread [Photos library download originals to this Mac stuck for over a day](https://forums.macrumors.com/threads/photos-library-download-originals-to-this-mac-stuck-for-over-a-day.2243558). Run `check` again later. While the number goes down, the download works.

### osxphotos is not installed, or check names another version

The script prints the install command. It installs uv, then osxphotos 0.77.2, the version that the script expects:

```
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install --python 3.13 "osxphotos==0.77.2"
```

Open a new Terminal window after the install, then run the command again. If another version is installed, the script prints the same command with `--force`.

### macOS did not let osxphotos read the Photos library

Open **System Settings** > **Privacy & Security** > **Photos**, and give full access to the app that runs the command, such as Terminal. Quit and reopen that app, then run the command again. If the error stays, also turn on **Full Disk Access** for the same app, in **Privacy & Security**. If it fails when Claude Code runs it inside the Claude app, ask Claude Code for the command and run it in Terminal yourself.

### The Mac runs out of space

**Download Originals to this Mac** needs room for your whole library. Move the Photos library to an external drive, as [README.md](../README.md#on-a-mac-the-photos-app) describes, then select **Download Originals to this Mac** again. Apple's page [Move your Photos library to save space on your Mac](https://support.apple.com/108345) has the steps.

### `export` says "Result: not finished"

Run the same command again. It continues with what is not done yet. If it lists a file under "Problem", the script tells you to delete that file in `DIR` and run the command again, so osxphotos copies it again from the Photos library. Delete only that file, and only in `DIR`. Never delete anything in the Photos library.

### `export` or `verify` exits with code 2

Code 2 means some originals are still only in iCloud, so the export could not copy them yet. Wait until `check` reports 0, then run the export again.

## Downloading with iCloud for Windows

All fixes in this section come from users in Apple's support forums. Apple has no page on them.

### iCloud Photos is not in File Explorer

Click **Pictures** in the navigation pane of File Explorer, then double-click **iCloud Photos**. Apple's guide [Download and view iCloud Photos on your Windows computer](https://support.apple.com/guide/icloud-windows/icw2ed20ffd1) describes both places. If the folder is missing in both, open iCloud for Windows and check that you are signed in and that **Photos** is on.

### Photos stay as clouds, or the download stalls

A cloud icon means the file is still only in iCloud. These problems are reported:

- The download stops at "Pending sync", for example after about 80 GB ([Always Keep on This Device - Not Reliable](https://discussions.apple.com/thread/252128062)).
- The sync stays at "Initializing" after a few hundred photos ([stuck on "Initializing..."](https://discussions.apple.com/thread/256054345)).
- **Always keep on this device** does nothing for days.

What helped some users:

1. Right-click **iCloud Photos**, clear **Always keep on this device**, then choose it again.
2. Quit iCloud for Windows and open it again.
3. If your Windows version offers it, open **Settings** > **Apps** > **Installed apps**, open the advanced options for iCloud, and set background app permissions to **Always**.
4. Sign out of iCloud for Windows and in again. That is Apple's app, so it asks for your Apple Account password in its own window. Never type it into the chat.

Keep the PC plugged in and awake, with sleep set to **Never**, until every photo shows a solid green check. The iCloud icon in the notification area shows the status. If you cannot see it, click **Show hidden icons**. Apple describes this in [If your iCloud Photos aren't loading on your PC](https://support.apple.com/118445).

If nothing helps, use the [Immich app on your iPhone](../README.md#with-any-computer-the-immich-app-on-your-iphone) instead.

### Photos come down as JPEG files

Without the option **Download High-Efficiency format photos and videos**, iCloud for Windows turns HEIF photos into JPEG, so you do not get your originals. Turn the option on in the Photos options of iCloud for Windows. It needs iCloud for Windows 14.2 or later. Users report that the option is sometimes greyed out, for example in the thread [In iCloud Photos on Windows, how do I turn off High-Efficiency Format when the option is disabled](https://discussions.apple.com/thread/255695746).

### Live Photos come down without their video

Users report that the short video of some Live Photos is missing on the PC ([iCloud for Windows not downloading live photo MOV files](https://discussions.apple.com/thread/253244992)). The still photo is there. Open a few Live Photos in Immich. If they do not play, back up those photos with the Immich app on your iPhone.

### A photo comes down twice

A photo that was edited on an iPhone or Mac can come down as two files, the edited one and the original. Apple's support pages mention these extra copies. Keep both.

### Drive C: is full

Users report that iCloud for Windows keeps the iCloud Photos folder under `C:\Users\<your name>\Pictures` and cannot move it to another drive, for example in the thread [How do I change iCloud photos location on PC?](https://discussions.apple.com/thread/256052484). If drive C: cannot hold your whole library, use the Immich app on your iPhone instead.

## Backing up with the Immich app on your iPhone

### The first backup takes hours

If **Optimize iPhone Storage** is on, the app downloads each full-size original from iCloud before it checks and uploads it. Keep the phone on Wi-Fi and charging, with the app open on its backup screen. By default, the app uploads only on Wi-Fi. You can change that in its backup settings.

### The iPhone fills up and the backup stalls

The app copies originals from iCloud into its cache before it uploads them. Users report that the cache can fill the iPhone while uploads stall ([Immich issue #22850](https://github.com/immich-app/immich/issues/22850)). Free some space on the iPhone, open the app on its backup screen again, and keep it there.

### Remainder does not reach 0

Keep the app open on its backup screen. Uploads in the background run only when iOS allows them. If the number stops going down, check that the iPhone is on Wi-Fi and has free space, and that the computer and Immich are running.

## Checking

### The counts do not match

The counts measure different things:

- A Live Photo counts once on the iPhone and in Immich, but it is two files on disk: a photo and a short video.
- An edited photo can be two files: the original and the edited version.
- Hidden photos are in the Photos library and in Settings > General > About, but the Photos app on the iPhone leaves them out of its library count. Users report this, for example in the MacRumors thread [Settings > General > About showing wrong information for photos and videos](https://forums.macrumors.com/threads/settings-general-about-showing-wrong-information-for-photos-and-videos.2163409).
- The Mac export leaves out photos in shared albums and in Recently Deleted.
- Photos that never reached iCloud are not in the Photos library on your Mac or in iCloud for Windows. This happens when iCloud storage was full. The Immich app uploads those from your iPhone.

Claude Code explains each difference before it goes on. If photos are missing for any other reason, it stops.

### `export_photos.py verify` reports fewer than N of N

Run the export again. It copies the files that are missing or failed. Then run verify again:

```
python3 scripts/mac/export_photos.py verify --to DIR
```

Do not go on to Immich, and never start the downgrade, until verify reports N of N.

### The download folder is inside iCloud Drive or OneDrive

Stop. A synced folder uploads your library to the cloud again and can turn local files into online-only placeholders. Ask Claude Code to move the download folder, including the hidden `.homecloud` folder, to a folder that does not sync. Then run the export or the copy again with the new place, and check again.

### The disk is full

Free some space, or move the download folder to a bigger drive. Move the whole folder, including the hidden `.homecloud` folder. The manifest stores paths relative to the folder, so the files still match. Then run the export again with `--to` set to the new place, and check again.

## Docker and Immich

### Docker is not found, or it is not running

Open Docker Desktop and wait until it shows that it is running. On a Mac it is in **Applications**. On Windows it is in the **Start** menu. Then check:

```
docker compose version
```

It should print a version number. Immich needs `docker compose`, with a space. The older `docker-compose` command does not work with Immich.

### Docker Desktop on Windows asks for WSL 2 or virtualization

Docker Desktop on Windows uses WSL 2. To install it, open PowerShell as administrator, run this command from Microsoft, and restart the computer:

```
wsl --install
```

Docker needs WSL 2.1.5 or later. `wsl --version` shows your version, and `wsl --update` updates it. If Docker says virtualization is off, turn it on in your computer's BIOS or UEFI settings. The steps depend on the maker of your computer.

### Immich does not open

Immich takes a few minutes to start the first time, because it downloads its parts. Then open `http://localhost:2283` on the computer. If the page still does not load, run these in the Immich folder, which is `immich-app` in your home folder unless you chose another:

```
docker compose ps
docker logs immich_server
```

The first command shows whether each part is running. The second shows the server's log. Show both to Claude Code.

### Immich restarts, or face and search jobs crash

Docker probably has too little memory. Immich needs at least 6 GB.

- On a Mac, Docker Desktop uses half of the Mac's memory by default. Raise it in Docker Desktop under **Settings** > **Resources** > **Memory limit**.
- On Windows, WSL 2 sets Docker's memory limit. Microsoft's WSL documentation explains how to change it in a file named `.wslconfig`.

If the computer has too little memory to spare, turn off machine learning. In Immich, open **Administration** > **Settings** > **Machine Learning Settings**. Search and face grouping then work less well.

### Immich shows database errors at start on Windows

Immich's database cannot live on an NTFS or exFAT drive. Immich's docs give this fix. In the `.env` file, change the database line to:

```
DB_DATA_LOCATION=pgdata
```

Then add `pgdata:` under `volumes:` at the bottom of `docker-compose.yml`, next to `model-cache:`, and start Immich again.

### The machine learning part does not start on an old PC

Immich's machine learning part needs an Intel or AMD processor with the x86-64-v2 feature set. Most processors made since about 2012 have it. On an older PC, turn off machine learning as described above.

## Importing into Immich

### The import has no API key, or Immich does not accept the key

The setup saves the key for you. To make a new one, run this in your own Terminal or PowerShell window:

```
python3 scripts/immich/bootstrap.py
```

It keeps a saved key that still works. Otherwise it asks for your Immich email and password, in a window on a Mac or in the terminal on Windows, and saves a new key named "Homecloud" in `.homecloud/immich-api-key` in your home folder. Nothing shows the key. It ends with "Result: OK".

You can also make a key yourself. In Immich, open **Account Settings** > **API Keys** > **New API Key**. Tick **Select all**, click **Create**, and copy the key. Immich shows it only once. Then run this in your own Terminal or PowerShell window, and paste the key when it asks. It stays hidden.

```
python3 scripts/immich/import_library.py save-key
```

### The import says there is no manifest

`import_library.py` needs the list of files, `DIR/.homecloud/manifest.jsonl`. The kit's scripts write it: `scripts/icloud.py download` for Path A, `scripts/mac/export_photos.py export` for the Photos app on a Mac, and `scripts/google/google_photos.py verify` for Google Photos. The message names only the iCloud and iPhone scripts, so run the one for your way. For a copy from iCloud for Windows or from Apple's data copy, make the list with `python3 scripts/immich/make_manifest.py --dir DIR`. Then run the import again.

### The import says there is not enough free space

The import copies every file into Immich's library folder. The computer needs room for your library twice, plus about a fifth for thumbnails and video previews. Free up space, or use a bigger drive for Immich's library folder. That folder is the `UPLOAD_LOCATION` line in the `.env` file in the Immich folder. If Immich holds no photos yet, change that line, then run `docker compose up -d` in the Immich folder. [scripts/immich/README.md](../scripts/immich/README.md) also describes an External Library, which reads the files where they are.

### Immich shows a different number than the export

The two counts measure different things:

- A Live Photo is two files on disk, a photo and a short video. Immich shows it as one item and leaves the video part out of its counts.
- When two files have exactly the same content, Immich keeps one of them.
- After an import, Immich keeps working for hours on thumbnails, faces, and video. To see what is left, open **Administration** > **Job Queues**.

To compare the right way, run the kit's check. It counts files, matches each one by its content, and only reads:

```
python3 scripts/immich/import_library.py verify --dir DIR
```

### The check says favorites or albums are not complete

Every file is in Immich, but the favorite marks, albums, or archived photos are not done yet. Run apply, then check again:

```
python3 scripts/immich/import_library.py apply --dir DIR
python3 scripts/immich/import_library.py verify --dir DIR
```

### Live Photos show up as separate short videos

Immich links each Live Photo's video to its photo while it reads the files' details. Until then, the videos show up on their own. Wait until the Job Queues are done, then run verify again. It reports how many are linked.

### Immich on Windows does not see the External Library

The path in `docker-compose.yml` must name your folder the way Docker on Windows expects, with forward slashes and quotes, such as `- "C:/Users/you/Homecloud:/mnt/media/homecloud:ro"`. Immich's [libraries docs](https://docs.immich.app/features/libraries) show this form. After you change the file, run `docker compose up -d` in the Immich folder. In Immich, the import path is the folder as Immich sees it inside Docker, here `/mnt/media/homecloud`.

## Tailscale and the phone app

### The Immich app cannot reach the computer away from home

1. On the phone, open the Tailscale app and check that it is connected. An iPhone or an Android phone runs one VPN at a time, so another VPN app can keep Tailscale off.
2. Check that the computer is on, awake, and signed in to Tailscale with the same account, or shared with yours.
3. In the Immich app, check the server address. It must be the computer's Tailscale address, such as `http://100.101.102.103:2283`. A home address such as `192.168.1.20` works only on your home Wi-Fi.
4. Open the same address in the phone's browser, such as Safari on an iPhone or Chrome on Android. If Immich loads there, the problem is the address in the app.
5. On Windows, if Immich does not load in the phone's browser either, open **Windows Security** > **Firewall & network protection** > **Allow an app through firewall**, click **Change settings**, and tick **Private** for **Docker Desktop Backend**.

### Uploads do not run in the background

Your phone decides when apps run in the background. The Immich app uploads new photos when you open it, and in the background when the phone lets it ([Immich](https://github.com/immich-app/immich/blob/v3.3.5/docs/docs/features/mobile-backup.md)).

On an iPhone, to make background uploads happen more often:

- Open **Settings** > **General** > **Background App Refresh**. Turn it on at the top, and for Immich.
- Turn off **Low Power Mode** when you do not need it.
- Turn off **Low Data Mode** for your home Wi-Fi. It is in **Settings** > **Wi-Fi**, under the info button next to the network.
- Do not swipe the Immich app away in the app switcher. A closed app does not upload in the background.

The iPhone app has no setting to upload only while charging.

On an Android phone:

- Open Immich's Backup screen. If a link about notifications or battery shows at the bottom, tap it and allow what it asks. When both are allowed, the links go away.
- Open the Android **Settings** > **Apps** > **Immich** > **Battery**, and choose **Unrestricted**. The menu names differ by phone maker. The battery link in Immich has a **Show me how** button that opens [dontkillmyapp.com](https://dontkillmyapp.com), a guide for each maker.
- Immich's backup settings can hold background uploads until the phone is charging. If that is on, uploads wait for the charger.

For a sure upload, open the Immich app on its Backup page and keep it open until it shows nothing left to upload. On an iPhone, that upload on screen stops when you leave the app.

## Offline vault

### The vault script does not find the iPhone

1. Unlock the iPhone and connect it with a cable that carries data, not only power.
2. If the iPhone asks whether to trust this computer, tap **Trust** and enter your passcode.
3. Keep the iPhone unlocked while the script runs.
4. On Windows, install the Apple Devices app from the Microsoft Store, or iTunes. The script needs the Apple device driver that comes with either one.

Then run `python3 scripts/vault/vault.py check --to DIR`. It only reads, so it is safe to run as often as you like.

### The vault script says some photos are not on the phone in full size

This happens when iCloud Photos is on with **Optimize iPhone Storage**. The iPhone holds small copies, and the originals are in iCloud. The script does not count those photos as safe. Get them through the Photos app on a Mac, iCloud for Windows, or the Immich app. Or choose **Download and Keep Originals** on the iPhone and run the copy again.

## Turning off iCloud Photos

### The iPhone has no room to download your photos

See "If Optimize iPhone Storage is on" in [downgrade.md](downgrade.md).

### iCloud Backup fails after you downgrade

With iCloud Photos off, your iPhone's iCloud Backup tries to include the photo library, which does not fit in 5 GB. Turn off the photo library in the backup settings. [downgrade.md](downgrade.md) shows where.

## When Apple's apps cannot finish the download

You can still get your photos another way:

- Use the [Immich app on your iPhone](../README.md#with-any-computer-the-immich-app-on-your-iphone). It needs no Apple app on the computer.
- Ask Apple for a copy at [privacy.apple.com](https://privacy.apple.com). You sign in there on Apple's own site. Choose **Request a copy of your data**, then iCloud Photos. Apple can take up to seven days.
- Switch to [Path A](../README.md#path-a-sign-in-to-icloudcom), which downloads from iCloud.com after you sign in. [two-paths.md](two-paths.md) explains what that sign-in leaves on the computer.

## Path A: the iCloud.com download

These fixes are for `scripts/icloud.py`, which [README.md](../README.md#path-a-sign-in-to-icloudcom) describes.

### Nothing appears when you type your password

That is normal. The login hides your password as you type. Type it and press Return.

### The six-digit code does not arrive

Look at your other Apple devices signed in with the same Apple Account. The code appears there, sometimes after you tap **Allow**. If no device shows it, get a code on a trusted iPhone. Open **Settings**, tap your name, then **Sign-In & Security**, and tap **Get Verification Code**. Users report that this button shows only while the iPhone is offline, for example in Airplane Mode.

### Sign-in fails and you use Advanced Data Protection

Advanced Data Protection turns off web access to your iCloud data. To turn it on, open **Settings** on your iPhone, tap your name, tap **iCloud**, tap **iCloud.com**, and turn on **Allow Data Access**. Before iOS 26.4, this switch was named **Access iCloud Data on the Web**. Then run the login again:

```
python3 scripts/icloud.py login
```

With Advanced Data Protection, you approve the sign-in on a trusted device. For about an hour, that device then approves each new kind of data by itself and shows a notification. After the hour, a new kind of data needs a new approval. Photos that the download has already opened stay open until the session ends. If a download stops and asks for an approval, approve it on your device, then run the same download command again.

### A command says the session expired

Apple ends sign-in sessions after some time. Run the login command again in your own Terminal window, then repeat the command that failed.

### The download stopped

Run the same download command again. It continues with the files that are not done yet. Common causes:

- The computer went to sleep. On macOS, run the download under `caffeinate -i`. On Windows, set sleep to **Never** while plugged in until the download ends.
- The session expired. Run the login again first.
- The Claude Code window closed. Start Claude Code again in the kit folder and ask it to continue the download.

### The same files fail every time

Ask Claude Code to list the failed files with their dates. Look for those photos on your iPhone or in the Photos app on a Mac. If Apple's own apps cannot open them either, the problem is on Apple's side. Do not turn off iCloud Photos while any file is missing.

### verify reports fewer than N of N

Run the download again. It fetches the files that are missing or failed. Then run verify again:

```
python3 scripts/icloud.py verify --to DIR
```

### When you are done

When verify reports N of N, it signs you out. In your own Terminal window it asks first, and when Claude Code runs it, it signs out by itself. If you kept the session with `--keep-session`, or answered no, log out yourself, so the session key no longer sits on the computer:

```
python3 scripts/icloud.py logout
```

## Google Photos

These fixes are for Path G, `scripts/google/google_photos.py` and its browser script, and for Google Takeout. [google-photos.md](google-photos.md) describes both.

### `prepare` finds no browser

The browser script needs Chrome, Edge, Brave, Arc, Vivaldi, or Opera, because only these browsers let a web page save files into a folder you pick. Safari and Firefox cannot. Install Google Chrome from <https://www.google.com/chrome/>, sign in to Google Photos there, and run `prepare` again. If you use Google Photos in another of these browsers, add `--browser` with its name, such as `--browser edge`.

### `prepare` refuses the folder

Browsers do not let a web page save into your home folder, Desktop, Documents, or Downloads themselves, or into system folders, so `prepare` refuses them too. Pick a new folder directly in your home folder, outside the folders that sync to a cloud service, and run `prepare` again with it.

If `prepare` says that the folder holds another script's copy, the folder already has a list from an iCloud copy. Use a new, empty folder for Google Photos, so the two copies stay apart.

### The script is not on the clipboard

`prepare` prints where it saved the script: `DIR/.homecloud/homecloud_google.js`. Open that file in a text editor, select everything, and copy it.

### The console does not open

Press Cmd+Option+J on a Mac or Ctrl+Shift+J on Windows while the Google Photos tab is in front. If nothing opens, press Cmd+Option+I on a Mac or F12 on Windows, and click **Console** in the panel that opens.

### The console does not let you paste

The first time, the browser blocks the paste and shows a warning that asks you to type `allow pasting`. If your browser uses another language, the words are in that language. Type exactly what it shows, press Enter, then paste again and press Enter. The warning is there because scammers tell people to paste code into the console. Paste only the script that `prepare` made. Typing it switches the warning off in this browser profile. When the move is done, switch it back on: in DevTools, click the gear icon at the top right, and at the bottom of **Preferences** click **Restore defaults and reload**. This also resets any other DevTools setting you changed.

### No Homecloud panel appears

The panel appears at the bottom right of the page. Check that the console belongs to the photos.google.com tab, that you pasted the whole script, and that you pressed Enter. If the console shows a red error, tell Claude Code what it says.

### The panel says "This page is not ready" or that you are not signed in

Sign in to Google Photos in that tab, reload the page, paste the script again, and click **Start**. It continues where it stopped.

### The panel says you picked another folder

The panel accepts only the folder that `prepare` made, by its name. Click **Start** again and pick the folder with the name that the panel shows.

### The folder window says "Can't open this folder"

The browser does not let a web page use that folder, because it holds system files. Click **Choose a different folder** and pick `DIR`, the folder that `prepare` printed.

### The panel says "This browser cannot save files into a folder"

Safari and Firefox cannot do it. Use Chrome, Edge, Brave, Arc, Vivaldi, or Opera. In Brave, the folder window is switched off until you turn it on. Open `brave://flags/#file-system-access-api` in Brave, set it to **Enabled**, and click **Relaunch**. Then open Google Photos again and repeat the steps.

### The panel says "Not enough free space"

Nothing was downloaded. `prepare` measured the free space and left a safety margin of at least 5 GB, or 2% of the disk when that is more. Free up space, or pick a folder on a bigger drive, such as an external drive. Then run `prepare` again with that folder, and repeat the steps.

### The panel says that many downloads failed in a row

Check the internet connection. Then reload the page, paste the script again, and click **Start**. It continues where it stopped. If the panel says that the disk is full, free some space and click **Start**.

### `status` reports no update for 10 minutes or more

The tab may have closed, or the computer may have slept. Run `prepare` again with the same `DIR` and repeat the steps. The script skips every item that is already there with the right size. Keep the tab open, ideally in front, and the computer awake and plugged in.

### The panel says that the listing or the downloads failed

The panel says that Google Photos did not answer the way the script expects, or that downloads do not work from this page. Nothing was changed in Google Photos. Tell Claude Code what the panel says. If Google changed its internal requests, the script cannot work until it is updated. Google Takeout still works:

```
python3 scripts/google/google_photos.py takeout --to DIR
```

Do not paste any other code into the console, whoever sends it.

### `verify` says "Result: NOT COMPLETE"

Some items are missing, failed, or have the wrong size. `verify` names a few of them and writes the full list to `DIR/.homecloud/google-problems.txt`. If the Google Photos tab with the panel is still open, click **Start** there. Otherwise run `prepare` again with the same `DIR` and repeat the steps. The script downloads only what is missing. Then run `verify` again.

If `verify` reports files that end in `.crswap`, those are pieces of downloads that did not finish. They hold nothing you need, and the import skips them.

### `verify` says some albums have fewer items than Google counts

Photos that other people added to a shared album stay theirs, so they are not in your library. To keep some of them, select them in the shared album in Google Photos and download them. Then upload them in Immich's web page with its **Upload** button.

### The import's plan talks about items hidden in iCloud

The import uses the same words for every library. For Google Photos, it means the items that you archived in Google Photos. They go to the Archive in Immich.

### Takeout: the import finds no archives, or says parts are missing

`takeout --import` reads the `.zip` parts, named `takeout-...zip`, directly in `DIR`. Move every part from your Downloads folder into `DIR`, and do not unpack them. If a part is missing or cut off, download it again from Google's email. Google lets you download each part 5 times, within 7 days. immich-go reads `.zip` parts only, so if you chose `.tgz`, create the export again with `.zip`.

### Takeout: Google asks for your password when you download a part

That is Google's own check. Type the password there, in the browser, never in a chat.

### Takeout: immich-go stopped

Run the same command again. immich-go skips what Immich already has, and the second run may go on although Immich is no longer empty:

```
python3 scripts/google/google_photos.py takeout --to DIR --import
```

If it says that there is not enough free space where Immich keeps its files, free up space first.
