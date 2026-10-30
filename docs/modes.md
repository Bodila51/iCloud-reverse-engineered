# Choose how your photos live at home

Homecloud copies your iCloud Photos library to your computer once, with Path A or Path B, as [two-paths.md](two-paths.md) explains. After that, new photos still need a way to get home. The four modes are four ways to do that, and each one gives up something different. Claude Code picks a mode from three questions, and you can switch later.

Run the commands on this page from the kit folder. On Windows, type `py` instead of `python3`. The Path A script and the Offline vault script also need the kit's virtual environment active, as [README.md](../README.md#do-it-by-hand) shows.

## How the three questions pick a mode

Claude Code asks these in order and stops at the first yes:

1. Will this computer be at home and switched on most of the day? Yes means **Always on**.
2. Would you pay $0.99 a month to keep recent photos syncing like today? Yes means **Hybrid**.
3. Do you want a phone app that uploads new photos by itself? Yes means **Home archive**. No means **Offline vault**.

The order matters. A computer that stays on gives you the most without a storage fee, so it comes first. If you have none, a small Apple plan keeps your phone working the way it does today. If you want to pay nothing for storage, the last question decides between an app and a cable.

## Always on

Your computer runs Immich and Tailscale all the time. iCloud Photos is off, and you are on Apple's free 5 GB plan. The Immich app on your iPhone uploads new photos to the computer, at home or away, whenever iOS lets it run. Your whole library is in the app wherever you are.

Storage costs $0 a month. The computer uses electricity, and a small PC is a one-time cost if you buy one. A mini PC with an Intel N150 chip idles at about 6 to 12 watts, and an old laptop at about 10 to 15 watts. A plug-in power meter shows what yours uses.

What you give up:

