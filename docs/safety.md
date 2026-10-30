# How Homecloud keeps your photos safe

Homecloud moves your photos to your computer in one of two ways: Path A downloads them from iCloud.com after you sign in, and Path B lets Apple's own apps download them. [two-paths.md](two-paths.md) compares them. Once you turn off iCloud Photos, your computer and your drives hold the only complete copy. A mistake can then cost photos you cannot take again, so the kit is built around a few hard rules. This page explains each rule, what it protects, and what can still go wrong.

## The promises

- Your Apple password never goes into the chat. With Path B, nobody asks for it. With Path A, you type it only in your own Terminal window, and it goes only to Apple.
- With Path A, the script sends only read requests to iCloud Photos, and the session key is deleted when you log out.
- Homecloud never deletes, moves, or changes anything in iCloud, in the Photos library on your Mac, or in the iCloud Photos folder on your PC.
- Claude Code asks before every step that installs or changes something on your computer.
- Every copy is checked, and any mismatch stops the setup.
- Install commands come only from each tool's official docs.
- Immich is never opened to the internet.
- You turn off iCloud Photos yourself, last.

## Your Apple password

With Path B, Apple's own apps download your photos, with the sign-in you already have on your devices:

- On a Mac, the Photos app downloads every original when you select **Download Originals to this Mac**.
- On a PC, iCloud for Windows downloads every original when you choose **Always keep on this device**. If iCloud for Windows is new on your PC, Apple's app asks you to sign in once, in its own window, as it would without Homecloud.
- With the Immich app, iOS fetches each original from iCloud for the app.

