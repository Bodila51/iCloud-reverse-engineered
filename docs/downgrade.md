# Turn off iCloud Photos and downgrade your plan

Do these steps last, in this order. Homecloud never does them for you. If a check fails, stop there and fix it first. [troubleshooting.md](troubleshooting.md) covers the common failures.

In Hybrid mode you keep iCloud Photos on. Follow [Hybrid mode](#hybrid-mode) at the end instead.

If your photos came from Google Photos, follow [After the move](google-photos.md#after-the-move) in google-photos.md instead of this page.

Apple renames menus between iOS versions. The steps below name the current ones and say where older versions differ. If your screen differs, Apple's own pages have the current steps: [How to turn off iCloud Photos](https://support.apple.com/102179) and [Downgrade or cancel your iCloud+ plan](https://support.apple.com/108318).

## 1. Check the copy

Run the check for the way your photos came down. Run commands from the kit folder. On Windows, type `py` instead of `python3`.

- With Path A, this check only reads the folder and works without internet. Go on only if it reports N of N and exits with code 0. If you are still signed in, it then signs you out.

  ```
  python3 scripts/icloud.py verify --to DIR
  ```

- With the Photos app on a Mac, this check only reads the folder. Go on only if it reports "Files verified: N of N" and exits with code 0.

  ```
  python3 scripts/mac/export_photos.py verify --to DIR
  ```

- With iCloud for Windows, ask Claude Code to compare `DIR` with the iCloud Photos folder again. Go on only if both hold the same files with the same sizes, N of N.
- With the Immich app, open it on its backup screen. Go on only if **Remainder** is 0.

Then compare the number of photos and the number of videos in Immich with that count, or with the numbers on your iPhone. [troubleshooting.md](troubleshooting.md#the-counts-do-not-match) explains the usual small differences. Go on only if no photo or video is missing.

## 2. Check that Immich holds the same library

With Path A or the Photos app on a Mac, this check only reads:

```
python3 scripts/immich/import_library.py verify --dir DIR
```

Go on only if it ends with "Result: OK" and reports N of N files. The check matches every file by its content, so a Live Photo counts as two files here, a photo and a short video, although Immich shows it as one. With iCloud for Windows, Apple's data copy, and the Immich app, step 1 already compared the counts in Immich.

Then open a few old photos and videos in the Immich app and check that they open and play.

In Offline vault mode without Immich, skip this step.

## 3. Make a second copy and check it

Copy the whole download folder to another drive, including the hidden `.homecloud` folder. With the Immich app, copy Immich's library folder instead. Then check the copy. Here `COPY` is the folder on the second drive. With Path A, run:

```
python3 scripts/icloud.py verify --to COPY
```

With the Photos app on a Mac, run:

```
python3 scripts/mac/export_photos.py verify --to COPY
```

Go on only if it reports N of N. With iCloud for Windows and Apple's data copy, and for Immich's library folder, ask Claude Code to compare the number of files and their sizes with the original folder.

## 4. Let the Immich app finish uploading

On your iPhone, open the Immich app on its Backup page, and keep it open until **Remainder** shows 0. This also saves photos that exist only on your iPhone, such as photos that never reached iCloud because your storage was full. Those photos are not in your second copy yet. They stay on your iPhone too, and your next monthly copy adds them.

In Offline vault mode, connect the iPhone and run `python3 scripts/vault/vault.py copy --to DIR` once instead. If **Optimize iPhone Storage** is on, the script reports photos that are only small copies on the phone. Their originals are in iCloud, so the copy you checked in step 1 already has them.

## 5. Turn off iCloud Photos on every device

Turning off iCloud Photos on a device stops syncing on that device. It does not delete your photos from iCloud.

### On your iPhone

1. Open **Settings**, tap your name, tap **iCloud**, then tap **Photos**.
2. Look at the options under the switch. If **Optimize iPhone Storage** has a check mark, read the next section first.
3. Turn off **Sync this iPhone**. On older iOS versions this switch is named **iCloud Photos**.
4. The iPhone asks what to do with the photos on it, and offers two buttons. The exact message differs between iOS versions.
   - **Download Photos & Videos** downloads full-quality copies of everything in iCloud Photos to the iPhone. Your photos stay on the phone.
   - **Remove from iPhone** removes the iCloud photos and videos from the iPhone, without saving a copy on it. The copies in iCloud stay.
5. To keep your photos on the phone, tap **Download Photos & Videos**.

### If Optimize iPhone Storage is on

With this setting, the iPhone keeps small versions of many photos, and the full-size originals live only in iCloud. **Download Photos & Videos** then has to download every original. The iPhone needs about as much free space as your iCloud Photos library, which the scan showed.

To see the free space, open **Settings** > **General** > **iPhone Storage**.

If the iPhone has room:

1. In **Settings** > your name > **iCloud** > **Photos**, choose **Download and Keep Originals**.
2. Keep the iPhone on Wi-Fi and charging until the download finishes. A large library can take a night.
3. Turn off **Sync this iPhone**, and tap **Download Photos & Videos**.

If the iPhone does not have room, you cannot keep every photo on the phone, and you do not need to. Your full library is in Immich and on your second drive. Tap **Remove from iPhone**. You still see every photo in the Immich app. Do this only after steps 1 to 4 have passed.

### Stop iCloud Backup from saving your photos

With iCloud Photos off, your iPhone's iCloud Backup starts to include the photo library. The library does not fit in 5 GB, and then the whole backup fails. Turn the photo library off in the backup settings:

1. Open **Settings**, tap your name, tap **iCloud**.
2. Open your storage (**Manage Account Storage** or **Storage**), then **Backups**, then this iPhone.
3. Turn off **Photo Library**.

Your messages, settings, and app data keep backing up while they fit in 5 GB.

### On an iPad

Follow the iPhone steps. The switch is named **Sync this iPad**.

### On a Mac

1. Open the **Photos** app. Choose **Photos** > **Settings**, then click **iCloud**. On older macOS versions, the menu item is **Preferences**.
2. Uncheck **iCloud Photos**.
3. The Mac asks what to do:
   - **Download** downloads your photos in iCloud to this Mac.
   - **Remove from Mac** removes the photos and videos that are not fully downloaded to this Mac. They stay in iCloud and on your other devices.

To keep full copies in the Photos app on this Mac, first choose **Download Originals to this Mac** in the same pane. If your photos came down through this Mac, that setting is already on. Wait for the download to finish, then uncheck **iCloud Photos** and click **Download**. If this Mac is your Homecloud computer, your library is already in Immich, and **Remove from Mac** saves disk space.

### On a Windows PC

Do this only after steps 1 to 4 have passed, because the copy in `DIR` is the one you keep. Open the **iCloud** app, which is iCloud for Windows, and turn off **Photos**. Apple's page [If your iCloud photos and videos aren't displaying correctly on your PC](https://support.apple.com/109029) describes how iCloud for Windows can then remove its downloaded photos from the PC. They stay in iCloud. Your copy in `DIR`, and Immich, do not change.

### On other devices

Turn off iCloud Photos on any other device that uses your Apple Account. On an Apple TV with tvOS 26, the switch is under **Settings** > **Profiles and Accounts** > the default profile > **Photos**. Older versions name it **Users and Accounts**.

## 6. Make room in 5 GB

Apple advises getting under the new limit before you change the plan. To see what uses your storage, open **Settings**, tap your name, and tap **iCloud**. The bar at the top shows each kind of data.

Your photos are still in iCloud. Turning off iCloud Photos did not delete them. If you change the plan while they take more than 5 GB, your account is over its limit when the new plan starts. Apple lists what happens while you are over the limit:

- Your devices stop backing up to iCloud.
- iCloud Drive and other iCloud apps stop updating across your devices.
- iCloud Mail can stop receiving new email.

Apple's iCloud terms also say that Apple may delete data above your limit after 30 days' notice. Do not count on the copy in iCloud as a backup after you downgrade.

You have two choices for the photos in iCloud:

- **Leave them in iCloud.** You give up the services above while you are over the limit.
- **Delete them from iCloud yourself, when you are sure.** On your iPhone, open **Settings** > your name > **iCloud** > **Storage** > **iCloud Photos**, and tap **Turn Off and Delete Photos**. On iOS 17 and earlier, the button is **Turn Off & Delete from iCloud**. Apple keeps the photos for 30 days, and you can undo the deletion during that time. After 30 days, Apple deletes them for good.

Homecloud never deletes anything in iCloud for you. Before you delete, make sure steps 1 to 4 passed, and use Immich for a few weeks first.

Other data needs room in the 5 GB too. Your device backups, iCloud Drive, Mail, and Messages all count. WhatsApp on iPhone backs up chats to iCloud from inside the app, so a large chat backup counts as well.

## 7. Change your plan

The new plan starts when your current billing period ends. Until then, nothing changes.

On your iPhone:

1. Open **Settings** and tap your name.
2. On iOS 18.4 and later, tap **Subscriptions** > **iCloud+** > **See All Plans**. On iOS 18 to 18.3, tap **iCloud** > **Manage Plan**. On iOS 17 and earlier, tap **iCloud** > **Manage Account Storage** > **Change Storage Plan** > **Downgrade Options**.
3. Choose the free 5 GB plan and follow the steps on screen. If the iPhone asks for your Apple Account password, that is Apple's own screen for your plan. Type it on the iPhone as usual, never in a chat.

On a Mac, open **System Settings**, click your name, click **iCloud**, then **Manage Plan**.

On a Windows PC, open iCloud for Windows and look for **Storage** or **Manage**, then **Change Storage Plan** and **Downgrade Options**.

If you share iCloud+ with your family, everyone in the group uses the shared storage, so a smaller plan leaves less room for all of them. Every Apple One plan includes iCloud+ storage, so you reach the free 5 GB only by cancelling Apple One, under **Settings** > your name > **Subscriptions** > **Apple One**.

## Hybrid mode

In Hybrid mode you keep iCloud Photos on with the 50 GB plan.

1. Do steps 1 to 4.
2. Skip step 5. iCloud Photos stays on.
3. Get under 50 GB before you change the plan. Delete older photos from iCloud yourself, as [modes.md](modes.md#hybrid) describes. Deleted photos usually wait in **Recently Deleted** for 30 days. When your iCloud storage is over its limit, Apple says a deleted photo skips Recently Deleted and is gone for good at once, so check that the photos are safe at home before you delete them.
4. Do step 7 and choose the 50 GB plan.

## If you used Path A

Before step 5, make sure the Path A session is signed out, so the session key no longer sits on the computer. That key opens roughly what a browser signed in to iCloud.com opens, as [two-paths.md](two-paths.md#what-the-session-key-opens) lists. `verify` signs you out when every file is there, unless you used `--keep-session` or answered no. To be sure, run:

```
python3 scripts/icloud.py logout
```

If nothing is signed in, it says so and changes nothing.
