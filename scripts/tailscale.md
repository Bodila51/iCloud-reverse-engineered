# Tailscale and the Immich phone app

Tailscale links your phone and your computer through an encrypted private network. With it, the Immich app reaches your computer at home and away from home, and you open no port on your router. Immich's remote access guide lists Tailscale as one way to do this. It also says never to forward port 2283 to the internet.

Do these steps after Immich runs on your computer, as [immich/README.md](immich/README.md) describes. You create your own Tailscale account and type every sign-in yourself.

## The one-command way

`scripts/setup.sh` on a Mac and `scripts/setup.ps1` on Windows run this part for you. To run only the Tailscale part, from the kit folder:

```
bash scripts/tailscale_setup.sh --yes
```

On Windows:

```
powershell -ExecutionPolicy Bypass -File scripts/tailscale_setup.ps1 -Yes
```

Without `--yes` (`-Yes` on Windows), it shows its plan and asks first. It does this:

1. If Tailscale is missing, it installs it. On a Mac, it downloads Tailscale's standalone app, the variant Tailscale recommends, and macOS asks for your Mac password. On Windows, winget installs it, and Windows asks for permission. Both check that Tailscale signed what they install.
2. It starts Tailscale. The first time on a Mac, macOS asks you to allow Tailscale's system extension and its VPN configuration. Allow both.
3. If the computer is not signed in, a browser window opens. Sign in there, or create your free account.
4. It waits up to 5 minutes until the computer is connected. Then it prints the address for the Immich app, such as `Address: http://100.x.y.z:2283`, and the computer's MagicDNS name. If Tailscale is already connected, it only prints these lines.

It never uses or prints an auth key. If your Tailscale account asks for new devices to be approved, it tells you where to approve this one.

Then make the page for your phone, with that address and your Immich email:

```
python3 scripts/phone_card.py --url http://100.x.y.z:2283 --email you@example.com --open
```

It writes `phone.html` in the `.homecloud` folder in your home folder and opens it in your browser. The page shows the server address in large type and your Immich email, then one part for the iPhone and one for Android. Each part has two QR codes, for Tailscale and for Immich, in the App Store or in Google Play, and its own steps: five for the iPhone and six for Android. It holds no password, token or key. The script also prints the steps in the terminal.

On an iPhone, follow the five steps in the iPhone part:

1. Point the iPhone camera at the Tailscale code, tap the link, and install **Tailscale**.
2. Open Tailscale and tap **Get Started**. When iOS asks to add a VPN configuration, tap **Allow**. Sign in with the same account you use on the computer.
3. Point the camera at the Immich code, tap the link, and install **Immich**.
4. Open Immich, enter the server address, and sign in with your Immich email and password.
5. Tap the cloud icon at the top right. Under **Backup Albums**, tap **Select** and pick the album to back up, or **Recents** for all your photos and videos. Go back and tap **Enable Backup**.

When iOS asks about access to your photos, tap **Allow Full Access**. With limited access, Immich sees only the photos you pick.

On an Android phone, follow the six steps in the Android part:

1. Point the phone camera at the Tailscale code, tap the link, and install **Tailscale** from Google Play.
2. Open Tailscale and tap **Get Started**. Tap **Connect** if you see it, and tap **OK** when Android asks to set up a VPN connection. Then tap **Log in** and sign in with the same account you use on the computer.
3. Point the camera at the Immich code, tap the link, and install **Immich** from Google Play.
4. Open Immich, enter the server address, and sign in with your Immich email and password.
5. Tap the cloud icon at the top right. Under **Backup Albums**, tap **Select**. Tap **Camera** and any other album you want to back up. Go back and turn on **Enable Backup**.
6. Let Immich run in the background. At the bottom of the Backup screen, tap the link about notifications if you see it, and tap **Allow**. Then open the Android **Settings** > **Apps** > **Immich** > **Battery** and choose **Unrestricted**.