With Path A, you type your password only in your own Terminal window, as [Path A: the iCloud.com sign-in](#path-a-the-icloudcom-sign-in) explains.

Claude Code never asks for your Apple Account password or a verification code in the chat, and it runs `scripts/icloud.py` only if you picked Path A. If you ever type your password into the chat by mistake, change your Apple Account password right away. On your iPhone, open **Settings**, tap your name, then **Sign-In & Security**, then **Change Password**.

## What the kit reads, and what it never touches

On a Mac, `scripts/mac/export_photos.py` uses osxphotos to read the Photos library. osxphotos reads a temporary copy of the Photos database and copies the files out of the library. The script never passes the osxphotos options that change the library. One option, `--download-missing`, asks Photos itself to download originals. The kit does not use it unless you choose it.

On a PC, Claude Code copies the iCloud Photos folder with robocopy, the copy tool that comes with Windows. It never uses the robocopy options that delete or move files: `/MIR`, `/MOV`, `/MOVE`, and `/PURGE`. This matters because the iCloud Photos folder is not a normal folder. Apple's guide [Delete and recover photos and videos in iCloud for Windows](https://support.apple.com/guide/icloud-windows/delete-and-recover-photos-and-videos-icw5c1e2d1a7/icloud) says a delete there deletes the photo from iCloud and from all your devices. The same holds in the Photos library on a Mac while iCloud Photos is on.

With Path A, `scripts/icloud.py` sends only read requests to iCloud Photos. Every request is checked against a short list of read-only iCloud addresses before it leaves the computer. The one exception is `logout`, which ends this computer's session at Apple and changes nothing in your photos.

The Immich app only reads the photo library on your iPhone when it backs up. The only part of the app that removes photos from the iPhone is **Free Up Space**, which you start yourself.

The only steps that touch iCloud Photos settings are the ones in [downgrade.md](downgrade.md). You do those yourself, on your own devices, at the end.

## How the copy avoids losing files

With the Photos app on a Mac, the export never deletes a file. A file that does not match its original is marked `failed` in the manifest, and the export lists it under "Problem". Delete that one file in `DIR` and run the export again, and osxphotos copies it again from the Photos library. osxphotos remembers what it already exported, in `DIR/.osxphotos_export.db`, so a second run copies only what is new or changed.

The export writes a list of every file it expects to `DIR/.homecloud/manifest.jsonl`. Each line holds the file's name, its place in the folder, its size, its SHA-256 checksum, and its status. It also keeps the date taken, the favorite mark, the hidden mark, and the album names, so they survive even if you later delete the photo from iCloud. The Immich import reads them to mark your favorites, fill your albums, and archive the photos you hid. Files land in `DIR/YYYY/MM/` under their original names. If two files share a name, the second one gets a number in its name, so nothing is overwritten.

With Path A, the download writes the same kind of manifest, with the favorite mark, the hidden mark, and the album names. Files that finished downloading stay in the manifest, so a second run skips them.

On a PC, iCloud for Windows gives the kit only the files, with no favorites or albums. Claude Code copies them into `DIR` and writes no manifest, because no kit script does that step.

## How the check works

With the Photos app on a Mac, the check is part of the export. After each run, the export compares every copied file with its original in the Photos library: the size and the SHA-256 checksum must match. The Photos library also gives the expected number, so the result reads "Files verified: N of N". The export exits with code 0 only when every file passes. Code 2 means that some originals are still only in iCloud.

To check the folder again later, or to check your second copy, run:

```
python3 scripts/mac/export_photos.py verify --to DIR
```

Verify reads only the folder. It compares each file with the size and checksum in the manifest and prints "Files verified: N of N". Set `--to` to the copy on your second drive to check that copy. Copy the whole folder, including the hidden `.homecloud` folder, before you check it.

With Path A, the manifest lists every photo and video in iCloud's own list of your library. `python3 scripts/icloud.py verify --to DIR` checks that each file exists with the right size and SHA-256 checksum, and prints N of N. It works without internet, so it can check your second copy too.

On a PC, with the Immich app, and with Apple's data copy, no list gives the kit an expected number. You read the number of photos and videos on your iPhone and type them into Claude Code. Claude Code compares them with what Immich holds and shows the result as N of N. On a PC, Claude Code also checks that `DIR` has the same files, with the same sizes, as the iCloud Photos folder. These checks count files. They do not compare each file with its original in iCloud, so they are weaker than the Mac check.

Claude Code does not go on to the downgrade until the check passes. On a mismatch, it stops and shows you what is missing.

## Keep your photos out of cloud-synced folders

The download folder, the second copy, and Immich's files must not sit in a folder that syncs to a cloud service. A synced folder would try to upload your whole library again, and it can replace local files with online-only placeholders to save space. On a Mac, Desktop and Documents sync to iCloud Drive if you turned that on. On Windows, OneDrive often backs up Desktop, Documents, and Pictures. The prompt tells Claude Code to check this before it suggests a folder.

The iCloud Photos folder on a PC is itself a synced folder. That is why Claude Code copies your photos out of it into `DIR`, and Immich reads `DIR`, not the iCloud Photos folder.

## What Claude Code can do on your computer

Claude Code runs commands on your computer with your user account. The prompt tells it to ask before it installs, creates, starts, or changes anything. You see each command before it runs.

Claude Code reads what the commands print: counts, sizes, file names, and errors. That text becomes part of your chat with Claude, and Anthropic processes it like any other chat. The prompt tells Claude Code not to open your photos.

## Where the software comes from

Claude Code installs only from these sources:

- Docker Desktop, from Docker's install guide at docs.docker.com.
- Immich, with the `docker-compose.yml` and `example.env` files from the latest Immich release on GitHub.
- The Immich command-line tool, which the import runs from Immich's official Docker image, as Immich's docs describe.
- Tailscale, from tailscale.com or the app store for your system.
- The Immich and Tailscale iPhone apps, from the App Store.
- iCloud for Windows, from the Microsoft Store. You install it yourself.
- Python, from python.org, the Microsoft Store, or Apple's Command Line Tools.
- For the Mac export in `scripts/mac/`, osxphotos, installed with uv from astral.sh as the osxphotos README recommends.
- For Offline vault, the Python packages in `scripts/vault/requirements.txt`, from PyPI. On Windows, it also needs Apple's device driver, which comes with the Apple Devices app from the Microsoft Store or with iTunes.

## Who can reach your photos

Immich runs on your computer and listens on port 2283. Never forward that port on your router. Immich's own docs warn that this puts its web page on the open internet without encryption.

Tailscale connects your own devices through encrypted links. Only devices signed in to your Tailscale account, and the people you share the computer with, can reach Immich. If you lose a device, remove it from your Tailscale account in the Tailscale admin console.

Immich has its own accounts. Use a strong password for your Immich account, and do not reuse your Apple Account password.

The Mac import needs an Immich API key, which works like a password for your Immich account. You paste it into your own Terminal window with `python3 scripts/immich/import_library.py save-key`, where it stays hidden, never into the chat. The script saves it in `.homecloud/immich-api-key` in your home folder. Anyone who can use your account on the computer can read that file. To remove it, delete that file and delete the key in Immich under **Account Settings** > **API Keys**.

## What can still delete photos

Homecloud cannot stop these, so you should know about them:

- **A dead drive without a second copy.** This is the most likely way to lose everything. Keep a second copy on another drive, and copy Immich's library folder to it every month.
- **Emptying the trash in Immich.** Immich never changes your original files, but emptying its trash deletes them from the computer.
- **A delete in the Photos library or the iCloud Photos folder.** While iCloud Photos is on, it deletes the photo from iCloud and from your other devices.
- **Turning off Photos in iCloud for Windows.** Apple's page [If your iCloud photos and videos aren't displaying correctly on your PC](https://support.apple.com/109029) describes how iCloud for Windows can then remove its downloaded photos from the PC. The copy in `DIR` stays.
- **Free Up Space in the Immich app.** It removes photos from your iPhone that are already in Immich. If iCloud Photos is still on, it also removes them from iCloud and from your other devices. Removed photos usually wait in **Recently Deleted** for 30 days, but when your iCloud storage is over its limit, Apple says a deleted photo is gone for good at once.
- **Turn Off and Delete Photos in Apple's settings.** This deletes your photos from iCloud after 30 days. [downgrade.md](downgrade.md) explains when, and if, to use it.

## Backups of Immich itself

Immich keeps your photos in its library folder, set as `UPLOAD_LOCATION` in its `.env` file. Its live database sits in a separate folder, `DB_DATA_LOCATION`. Each day at 2:00 AM, if the computer is on, Immich writes a backup of the database to the `backups` folder inside the library folder and keeps the last 14.

Copy the whole library folder to your second drive. That one copy holds both the photos and the database backups. Immich's docs say a database backup alone holds no photos, and the photos alone lose your albums and other Immich data. With iCloud for Windows, Immich reads your first copy from `DIR` as an External Library, so keep the copy of `DIR` on your second drive too.

## Path A: the iCloud.com sign-in

[README.md](../README.md#path-a-sign-in-to-icloudcom) has the steps for `scripts/icloud.py`. You sign in with this command, which you run in your own Terminal window:

```
python3 scripts/icloud.py login
```

The script asks for your Apple Account email, your password, and the six-digit code from one of your Apple devices. You type all three into your own window. Claude Code cannot type into that window, and it never sees what you type there. The script sends your password only to Apple's sign-in service, the same one iCloud.com uses. It does not save the password, write it to a log, or accept it as a command-line argument, because command lines can show up in process lists and shell history.

After you sign in, the script keeps a session key in `.homecloud/session` in your home folder, readable only by your user account. The scan and the download use it instead of your password. That key opens roughly what a browser signed in to iCloud.com opens, such as Photos, iCloud Drive, Notes, and iCloud Mail. It does not open your Keychain passwords, Messages, or Health. Any program that runs as you on the computer could read it, and so could backups of the computer. [two-paths.md](two-paths.md#what-the-session-key-opens) lists what it opens and who can read it. Treat that folder like a password. The kit's rules tell Claude Code never to open or print it.

You need the key only for the one-time move. When `python3 scripts/icloud.py verify --to DIR` reports N of N, it signs you out. In your own Terminal window it asks first, and when Claude Code runs it, it signs out by itself. To sign out at any other time, run:

```
python3 scripts/icloud.py logout
```

`logout` ends this computer's session at Apple and deletes `.homecloud/session`. `--keep-session` tells `verify` to keep the key, for example when you download again soon in Hybrid mode. Then run `logout` yourself when you are done.

Only Hybrid mode needs a fresh sign-in each month. The Photos app on a Mac does the monthly step without one.
