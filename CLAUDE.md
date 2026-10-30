# Notes for Claude Code and other AI agents

This repository is Homecloud. It moves a person's iCloud Photos library to Immich on their own computer, which their iPhone reaches through Tailscale.

- [PROMPT.md](PROMPT.md) is the procedure. Follow it step by step.
- [docs/two-paths.md](docs/two-paths.md) is the explanation of the two ways to move the photos: Path A, sign in to iCloud.com, and Path B, no sign-in.

When you help a person run the kit, find out by yourself what you can: the system, the memory, the free disk space, the drives, what is installed, and on a Mac the Photos library. Explain both paths before you run anything that changes their computer. Use their language and plain words. Say what each path gains and costs, what the Path A session key opens, who can read it, and that it is needed only for the one-time move. Ask at most two questions: which path, with your recommendation by the criteria in docs/two-paths.md, and whether this computer stays on at home. Never push everyone to one path.

Hard rules:

- Show the whole plan once, with the few things the person does, and get one yes before you install, create, start, or change anything. Ask again only before deleting data or a step that cannot be undone. Reading and checking need no question.
- Never ask for, type, store, or log the Apple Account password or a verification code. In Path A, the person runs `python3 scripts/icloud.py login` in their own Terminal or PowerShell window and types them there.
- Never open, print, copy, or upload the files in `~/.homecloud/session`, and never print a token.
- The person types their Immich password only in the window that `scripts/immich/bootstrap.py` opens, never in the chat. Never ask for the Immich API key in the chat, and never print, copy, or show it.
- Never delete or change anything in iCloud, in the Photos library on a Mac, or in the iCloud Photos folder on a PC.
- If Auto mode's safety check blocks a step, do not work around it. Tell the person what it blocked and give them the command for their own Terminal or PowerShell window.
- Before any download, compare the free disk space with the size of the library. docs/two-paths.md lists what each path needs.
- After a Path A move, make sure the session is signed out with `python3 scripts/icloud.py logout`, unless the person chose to keep it.
- The person turns off iCloud Photos and changes the plan themselves, last, after the check reports N of N and a second copy has passed its check.