When Android asks about your photos and videos, allow access to all of them. If you pick only some, Immich sees only those. Phone makers name the battery menu differently, such as **App battery usage**. If you cannot find it, tap the battery link in Immich. Its **Show me how** button opens [dontkillmyapp.com](https://dontkillmyapp.com), a guide for each maker.

On either phone, if the import is still running on the computer, wait with step 5 until it ends. The app then skips every photo that Immich already has.

Two things the scripts leave to you:

- Turn off key expiry for the computer, as [Turn off key expiry for the computer](#turn-off-key-expiry-for-the-computer) shows. Otherwise Tailscale signs the computer out after 180 days. The script prints the link.
- On a Mac, make Tailscale open when you sign in, as step 5 under [Mac](#mac) shows.

## What it costs

Tailscale's Personal plan is free for non-commercial use. It covers up to 6 users and unlimited personal devices. Tailscale changed these limits in April 2026, so older pages still say 3 users and 100 devices. Check [tailscale.com/pricing](https://tailscale.com/pricing) for the current terms.

Sharing your computer with family members, as shown in [Family members](#family-members-share-the-computer-do-not-invite), uses none of the 6 user places.

## By hand

These are the same steps without the scripts, with more detail. Use them if a script stops, or to check something.

### Step 1: Tailscale on the computer

#### Mac

1. Download Tailscale's standalone app, which Tailscale recommends for macOS: <https://pkgs.tailscale.com/stable/Tailscale-latest-macos.pkg>. Open the file and follow the installer.
2. Install only this version. Tailscale warns against running it next to the Mac App Store version on the same Mac.
3. Open Tailscale from Applications and follow its prompts. macOS asks you to allow Tailscale's system extension and VPN configuration. Allow them.
4. Sign in when Tailscale asks, or click the Tailscale icon in the menu bar and choose **Log in**. Sign in, or create your account, in the browser window that opens.
5. Make Tailscale open when you sign in to the Mac. Open **System Settings** > **General** > **Login Items & Extensions** (**Login Items** on older macOS). If Tailscale is not under **Open at Login**, click **+** and add it from Applications.
6. Optional, for the check commands below: in Tailscale's **Settings**, under **CLI integration**, click **Install Now** and type your Mac password. This needs macOS 13 or later and adds the `tailscale` command. Without it, type `/Applications/Tailscale.app/Contents/MacOS/Tailscale` in place of `tailscale`.

#### Windows

1. Download the installer from <https://tailscale.com/download/windows> and run it.
2. The Tailscale icon appears in the notification area of the taskbar. Right-click it and choose **Log in**. Sign in, or create your account, in the browser window that opens.
3. The `tailscale` command works in PowerShell. If PowerShell does not find it, type `& "C:\Program Files\Tailscale\tailscale.exe"` in place of `tailscale`.

#### Turn off key expiry for the computer

Tailscale signs each device out after 180 days by default. Then the phone cannot reach the computer until you sign in on the computer again. For a computer that serves your photos, turn this off:

1. Open the Machines page of the admin console: <https://login.tailscale.com/admin/machines>.
2. Click the menu (**...**) at the end of your computer's row.
3. Choose **Disable key expiry**.

### Step 2: check the computer

Run these in Terminal on a Mac or in PowerShell on Windows:

```
tailscale status
tailscale ip -4
```

`tailscale status` lists your devices. The first line is this computer. `tailscale ip -4` prints this computer's Tailscale address, which starts with 100. Below, `100.x.y.z` stands for that address. The Tailscale setup script prints it as "Address", and the Immich setup script as "Through Tailscale".

The computer also has a name. MagicDNS is on by default for Tailscale accounts made since October 2022. The full name looks like `computer-name.tailnet-name.ts.net`, and the Machines page of the admin console shows it.

Check that Immich answers on the Tailscale address. On Windows, type `curl.exe` instead of `curl`.

```
curl http://100.x.y.z:2283/api/server/ping
```

It prints `{"res":"pong"}`. If it prints nothing or an error, check that `http://localhost:2283` opens in a browser on the computer. If that fails too, Immich is not running: start Docker Desktop and wait a minute.

### Step 3: Tailscale on the phone

On an iPhone:

1. Install **Tailscale** from the App Store and open it.
2. Tap **Get Started** and accept the prompt to add a VPN configuration. You can also allow notifications, which tell you when you need to sign in again.
3. Sign in with the same account as on the computer.
4. Check that the Tailscale app lists your computer.
5. Keep Tailscale connected. While it is off, the Immich app cannot reach the computer. To let it connect by itself, tap your profile picture at the top right, then **VPN On Demand**, and turn it on.
6. Try the address. Open Safari on the iPhone and go to `http://100.x.y.z:2283`. The Immich sign-in page should open.

An iPhone allows only one VPN with On Demand at a time. If you turn on On Demand in another VPN app, Tailscale stops connecting by itself, and the Immich app cannot upload until you connect Tailscale again.

On an Android phone:

1. Install **Tailscale** from Google Play and open it.
2. Tap **Get Started**. Tap **Connect** if you see it, and tap **OK** when Android asks to set up a VPN connection.
3. Tap **Log in** and sign in with the same account as on the computer.
4. Check that the Tailscale app lists your computer.
5. Keep Tailscale connected. While it is off, the Immich app cannot reach the computer.
6. Try the address. Open the phone's browser, such as Chrome, and go to `http://100.x.y.z:2283`. The Immich sign-in page should open.

Android, too, runs one VPN at a time. Another VPN app that connects pushes Tailscale off, and the Immich app cannot reach the computer until you connect Tailscale again.

### Step 4: the Immich app

1. Install **Immich** from the App Store on an iPhone, or from Google Play on Android, and open it.
2. Enter the server address: `http://100.x.y.z:2283`. The name works too: `http://computer-name.tailnet-name.ts.net:2283`. The 100 address works even when MagicDNS is off.
3. Sign in with your Immich email and password.
4. Tap the cloud icon at the top right to open the backup screen. Select the albums to back up, then tap **Enable Backup**. On an iPhone, **Recents** holds all your photos and videos. On Android, the albums are the folders on your phone, such as **Camera**. When the phone asks about access to your photos, allow access to all of them. With limited access, the app sees only the photos you pick.
5. Let the app run in the background. On an iPhone, open **Settings** > **General** > **Background App Refresh**, turn it on, and turn it on for Immich. On Android, tap the links about notifications and battery at the bottom of the Backup screen, and set Immich's battery use to **Unrestricted**, as step 6 of the Android part shows.

How uploads work on an iPhone:

- iOS decides when background uploads run. The app cannot choose the time.
- Opening the Immich app uploads new photos. That upload stops when you leave the app.
- Low Power Mode, Low Data Mode and force-quitting the app stop background uploads.

How uploads work on Android:

- Android decides when background uploads run. Strict battery settings, which some phone makers use, can stop them. [dontkillmyapp.com](https://dontkillmyapp.com) shows how to relax them for each maker.
- The backup settings can let background uploads run only while the phone is charging.
- Opening the Immich app uploads new photos.

On both, the app uploads on Wi-Fi only by default. The backup settings have switches for mobile data.

Photos that the import already put on the computer:

- When you first select albums, the app computes a checksum of each photo's content. It skips photos that Immich already has, including the ones the import added.
- If a photo on the phone is not byte for byte the same as the downloaded copy, the app uploads it again, and Immich shows it twice.
- If iCloud Photos is still on with **Optimize iPhone Storage**, the iPhone app downloads each photo from iCloud to compute its checksum. That takes time, space and mobile data while it runs.

### Step 5: try it away from home

Turn off Wi-Fi on the phone, so it uses mobile data. Open the Immich app. Your photos should load. If they do not, see [If the app cannot connect](#if-the-app-cannot-connect).

## Family members: share the computer, do not invite

Each family member uses their own Tailscale account, and you share only the Immich computer with them.

1. In Immich, open **Administration** > **Users** > **Create user**, and make an account for each person.
2. The family member creates their own free Tailscale account and installs Tailscale on their phone, as in step 3.
3. In the Machines page of your admin console, click the menu (**...**) at the end of your computer's row and choose **Share**. Make a share link or an email invite and send it. They open it and accept it with their own account.
4. They install the Immich app and sign in with `http://100.x.y.z:2283` and their own Immich account. The computer keeps the same 100 address for them. Its name works only as the full name. If the name fails, use the 100 address.
5. They turn on backup and let the app run in the background, as in step 4.

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

1. Is Tailscale connected on the phone? Open the Tailscale app.
2. Is the computer on, awake and signed in? After a restart, Docker Desktop and Immich wait until someone signs in. A Mac with FileVault on cannot sign in by itself.
3. Does `http://localhost:2283` open on the computer? If not, start Docker Desktop.
4. Does `tailscale status` on the computer show it as connected? If its key expired, sign in on the computer again and turn off key expiry.
5. Is another VPN turned on, on the phone or the computer? Turn it off and try again.
6. Does `http://100.x.y.z:2283` open in the phone's browser, Safari on an iPhone or Chrome on Android? If the browser works but the app does not, check the server address in the app.

To keep the computer awake, use the scripts in [always_on](always_on/).

## Undo

- iPhone: press and hold the Tailscale or Immich icon and choose **Remove App**. Removing the Immich app does not delete any photo from your iPhone.
- Android: open the app's page in Google Play and tap **Uninstall**. Removing the Immich app does not delete the photos in your phone's gallery.
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
- Tailscale, install on Android: <https://tailscale.com/docs/install/android>
- Tailscale, other VPNs (one VPN at a time on a phone): <https://tailscale.com/docs/reference/faq/other-vpns>
- Immich, Android background backup (battery optimization, dontkillmyapp.com, charging only): <https://docs.immich.app/features/mobile-backup>
- Immich app source v3.3.5 (Backup screen labels, Android albums as phone folders): <https://github.com/immich-app/immich/tree/v3.3.5/mobile>
- Tailscale, VPN On Demand (one On Demand VPN at a time): <https://tailscale.com/kb/1291/ios-vpn-on-demand>
- Tailscale, key expiry (180 days, Disable key expiry): <https://tailscale.com/kb/1028/key-expiry>
- Tailscale, MagicDNS (default since 20 October 2022, full names for shared devices): <https://tailscale.com/docs/features/magicdns>
- Tailscale, inviting compared with sharing, and device sharing (quarantine, 30-day links): <https://tailscale.com/docs/reference/inviting-vs-sharing>, <https://tailscale.com/kb/1084/sharing>
- Tailscale, pricing (Personal: free for non-commercial use, 6 users, unlimited personal devices): <https://tailscale.com/pricing>
- Apple, Low Power Mode: <https://support.apple.com/en-us/101604>, and Low Data Mode: <https://support.apple.com/en-us/102433>
