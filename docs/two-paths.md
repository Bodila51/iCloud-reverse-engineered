# Two ways to move your photos

Homecloud moves your iCloud Photos library to Immich on your own computer. Your iPhone then reaches it through Tailscale. There are two ways to get the old library out of iCloud, and both are fine choices. They end in the same place and differ only in how the photos get there.

Claude Code explains both ways, recommends one with its reason, and asks you to pick. It runs nothing that changes your computer until you say yes to its plan.

## Path A: sign in to iCloud.com

`scripts/icloud.py` downloads every original from iCloud's servers, the same way the iCloud.com website loads your photos. It is built on [pyicloud](https://github.com/timlaing/pyicloud), an open-source Python client for iCloud.com. It is not made or endorsed by Apple.

What you gain:

- It runs on any computer: Mac, Windows, or Linux. It needs no Apple app. Your iPhone is needed only for the sign-in code.
- It can download straight to an external drive.
- Before it starts, `scan` shows the exact number of photos and videos, and their size per year.
- At the end, `verify` checks every file against iCloud's own list of your photos and reports N of N.
- It downloads several files at once. After a stop, it goes on where it left off.

What it costs:

- You type your Apple Account password once, in your own Terminal or PowerShell window, never in a chat. The script sends it only to Apple. It never saves the password, shows it, or writes it to a log.
- While you are signed in, a session key stays on the computer. The next sections say what it opens and who can read it.
- It is unofficial. When Apple changes iCloud.com, Path A can stop working until someone updates the kit.
- With Advanced Data Protection on, you first allow web access on your iPhone. On iOS 26.4 or later, open **Settings** > your name > **iCloud** > **iCloud.com**, and turn on **Allow Data Access**. On earlier iOS, open **Settings** > your name > **iCloud**, and turn on **Access iCloud Data on the Web**.
- It needs Python 3.10 or newer, because pyicloud does.

### What the session key opens

After you sign in, the script keeps a session key in the folder `.homecloud/session` in your home folder. Your password is not in it. While the key exists, it opens roughly what you see when you sign in to iCloud.com in a browser:

- **Photos.** The key allows what iCloud.com allows, which includes deleting. The script itself sends only read requests to Photos.
- **iCloud Drive, Notes, Contacts, Calendar, and Reminders.**
- **iCloud Mail.** A web session can open it. The script never requests it.
- **Find My**, which shows where your devices are.

The key does not open your Keychain passwords, Messages, or Health.

With Advanced Data Protection on, the key reaches your end-to-end encrypted data, such as Photos, iCloud Drive, and Notes, only after you allow web access on your iPhone.

### Who can read the key

Only your user account can read the folder. That still includes:

- you,
- any program that runs as your user on that computer, including an AI coding agent such as Claude Code,
- anyone who uses the computer while it is unlocked,
- backups of the computer, such as Time Machine.

The authors of pyicloud do not get the key. The code runs on your computer and talks only to Apple. Tailscale and Immich never see the key either. The kit's rules in [CLAUDE.md](../CLAUDE.md) tell Claude Code never to open or print these files.

### How to end it

You need the key only for the one-time move. After the move, new photos reach your computer from the iPhone, through the Immich app.

When `verify` reports N of N, it signs you out. In your own Terminal window it asks first. When Claude Code runs it, it signs out by itself. You can also sign out at any time:

```
python3 scripts/icloud.py logout
```

`logout` ends this computer's session at Apple and deletes the folder `.homecloud/session`. It does not touch your photos. Add `--keep-session` to `verify` only if you will download again soon, for example in Hybrid mode, and run `logout` when you are done.

When you no longer need the kit, also delete the whole `.homecloud` folder in your home folder.

### Why the repository is called iCloud-reverse-engineered

Apple offers no public way for other programs to download your iCloud Photos. The authors of pyicloud studied how iCloud.com talks to Apple's servers, including the sign-in, the two-factor code, and the requests for your photos, and rebuilt that in Python. That is what reverse-engineered means here. It is also why Path A can break without warning when Apple changes iCloud.com.

Path B is not reverse-engineered. Apple's own apps do the download. The Immich app reads your photos through PhotoKit, Apple's photo framework on the iPhone, like any photo app.

## Path B: no sign-in

Apple's own apps download the originals with the sign-in you already have on your devices. Nobody asks for your Apple password, and no key stays on the computer. Pick one of four options:

- **B1, the Photos app on a Mac.** Photos downloads every original when you select **Download Originals to this Mac**. Then `scripts/mac/export_photos.py` copies them out and compares each file with its original in the Photos library, byte for byte. Your albums and favorites come along.
- **B2, the Immich app on your iPhone, with any computer.** The app uploads your whole library to Immich. iOS fetches each original that lives only in iCloud. A big library takes hours or days. Keep the app open on its Backup screen, with the iPhone charging and on Wi-Fi.
- **B3, iCloud for Windows on a PC.** It downloads every original to the iCloud Photos folder on drive C:, which needs room for your whole library. If the app is new on your PC, it asks you to sign in once, in Apple's own window. Large downloads can stall.
- **B4, Apple's data copy.** At [privacy.apple.com](https://privacy.apple.com), you can ask Apple for a copy of your photos. You sign in there yourself, on Apple's own site. It is official, but it can take up to 7 days, and it arrives as zip archives that you download and unpack. No kit script reads these archives yet.

What it costs:

- A big library takes longer, and the move depends on Apple's apps finishing.
- You don't see the exact size before you start. Your iPhone shows the total under **Settings** > your name > **iCloud**.
- Only B1 compares every file with its original. With B2, B3, and B4, the check compares the number of photos and videos with the numbers on your iPhone.

[README.md](../README.md#path-b-no-sign-in) has the steps for each option.

## Side by side

| | Path A: sign in to iCloud.com | Path B: no sign-in |
|---|---|---|
| Your Apple password | You type it once, in your own Terminal window. It goes only to Apple. | Nobody asks for it. With B3, Apple's app asks once if it is new on your PC. |
| How the photos travel | The computer downloads them from iCloud's servers. | Apple's apps download them: Photos, iCloud for Windows, or iOS for the Immich app. |
| Built on | pyicloud, an unofficial client of iCloud.com | Apple's own apps, and PhotoKit for the Immich app |
| Computer | Mac, Windows, or Linux | B1 a Mac, B3 a PC, B2 and B4 any computer |
| iPhone needed | Only for the sign-in code | With B2, the Immich app stays open until the end |
| Speed on a big library | Usually the fastest | Slower. B2 can take days. B4 can take 7 days. |
| Exact size before the start | Yes, per year | No. Your iPhone shows the total. |
| Check | Every file against iCloud's own list, N of N | B1: every file against the Photos library. Others: counts against your iPhone. |
| Shared Albums and Recently Deleted | Not included | Not included |
| Breaks when Apple changes iCloud.com | Yes, until the kit is updated | No |
| Left on the computer afterwards | A session key, until you log out | Nothing |
| Disk space on the computer | About 2.2 times your library | B1: about 2.2 times, plus the Photos library. B2: about 1.1 times. B3: about 2.2 times, plus the iCloud Photos folder on drive C:. |

Paths A and B1 need about 2.2 times your library, because the files land in a download folder first and are then copied into Immich. With B1, the Photos library holds its own copy of the originals on top of that, unless it lives on an external drive. With B2, Immich holds the only copy on the computer. With B3, the iCloud Photos folder on drive C:, the download folder, and Immich each hold a copy.

## Which one to pick

There is no wrong pick. Claude Code recommends one, with its reason, and you choose:

- **Path B** if your Apple account holds a lot of sensitive data, such as mail, notes, and documents, or if you don't want any key on the computer, even for a day.
- **Path A** if your library is big, if you use Windows or Linux, or if you want the photos to go straight to an external drive.

If both fit, Claude Code says so and lets you choose.

## After the move

Both paths end the same way:

- The Immich app on your iPhone uploads your new photos to your computer.
- Tailscale lets the app reach your computer when you are away from home.
- You turn off iCloud Photos and change your plan yourself, last, only after the check reports N of N and a second copy on another drive has passed its check. [downgrade.md](downgrade.md) has the steps.
- Your mode sets the rest of the routine. [modes.md](modes.md) explains each mode.

Only Hybrid mode brings photos home from iCloud again each month. With Path A, that takes a fresh sign-in each month. B1, the Photos app on a Mac, does it with no sign-in.

## If you want to stop or undo

Neither path deletes or switches off anything in iCloud. Do not delete photos from iCloud or turn off iCloud Photos until the check passes. That step is yours, and it is the hardest one to undo.

To remove Homecloud from the computer, delete the kit's folder, and Docker Desktop and Tailscale if you don't need them. If you signed in with Path A, run `python3 scripts/icloud.py logout` first, then delete the `.homecloud` folder in your home folder. On the iPhone, delete the Immich and Tailscale apps.
