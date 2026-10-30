"""Tests for scripts/phone_card.py and scripts/tailscale_setup.sh (with stand-in commands)."""

import html
import importlib.util
import os
import re
import shutil
import stat
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SETUP_SH = ROOT / "scripts" / "tailscale_setup.sh"


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


pc = load("phone_card", "scripts/phone_card.py")

URL = "http://100.101.102.103:2283"
WIFI_URL = "http://192.168.1.20:2283"  # what setup.sh --no-tailscale passes: not a Tailscale address
TS_APP = "https://apps.apple.com/app/tailscale/id1470499037"
IMMICH_APP = "https://apps.apple.com/app/immich/id1613945652"
TS_PLAY = "https://play.google.com/store/apps/details?id=com.tailscale.ipn"
IMMICH_PLAY = "https://play.google.com/store/apps/details?id=app.alextran.immich"
ALL_LINKS = (TS_APP, IMMICH_APP, TS_PLAY, IMMICH_PLAY)  # the order of the codes on the page


def make_card(homecloud_home, capsys, *extra, url=URL):
    assert pc.main(["--url", url + "/", *extra]) == 0
    page = (homecloud_home / "phone.html").read_text(encoding="utf-8")
    return page, capsys.readouterr().out


def svg_matrices(page):
    """Rebuilds the module grid of each inline SVG QR code from its path."""
    grids = []
    for svg in re.findall(r"<svg class=\"qr\".*?</svg>", page, re.S):
        dim = int(re.search(r'viewBox="0 0 (\d+) \1"', svg).group(1))
        grid = [[False] * dim for _ in range(dim)]
        d = re.search(r'<path fill="#000000" d="([^"]*)"', svg).group(1)
        for x, y, run in re.findall(r"M(\d+) (\d+)h(\d+)v1h-\3z", d):
            for i in range(int(run)):
                grid[int(y)][int(x) + i] = True
        assert re.sub(r"M\d+ \d+h(\d+)v1h-\1z", "", d) == ""  # nothing but module runs
        grids.append(grid)
    return grids


def part(page, name):
    """The HTML of one phone part of the page, by its id: "iphone" or "android"."""
    found = re.search(r'<section class="phone" id="%s".*?</section>' % name, page, re.S)
    assert found, "the page has no %s part" % name
    return found.group(0)


def step_items(section):
    """The numbered steps of one part, each as its HTML."""
    steps = re.search(r'<ol class="steps">(.*?)</ol>', section, re.S).group(1)
    return re.findall(r"<li>(.*?)</li>", steps, re.S)


def plain(fragment):
    """The text of an HTML fragment, without its tags."""
    return html.unescape(re.sub(r"<[^>]+>", "", fragment))


class Balance(HTMLParser):
    """Collects the tags that do not close in the right order."""

    VOID = frozenset(("meta", "link", "br", "hr", "img", "input"))

    def __init__(self):
        super().__init__()
        self.open = []
        self.wrong = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.open.append(tag)

    def handle_startendtag(self, tag, attrs):
        pass  # <rect .../> and <path .../> in the QR codes

    def handle_endtag(self, tag):
        if not self.open or self.open.pop() != tag:
            self.wrong.append(tag)


# --------------------------------------------------------------------------
# phone_card.py
# --------------------------------------------------------------------------

def test_iphone_part_has_codes_address_email_and_five_steps(homecloud_home, capsys):
    page, out = make_card(homecloud_home, capsys, "--email", "jane@example.com")

    iphone = part(page, "iphone")
    assert iphone.count('<svg class="qr"') == 2
    assert TS_APP in iphone and IMMICH_APP in iphone
    assert "play.google.com" not in iphone  # the iPhone part keeps to the App Store
    assert '<p class="big">%s</p>' % URL in page  # trailing slash dropped, large type
    assert "jane@example.com" in page
    items = step_items(iphone)
    assert len(items) == 5
    steps = " ".join(items)
    for words in ("Tailscale", "Get Started", "Allow", "same account", "Immich", URL,
                  "Backup Albums", "Select", "Recents", "Enable Backup"):
        assert words in steps
    assert "Tailscale is connected" in page  # a 100.x address works only through Tailscale
    assert "<script" not in page and "http://" not in page.replace(URL, "").replace("http://www.w3.org", "")

    for n in range(1, 6):
        assert "\n  %d. " % n in out
    assert "Server address: " + URL in out and "Immich email: jane@example.com" in out
    assert "Saved: " + str(homecloud_home / "phone.html") in out
    assert "\u2014" not in page + out  # no em dashes in user text

    if os.name != "nt":
        assert stat.S_IMODE(os.stat(homecloud_home).st_mode) == 0o700
        assert stat.S_IMODE(os.stat(homecloud_home / "phone.html").st_mode) == 0o600


