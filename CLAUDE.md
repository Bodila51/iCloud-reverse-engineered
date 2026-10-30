# Notes for Claude Code and other AI agents

This repository is Homecloud. It moves a person's iCloud Photos or Google Photos library to Immich on their own computer, which their iPhone or Android phone reaches through Tailscale.

- [PROMPT.md](PROMPT.md) is the procedure. Follow it step by step.
- [docs/two-paths.md](docs/two-paths.md) is the explanation of the two ways to move iCloud Photos: Path A, sign in to iCloud.com, and Path B, no sign-in.
- [docs/google-photos.md](docs/google-photos.md) is the explanation of Path G, Google Photos: a browser script that the person pastes into their own signed-in photos.google.com tab, with Google Takeout as the official route.

When you help a person run the kit, find out by yourself what you can: the system, the memory, the free disk space, the drives, what is installed, which Chromium browsers are installed, and on a Mac the Photos library. Explain the ways before you run anything that changes their computer. Use their language and plain words. Say what each path gains and costs, what the Path A session key opens, who can read it, and that it is needed only for the one-time move. For Path G, say its risks plainly, as docs/google-photos.md lists them. Ask at most two questions, in one message: where the photos are and which way, with your recommendation by the criteria in docs/two-paths.md and docs/google-photos.md, and whether this computer stays on at home. Never push everyone to one path.

Hard rules:

- Show the whole plan once, with the few things the person does, and get one yes before you install, create, start, or change anything. Ask again only before deleting data or a step that cannot be undone. Reading and checking need no question.
- Never ask for, type, store, or log the Apple Account password or a verification code. In Path A, the person runs `python3 scripts/icloud.py login` in their own Terminal or PowerShell window and types them there.
- Never open, print, copy, or upload the files in `~/.homecloud/session`, and never print a token.
- The person types their Immich password only in the window that `scripts/immich/bootstrap.py` opens, never in the chat. Never ask for the Immich API key in the chat, and never print, copy, or show it.
- Never delete or change anything in iCloud, in the Photos library on a Mac, or in the iCloud Photos folder on a PC.
- If Auto mode's safety check blocks a step, do not work around it. Tell the person what it blocked and give them the command for their own Terminal or PowerShell window.
- Before any download, compare the free disk space with the size of the library. docs/two-paths.md and docs/google-photos.md list what each path needs.
- After a Path A move, make sure the session is signed out with `python3 scripts/icloud.py logout`, unless the person chose to keep it.
- The person turns off iCloud Photos and changes the plan themselves, last, after the check reports N of N and a second copy has passed its check.

Hard rules for Google Photos (Path G):

- Never ask for, type, store, or log a Google password or a verification code. If Google asks the person to sign in, they do it on Google's own page in their browser.
- Never read, export, copy, or store Google cookies or tokens, and never open the browser's profile files.
- Never run the browser script anywhere except the person's own signed-in photos.google.com tab, where the person pastes it. Never use another browser profile, a separate or headless browser, or browser automation for it. Never give the person any other code to paste into a browser console.
- Nothing in Google Photos is deleted or changed by the kit. The person turns off backup in the Google Photos app, deletes, and changes or cancels the Google One plan themselves, last, after `python3 scripts/google/google_photos.py verify --to DIR` reports N of N and a second copy has passed its check.
- State these risks plainly before the person chooses: Google's terms forbid reverse engineering and some automated access; Google can restrict or close the whole Google Account, with Gmail, Drive, and YouTube, although no case was found for read-only tools that run in the person's own tab; the internal requests can change any day; the console warning exists because scammers use the console, so the person pastes only this kit's script; typing `allow pasting` switches that warning off in the browser profile until the person resets DevTools, which they do after the move; items saved in Storage saver have no original; Locked Folder and Trash are not included.
- If the browser script stops because Google's requests or the download failed, tell the person what it said and offer Google Takeout with `python3 scripts/google/google_photos.py takeout --to DIR`. Do not edit the script to get past the failure.
