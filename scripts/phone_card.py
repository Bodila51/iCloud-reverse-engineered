#!/usr/bin/env python3
"""Homecloud: a page with the phone steps for the Immich app, for an iPhone and for an Android phone.

    python3 scripts/phone_card.py --url http://100.x.y.z:2283 [--email you@example.com] [--open]

It writes ~/.homecloud/phone.html (folder mode 0700, file mode 0600) and prints the same steps as
text. The page is meant for the computer screen: you scan its codes with the phone camera.
It has one part for the iPhone and one for Android, so nobody has to say which phone they have.
Each part shows a code for Tailscale and a code for Immich (the App Store for the iPhone, Google
Play for Android) and its own numbered steps: five for the iPhone, six for Android. Above both parts
are the server address in large type and, if you pass --email, your Immich email.
--open opens the page in your default browser.

The page holds no password, token or key. The script changes nothing but this one file.
HOMECLOUD_HOME, if set, replaces ~/.homecloud (the tests use it).

QR codes: scripts/vendor/qrcodegen.py is qrcodegen 1.8.0 from PyPI by Project Nayuki, MIT license,
unchanged (see scripts/vendor/qrcodegen-LICENSE.txt). Python 3.9 or later, standard library
otherwise.

Sources
  [A1] Tailscale in the App Store, app id 1470499037, seller Tailscale Inc.:
       https://apps.apple.com/app/tailscale/id1470499037 (linked from tailscale.com/docs/install/ios)
  [A2] Immich in the App Store, app id 1613945652: https://apps.apple.com/app/immich/id1613945652
       (linked from Immich's docs: docs/docs/partials/_mobile-app-download.md)
  [I1] Immich backup screen: the cloud icon at the top right opens "Backup"; the card
       "Backup Albums" has the button "Select"; "Recents" holds every photo and video; then
       "Enable Backup". github.com/immich-app/immich: mobile/lib/pages/backup/backup.page.dart,
       mobile/lib/pages/backup/backup_album_selection.page.dart, i18n/en.json
  [I2] Immich mobile backup (full photo access, Background App Refresh):
       https://docs.immich.app/features/mobile-backup
  [G1] Tailscale in Google Play, package com.tailscale.ipn:
       https://play.google.com/store/apps/details?id=com.tailscale.ipn (the link in the README of
       github.com/tailscale/tailscale-android, whose activities are com.tailscale.ipn.*)
  [G2] Immich in Google Play, package app.alextran.immich:
       https://play.google.com/store/apps/details?id=app.alextran.immich (the link in
       docs/docs/partials/_mobile-app-download.md; applicationId in mobile/android/app/build.gradle;
       both in github.com/immich-app/immich at v3.3.5)
  [G3] Tailscale on Android: "Get Started", then "Connect" and Android's VPN request ("OK"), then
       "Log in". github.com/tailscale/tailscale-android: android/src/main/res/values/strings.xml,
       android/src/main/java/com/tailscale/ipn/ui/view/IntroView.kt and MainView.kt;
       tailscale.com/docs/install/android
  [G4] Immich on Android, base https://raw.githubusercontent.com/immich-app/immich/v3.3.5/ :
       the Backup screen lists the phone's folders as albums, such as "Camera"
       (mobile/android/app/src/main/kotlin/app/alextran/immich/sync/MessagesImplBase.kt, getAlbums).
       Once "Enable Backup" is on, it shows a link for notifications and a link for battery
       optimizations until each is allowed; "Show me how" opens dontkillmyapp.com
       (mobile/lib/pages/backup/backup.page.dart, mobile/lib/providers/permission.provider.dart,
       mobile/android/app/src/main/kotlin/app/alextran/immich/permission/PermissionApiImpl.kt,
       i18n/en.json). The background upload is a foreground service that needs POST_NOTIFICATIONS
       (mobile/android/app/src/main/AndroidManifest.xml). Docs: docs/docs/features/mobile-backup.md
       (Android: strict battery optimization can stop the background worker, with a link to
       dontkillmyapp.com) and docs/docs/FAQ.mdx.
  [G5] "Unrestricted" battery use: developer.android.com/topic/performance/background-optimization
       ("Battery usage" page: Unrestricted, Optimized, Restricted). In the Android Settings source,
       Unrestricted puts the app on the power allowlist, which is what Immich checks with
       PowerManager.isIgnoringBatteryOptimizations (aosp-mirror/platform_packages_apps_settings,
       src/com/android/settings/fuelgauge/BatteryOptimizeUtils.java).
"""