def test_android_part_has_its_own_codes_and_six_steps(homecloud_home, capsys):
    page, _ = make_card(homecloud_home, capsys, "--email", "jane@example.com")

    android = part(page, "android")
    assert android.count('<svg class="qr"') == 2
    assert TS_PLAY in android and IMMICH_PLAY in android
    assert "apps.apple.com" not in android  # Google Play links only
    steps = [plain(item) for item in step_items(android)]
    assert len(steps) == 6
    assert "install Tailscale from Google Play" in steps[0]
    assert all(words in steps[1] for words in ("Get Started", "OK", "Log in", "same account"))
    assert "install Immich from Google Play" in steps[2]
    assert URL in steps[3] and "jane@example.com" in steps[3] and "Immich password" in steps[3]
    assert all(words in steps[4] for words in ("Backup Albums", "Select", "Camera", "Enable Backup"))
    # what background backup needs: notifications, then unrestricted battery use
    assert all(words in steps[5] for words in ("notifications", "Allow", "Settings", "Apps", "Immich",
                                               "Battery", "Unrestricted"))
    for words in ("iOS", "iPhone", "Recents", "Allow Full Access", "Background App Refresh"):
        assert words not in " ".join(steps)  # nothing from the iPhone steps
    tips = plain(re.search(r'<ul class="tips">(.*?)</ul>', android, re.S).group(1))
    assert "Tailscale connected" in tips and "dontkillmyapp.com" in tips
    assert "Background App Refresh" not in tips


def test_one_page_holds_both_phones_and_asks_nothing(homecloud_home, capsys):
    page, _ = make_card(homecloud_home, capsys, "--email", "jane@example.com")

    assert re.findall(r'<section class="phone" id="(\w+)"', page) == ["iphone", "android"]  # in this order
    assert page.count('<svg class="qr"') == 4 and page.count('<ol class="steps">') == 2
    assert re.findall(r'<span class="tag">(Step \d)</span>', page) == ["Step 1", "Step 3", "Step 1", "Step 3"]
    assert page.count('<p class="big">') == 1 and "(step 4)" in page  # one address, step 4 on both phones
    assert '<a href="#iphone">iPhone</a>' in page and '<a href="#android">Android</a>' in page
    ids = re.findall(r'\bid="([^"]+)"', page)
    assert len(ids) == len(set(ids))
    assert all(target in ids for target in re.findall(r'<a href="#([^"]+)"', page))  # each link lands
    assert "<script" not in page  # no choice to make and nothing to run

    parser = Balance()
    parser.feed(page)
    parser.close()
    assert parser.wrong == [] and parser.open == []  # every tag closes, in order


def test_text_output_has_the_steps_of_both_phones_and_the_four_links(homecloud_home, capsys):
    _, out = make_card(homecloud_home, capsys, "--email", "jane@example.com")

    lines = out.splitlines()
    first, second, third = (lines.index(x) for x in ("On your iPhone:", "On your Android phone:",
                                                     "Server address: " + URL))
    assert first < second < third  # the iPhone steps, then the Android steps, then the address
    iphone = out.split("On your iPhone:\n", 1)[1].split("\n\n", 1)[0]
    android = out.split("On your Android phone:\n", 1)[1].split("\n\n", 1)[0]
    assert re.findall(r"^  (\d+)\. ", iphone, re.M) == ["1", "2", "3", "4", "5"]
    assert re.findall(r"^  (\d+)\. ", android, re.M) == ["1", "2", "3", "4", "5", "6"]
    assert "install Tailscale from Google Play." in android
    assert "Tap Camera and any other album you want to back up." in android
    assert "Settings > Apps > Immich > Battery and choose Unrestricted." in android
    for line in ("Tailscale in the App Store: " + TS_APP, "Immich in the App Store: " + IMMICH_APP,
                 "Tailscale in Google Play: " + TS_PLAY, "Immich in Google Play: " + IMMICH_PLAY):
        assert line in lines
    assert lines[-1] == "Saved: " + str(homecloud_home / "phone.html")


