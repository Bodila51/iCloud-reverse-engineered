# Tailscale and the Immich iPhone app

Tailscale links your iPhone and your computer through an encrypted private network. With it, the Immich app reaches your computer at home and away from home, and you open no port on your router. Immich's remote access guide lists Tailscale as one way to do this. It also says never to forward port 2283 to the internet.

Do these steps after Immich runs on your computer, as [immich/README.md](immich/README.md) describes. You create your own Tailscale account and type every sign-in yourself.

## What it costs

Tailscale's Personal plan is free for non-commercial use. It covers up to 6 users and unlimited personal devices. Tailscale changed these limits in April 2026, so older pages still say 3 users and 100 devices. Check [tailscale.com/pricing](https://tailscale.com/pricing) for the current terms.

Sharing your computer with family members, as shown in [Family members](#family-members-share-the-computer-do-not-invite), uses none of the 6 user places.

## Step 1: Tailscale on the computer

### Mac

1. Download Tailscale's standalone app, which Tailscale recommends for macOS: <https://pkgs.tailscale.com/stable/Tailscale-latest-macos.pkg>. Open the file and follow the installer.
2. Install only this version. Tailscale warns against running it next to the Mac App Store version on the same Mac.
3. Open Tailscale from Applications and follow its prompts. macOS asks you to allow Tailscale's system extension and VPN configuration. Allow them.
4. Sign in when Tailscale asks, or click the Tailscale icon in the menu bar and choose **Log in**. Sign in, or create your account, in the browser window that opens.
5. Make Tailscale open when you sign in to the Mac. Open **System Settings** > **General** > **Login Items & Extensions** (**Login Items** on older macOS). If Tailscale is not under **Open at Login**, click **+** and add it from Applications.
6. Optional, for the check commands below: in Tailscale's **Settings**, under **CLI integration**, click **Install Now** and type your Mac password. This needs macOS 13 or later and adds the `tailscale` command. Without it, type `/Applications/Tailscale.app/Contents/MacOS/Tailscale` in place of `tailscale`.

### Windows

1. Download the installer from <https://tailscale.com/download/windows> and run it.
2. The Tailscale icon appears in the notification area of the taskbar. Right-click it and choose **Log in**. Sign in, or create your account, in the browser window that opens.
3. The `tailscale` command works in PowerShell. If PowerShell does not find it, type `& "C:\Program Files\Tailscale\tailscale.exe"` in place of `tailscale`.

### Turn off key expiry for the computer

Tailscale signs each device out after 180 days by default. Then the iPhone cannot reach the computer until you sign in on the computer again. For a computer that serves your photos, turn this off:

1. Open the Machines page of the admin console: <https://login.tailscale.com/admin/machines>.
2. Click the menu (**...**) at the end of your computer's row.
3. Choose **Disable key expiry**.

## Step 2: check the computer

Run these in Terminal on a Mac or in PowerShell on Windows:

```
tailscale status
tailscale ip -4
```

`tailscale status` lists your devices. The first line is this computer. `tailscale ip -4` prints this computer's Tailscale address, which starts with 100. Below, `100.x.y.z` stands for that address. The Immich setup script also prints it as "Through Tailscale".

The computer also has a name. MagicDNS is on by default for Tailscale accounts made since October 2022. The full name looks like `computer-name.tailnet-name.ts.net`, and the Machines page of the admin console shows it.

Check that Immich answers on the Tailscale address. On Windows, type `curl.exe` instead of `curl`.

```
curl http://100.x.y.z:2283/api/server/ping
```

It prints `{"res":"pong"}`. If it prints nothing or an error, check that `http://localhost:2283` opens in a browser on the computer. If that fails too, Immich is not running: start Docker Desktop and wait a minute.

## Step 3: Tailscale on the iPhone

1. Install **Tailscale** from the App Store and open it.
2. Tap **Get Started** and accept the prompt to add a VPN configuration. You can also allow notifications, which tell you when you need to sign in again.
3. Sign in with the same account as on the computer.
4. Check that the Tailscale app lists your computer.
5. Keep Tailscale connected. While it is off, the Immich app cannot reach the computer. To let it connect by itself, tap your profile picture at the top right, then **VPN On Demand**, and turn it on.
6. Test the address. Open Safari on the iPhone and go to `http://100.x.y.z:2283`. The Immich sign-in page should open.

An iPhone allows only one VPN with On Demand at a time. If you turn on On Demand in another VPN app, Tailscale stops connecting by itself, and the Immich app cannot upload until you connect Tailscale again.

## Step 4: the Immich app

1. Install **Immich** from the App Store and open it.
2. Enter the server address: `http://100.x.y.z:2283`. The name works too: `http://computer-name.tailnet-name.ts.net:2283`. The 100 address works even when MagicDNS is off.
3. Sign in with your Immich email and password.
4. Tap the cloud icon at the top right to open the backup screen. Select the albums to back up, then tap **Enable Backup**. When iOS asks about access to your photos, allow full access. With limited access, the app sees only the photos you pick.
5. Open the iPhone **Settings** > **General** > **Background App Refresh**. Turn it on, and turn it on for Immich.

How uploads work on an iPhone:

- iOS decides when background uploads run. The app cannot choose the time.
- Opening the Immich app uploads new photos. That upload stops when you leave the app.
- Low Power Mode, Low Data Mode and force-quitting the app stop background uploads.
- By default the app uploads on Wi-Fi only. The backup settings have switches for mobile data.

Photos that the import already put on the computer:

- When you first select albums, the app computes a checksum of each photo's content. It skips photos that Immich already has, including the ones the import added.
- If a photo on the iPhone is not byte for byte the same as the downloaded copy, the app uploads it again, and Immich shows it twice.
- If iCloud Photos is still on with **Optimize iPhone Storage**, the app downloads each photo from iCloud to compute its checksum. That takes time, space and mobile data while it runs.

## Step 5: test away from home

Turn off Wi-Fi on the iPhone, so it uses mobile data. Open the Immich app. Your photos should load. If they do not, see [If the app cannot connect](#if-the-app-cannot-connect).

## Family members: share the computer, do not invite

Each family member uses their own Tailscale account, and you share only the Immich computer with them.

1. In Immich, open **Administration** > **Users** > **Create user**, and make an account for each person.
2. The family member creates their own free Tailscale account and installs Tailscale on their iPhone, as in step 3.
3. In the Machines page of your admin console, click the menu (**...**) at the end of your computer's row and choose **Share**. Make a share link or an email invite and send it. They open it and accept it with their own account.
4. They install the Immich app and sign in with `http://100.x.y.z:2283` and their own Immich account. The computer keeps the same 100 address for them. Its name works only as the full name. If the name fails, use the 100 address.
5. They turn on backup and Background App Refresh, as in step 4.

Why share and not invite:

- A shared device uses none of your 6 user places.
- The family member reaches only this computer, not your other devices.
- Tailscale keeps a shared device in quarantine: it answers connections but cannot start them, so the computer cannot reach their devices.
- An invited user joins your network, takes one of the 6 places, and can reach all your devices unless you add access rules.

An unused share link expires after 30 days. Make a new one if that happens.

Two things to know with several people on one computer:

- Whoever runs the computer can read every person's photos on its disk.
- The computer now holds everyone's photos, so the second copy on another drive matters even more.

## If the app cannot connect

Check these in order:

1. Is Tailscale connected on the iPhone? Open the Tailscale app.
2. Is the computer on, awake and signed in? After a restart, Docker Desktop and Immich wait until someone signs in. A Mac with FileVault on cannot sign in by itself.
3. Does `http://localhost:2283` open on the computer? If not, start Docker Desktop.
4. Does `tailscale status` on the computer show it as connected? If its key expired, sign in on the computer again and turn off key expiry.
5. Is another VPN turned on, on the iPhone or the computer? Turn it off and try again.
6. Does `http://100.x.y.z:2283` open in Safari on the iPhone? If Safari works but the app does not, check the server address in the app.

To keep the computer awake, use the scripts in [always_on](always_on/).

## Undo

- iPhone: press and hold the Tailscale or Immich icon and choose **Remove App**. Removing the Immich app does not delete any photo from your iPhone.
- Computer: uninstall Tailscale. On a Mac, quit it from the menu bar and move it from Applications to the Trash. On Windows, open **Settings** > **Apps** > **Installed apps**, select Tailscale, then **Uninstall**. For the full steps, search Tailscale's docs for "uninstall".
- Admin console: on the Machines page, open the menu of each device you no longer use and remove it.

## Sources

Tailscale pages were read as search extracts, because tailscale.com cannot be opened from the machine that wrote this guide. Check the current pages when something looks different.

- Immich, remote access ("Never forward port 2283", Tailscale as option 2): <https://docs.immich.app/guides/remote-access>
- Immich, mobile app login (`http://<machine-ip-address>:2283`) and backup screen: <https://docs.immich.app/features/mobile-app>
- Immich, mobile backup (checksums, Background App Refresh, iOS controls the timing, iCloud originals): <https://docs.immich.app/features/mobile-backup>
- Immich, user management: <https://docs.immich.app/administration/user-management>
- Tailscale, macOS variants (standalone recommended, do not install both, CLI integration): <https://tailscale.com/docs/concepts/macos-variants>
- Tailscale, install on Windows: <https://tailscale.com/docs/install/windows>
- Tailscale, install on iOS: <https://tailscale.com/docs/install/ios>
- Tailscale, VPN On Demand (one On Demand VPN at a time): <https://tailscale.com/kb/1291/ios-vpn-on-demand>
- Tailscale, key expiry (180 days, Disable key expiry): <https://tailscale.com/kb/1028/key-expiry>
- Tailscale, MagicDNS (default since 20 October 2022, full names for shared devices): <https://tailscale.com/docs/features/magicdns>
- Tailscale, inviting compared with sharing, and device sharing (quarantine, 30-day links): <https://tailscale.com/docs/reference/inviting-vs-sharing>, <https://tailscale.com/kb/1084/sharing>
- Tailscale, pricing (Personal: free for non-commercial use, 6 users, unlimited personal devices): <https://tailscale.com/pricing>
- Apple, Low Power Mode: <https://support.apple.com/en-us/101604>, and Low Data Mode: <https://support.apple.com/en-us/102433>