import argparse
import datetime
import html
import importlib.util
import ipaddress
import os
import re
import subprocess
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
QRCODEGEN_PATH = os.path.join(HERE, "vendor", "qrcodegen.py")

TAILSCALE_APP_URL = "https://apps.apple.com/app/tailscale/id1470499037"  # [A1]
IMMICH_APP_URL = "https://apps.apple.com/app/immich/id1613945652"  # [A2]
TAILSCALE_PLAY_URL = "https://play.google.com/store/apps/details?id=com.tailscale.ipn"  # [G1]
IMMICH_PLAY_URL = "https://play.google.com/store/apps/details?id=app.alextran.immich"  # [G2]
QUIET_ZONE = 4  # light modules around the code, as the QR standard asks
PAGE_NAME = "phone.html"


# --------------------------------------------------------------------------
# Paths and checks
# --------------------------------------------------------------------------

def homecloud_home():
    return os.path.abspath(os.path.expanduser(os.environ.get("HOMECLOUD_HOME") or "~/.homecloud"))


def check_url(raw):
    """Returns the address without a trailing slash, or raises ValueError with a plain sentence."""
    url = (raw or "").strip().rstrip("/")
    example = "Give the address as http://100.x.y.z:2283, the one that tailscale_setup prints."
    if not url or any(c.isspace() for c in url) or any(c in url for c in "\"'<>\\`"):
        raise ValueError("This server address does not look right: %r. %s" % (raw, example))
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("The server address must start with http:// or https://. " + example)
    try:
        port = parts.port  # raises ValueError when the port is not a number from 0 to 65535
    except ValueError:
        port = 0
    if port == 0:
        raise ValueError("The port in the server address is not right. " + example)
    host = parts.hostname.lower()
    local = host == "localhost" or host.endswith(".localhost")
    try:
        ip = ipaddress.ip_address(host)
        local = local or ip.is_loopback or ip.is_unspecified
    except ValueError:
        pass
    if local:
        raise ValueError(
            "The phone cannot reach %s, because that name means the phone itself. %s" % (host, example)
        )
    return url


def check_email(raw):
    email = (raw or "").strip()
    if not re.fullmatch(r"[^@\s<>\"'`\\]+@[^@\s<>\"'`\\]+", email):
        raise ValueError("This email does not look right: %r." % raw)
    return email