def test_without_a_tailscale_address_the_tailscale_code_steps_and_tips_are_left_out(homecloud_home, capsys):
    page, out = make_card(homecloud_home, capsys, "--email", "jane@example.com", url=WIFI_URL)

    assert "Tailscale" not in page and "Tailscale" not in out  # no code, step, tip or note about it
    assert TS_APP not in page + out and TS_PLAY not in page + out
    q = pc.QUIET_ZONE
    grids = svg_matrices(page)
    assert [[row[q:-q] for row in grid[q:-q]] for grid in grids] == [pc.qr_matrix(IMMICH_APP),
                                                                     pc.qr_matrix(IMMICH_PLAY)]
    assert re.findall(r'<span class="tag">(Step \d)</span>', page) == ["Step 1", "Step 1"]
    assert '<p class="big">%s</p>' % WIFI_URL in page and "(step 2)" in page and "(step 4)" not in page

    iphone = [plain(item) for item in step_items(part(page, "iphone"))]
    android = [plain(item) for item in step_items(part(page, "android"))]
    assert len(iphone) == 3 and len(android) == 4
    assert "install Immich." in iphone[0] and "install Immich from Google Play." in android[0]
    assert all(WIFI_URL in step[1] and "jane@example.com" in step[1] for step in (iphone, android))
    assert "Recents" in iphone[2] and "Camera" in android[2] and "Unrestricted" in android[3]
    assert "follow the three steps" in part(page, "iphone") and "follow the four steps" in part(page, "android")
    for name, kept in (("iphone", ("Allow Full Access", "Background App Refresh")),
                       ("android", ("allow access to all", "dontkillmyapp.com"))):
        tips = plain(re.search(r'<ul class="tips">(.*?)</ul>', part(page, name), re.S).group(1))
        assert all(words in tips for words in kept)

    iphone_text = out.split("On your iPhone:\n", 1)[1].split("\n\n", 1)[0]
    android_text = out.split("On your Android phone:\n", 1)[1].split("\n\n", 1)[0]
    assert re.findall(r"^  (\d+)\. ", iphone_text, re.M) == ["1", "2", "3"]
    assert re.findall(r"^  (\d+)\. ", android_text, re.M) == ["1", "2", "3", "4"]
    lines = out.splitlines()
    assert "Immich in the App Store: " + IMMICH_APP in lines and "Immich in Google Play: " + IMMICH_PLAY in lines


@pytest.mark.parametrize("extra", [(), ("--email", "jane@example.com")])
def test_text_for_the_person_follows_the_style_rules(extra, homecloud_home, capsys):
    page, out = make_card(homecloud_home, capsys, *extra)
    shown = plain(re.sub(r"<style>.*?</style>", "", page, flags=re.S))
    printed = "\n".join(line for line in out.splitlines() if str(homecloud_home) not in line)
    for text in (shown, printed):
        assert "\u2014" not in text and "\u2013" not in text  # no em or en dashes
        assert not re.search(r"\b(test|tests|tested|testing|untested)\b", text, re.I)  # no talk about testing


def test_command_line_options_are_unchanged(capsys):
    with pytest.raises(SystemExit) as stop:
        pc.main(["--help"])
    assert stop.value.code == 0
    shown = capsys.readouterr().out
    assert all(option in shown for option in ("--url", "--email", "--open"))
    with pytest.raises(SystemExit) as stop:
        pc.main([])
    assert stop.value.code == 2  # --url is required


def test_without_email_the_step_asks_for_your_own(homecloud_home, capsys):
    page, out = make_card(homecloud_home, capsys)
    assert "Your Immich email" not in page
    assert out.count("Sign in with your Immich email and password.") == 2  # step 4 on each phone


def test_svg_matches_the_qr_code_of_each_store_link(homecloud_home, capsys):
    page, _ = make_card(homecloud_home, capsys)
    grids = svg_matrices(page)
    assert len(grids) == 4
    for grid, link in zip(grids, ALL_LINKS):
        modules = pc.qr_matrix(link)
        q = pc.QUIET_ZONE
        assert len(grid) == len(modules) + 2 * q
        assert [row[q:-q] for row in grid[q:-q]] == modules
        assert not any(grid[0]) and not any(row[0] for row in grid)  # quiet zone stays light