- A computer that stays on and awake. A MacBook sleeps when you close the lid, unless an external display is attached. Keep the lid open and let the screen turn off. If the lid must stay closed, read [A MacBook with the lid closed](#a-macbook-with-the-lid-closed).
- iOS decides when the Immich app uploads in the background. The iPhone app has no setting to upload only while charging. When you open the app, it stops the background uploads and uploads on screen instead. That upload stops when you leave the app.
- After a power cut or a restart for an update, Immich stays down until someone signs in to the computer, because Docker Desktop starts at sign-in. A Mac with FileVault on cannot sign in by itself.
- Away from home, the app reaches the computer only while Tailscale is connected on the iPhone.

What you do each day:

- Open the Immich app on its Backup page and keep it open until it shows nothing left to upload. Background uploads often get there first, but this is the sure way. The newest photos go first.

What you do each month:

- Copy Immich's library folder to your second drive. This folder also holds the database backups that Immich makes each day.

After each major iOS update, open a new Live Photo in Immich and check that it plays.

What Claude Code sets up:

- Docker Desktop starts when you sign in to the computer. This setting is off by default. Immich starts with Docker, because its compose file tells Docker to restart it.
- The computer does not sleep while it is plugged in. On a MacBook, that is **Prevent automatic sleeping on power adapter when the display is off**, under **System Settings** > **Battery** > **Options**. On a Windows laptop, sleep is set to **Never** while plugged in, and closing the lid does nothing while plugged in.
- Tailscale starts when you sign in to the computer.

If your laptop has a charge limit setting, turn it on, so the battery does not sit at 100% all the time.

The setup files are in `scripts/always_on/`.

### A MacBook with the lid closed

Apple supports closed-lid use only with power, an external display, a keyboard, and a mouse attached. Without them, the Mac sleeps when the lid closes, whatever its settings say. One command keeps it awake anyway. You type your Mac's own password for `sudo` in your own Terminal window.

```
sudo pmset -a disablesleep 1
```

Apple does not document this setting. It is in Apple's open-source code for `pmset`, but not in its manual page, and the last public report of it working is from macOS 26.3. It stops all sleep, also on battery, so undo it before you carry the Mac in a bag. With the lid closed, the Mac also cools less well. To undo it:

```
sudo pmset -a disablesleep 0
```

While it is on, `pmset -g` shows `SleepDisabled 1`.

## Home archive

Your computer runs Immich only when it is on. iCloud Photos is off, and you are on the free 5 GB plan. New photos stay on your iPhone until the computer is on. Then you open the Immich app, and it uploads them, at home or away through Tailscale. iOS does not know when the computer is on, so a background upload reaches it only by chance.

To keep space free on the phone, you keep about the last year of photos on it and remove older ones with **Free Up Space** in the Immich app. You pick a cutoff date. Free Up Space leaves out your favorites by default and never offers photos in shared albums. You can also keep chosen albums. It offers a photo only when Immich holds a copy with exactly the same bytes. The files that Homecloud imported from your Mac or PC can differ from the files on your iPhone byte for byte, and then Free Up Space does not offer those photos. Photos that the Immich app uploaded itself always match. Let the Immich app finish its own upload first, and read the preview before you confirm. Removed photos go to **Recently Deleted** on the iPhone for 30 days. The space comes back when you empty Recently Deleted.

Use Free Up Space only after iCloud Photos is off on your iPhone. While iCloud Photos is on, removing a photo from the iPhone also removes it from iCloud and from your other devices.

Storage costs $0 a month.

What you give up:

- Older photos open only while the computer is on.
- New photos exist only on your iPhone until the computer is on again. If you lose the phone before that, you lose those photos.

What you do each time you turn the computer on:

- Open the Immich app on its Backup page and keep it open until it shows nothing left to upload. The newest photos go first.

What you do each month:

1. Copy Immich's library folder to your second drive.
2. If the phone is getting full, run **Free Up Space** with a cutoff date about a year back. Read its preview before you confirm.

## Hybrid

This mode is still being tested. The steps below are the current plan, and they can change.

You keep Apple's 50 GB plan, and iCloud Photos stays on. Recent photos sync between your devices the way they do today. Once a month, Apple's own app brings the new photos down to your computer, and Homecloud copies and checks them. Then you delete photos older than a cutoff date from iCloud yourself, which keeps you under 50 GB. No script does that step for you, and Homecloud never deletes anything in iCloud.

Storage costs $0.99 a month in the US. The price of the 50 GB plan differs by country.

What you give up:

- You still pay $0.99 a month.
- Older photos open only while the computer is on.
- The monthly delete is a manual step, in Apple's Photos app, with iCloud for Windows, or on the iPhone.
- Deleting a photo from iCloud also removes its favorite mark and its albums in Apple Photos. On a Mac, Homecloud saves both in its manifest before you delete.

What you do each month:

1. Bring the new photos home and check them.
   - With Path A, sign in again in your own Terminal window with `python3 scripts/icloud.py login`, then run `python3 scripts/icloud.py download --to DIR` again. It copies only what is new. Then run `python3 scripts/icloud.py verify --to DIR`, which signs you out when every file is there. Go on only if it reports N of N.
   - With the Photos app on a Mac, keep **Download Originals to this Mac** on in Photos and run the export again. It copies only what is new since the last run, and checks it. Go on only if it ends with "Result: all N files are copied and verified."

     ```
     caffeinate -i python3 scripts/mac/export_photos.py export --to DIR
     ```

   - On Windows, wait until every photo in the iCloud Photos folder has a solid green check. Copy the new files into `DIR` with robocopy, without any option that deletes or moves files. Then run `python3 scripts/immich/make_manifest.py --dir DIR` to list them.
   - With the Immich app, open it on its backup screen and wait until **Remainder** is 0.
2. Add the new files to Immich with `scripts/immich/import_library.py`, as in the first move. The import skips files that Immich already has, and it ends with a check. Go on only if it reports "Result: OK".

   ```
   python3 scripts/immich/import_library.py all --dir DIR
   ```
3. Copy the download folder to your second drive. With the Immich app, copy Immich's library folder.
4. Pick a cutoff date, for example one year back. Keep it a few days older than your newest checked photo, because dates can shift by a day across time zones.
5. Delete the photos older than the cutoff from iCloud yourself. Pick one way:
   - On a Mac, open Photos and choose **File** > **New Smart Album** with the rule **Date Captured** is before your cutoff. Compare its count with Homecloud's numbers, then select all and delete. For a smaller batch, work one year at a time.
   - On a Windows PC, delete them in the iCloud Photos folder of iCloud for Windows. A delete there deletes from iCloud and from all your devices.
   - On the iPhone, use **Free Up Space** in the Immich app with your cutoff date. It deletes only photos whose bytes match a copy in Immich. Files that Homecloud imported from your Mac or PC may not match, so read its preview count first. The first run downloads each old original from iCloud to the phone to check it.

While iCloud Photos is on, a delete on any device also deletes from iCloud and from all your other devices. Deleted photos wait in **Recently Deleted** for 30 days. Apple says they do not count toward your iCloud storage during that time. When your iCloud storage is over its limit, Apple says a deleted photo skips Recently Deleted and is gone for good at once. Delete only photos that the kit has checked at home.

## Offline vault

No apps on your phone and no Tailscale. iCloud Photos is off, and you are on the free 5 GB plan. Once a month you connect your iPhone to the computer with a cable. A script copies the photos and videos that are new since last time to your vault drive and checks each file.

The script runs on the computer. It needs Python and pymobiledevice3, a free tool under the GPL-3.0 license, which the kit installs from PyPI into a virtual environment.

The cable copy sees only the originals stored on the iPhone. With **Optimize iPhone Storage** on, many originals live only in iCloud. That is why the first full copy of your library comes through Path A or Path B, and why the iPhone must hold its own originals, with iCloud Photos off.

Immich is optional in this mode. If you install it, you can browse the vault on the computer.

Storage costs $0 a month.

What you give up:

- You have to remember to do it. Nothing happens by itself.
- You have to be there. The iPhone must stay unlocked during the copy, and it asks for its passcode the first time it trusts the computer.
- There is no phone app.
- New photos exist only on your iPhone until the next monthly copy.
- Favorites and albums are not in the photo files. The vault script reads them from the iPhone's photo list when it can, and saves them in its manifest.
- On Windows, the copy needs Apple's device driver, which comes with iTunes or the Apple Devices app.

What you do each month:

1. Unlock your iPhone and connect it to the computer with a cable.
2. If the iPhone asks whether to trust this computer, tap **Trust** and enter your passcode.
3. Copy and check the new photos. Keep the iPhone unlocked until the script finishes.

   ```
   python3 scripts/vault/vault.py copy --to DIR
   ```

   To see first what would be copied, run the same command with `check` in place of `copy`. The script never deletes or changes anything on the iPhone.
4. If you use Immich, add the new files to it with `python3 scripts/immich/import_library.py all --dir DIR`. It skips files that Immich already has.
5. Copy the vault to your second drive. `python3 scripts/vault/vault.py verify --to DIR` checks the vault again later, without the iPhone.
6. When the script says it is safe to free up photos on the phone, and the second copy is done, you can delete those photos from the iPhone yourself.

If you also back up the whole iPhone to the computer, it asks for its passcode each time. Apple added that in iOS 16.1.

## What every mode has in common

You own the backups now. Apple no longer keeps a copy for you, so keep a second copy on another drive. If the only drive with your photos dies, the photos are gone. A drive kept somewhere else, such as at work or with family, also covers fire and theft.

Copy two things:

- The download folder, once, after the first check passes. Check the copy too. On a Mac, run `python3 scripts/mac/export_photos.py verify --to COPY`, where `COPY` is the folder on the second drive. On Windows, compare the number of files and their sizes. Immich on Windows reads its photos from this folder, so copy it again after each monthly step that adds files to it.
- Immich's library folder, every month. Each day at 2:00 AM, if the computer is on, Immich writes a backup of its database into this folder and keeps the last 14. The photos and the database backups belong together.

## Share it with your family

Immich gives each person their own account and library, so one computer can serve the whole family. This works in Always on and Home archive.

1. In Immich, open **Administration** > **Users** and create an account for each person.
2. Each person makes their own free Tailscale account and installs Tailscale on their iPhone.
3. In the Tailscale admin console, share the Immich computer with each person, and send them the link. They open it and accept.
4. Each person installs the Immich app and signs in with the computer's Tailscale address, such as `http://100.101.102.103:2283`, and their own Immich account.
5. Each person turns on **Background App Refresh** for Immich and picks the albums to back up.

Share the computer instead of inviting family members to your Tailscale account. A shared computer does not use one of your 6 user places on the free plan, and the person reaches only that computer, not your other devices. To see each other's photos, turn on partner sharing in Immich.

Whoever runs the computer can open every person's files on its disk. Everyone's photos depend on that computer and its second drive, so the monthly copy matters even more.

## Switch to another mode later

- To go from Home archive to Always on, keep the computer on and ask Claude Code to run the setup in `scripts/always_on/`.
- To go from Hybrid to Home archive or Always on, follow [downgrade.md](downgrade.md) and choose the free 5 GB plan.
- To go from Always on or Home archive to Offline vault, turn off backup in the Immich app and start the monthly cable routine.

## Path A and the monthly routine

With Path A, you sign in once for the first full copy, and `verify` signs you out at the end. [README.md](../README.md#path-a-sign-in-to-icloudcom) has the steps. Hybrid is the only mode that brings photos home from iCloud again each month, so with Path A it needs a fresh sign-in each month, as step 1 of [Hybrid](#hybrid) shows. The Photos app on a Mac does the same monthly step with no sign-in.
