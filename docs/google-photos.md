# Move your photos from Google Photos

Homecloud can also move a Google Photos library to Immich on your own computer. This is Path G. After the move, your Android phone or iPhone backs up new photos to Immich, and reaches it away from home through Tailscale. This page explains how the move works, what comes along, what can go wrong, the official Takeout route, and what you do after the move. For iCloud Photos, read [two-paths.md](two-paths.md).

Claude Code explains this way and its risks before it runs anything that changes your computer, and you choose. [README.md](../README.md) has the rest of the setup.

## How it works

Google offers no public way for other programs to download a whole Google Photos library. Since March 31, 2025, Google's Photos Library API gives an app only the photos and videos that the app uploaded itself ([Google Photos APIs updates](https://developers.google.com/photos/support/updates)). Google's own way to get everything out is Google Takeout. You can use it too, as [Google Takeout, the official route](#google-takeout-the-official-route) shows.

Path G works through Google Photos in your own browser. When you open photos.google.com, the page loads your library with its own internal requests to Google's servers. The kit's browser script, `scripts/google/homecloud_google.js`, runs inside that page and repeats those requests:

- It lists every photo and video in your library, with its name, date taken, size, favorite mark, archive mark, and albums.
- It downloads each file.
- It saves the files into a folder on your computer that you pick, called `DIR`, sorted by the year and month they were taken, counted in UTC, such as `DIR/2024/2024-07/`. If two files in the same folder share a name, the second one gets a number, such as `PXL_0001 (2).jpg`, so nothing is overwritten.

### What is reverse engineered

Google does not publish these internal requests. People studied how the web app talks to Google's servers and wrote down how the requests work, for example in the open-source [Google Photos Toolkit](https://github.com/xob0t/Google-Photos-Toolkit). That is what reverse engineered means here. Google can change these requests whenever it changes its web app, and then the script stops until someone updates it.

The script only reads. It sends list, info, and download requests. It never sends a request that changes anything in Google Photos: no delete, trash, archive, favorite, album, sharing, or Locked Folder request.

### Why no password is needed

The script runs in the browser tab where you are already signed in to Google. Your browser adds your sign-in to each request, as it does for the page itself. So the script never sees your password or a code. It never reads your cookies. It reads the page's own request key, as the page does, uses it only for its requests to photos.google.com, and never saves it, so nothing stays on the computer that could open your Google Account. If Google asks you to sign in, you do that on Google's own page, as you always do.

Path A for iCloud is different: it keeps a session key on the computer until you log out, as [two-paths.md](two-paths.md#what-the-session-key-opens) explains.

## The steps

Claude Code runs the commands, and you do steps 2 and 3 in your browser. The commands say `python3`. On Windows, Claude Code types `py` instead. The script needs Python 3.9 or newer and no extra packages.

1. Claude Code picks the folder `DIR`, with room for your library, and runs:

   ```
   python3 scripts/google/google_photos.py prepare --to DIR
   ```

   It creates `DIR`, writes the browser script with its settings filled in, copies the script to the clipboard, opens <https://photos.google.com>, and prints the steps. It opens the first of these browsers that it finds: Chrome, Edge, Brave, Arc, Vivaldi, or Opera. Safari and Firefox do not have the folder window that the script uses ([MDN](https://developer.mozilla.org/en-US/docs/Web/API/Window/showDirectoryPicker)). If you use Google Photos in another of these browsers, Claude Code adds `--browser` with its name, such as `--browser edge`.

   `prepare` also measures the free space on the disk with `DIR` and tells the browser script how much of it the script may use. It always leaves at least 5 GB free, or 2% of the disk when that is more.

   `DIR` must be a folder that a web page may save into. Browsers refuse your home folder, Desktop, Documents, and Downloads themselves, and system folders such as Library and Applications on a Mac, or Program Files and AppData on Windows. A new folder directly in your home folder works, outside the folders that sync to a cloud service. Keep Google Photos in its own folder, apart from any iCloud copy: `prepare` and `verify` leave alone a folder that holds another script's list.

   In Brave, first open `brave://flags/#file-system-access-api`, set it to **Enabled**, and click **Relaunch**. Brave keeps the folder window switched off until then ([Brave](https://github.com/brave/brave-core/blob/master/browser/about_flags.cc)). `prepare` prints this step when it opens Brave. When the move is done, set the flag back to **Default** and relaunch Brave.

2. In the Google Photos tab that opens:
   - If Google asks you to sign in, sign in on Google's own page, with the Google Account that holds your photos. If you use more than one account, check the picture at the top right.
   - Open the console. Press Cmd+Option+J on a Mac, or Ctrl+Shift+J on Windows ([Chrome DevTools](https://developer.chrome.com/docs/devtools/open)). If nothing opens, press Cmd+Option+I on a Mac or F12 on Windows, and click **Console**.
   - Click in the console, paste the script with Cmd+V on a Mac or Ctrl+V on Windows, and press Return or Enter.
   - The first time, the browser blocks the paste and shows a warning that asks you to type `allow pasting`. If your browser uses another language, the words are in that language. Type exactly what it shows, press Enter, then paste again and press Enter ([Chrome](https://developer.chrome.com/blog/self-xss), [Edge](https://learn.microsoft.com/en-us/microsoft-edge/devtools/console/console-javascript)). Typing it switches the warning off in this browser profile until you reset it, as step 6 shows.

3. A Homecloud panel appears at the bottom right of the page. Click **Start**. A folder window opens. Choose `DIR`, the folder that `prepare` printed. The panel accepts only a folder with that name. When the browser asks "Allow this site to edit files?", click **Allow**. The script lists your library, with your archived items, and reads your own albums. It compares the size of the files with the free space that `prepare` measured, and if they do not fit, it stops before any download. Then it downloads the first few items to find a download method that works, and then the rest. Keep the tab open, ideally in front, and the computer plugged in and awake, until the panel says **Done**. You can use other tabs and apps meanwhile. While the tab is in front, the script keeps the screen on, if your browser allows it. To pause, click **Stop**, and click **Start** to go on.

   If the panel says **Finished** and that some items did not download, click **Start** again. It skips every item that is already saved. If they still fail, tell Claude Code.

4. While it works, Claude Code reads the progress now and then:

   ```
   python3 scripts/google/google_photos.py status --to DIR --wait 300
   ```

   With `--wait 300`, `status` checks every 10 seconds, for up to 300 seconds, and returns as soon as the script ends. Exit code 0 means done, 4 means still running or not started yet, and 1 means the script failed or was stopped. If nothing has changed for 10 minutes, it asks whether the tab is still open. On a Mac, Claude Code puts `caffeinate -i` in front, so the Mac stays awake while it waits.

5. When the panel says **Done**, Claude Code checks the files and imports them into Immich:

   ```
   python3 scripts/google/google_photos.py verify --to DIR
   python3 scripts/immich/import_library.py all --dir DIR --yes
   ```

   Claude Code runs the import only when `verify` ends with "Result: OK". [How the check works](#how-the-check-works) explains `verify`. The import copies every file into Immich, marks your favorites, fills your albums, and archives the items that you archived in Google Photos. Then it checks that Immich holds every file, and ends with "Result: OK".

6. Switch the console's paste warning back on: in DevTools, click the gear icon at the top right, and at the bottom of **Preferences** click **Restore defaults and reload**. This also resets any other DevTools setting you changed. In Brave, also set `brave://flags/#file-system-access-api` back to **Default** and relaunch Brave.

If the tab closes, the computer sleeps, or the download stops for another reason, do steps 1 to 3 again with the same `DIR`. The script skips every item that is already in `DIR` with the right size. If the panel shows a problem, tell Claude Code what it says. If Google changed its requests, the script cannot work until it is updated, and Claude Code offers you [Google Takeout](#google-takeout-the-official-route).

The disk needs room for about 2.2 times your library: the files land in `DIR` and are then copied into Immich, which also makes thumbnails and video previews. `DIR` can be on an external drive.

## What comes along, and what does not

What comes along:

- Every photo and video in your library. For items that you backed up in **Original quality**, that is the file at its full resolution.
- The video part of each Live Photo from an iPhone, as its own file next to its photo, when Google gives it as a separate file. `verify` counts the Live Photos that come without one.
- Your favorites, your albums, and your archive. They go into the list, and the import sets them in Immich: favorites as favorites, albums as albums, and archived items as archived.

What does not:

- **Trash.** Items in the trash are left out. Restore the ones you want before you start. Google keeps backed-up items in the trash for 60 days ([Google](https://support.google.com/photos/answer/6128858)).
- **Locked Folder.** Its items stay in Google Photos. To move some of them, first move them out of the Locked Folder in the Google Photos app ([Google](https://support.google.com/photos/answer/10694388)).
- **Albums that other people shared with you.** Their photos are not in your library unless you saved them. To include them, open the shared album and click **Save** first ([Google](https://support.google.com/photos/answer/6131416)). Saved items count toward your Google storage.
- **The originals of Storage saver items.** If you backed up photos in **Storage saver**, called High quality before, Google keeps only a smaller copy. Photos above 16 MP were resized to 16 MP, and videos above 1080p to 1080p ([Google](https://support.google.com/photos/answer/6220791)). That smaller copy is what comes down. The list marks these items.
- Google's face groups, memories, and comments. They are not in the list. Immich finds faces itself while its machine learning is on.

## How the check works

`verify` compares three things: the list of your library that the script read from Google, `DIR/.homecloud/google-list.json`; the record of finished items, `DIR/.homecloud/google-done.jsonl`; and the files in `DIR`. It sets each file's modification date to the time the photo or video was taken. It writes `DIR/.homecloud/manifest.jsonl`, the list that the Immich import reads. It names what is missing or has the wrong size, and writes the full list of problems to `DIR/.homecloud/google-problems.txt`. It counts the items in Storage saver, and names albums that have fewer items in your list than Google counts, because photos that other people added to a shared album stay theirs.

Its last line starts with `Result:`. "Result: OK" means that every item on the list is in `DIR` with the right size, and then it exits with code 0. "Result: NOT COMPLETE" says how many are there and how to get the rest.

Know the limit of this check. The list comes from the same internal requests as the download. If Google changes them so that the list comes back short, the check cannot see the gap. So before you delete anything in Google Photos, open Immich and compare a few years and a few albums with Google Photos.

After the import, `python3 scripts/immich/import_library.py verify --dir DIR` checks that Immich holds every file. It ends with "Result: OK".

## The risks

Read these before you choose this way. Claude Code says them too, before you choose.

- **Google's terms.** Google's terms of service forbid reverse engineering its services and some kinds of automated access. The script repeats Google's internal requests from your own tab, so Google could see it as a breach of its terms.
- **Your whole Google Account.** Google can suspend or close an account that breaks its terms ([Google](https://support.google.com/accounts/answer/40039)). That would cut you off from Gmail, Google Drive, YouTube, and everything else that uses the account, not only Photos. No case of this was found for read-only tools that run in your own browser tab, but nobody can rule it out.
- **It can stop working any day.** The internal requests are not a public interface. When Google changes them, the script stops with a message, and you can switch to Takeout.
- **The console warning is real.** Browsers warn before you paste into the console, because scammers trick people into pasting code that takes over their accounts. Paste only the script that this kit's `prepare` copied for you, in the Google Photos tab it opened. Never paste code that someone sends you. Typing `allow pasting` switches the warning off in that browser profile, and browser sync can carry that to your other computers, until you reset DevTools as step 6 of [The steps](#the-steps) shows.
- **Storage saver items have no original.** Google kept only the smaller copy.
- **Locked Folder and Trash are not included.**

If you want no risk to your Google Account, use Google Takeout, Google's own export.

## Google Takeout, the official route

Google Takeout is Google's own export, at takeout.google.com ([Google](https://support.google.com/accounts/answer/3024190)). It carries no risk to your account. It costs you more time and more disk space:

- Google prepares the export first. That can take hours or days. Google sends you an email when it is ready.
- Your library comes as several zip archives, called parts. Each download link works for 7 days, and Google lets you download each archive 5 times.
- A photo in an album comes twice, once in its year folder and once in the album folder, so the parts can be bigger than your library. You need room for all parts, and about as much again where Immich keeps its files.
- The dates, places, and album names sit in separate JSON files next to the photos. [immich-go](https://github.com/simulot/immich-go), a community tool that [Immich's docs](https://github.com/immich-app/immich/blob/v3.3.5/docs/docs/features/command-line-interface.md) recommend for Takeout, reads those files and puts the photos into Immich with their dates and albums. Its authors ask you to keep a backup copy of your files.
- Takeout adds edited photos as extra files, with `-edited` in the name in English ([immich-go](https://github.com/simulot/immich-go/blob/v0.32.0/docs/misc/google-takeout.md)).
- There is no list from Google to check against, so the kit cannot report N of N. The check is weaker: read immich-go's report of what it found and uploaded, compare the item counts of a few albums in Immich with Google Photos, and open a few years.

The steps:

1. Claude Code runs:

   ```
   python3 scripts/google/google_photos.py takeout --to DIR
   ```

   It creates `DIR`, opens takeout.google.com in your browser, and prints these steps.
2. On takeout.google.com, sign in if Google asks, with the account that holds your photos. Click **Deselect all**, scroll down to **Google Photos**, and tick it. Click **Next step** at the bottom.
3. Choose **Send download link via email**, **Export once**, the file type **.zip**, and the largest file size, 50 GB, so there are fewer parts. immich-go reads .zip archives only. Click **Create export**.
4. When Google's email arrives, download every part. Google may ask for your password again. Type it there, in the browser, never in a chat.
5. Move every part into `DIR`. Keep them as they are, and do not unpack them.
6. Claude Code imports the parts into Immich:

   ```
   python3 scripts/google/google_photos.py takeout --to DIR --import
   ```

   It checks that no part is missing or cut off, and that Immich has room. It shows its plan and asks first. Claude Code adds `--yes` when you already said yes to the plan. Then it downloads immich-go v0.32.0 from GitHub, checks its SHA-256 checksum, keeps it in `.homecloud/tools` in your home folder, and runs it with the API key in an environment variable, never on the command line. immich-go creates your albums, includes photos whose JSON file is missing, leaves out what is in Google's trash, and skips files that Immich already has. If it stops, run the same command again.

   The import runs only into an Immich that holds no photos yet. When a file in the archives has the same name and time as a photo in Immich, but more bytes, immich-go uploads it and deletes the photo in Immich for good, past Immich's trash. If your photos are in both clouds, import the Takeout archives first.

Takeout leaves your photos in Google Photos, like Path G.

## After the move

Do these steps last, in this order. Start only when the check of your copy has passed: with Path G, `verify` reports N of N and the import ends with "Result: OK"; with Takeout, immich-go's report shows no errors and a few albums in Immich hold as many items as in Google Photos. Your second copy on another drive must have passed its check too. Homecloud never does these steps for you.

### 1. Back up your phone to Immich

The setup opens a page with QR codes and the steps for your phone, with one part for the iPhone and one for Android. [scripts/tailscale.md](../scripts/tailscale.md) explains each step.

On an Android phone:

1. Install **Tailscale** and **Immich** from Google Play. Sign in to Tailscale with the same account as on the computer. Sign in to Immich with the server address and email that the setup printed.
2. In Immich, tap the cloud icon at the top right. Under **Backup Albums**, tap **Select**. On Android, the albums are the folders on your phone. Tap **Camera** and any other album you want to back up. Go back and turn on **Enable Backup**.
3. When Android asks about your photos and videos, allow access to all of them.
4. Let Immich run in the background. At the bottom of the Backup screen, tap the link about notifications if you see it, and tap **Allow**. Then open the Android **Settings** > **Apps** > **Immich** > **Battery**, and choose **Unrestricted**. The menu names differ by phone maker. The battery link in Immich has a **Show me how** button that opens [dontkillmyapp.com](https://dontkillmyapp.com), a guide for each maker.

On an iPhone, install the same two apps from the App Store, and pick **Recents** under **Backup Albums**. Keep **Background App Refresh** on for Immich.

Then keep the app open on its Backup screen until **Remainder** shows 0. The app skips every photo that Immich already holds with exactly the same bytes. A photo whose file differs from the one that came from Google is uploaded again, and Immich then shows it twice.

### 2. Turn off backup in the Google Photos app

Do this on every phone and tablet that backs up to Google Photos, so new photos stop going to Google:

1. Open the Google Photos app, and tap your profile picture or initial at the top right.
2. Open the Photos settings, tap **Backup**, and turn off **Backup**.

Do not tap **Undo backup** on that screen. It deletes that device's backup from Google Photos ([Google](https://support.google.com/photos/answer/6193313)).

### 3. Free up space on the phone, if you need it

In the Immich app, **Free Up Space** removes photos and videos from the phone that Immich already holds with exactly the same bytes. Open it from your profile picture in the Immich app, or from its settings. You pick a cutoff date, and it keeps your favorites by default. It shows a preview before it removes anything. Read it before you confirm ([Immich](https://github.com/immich-app/immich/blob/v3.3.5/docs/docs/features/mobile-app.mdx)).

- On Android, the removed items go to the phone's trash. To get the space back, empty the trash in your gallery app. Keep folders of messaging apps, such as WhatsApp, under **Keep albums**. Immich warns that otherwise their chats show those photos blurry or missing.
- On an iPhone, the removed items go to **Recently Deleted** for 30 days. If iCloud Photos is also on, a delete on the iPhone also deletes the photo from iCloud, as [modes.md](modes.md#home-archive) explains.

### 4. Decide what happens to the photos in Google Photos

Your photos stay in Google Photos until you delete them. Homecloud never deletes anything there. They count toward your Google storage. The free 15 GB is shared by Gmail, Google Drive, and Google Photos ([Google](https://support.google.com/googleone/answer/9312312)). Photos and videos that you backed up in High quality before June 1, 2021 do not count ([Google](https://support.google.com/photos/answer/6220791)).

You have two choices:

- **Keep them in Google Photos**, with a plan that holds them, or within the free 15 GB.
- **Delete them yourself, when you are sure.** First turn off backup on every phone and tablet, as in step 2. Google says that a delete in Google Photos also deletes the same items on every device where backup is on ([Google](https://support.google.com/photos/answer/6128858)). Deleted items stay in the trash for 60 days. Use Immich for a few weeks before you delete.

### 5. Change or cancel your Google One plan

On a computer, open [one.google.com](https://one.google.com) and click **Settings**. Choose **Change membership plan**, or **Cancel membership**. On some screens, both are under **Manage membership**. If you pay through the App Store on an iPhone, cancel there: **Settings** > your name > **Subscriptions** ([Google](https://support.google.com/googleone/answer/9056360)). Your plan stays until the end of the billing period.

Before you drop to the free 15 GB, get under it. Google lists what happens while you are over the limit: Google Photos stops backing up, Google Drive takes no new files, and Gmail may stop sending and receiving email. If you stay over the limit for 2 years, Google may delete your content in Gmail, Google Drive, and Google Photos. Google says it contacts you at least 3 months before ([Google](https://support.google.com/googleone/answer/9312312)).

## Troubleshooting

[troubleshooting.md](troubleshooting.md#google-photos) has the fixes. The most common ones:

- The console does not let you paste: type the words that the warning shows, `allow pasting` in English, press Enter, and paste again. When the move is done, switch the warning back on: in DevTools, click the gear icon at the top right, and at the bottom of **Preferences** click **Restore defaults and reload**. This also resets any other DevTools setting you changed.
- The folder window says "Can't open this folder": the browser does not let a web page use that folder. Click **Choose a different folder** and pick `DIR`, the folder that `prepare` printed.
- In Brave, no folder window opens: turn on `brave://flags/#file-system-access-api` and relaunch Brave.
- The download stopped: do steps 1 to 3 again with the same `DIR`. The script skips what is done.
- The panel shows a problem with Google's requests: tell Claude Code what it says, and use [Google Takeout](#google-takeout-the-official-route).