def test_each_part_shows_the_codes_of_its_own_store(homecloud_home, capsys):
    page, _ = make_card(homecloud_home, capsys)
    q = pc.QUIET_ZONE
    for name, store, links in (("iphone", "the App Store", (TS_APP, IMMICH_APP)),
                               ("android", "Google Play", (TS_PLAY, IMMICH_PLAY))):
        grids = svg_matrices(part(page, name))
        assert [[row[q:-q] for row in grid[q:-q]] for grid in grids] == [pc.qr_matrix(link) for link in links]
        labels = re.findall(r'aria-label="(QR code: [^"]+)"', part(page, name))
        assert labels == ["QR code: Tailscale in " + store, "QR code: Immich in " + store]


def test_qr_codes_scan_back_to_the_store_links(homecloud_home, capsys):
    zxingcpp = pytest.importorskip("zxingcpp")  # test-only decoder, not a kit dependency
    np = pytest.importorskip("numpy")
    page, _ = make_card(homecloud_home, capsys)
    found = []
    for grid in svg_matrices(page):
        scale = 8
        img = np.array([[0 if cell else 255 for cell in row for _ in range(scale)]
                        for row in grid for _ in range(scale)], dtype=np.uint8)
        found.append([r.text for r in zxingcpp.read_barcodes(img)])
    assert found == [[link] for link in ALL_LINKS]


@pytest.mark.parametrize("url", ["http://localhost:2283", "http://127.0.0.1:2283", "http://[::1]:2283",
                                 "ftp://100.64.0.1", "100.64.0.1:2283", "http://100.64.0.1:port",
                                 'http://100.64.0.1:2283/"><script>'])
def test_refuses_addresses_the_phone_cannot_use(url, homecloud_home, capsys):
    assert pc.main(["--url", url]) == 2
    assert "http://100.x.y.z:2283" in capsys.readouterr().err
    assert not (homecloud_home / "phone.html").exists()


def test_refuses_a_bad_email_and_escapes_the_page(homecloud_home, capsys):
    assert pc.main(["--url", URL, "--email", "not an email"]) == 2
    assert pc.main(["--url", "http://jane-mac.tail1234.ts.net:2283/?a=1&b=2"]) == 0
    page = (homecloud_home / "phone.html").read_text(encoding="utf-8")
    assert "?a=1&amp;b=2" in page and "?a=1&b=2" not in page


def test_open_uses_the_platform_opener(homecloud_home, capsys, monkeypatch):
    calls = []

    class Done:
        returncode = 0

    monkeypatch.setattr(pc.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or Done())
    monkeypatch.setattr(pc.os, "name", "posix")
    for platform, opener in (("darwin", "open"), ("linux", "xdg-open")):
        monkeypatch.setattr(pc.sys, "platform", platform)
        make_card(homecloud_home, capsys, "--open")
        assert calls[-1] == [opener, str(homecloud_home / "phone.html")]

    started = []
    monkeypatch.setattr(pc.os, "name", "nt")
    monkeypatch.setattr(pc.os, "startfile", started.append, raising=False)
    assert pc.open_page("C:\\phone.html") and started == ["C:\\phone.html"]


# --------------------------------------------------------------------------
# tailscale_setup.sh, with stand-ins for tailscale, open, curl, pkgutil, sudo, installer
# --------------------------------------------------------------------------