def via_tailscale(url):
    """True when the address is a Tailscale address (100.64.0.0/10) or a MagicDNS name."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host.endswith(".ts.net"):
        return True
    try:
        return ipaddress.ip_address(host) in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        return False


# --------------------------------------------------------------------------
# QR codes
# --------------------------------------------------------------------------

_QRCODEGEN = None


def qrcodegen():
    """Loads scripts/vendor/qrcodegen.py by its path, so no installed package can stand in for it."""
    global _QRCODEGEN
    if _QRCODEGEN is None:
        spec = importlib.util.spec_from_file_location("homecloud_vendor_qrcodegen", QRCODEGEN_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _QRCODEGEN = module
    return _QRCODEGEN


def qr_matrix(text):
    """The modules of the QR code for text, as rows of booleans (True is dark)."""
    qr_class = qrcodegen().QrCode
    qr = qr_class.encode_text(text, qr_class.Ecc.MEDIUM)
    size = qr.get_size()
    return [[qr.get_module(x, y) for x in range(size)] for y in range(size)]


def qr_svg(text, label):
    """An inline SVG of the QR code: black modules on white, with the quiet zone around it."""
    rows = qr_matrix(text)
    size = len(rows)
    dim = size + 2 * QUIET_ZONE
    path = []
    for y, row in enumerate(rows):
        x = 0
        while x < size:
            if not row[x]:
                x += 1
                continue
            start = x
            while x < size and row[x]:
                x += 1
            run = x - start
            path.append("M%d %dh%dv1h-%dz" % (start + QUIET_ZONE, y + QUIET_ZONE, run, run))
    label = html.escape(label, quote=True)
    return (
        '<svg class="qr" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {d} {d}" role="img" '
        'aria-label="{label}" shape-rendering="crispEdges"><title>{label}</title>'
        '<rect width="{d}" height="{d}" fill="#ffffff"/>'
        '<path fill="#000000" d="{path}"/></svg>'
    ).format(d=dim, label=label, path="".join(path))


# --------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------

def sign_in_parts(email):
    """The Immich sign-in sentence, with the email when it is known."""
    if email:
        return [("t", "Sign in with "), ("v", email), ("t", " and your Immich password.")]
    return [("t", "Sign in with your Immich email and password.")]


def steps(url, email):
    """The five iPhone steps as lists of (kind, text): "t" plain, "b" a button or app name, "v" a value."""
    sign_in = sign_in_parts(email)
    return [
        [("t", "Point the iPhone camera at the Tailscale code, tap the link, and install "),
         ("b", "Tailscale"), ("t", ".")],
        [("t", "Open Tailscale and tap "), ("b", "Get Started"),
         ("t", ". When iOS asks to add a VPN configuration, tap "), ("b", "Allow"),
         ("t", ". Sign in with the same account you use on this computer.")],
        [("t", "Point the camera at the Immich code, tap the link, and install "), ("b", "Immich"),
         ("t", ".")],
        [("t", "Open Immich and enter the server address "), ("v", url), ("t", ". ")] + sign_in,
        [("t", "Tap the cloud icon at the top right. Under "), ("b", "Backup Albums"), ("t", ", tap "),
         ("b", "Select"), ("t", " and pick the album to back up, or "), ("b", "Recents"),
         ("t", " for all your photos and videos. Go back and tap "), ("b", "Enable Backup"),
         ("t", ".")],
    ]


TIPS = [
    [("t", "When iOS asks about access to your photos, tap "), ("b", "Allow Full Access"),
     ("t", ". With limited access, Immich sees only the photos you pick.")],
    [("t", ("Keep Tailscale connected on the iPhone. Away from home, Immich reaches this computer "
            "only through it."))],
    [("t", "Keep "), ("b", "Background App Refresh"),
     ("t", (" on for Immich in Settings > General. iOS decides when background uploads run. "
            "Opening Immich uploads new photos."))],
]


def android_steps(url, email):
    """The six Android steps, in the same form as steps(). Steps 1 to 5 match the iPhone numbers."""
    return [
        [("t", "Point the phone camera at the Tailscale code, tap the link, and install "),
         ("b", "Tailscale"), ("t", " from Google Play.")],  # [G1]
        [("t", "Open Tailscale and tap "), ("b", "Get Started"),
         ("t", ". Tap "), ("b", "Connect"), ("t", " if you see it, and tap "), ("b", "OK"),
         ("t", " when Android asks to set up a VPN connection. Then tap "), ("b", "Log in"),
         ("t", " and sign in with the same account you use on this computer.")],  # [G3]
        [("t", "Point the camera at the Immich code, tap the link, and install "), ("b", "Immich"),
         ("t", " from Google Play.")],  # [G2]
        [("t", "Open Immich and enter the server address "), ("v", url), ("t", ". ")] + sign_in_parts(email),
        [("t", "Tap the cloud icon at the top right. Under "), ("b", "Backup Albums"), ("t", ", tap "),
         ("b", "Select"), ("t", ". Tap "), ("b", "Camera"),
         ("t", " and any other album you want to back up. Go back and turn on "),
         ("b", "Enable Backup"), ("t", ".")],  # [G4]
        [("t", ("Let Immich run in the background. At the bottom of the Backup screen, tap the link "
                "about notifications if you see it, and tap ")), ("b", "Allow"),
         ("t", ". Then open the Android "), ("b", "Settings"), ("t", " > "), ("b", "Apps"),
         ("t", " > "), ("b", "Immich"), ("t", " > "), ("b", "Battery"),
         ("t", " and choose "), ("b", "Unrestricted"), ("t", ".")],  # [G4] [G5]
    ]


ANDROID_TIPS = [
    [("t", ("When Android asks about your photos and videos, allow access to all of them. "
            "If you pick only some, Immich sees only those."))],
    [("t", ("Keep Tailscale connected on the phone. Away from home, Immich reaches this computer "
            "only through it."))],
    [("t", ("Phone makers differ in how much they limit apps in the background, and the battery menu can "
            "have another name, such as ")), ("b", "App battery usage"),
     ("t", ". If you cannot find the setting, tap the battery link in Immich. Its "), ("b", "Show me how"),
     ("t", " button opens dontkillmyapp.com, a guide for each maker.")],
    [("t", ("When notifications and battery use are allowed, the two links at the bottom of the Backup "
            "screen go away. Android decides when background uploads run. Opening Immich uploads new "
            "photos."))],
]

NUMBER_WORDS = ("no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")


def phones(url, email):
    """The two parts of the page, in page order: the iPhone first, then Android.

    The page and the text both read from this list. Step 1 and step 3 are the two codes, and step 4
    is where the server address goes, in both parts.
    """
    iphone = steps(url, email)
    android = android_steps(url, email)
    return [
        {
            "id": "iphone",
            "name": "iPhone",
            "intro": "Scan each code with the iPhone camera, then follow the %s steps." % NUMBER_WORDS[len(iphone)],
            "store": "the App Store",
            "codes_label": "App Store codes",
            "apps": [("Step 1", "Tailscale", TAILSCALE_APP_URL), ("Step 3", "Immich", IMMICH_APP_URL)],
            "steps_title": "On your iPhone",
            "steps": iphone,
            "tips": TIPS,
        },
        {
            "id": "android",
            "name": "Android",
            "intro": "Scan each code with the phone camera, then follow the %s steps." % NUMBER_WORDS[len(android)],
            "store": "Google Play",
            "codes_label": "Google Play codes",
            "apps": [("Step 1", "Tailscale", TAILSCALE_PLAY_URL), ("Step 3", "Immich", IMMICH_PLAY_URL)],
            "steps_title": "On your Android phone",
            "steps": android,
            "tips": ANDROID_TIPS,
        },
    ]


def as_text(parts):
    return "".join(text for _, text in parts)


def as_html(parts):
    out = []
    for kind, text in parts:
        text = html.escape(text, quote=True)
        if kind == "b":
            out.append("<strong>%s</strong>" % text)
        elif kind == "v":
            out.append('<span class="value">%s</span>' % text)
        else:
            out.append(text)
    return "".join(out)


# --------------------------------------------------------------------------
# Page
# --------------------------------------------------------------------------

STYLE = """
:root {
  --bg: #f5f6f8;
  --card: #ffffff;
  --ink: #15171c;
  --muted: #586070;
  --line: #dfe3e9;
  --accent: #2554d0;
  --accent-ink: #ffffff;
  --soft: #eef2fb;
  color-scheme: light;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font: 17px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
main { max-width: 1040px; margin: 0 auto; padding: 32px 16px 48px; }
h1 { font-size: 32px; line-height: 1.2; margin: 0 0 6px; }
h2 { font-size: 28px; line-height: 1.2; margin: 0 0 6px; }
h3 { font-size: 20px; margin: 0 0 12px; }
.lead { color: var(--muted); margin: 0 0 24px; font-size: 18px; }
.jump { display: flex; flex-wrap: wrap; gap: 12px; margin: 0; }
.jump a {
  display: inline-block; min-height: 44px; padding: 9px 22px; border: 2px solid var(--accent);
  border-radius: 999px; color: var(--accent); font-weight: 700; text-decoration: none;
}
.jump a:hover, .jump a:focus-visible { background: var(--accent); color: var(--accent-ink); }
.jump a:focus-visible { outline: 3px solid var(--ink); outline-offset: 2px; }
.phone { margin-top: 44px; padding-top: 24px; border-top: 4px solid var(--accent); }
.phone > h3 { margin: 28px 0 12px; }
.codes { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 16px; padding: 20px 24px; }
.code { text-align: center; }
.tag {
  display: inline-block; background: var(--soft); color: var(--accent); font-weight: 700;
  font-size: 14px; letter-spacing: .02em; border-radius: 999px; padding: 2px 12px; margin-bottom: 8px;
}
.qr { display: block; width: 100%; max-width: 300px; height: auto; margin: 4px auto 12px; }
.link { color: var(--muted); font-size: 14px; overflow-wrap: anywhere; margin: 0; }
.address { margin: 20px 0; }
.label { color: var(--muted); font-size: 15px; margin: 0 0 4px; }
.big {
  font: 700 clamp(28px, 5.5vw, 52px)/1.15 ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  overflow-wrap: anywhere; margin: 0 0 8px; letter-spacing: .01em;
}
.email { font-size: 24px; font-weight: 600; overflow-wrap: anywhere; margin: 0; }
.note { color: var(--muted); font-size: 15px; margin: 10px 0 0; }
ol.steps { list-style: none; counter-reset: step; padding: 0; margin: 0 0 24px; display: grid; gap: 12px; }
ol.steps li {
  counter-increment: step; position: relative; background: var(--card);
  border: 1px solid var(--line); border-radius: 14px; padding: 16px 20px 16px 72px; font-size: 19px;
}
ol.steps li::before {
  content: counter(step); position: absolute; left: 20px; top: 14px; width: 36px; height: 36px;
  border-radius: 50%; background: var(--accent); color: var(--accent-ink); font-weight: 700;
  display: flex; align-items: center; justify-content: center; font-size: 18px;
}
.value { font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; font-weight: 700; overflow-wrap: anywhere; }
.tips { color: var(--muted); font-size: 16px; margin: 0; padding-left: 20px; }
.tips li { margin-bottom: 6px; }
footer { color: var(--muted); font-size: 14px; margin-top: 28px; }
@media (max-width: 680px) {
  .codes { grid-template-columns: 1fr; }
  ol.steps li { padding-left: 64px; font-size: 17px; }
  ol.steps li::before { left: 16px; }
}
@media print {
  body { background: #ffffff; }
  .card, ol.steps li { border-color: #999999; break-inside: avoid; }
  .jump { display: none; }
  .phone + .phone { break-before: page; }
}
"""


CODE_CARD = """      <div class="card code">
        <span class="tag">{tag}</span>
        <h3>{name}</h3>
        {svg}
        <p class="link">{link}</p>
      </div>"""

PHONE_SECTION = """  <section class="phone" id="{id}" aria-labelledby="{id}-title">
    <h2 id="{id}-title">{name}</h2>
    <p class="lead">{intro}</p>

    <div class="codes" role="group" aria-label="{codes_label}">
{cards}
    </div>

    <h3>{steps_title}</h3>
    <ol class="steps">
{items}
    </ol>

    <h3>Good to know</h3>
    <ul class="tips">
{tips}
    </ul>
  </section>"""


def phone_html(phone):
    """One part of the page: the two codes, the numbered steps and the tips for one kind of phone."""
    esc = html.escape
    cards = "\n".join(
        CODE_CARD.format(
            tag=esc(tag),
            name=esc(name),
            svg=qr_svg(link, "QR code: %s in %s" % (name, phone["store"])),
            link=esc(link),
        )
        for tag, name, link in phone["apps"]
    )
    return PHONE_SECTION.format(
        id=phone["id"],
        name=esc(phone["name"]),
        intro=esc(phone["intro"]),
        codes_label=esc(phone["codes_label"]),
        cards=cards,
        steps_title=esc(phone["steps_title"]),
        items="\n".join("      <li>%s</li>" % as_html(step) for step in phone["steps"]),
        tips="\n".join("      <li>%s</li>" % as_html(tip) for tip in phone["tips"]),
    )


def render_html(url, email, today=None):
    today = today or datetime.date.today().isoformat()
    esc = html.escape
    parts = phones(url, email)
    email_block = ""
    if email:
        email_block = (
            '\n      <p class="label">Your Immich email</p>\n'
            '      <p class="email">%s</p>' % esc(email)
        )
    note = ""
    if via_tailscale(url):
        note = '\n      <p class="note">Your phone reaches this address only while Tailscale is connected.</p>'
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>Homecloud phone setup</title>
<style>{style}</style>
</head>
<body>
<main>
  <h1>Set up your phone</h1>
  <p class="lead">Keep this page open on the computer. Use the part for your phone: iPhone or Android.</p>

  <nav class="jump" aria-label="Your phone">
{jump}
  </nav>

  <section class="card address" aria-label="Server address">
      <p class="label">Server address for the Immich app (step 4)</p>
      <p class="big">{url}</p>{email_block}{note}
  </section>

{sections}

  <footer>Made by Homecloud on {today}. This page holds no password or key.</footer>
</main>
</body>
</html>
""".format(
        style=STYLE,
        jump="\n".join('    <a href="#%s">%s</a>' % (part["id"], esc(part["name"])) for part in parts),
        url=esc(url),
        email_block=email_block,
        note=note,
        sections="\n\n".join(phone_html(part) for part in parts),
        today=esc(today),
    )


def write_page(text):
    """Writes the page with mode 0600 into the Homecloud folder (mode 0700). Returns its path."""
    home = homecloud_home()
    os.makedirs(home, mode=0o700, exist_ok=True)
    if os.name != "nt":
        os.chmod(home, 0o700)
    path = os.path.join(home, PAGE_NAME)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    if os.name != "nt":
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def open_page(path):
    """Opens the page in the default browser: open on a Mac, start on Windows, xdg-open on Linux."""
    try:
        if os.name == "nt":
            os.startfile(path)  # the call behind "start"
            return True
        command = ["open", path] if sys.platform == "darwin" else ["xdg-open", path]
        done = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return done.returncode == 0
    except OSError:
        return False


def text_steps(url, email):
    """The steps for each phone, then the address, the email and the four store links."""
    parts = phones(url, email)
    lines = []
    for part in parts:
        lines.append("%s:" % part["steps_title"])
        for number, step in enumerate(part["steps"], 1):
            lines.append("  %d. %s" % (number, as_text(step)))
        lines.append("")
    lines.append("Server address: %s" % url)
    if email:
        lines.append("Immich email: %s" % email)
    for part in parts:
        for _, name, link in part["apps"]:
            lines.append("%s in %s: %s" % (name, part["store"], link))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Write ~/.homecloud/phone.html with QR codes and the iPhone and Android steps for Immich.",
    )
    parser.add_argument("--url", required=True,
                        help="the address the Immich app uses, such as http://100.x.y.z:2283")
    parser.add_argument("--email", help="your Immich email, shown on the page")
    parser.add_argument("--open", action="store_true", help="open the page in your default browser")
    args = parser.parse_args(argv)

    try:
        url = check_url(args.url)
        email = check_email(args.email) if args.email else None
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        path = write_page(render_html(url, email))
    except OSError as exc:
        print("The page could not be saved in %s: %s" % (homecloud_home(), exc.strerror or exc), file=sys.stderr)
        return 1

    print(text_steps(url, email))
    print("")
    if args.open:
        if open_page(path):
            print("The page with the QR codes for iPhone and Android is open in your browser.")
        else:
            print("Open this file in your browser to see the QR codes for iPhone and Android: %s" % path)
    else:
        print("Open this file in your browser to see the QR codes for iPhone and Android: %s" % path)
    print("Saved: %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
