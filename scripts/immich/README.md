# Immich on your computer

These scripts install Immich, an open-source photo app that you run yourself, with Docker Desktop on a Mac or a Windows PC. Then they copy your download folder `DIR` into Immich and check that Immich holds every file. They follow Immich's own install guide. They never read or change iCloud, Google Photos, the Photos app or the files in `DIR`.

| File | What it does |
|---|---|
| `setup_mac.sh`, `setup_windows.ps1` | Install and start Immich. They show the plan and ask first. |
| `bootstrap.py` | Creates your Immich admin account and an API key for the import, and saves the key. You type the password in a window, never in a chat. |
| `import_library.py` | Copies `DIR` into Immich, sets favorites, albums and archived items, and checks the result. |
| `make_manifest.py` | Lists the photos and videos in a `DIR` that has no manifest, such as a copy from iCloud for Windows. |
| `undo_mac.sh`, `undo_windows.ps1` | Remove a test install after you type `DELETE`. |

Run every command from the kit folder. On Windows, type `py` where this page says `python3`.

## The one-command way

One command sets up Immich and everything around it. On a Mac:

```
bash scripts/setup.sh --yes
```

On Windows:

```
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1 -Yes
```

Without `--yes` (`-Yes` on Windows), the setup shows its plan and asks once before it changes anything. Claude Code adds `--yes` after you say yes to its own plan. For Immich, the setup does this:

1. It installs Docker Desktop if it is missing, and starts it. On a Mac, macOS asks for your Mac password to install it, and the setup gives Docker Desktop more memory when Immich needs it. On Windows, it first installs WSL 2 if Docker Desktop needs it. Windows asks for permission, and may need one restart: then run the same command again.
2. It runs `setup_mac.sh --yes`, or `setup_windows.ps1 -Yes` on Windows, which installs and starts Immich. Machine learning is on or off as [Machine learning on an 8 GB computer](#machine-learning-on-an-8-gb-computer) describes.
3. It runs `bootstrap.py`, which sets up Immich without its web page. A window asks you to choose an email and a password for your Immich admin account. It turns machine learning off in Immich's settings too, when the setup turned the service off. It creates an API key named "Homecloud" with all permissions and saves it in `~/.homecloud/immich-api-key`, which only your account can read. It prints your email, and never the password or the key.

Then it connects the computer to Tailscale and opens a page for your phone, with one part for the iPhone and one for Android, as [../tailscale.md](../tailscale.md) describes. It ends with `Result: OK`. If it stops, its last line says what to do. A second run skips what is done.

Then import your photos. The import reads the list of files in `DIR/.homecloud/manifest.jsonl`, which the copy step wrote: `scripts/icloud.py`, `scripts/mac/export_photos.py`, `scripts/google/google_photos.py verify`, or `make_manifest.py`. The key is already saved, so you need no `save-key`. `check` only reads and shows the plan. `all` asks first. Claude Code adds `--yes` after you agreed to its plan.

```
python3 scripts/immich/import_library.py check --dir DIR
python3 scripts/immich/import_library.py all --dir DIR
```

Go on only when it ends with `Result: OK` and exit code 0. [What the import does](#what-the-import-does) explains each part. Last, in Docker Desktop, open **Settings** > **General** and turn on **Start Docker Desktop when you sign in to your computer**. It is off by default, and without it Immich stays down after a restart until you open Docker Desktop.

### Only the Immich part

To set up Immich without Tailscale and the phone page, run these two on a Mac:

```
bash scripts/immich/setup_mac.sh --yes
python3 scripts/immich/bootstrap.py --url http://localhost:2283
```

On Windows, run `setup_windows.ps1 -Yes`, then `py scripts/immich/bootstrap.py --url http://localhost:2283` in your own PowerShell window. It asks for the email and the password right there in PowerShell, and nothing shows while you type the password.

- If the setup turned machine learning off, add `--ml off` to `bootstrap.py`.
- If Immich already has an account, `bootstrap.py` asks for that account's email and password. It makes a new key only if the saved key is missing or no longer works.
- A second run with a working key changes nothing.

## By hand

These are the same steps without `setup.sh` and `bootstrap.py`. Use them if a script stops, or to see what each step does.

### Before you start

- **Docker Desktop.** Download it from Docker's install page for [Mac](https://docs.docker.com/desktop/setup/install/mac-install/) or [Windows](https://docs.docker.com/desktop/setup/install/windows-install/). On a Mac, open `Docker.dmg`, drag Docker to Applications, open it and accept the agreement. Docker Desktop is free for personal use. The setup script checks for it and prints these links if it is missing.
- **Memory.** Immich asks for at least 6 GB of RAM, and 8 GB is better. Docker Desktop gets half of the computer's memory by default, so an 8 GB computer gives Docker about 4 GB. Then the setup turns machine learning off. See [Machine learning on an 8 GB computer](#machine-learning-on-an-8-gb-computer).
- **Disk space.** The import copies every file into Immich's library folder. So you need room for `DIR` twice, plus about a fifth for thumbnails and video previews. The database takes 1 to 3 GB. It must stay on the computer's own disk, never on a network share.
- **Where Immich stands.** Immich recommends Linux. Its docs say other systems "tend to provide a poor Docker experience and are strongly discouraged", and that its team can help less there. It does run with Docker Desktop on a Mac or Windows, and these scripts follow Immich's steps for that.

### Install on a Mac

1. Run the setup. It checks Docker, shows the plan and asks before it changes anything.

   ```
   bash scripts/immich/setup_mac.sh
   ```

   Without a terminal to ask in, as when Claude Code runs it, the script shows the plan and stops. Add `--yes` after you agree.

   The setup puts Immich in `~/immich-app`: `docker-compose.yml`, `.env`, the photo library in `library`, and the database in `postgres`. It writes a random database password into `.env`, which only your account can read. The first start downloads Immich's programs, a few gigabytes, so it can take several minutes. At the end it prints the addresses and the next steps.

   `bootstrap.py` does steps 2, 3, 5 and 6 below for you, as [The one-command way](#the-one-command-way) describes.

2. Open <http://localhost:2283>, click **Getting Started** and create your admin account. Use a strong password, and not your Apple or Google password.
3. If the setup turned machine learning off, open **Administration** > **Settings** > **Machine Learning Settings** and turn it off there too. Otherwise Immich queues machine learning jobs that fail for every photo.
4. In Docker Desktop, open **Settings** > **General** and turn on **Start Docker Desktop when you sign in to your computer**. It is off by default.
5. Make an API key. In Immich, click your initials at the top right, then **Account Settings** > **API Keys** > **New API Key**. Tick **Select all**, click **Create** and copy the key. Immich shows it only once.
6. Save the key in your own Terminal window, never in the chat. Paste it when the script asks. It stays hidden as you paste.

   ```
   python3 scripts/immich/import_library.py save-key
   ```

   The key goes into `~/.homecloud/immich-api-key`, which only your account can read.

7. Check the plan. This only reads.

   ```
   python3 scripts/immich/import_library.py check --dir DIR
   ```

8. Import. `all` uploads, sets favorites and albums, then checks. It asks first. Add `--yes` when you already agreed.

   ```
   python3 scripts/immich/import_library.py all --dir DIR
   ```

   Go on only when it ends with `Result: OK` and exit code 0.

9. Set up Tailscale and the phone app with [../tailscale.md](../tailscale.md). To keep the computer awake, use [../always_on/](../always_on/).

### Install on Windows

The steps are the same as on a Mac, with these commands:

```
powershell -ExecutionPolicy Bypass -File scripts/immich/setup_windows.ps1
py scripts/immich/import_library.py save-key
py scripts/immich/import_library.py check --dir DIR
py scripts/immich/import_library.py all --dir DIR
```

Differences on Windows:

- Immich goes into `$env:USERPROFILE\immich-app`. Keep it and `DIR` out of OneDrive.
- Immich's Windows note says Postgres does not work on NTFS folders. So the setup puts the database in a Docker volume named `pgdata`: the line `DB_DATA_LOCATION=pgdata` in `.env`, and `pgdata:` under `volumes:` at the end of `docker-compose.yml`. Keep both if you ever replace these files.
- If Windows asks whether Docker Desktop may use the network, allow it on private networks.

## What the import does

- `upload` runs the official Immich command-line tool from its Docker image. It reads `DIR` read-only and uploads every photo and video. Immich skips files it already has, so you can run it again after a break. It never uses the tool's delete option.
- `apply` reads `DIR/.homecloud/manifest.jsonl`. It marks favorites, creates missing albums by name and fills them, and archives the photos you hid in Apple Photos or archived in Google Photos. Add `--hidden keep` to leave them in the timeline. The import's own messages say "hidden in iCloud" for both. Running it again changes nothing more.
- `verify` only reads. It reports "Immich holds N of N files", then checks favorites, archived photos and albums. Exit code 0 means all is complete. Exit code 3 means every file is in Immich, but run `apply` again. Exit code 1 means files are missing, and it names them.

It finds each file in Immich by a checksum of its content, so the matching does not depend on file names.

## Why the import uploads instead of using an External Library

The import uploads a copy of each file, because Immich and the phone app then know each photo by a checksum of its content. The phone app skips every photo that Immich already has, and the kit can find each file again to set favorites and albums and to check the result. An External Library would read the files where they are and save disk space, but Immich knows those files by their path: the phone app would upload those photos again, and moving or renaming a file in `DIR` would lose its albums and favorites.

### The other way: an External Library

Use this only if the computer has no room for a second copy, and only if you will not back up the same photos from the phone app. These are the steps from Immich's External Library guide:

1. In `docker-compose.yml`, under `immich-server:`, add a line under `volumes:`. The `:ro` at the end keeps Immich from changing the files.

   ```
         - /Users/you/DIR:/mnt/homecloud:ro
   ```

   On Windows, write the folder with forward slashes and quotes: `- "C:/Users/you/DIR:/mnt/homecloud:ro"`. On a Mac, `DIR` must be under `/Users` or `/Volumes`, which Docker Desktop shares by default.

2. In the app folder, run `docker compose up -d`.
3. In Immich, open **Administration** > **External Libraries** > **Create Library**, and choose yourself as the owner.
4. Click **Add** in the Folder section and enter `/mnt/homecloud`.
5. Click the menu with three dots and choose **Scan New Library Files**.

What you give up with an External Library:

- `apply` and `verify` cannot find the files, so you set favorites and albums by hand, and nothing checks the count.
- The phone app uploads the photos on your phone a second time, because Immich knows External Library files by their path, not their content. Free Up Space in the app never offers them.
- Moving or renaming files in `DIR` makes them new items in Immich, without the albums and favorites you set.
- `DIR` must stay connected and in place. If a file disappears, Immich moves its item to the trash at the next scan.

## Machine learning on an 8 GB computer

Immich uses machine learning for some search features. It needs memory, so the setup decides with `--ml auto` (on Windows `-Ml auto`): if Docker Desktop has less than about 5.5 GB, it comments out the `immich-machine-learning` service in `docker-compose.yml` and marks those lines with `#ML-OFF#`. Immich's FAQ describes this way to stop the service.

What you lose:

- Smart search, which finds photos by what is in them, like "dog on a beach".
- People: faces are not found or grouped.
- Search for text in photos.
- Duplicate detection.
- Most of the Explore page. Immich's FAQ warns of "a poor experience for searching and the 'Explore' page".

What still works: the timeline, albums, favorites, the map, search by file name, date, place and camera, sharing, and the phone backup.

To turn it on later, for example on a computer with more memory:

```
bash scripts/immich/setup_mac.sh --ml on
```

On Windows, run `setup_windows.ps1 -Ml on`. Then turn machine learning on in **Administration** > **Settings** > **Machine Learning Settings**. Last, open **Administration** > **Job Queues** and run Smart Search and Face Detection for the missing photos. This takes a long time for a big library.

To keep it on but lighter, Immich's FAQ suggests these:

- Under **Administration** > **Settings** > **Job Settings**, lower the concurrency for Smart Search and Face Detection to 1.
- Under **Machine Learning Settings** > **Facial Recognition**, choose the smaller model `buffalo_s`. Then run Face Detection again for all photos.
- Add a memory limit to the `immich-machine-learning` service in `docker-compose.yml`. Immich warns that a limit set too low makes the container stop.
- Give Docker Desktop more memory under **Settings** > **Resources** > **Advanced** > **Memory limit**. This leaves less for everything else on the computer.

## Live Photos and the counts

A Live Photo is two files: a photo and a short video. The manifest lists both, the video with the kind `live_video`. Immich stores each file as its own item. When Immich reads the files' metadata, it links the video to its photo and hides the video. The timeline then shows one Live Photo.

Immich's own photo and video counts leave the hidden video parts out, so they are lower than the manifest's count. `verify` counts files, so each video part counts once, like any other file. It also reports how many video parts are linked to their photo. Linking happens while Immich reads metadata, which can take a while after a large import. Until then, those parts show as separate videos. Run `verify` again later.

## After a restart

- Immich's compose file has `restart: always`, so its containers start whenever Docker starts.
- Docker Desktop starts when you sign in, but only if **Start Docker Desktop when you sign in to your computer** is on.
- So after a restart, a power cut or an update, Immich stays down until someone signs in. A Mac with FileVault on waits at the password screen and cannot sign in by itself.

## Backups and updates

- Immich saves a copy of its database every night at 2:00, if the computer is on, in `backups` inside the library folder. It keeps the last 14. The database copy holds no photos, so copy the whole library folder to your second drive every month.
- To update Immich, read the release notes for breaking changes first. Then run `docker compose pull` and `docker compose up -d` in the app folder, as Immich's upgrade guide says.

## Remove a test install

Run this in your own Terminal window. It lists everything it would remove, with sizes, and removes nothing until you type `DELETE`.

```
bash scripts/immich/undo_mac.sh
```

On Windows: `powershell -ExecutionPolicy Bypass -File scripts/immich/undo_windows.ps1`.

It removes Immich's containers, programs and folder, including the photo library inside it. `--dir DIR` (Windows: `-Dir DIR`) also deletes the download folder. Use that only for a test folder, never for your only copy of your photos. It never touches iCloud, Google Photos, your phone or the Photos app. At the end it prints how to uninstall Docker Desktop and Tailscale by hand.

## Sources

- Immich install with Docker Compose: <https://docs.immich.app/install/docker-compose>
- Immich requirements, including the Windows note on Postgres: <https://docs.immich.app/install/requirements>
- Immich FAQ, machine learning and lower CPU and RAM use: <https://docs.immich.app/FAQ>
- Immich post-install steps: <https://docs.immich.app/install/post-install>
- Immich command-line tool: <https://docs.immich.app/features/command-line-interface>
- Immich External Libraries: <https://docs.immich.app/features/libraries> and <https://docs.immich.app/guides/external-library>
- Immich mobile backup and checksums: <https://docs.immich.app/features/mobile-backup>
- Immich backup and restore: <https://docs.immich.app/administration/backup-and-restore>
- Immich upgrading: <https://docs.immich.app/install/upgrading>
- Immich source, external files get a path checksum (`sha1-path`): `server/src/enum.ts` in <https://github.com/immich-app/immich>
- Docker Desktop settings (memory limit, start at sign-in): <https://docs.docker.com/desktop/settings-and-maintenance/settings/>
- Docker restart policies: <https://docs.docker.com/engine/containers/start-containers-automatically/>