FAKE_TAILSCALE = r"""#!/bin/bash
d="$FAKE_TS_DIR"
echo "$* BE_CLI=${TAILSCALE_BE_CLI:-}" >> "$d/calls"
state=$(cat "$d/state")
case "$1" in
  status)
    if [ -f "$d/down" ]; then echo "failed to connect to local Tailscale service" >&2; exit 1; fi
    auth=""
    if [ "$state" = NeedsLogin ] && [ -f "$d/authurl" ]; then auth=$(cat "$d/authurl"); fi
    cat <<EOF
{
  "Version": "1.90.0",
  "BackendState": "$state",
  "AuthURL": "$auth",
  "TailscaleIPs": [
    "100.101.102.103",
    "fd7a:115c:a1e0::1"
  ],
  "Self": {
    "HostName": "Janes-MacBook",
    "DNSName": "janes-macbook.tail1234.ts.net.",
    "TailscaleIPs": [
      "100.101.102.103"
    ],
    "Online": true,
    "KeyExpiry": "2027-04-28T17:00:00Z"
  },
  "MagicDNSSuffix": "tail1234.ts.net",
  "CurrentTailnet": {
    "Name": "jane@example.com",
    "MagicDNSSuffix": "tail1234.ts.net",
    "MagicDNSEnabled": $(cat "$d/magicdns" 2>/dev/null || echo true)
  },
  "Peer": null
}
EOF
    ;;
  ip)
    if [ "$state" = Running ]; then echo 100.101.102.103; else echo "no current Tailscale IPs" >&2; exit 1; fi ;;
  up)
    if [ "$state" = Stopped ]; then echo Running > "$d/state"; exit 0; fi
    echo "https://login.tailscale.com/a/abc123" > "$d/authurl"
    printf '\nTo authenticate, visit:\n\n\thttps://login.tailscale.com/a/abc123\n\n' >&2
    i=0
    while [ "$(cat "$d/state")" != Running ] && [ $i -lt 400 ]; do sleep 0.05; i=$((i + 1)); done
    echo "Success." >&2 ;;
esac
"""

FAKES = {
    # Opening the sign-in link stands for the person signing in; opening the app starts its service.
    "open": r"""#!/bin/bash
echo "$*" >> "$FAKE_TS_DIR/opened"
case "$1" in
  https://login.tailscale.com/*) [ -f "$FAKE_TS_DIR/never_sign_in" ] || echo Running > "$FAKE_TS_DIR/state" ;;
  *Tailscale.app) rm -f "$FAKE_TS_DIR/down" ;;
esac
""",
    "curl": r"""#!/bin/bash
echo "$*" >> "$FAKE_TS_DIR/curl"
while [ $# -gt 0 ]; do if [ "$1" = -o ]; then echo pkg > "$2"; fi; shift; done
""",
    "pkgutil": r"""#!/bin/bash
cat "$FAKE_TS_DIR/signature"
""",
    "id": r"""#!/bin/bash
echo 501
""",
    "sudo": r"""#!/bin/bash
echo "$*" >> "$FAKE_TS_DIR/sudo"
if [ "$1" = -n ]; then exit 1; fi
exec "$@"
""",
    "osascript": r"""#!/bin/bash
echo "$*" >> "$FAKE_TS_DIR/osascript"
shift
exec "$@"
""",
    "installer": r"""#!/bin/bash
echo "$*" >> "$FAKE_TS_DIR/installer"
mkdir -p "$HOMECLOUD_TS_APP/Contents/MacOS"
cp "$FAKE_TS_DIR/tailscale" "$HOMECLOUD_TS_APP/Contents/MacOS/Tailscale"
""",
}

SIGNED = """Package "Tailscale.pkg":
   Status: signed by a developer certificate issued by Apple for distribution
   Notarization: trusted by the Apple notary service
   Certificate Chain:
    1. Developer ID Installer: Tailscale Inc. (W5364U7YZB)
"""

needs_bash = pytest.mark.skipif(os.name == "nt" or not shutil.which("bash"), reason="needs bash")


@pytest.fixture
def mac(tmp_path):
    fake = tmp_path / "fake"
    bin_dir = tmp_path / "bin"
    fake.mkdir()
    bin_dir.mkdir()
    (fake / "tailscale").write_text(FAKE_TAILSCALE)
    (fake / "tailscale").chmod(0o755)
    for name, body in FAKES.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    (fake / "signature").write_text(SIGNED)
    (fake / "state").write_text("NeedsLogin\n")
    app = tmp_path / "Applications" / "Tailscale.app"

    class Mac:
        dir = fake

        def install_app(self):
            (app / "Contents" / "MacOS").mkdir(parents=True)
            shutil.copy(fake / "tailscale", app / "Contents" / "MacOS" / "Tailscale")

        def run(self, *args, timeout=60):
            env = dict(os.environ, FAKE_TS_DIR=str(fake), HOMECLOUD_TS_APP=str(app), HOMECLOUD_TS_POLL="0.05",
                       HOMECLOUD_ALLOW_NON_MAC="1", TMPDIR=str(tmp_path),
                       PATH=str(bin_dir) + os.pathsep + "/usr/bin" + os.pathsep + "/bin")
            return subprocess.run(["bash", str(SETUP_SH), *args], env=env, stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, timeout=timeout, check=False)

        def log(self, name):
            path = fake / name
            return path.read_text() if path.exists() else ""

    return Mac()


