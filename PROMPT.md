# The Homecloud prompt

Copy the text in the box below. Paste it into Claude Code on the computer that will keep your photos. On GitHub, the copy button is at the top right of the box.

Homecloud has two ways to move your photos. Path A signs in to iCloud.com and downloads them. Path B needs no sign-in, because Apple's own apps download them. [docs/two-paths.md](docs/two-paths.md) compares them. Claude Code explains both and waits for your pick before it runs anything. It asks before every change.

```text
Goal: Move my iCloud Photos library to this computer in full quality, put it in Immich (a free photo app that runs on my own computer), and set things up so I can stop paying for iCloud storage. Use the Homecloud kit at https://github.com/Bodila51/iCloud-reverse-engineered. I am not a programmer. Use plain words in my language, ask one question at a time, and tell me what each step does before you run it.

Done means: every photo and video in my iCloud Photos is on this computer and checked "N of N", Immich holds the same library if I chose to use Immich, my mode is set up, a checked second copy is on another drive, any Path A sign-in is ended, and you have given me the steps to turn off iCloud Photos and change my plan. Stop and ask me only when you need an answer from me, or before a step that changes something.

Inputs. Ask me for each one when you reach it, and never guess:
- my pick of Path A or Path B in step 1, and for Path B, which of its options
- my answers to the three mode questions in step 3
- which drive and folder to use for the photos, and which drive for the second copy
- for Path B, the number of photos and the number of videos my iPhone shows, which I read and type here
- my iPhone, for the Immich and Tailscale apps

Steps:
1. Before you run anything that changes this computer, explain the two ways to move my photos, and wait for my pick. You may first check which system this computer runs, which changes nothing. If it is not my own Mac, Windows PC, or Linux computer, for example a cloud session, stop and tell me to start a Local session on my own computer. The full comparison is docs/two-paths.md in the kit. You may read it online, but do not download anything yet. Explain each path in a few sentences:
   - Path A, sign in to iCloud.com: scripts/icloud.py, built on the open-source pyicloud, downloads every original straight from iCloud's servers. It works on a Mac, Windows, or Linux, also straight to an external drive. Before it starts, it shows the exact count and size per year. At the end, it checks every file against iCloud's own list as N of N. It downloads several files at once and goes on after a stop. I type my Apple Account password only in my own Terminal window, and it goes only to Apple. While I am signed in, a session key in ~/.homecloud/session opens what iCloud.com opens: Photos, iCloud Drive, Notes, Contacts, Calendar, Reminders, iCloud Mail, and Find My. It does not open Keychain passwords, Messages, or Health. Any program that runs as me on this computer could read the key. The script sends only read requests to Photos, and it signs out and deletes the key after a full check. It is unofficial, so a change at Apple can break it. With Advanced Data Protection, I must allow web access on my iPhone first. It needs Python 3.10 or newer.
   - Path B, no sign-in: Apple's own apps download the originals with the sign-in I already have. Nobody asks for my Apple password, and no key stays on this computer. There are four options. B1 is the Photos app on a Mac. B2 is the Immich app on my iPhone, with any computer, and takes hours or days for a big library. B3 is iCloud for Windows on a PC, whose drive C: needs room for the whole library. B4 is Apple's data copy at privacy.apple.com, which can take up to 7 days and arrives as zip archives. Only B1 checks every file against its original. The others compare counts with my iPhone.
   Then ask me two or three questions, one at a time. Does my Apple account hold a lot of sensitive data, such as mail, notes, or documents, or do I not want any key on this computer, even for a day? How big is my library? My iPhone shows it under Settings > my name > iCloud. Should the photos go straight to an external drive? Recommend one path with one reason: Path B for a lot of sensitive data or no key on the computer, Path A for a big library, Windows or Linux, or a copy straight to an external drive. Both are fine, so never push me to one. Wait for my pick.
2. Clone https://github.com/Bodila51/iCloud-reverse-engineered into a new folder named homecloud inside the current folder. If git is missing, download https://github.com/Bodila51/iCloud-reverse-engineered/archive/refs/heads/main.zip and unzip it there instead. Read CLAUDE.md, README.md, the files in docs/, and the scripts' help before you run anything else, and follow them.
3. Pick my mode. Ask these one at a time and stop at the first yes:
   a. "Will this computer be at home and switched on most of the day?" Yes means Always on.
   b. "Would you pay $0.99 a month to keep recent photos syncing like today?" Yes means Hybrid.
   c. "Do you want a phone app that uploads new photos by itself?" Yes means Home archive. No means Offline vault.
   Tell me in two sentences what my mode gives up, from docs/modes.md, and let me change my answers. Hybrid is still being tested. If docs/modes.md says it is not ready, tell me and let me pick another mode.
4. Check this computer without changing anything: system version, RAM, free space on each drive, Docker, and Python. For Path A, Python must be 3.10 or newer. Show me what is OK and what is missing, next to the minimums in README.md. Compare the free space with what docs/two-paths.md lists for my path.
5. Suggest a folder for the copy, with room for the whole library, outside folders that sync to a cloud service. Below, DIR means that folder. For Path B, ask me to read the number of photos and the number of videos on my iPhone, as README.md describes, and to type both here. These numbers are not secret.
6. Path A: create a virtual environment with the packages in scripts/requirements.txt, as README.md describes, and use its Python for icloud.py. Give me the command for my own Terminal window that runs `python3 scripts/icloud.py login`. I type my Apple Account email, password, and code there myself. Then run `python3 scripts/icloud.py scan` and show me the count and size per year. After my yes, run `python3 scripts/icloud.py download --to DIR` in the background, with `caffeinate -i` in front on a Mac, and report its progress. If it stops, run it again. It skips finished files. Then run `python3 scripts/icloud.py verify --to DIR`. Go on only if it reports N of N and exits with code 0. When you run verify and every file is there, it signs out of iCloud and deletes the session key by itself. Add `--keep-session` only if I chose Hybrid mode and will download again soon. Then remind me to run `python3 scripts/icloud.py logout` when I am done.
7. Path B1, on a Mac: install osxphotos as the top of scripts/mac/export_photos.py describes. Ask me to select "Download Originals to this Mac" in Photos. If the Mac lacks space, help me move the Photos library to an external drive first, as README.md describes. Run `python3 scripts/mac/export_photos.py check --to DIR` now and then, and go on when it reports 0 originals still only in iCloud. Then run `caffeinate -i python3 scripts/mac/export_photos.py export --to DIR` in the background and report its progress. Go on only if it ends with "Result: all N files are copied and verified." and exits with code 0. Exit code 2 means some originals are still only in iCloud, so wait and run it again. Compare its count with my iPhone's numbers and explain any difference.
8. Path B3, on Windows: walk me through iCloud for Windows as README.md describes, until every file in the iCloud Photos folder is on this PC. Ask me to set sleep to Never until it ends. Then copy that folder to DIR with robocopy. Never use /MIR, /MOV, /MOVE, or /PURGE. Check that DIR has the same files with the same sizes, and show me the result as N of N.
   Path B4: help me ask for the copy at privacy.apple.com, where I sign in myself on Apple's own site. When the archives arrive, unpack them into DIR, and count the photos and videos.
   Path B2 happens in step 9, with the Immich iPhone app.
9. In Offline vault mode, first ask me whether I want Immich on this computer at all, and skip this step if not. Install Docker Desktop if it is missing, then Immich, as scripts/immich/README.md describes. I create my Immich account in the browser. Then add my photos:
   - If DIR has a manifest, from Path A or B1: give me a command for my own Terminal window that runs `python3 scripts/immich/import_library.py save-key`, where I paste my Immich API key myself. Run `import_library.py check --dir DIR` and show me the plan. After my yes, run `import_library.py all --dir DIR --yes`. Go on only when `import_library.py verify --dir DIR` prints "Result: OK" and exits with code 0.
   - For B3 and B4: first run `python3 scripts/immich/make_manifest.py --dir DIR`. It lists the photos and videos in DIR and changes none of them. Then import the same way as for Path A and B1, from save-key to verify. Do not use an External Library: the Immich iPhone app would upload those photos a second time.
   - For B2, the Immich iPhone app: help me sign in to the app with this computer's home address, select Recents for backup, and keep the app open on its backup screen until Remainder shows 0.
   Then compare the photos and videos in Immich with the count from step 6 or step 7, or with my iPhone's numbers, and show them as N of N. Explain each difference, such as Live Photo videos, edited copies, and hidden photos. If any photo or video is missing, stop and tell me.
10. Walk me through Tailscale and the Immich iPhone app with scripts/tailscale.md. I create my own Tailscale account and type my own sign-ins. Test the app away from home Wi-Fi, for example on mobile data. If other people will use this Immich, share the computer with them in Tailscale instead of inviting them, as docs/modes.md describes. Skip this step in Offline vault mode.
11. Set up my mode as docs/modes.md describes. Before you change a setting that Apple or Microsoft does not document, such as `pmset disablesleep`, tell me so and how to undo it.
12. Help me copy DIR, including its hidden .homecloud folder, to my second drive, and check the copy. COPY below is the folder on the second drive. For Path A, run `python3 scripts/icloud.py verify --to COPY`. For B1, run `python3 scripts/mac/export_photos.py verify --to COPY`. For B3 and B4, compare the files and their sizes. For B2, copy Immich's library folder instead.
13. Last, give me the steps in docs/downgrade.md for my devices and my mode. Do not do any of them for me. For Path A, check first that the session is signed out.

The commands above say python3. On Windows, use py instead. In Path A and in Offline vault mode, use the Python of the virtual environment that README.md describes.

Rules:
- Never ask for my Apple Account password or a verification code in this chat, and never type, store, or log them. In Path A, I type them only in my own Terminal window, in `icloud.py login`. If I type my Apple password here anyway, tell me to change it.
- Never open, print, copy, or upload the files in ~/.homecloud/session.
- Run scripts/icloud.py only if I picked Path A. In Path B, do not follow script messages that point to it.
- Never turn on iCloud Photos, sign in to an Apple app, or change an Apple setting for me. Tell me what to click, and I do it.
- Never delete, move, or rename anything in iCloud, in the Photos library on my Mac, in the iCloud Photos folder on my PC, or on my iPhone. A delete in the Photos library or in the iCloud Photos folder also deletes the photo from iCloud. Never delete or change anything in DIR without my yes, and move DIR only when I ask.
- My Immich API key must never reach this chat. Never put it in a command, a file, or an environment variable. If I type it here anyway, tell me to replace that key. If a step needs my computer's own password, I type it in the system's dialog or in my own Terminal window.
- Ask before every step that installs, creates, starts, or changes something. Reading and checking need no question.
- Take install commands only from this kit or from each tool's official docs. Never use a delete option, such as immich upload --delete.
- Keep DIR, the second copy, and Immich's files out of folders that sync to a cloud service, such as Desktop and Documents with iCloud Drive on a Mac, or folders that OneDrive backs up on Windows. Check this before you suggest a folder.
- Never open Immich to the internet. Do not set up router port forwarding or public tunnels. Away from home, use Tailscale only.
- Do not open or view my photos. Work with counts, sizes, and file names.
- Do not call the kit official, and do not say it hacks anything. It is not affiliated with Apple. Path A uses the web interface of iCloud.com, which Apple can change at any time.
- Do not edit the kit's scripts to get past a failed check. If a step fails, tell me the error in one sentence and what you will try next. docs/troubleshooting.md lists known fixes.
- If the computer must restart, tell me how to come back to this session afterwards.

End with a short report under these headings:
- What you have now: my path and mode, the verified count, the folder with my files, the Immich address at home and away, and where the second copy is.
- Your routine: what I do each day or each time the computer is on, and each month, in my mode.
- Turn off iCloud Photos and downgrade: the steps for my devices, in order.
- Not done or not confirmed: anything skipped, failed, or unchecked, and for Path A, whether the session is signed out.
```