@needs_bash
def test_ts_script_passes_bash_n_and_shellcheck():
    assert subprocess.run(["bash", "-n", str(SETUP_SH)], check=False).returncode == 0
    shellcheck = shutil.which("shellcheck")
    if shellcheck:
        done = subprocess.run([shellcheck, "-s", "bash", str(SETUP_SH)], capture_output=True, text=True, check=False)
        assert done.returncode == 0, done.stdout


@needs_bash
def test_ts_already_connected_only_prints_the_address(mac):
    mac.install_app()
    (mac.dir / "state").write_text("Running\n")
    done = mac.run()  # no --yes and no terminal: nothing to ask, nothing changes
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines() == ["Address: http://100.101.102.103:2283",
                                        "Name: janes-macbook.tail1234.ts.net"]
    assert " up " not in " " + mac.log("calls") and mac.log("opened") == ""
    assert "BE_CLI=1" in mac.log("calls") and "BE_CLI= " not in mac.log("calls")


@needs_bash
def test_ts_asks_before_it_changes_anything(mac):
    done = mac.run()
    assert done.returncode == 2
    assert "Plan:" in done.stdout and "Nothing was changed" in done.stdout
    assert mac.log("curl") == "" and mac.log("installer") == "" and mac.log("opened") == ""


@needs_bash
def test_ts_installs_signs_in_and_prints_the_address(mac):
    done = mac.run("--yes")
    assert done.returncode == 0, done.stdout + done.stderr
    out = done.stdout
    assert "https://pkgs.tailscale.com/stable/Tailscale-latest-macos.pkg" in mac.log("curl")
    assert re.fullmatch(r"-pkg \S+/Tailscale\.pkg -target /\n", mac.log("installer"))
    assert "installer -pkg" in mac.log("osascript")  # no terminal: macOS asks for the password itself
    opened = mac.log("opened").splitlines()
    assert opened[0].endswith("Tailscale.app") and opened[1] == "https://login.tailscale.com/a/abc123"
    assert "Address: http://100.101.102.103:2283\nName: janes-macbook.tail1234.ts.net\n" in out
    assert "Disable key expiry" in out
    assert re.findall(r"^up BE_CLI=1$", mac.log("calls"), re.M) == ["up BE_CLI=1"]  # no flags, no auth key
    assert "auth-key" not in mac.log("calls") + out


@needs_bash
def test_ts_refuses_a_package_that_tailscale_did_not_sign(mac):
    (mac.dir / "signature").write_text('Package "Tailscale.pkg":\n   Status: no signature\n')
    done = mac.run("--yes")
    assert done.returncode == 1
    assert "not signed by Tailscale" in done.stderr
    assert mac.log("installer") == ""


@needs_bash
def test_ts_stopped_turns_on_with_another_port(mac):
    mac.install_app()
    (mac.dir / "state").write_text("Stopped\n")
    (mac.dir / "magicdns").write_text("false\n")
    done = mac.run("--yes", "--port", "8443")
    assert done.returncode == 0, done.stderr
    assert "Address: http://100.101.102.103:8443" in done.stdout
    assert "MagicDNS is off" in done.stdout
    assert "https://login" not in mac.log("opened")


@needs_bash
def test_ts_waits_for_the_service_then_times_out_with_a_clear_sentence(mac):
    mac.install_app()
    (mac.dir / "never_sign_in").write_text("")
    (mac.dir / "down").write_text("")  # removed when the script opens the app
    done = mac.run("--yes", "--timeout", "1")
    assert done.returncode == 1
    assert "You have not finished signing in to Tailscale" in done.stderr
    assert "https://login.tailscale.com/a/abc123" in mac.log("opened")


@needs_bash
def test_ts_rejects_bad_options(mac):
    assert mac.run("--port", "http").returncode == 2
    assert mac.run("--timeout", "-1").returncode == 2
    assert mac.run("--nope").returncode == 2
