#!/usr/bin/env python3
"""Homecloud: set up Immich, so you never open its web page or copy an API key.

    python3 scripts/immich/bootstrap.py --url http://localhost:2283
            [--email EMAIL] [--name NAME] [--ml off] [--password-stdin]

What it does, in order
    1. Waits until Immich answers.
    2. If Immich has no account yet, it creates the first admin account and signs in.
       If it has one, it uses the saved API key when that key still works. Otherwise it
       asks for the admin's email and password and signs in.
    3. With --ml off (or --ml on), it reads the server settings, changes only the switch
       for machine learning, and writes them back.
    4. Marks Immich's welcome screens as done, so the web page does not show them.
    5. Creates an API key named "Homecloud" with all permissions and saves it in the file
       that import_library.py reads (~/.homecloud/immich-api-key, mode 0600, folder 0700).
       A second run keeps a saved key that still works.
    6. Signs out of the setup session.

The password
    --password-stdin reads it from standard input (one line). Otherwise a Mac shows a
    window with a hidden text field (osascript), and asks twice for a new account. Other
    computers ask in the terminal with hidden input. The password goes only to Immich. It
    is never printed, logged, saved or put on a command line. The same goes for the
    session token and the API key. osascript gets only the question and the email.

Exit codes
    0  done; the last line is "Result: OK"
    1  something failed
    2  usage error, or an answer is missing (Cancel, no window and no terminal)

Sources: Immich v3.3.5, open-api/immich-openapi-specs.json
    GET  /server/ping       pingServer, ServerPingResponse.res is "pong"
    GET  /server/version    getServerVersion: major, minor, patch
    GET  /server/config     getServerConfig, ServerConfigDto.isInitialized. Deprecated in
                            v3.2.0. Its replacement GET /public/config has no such field.
    POST /auth/admin-sign-up  signUpAdmin, SignUpDto {email, name, password}, HTTP 201
    POST /auth/login        login, LoginCredentialDto {email, password}, HTTP 201
                            LoginResponseDto {accessToken, userEmail, isAdmin}
    POST /auth/logout       logout
    GET, PUT /admin/config  getAdminConfig, updateAdminConfig (added in v3.2.0), AdminConfigDto
    GET, PUT /system-config getConfig, updateConfig (deprecated in v3.2.0, same body), used
                            when /admin/config is missing.
                            AdminConfigDto.machineLearning.enabled
    POST /system-metadata/admin-onboarding  updateAdminOnboarding, {isOnboarded: true}
    PUT  /users/me/onboarding               setUserOnboarding, {isOnboarded: true}
    POST /api-keys          createApiKey, ApiKeyCreateDto {name, permissions: ["all"]},
                            HTTP 201, ApiKeyCreateResponseDto.secret
    GET  /users/me          getMyUser, UserAdminResponseDto {id, email, isAdmin}
    GET  /api-keys/me       getMyApiKey, ApiKeyResponseDto.permissions
    Sign-in: "Authorization: Bearer <accessToken>" (securitySchemes.bearer); API key:
    the x-api-key header (securitySchemes.api_key).
    Password length: at least 8 characters, as ChangePasswordDto.newPassword (minLength 8).
    SignUpDto sets no minimum.
    osascript, display dialog with "default answer" and "hidden answer": Apple's
    AppleScript Language Guide, Standard Additions.
"""

from __future__ import annotations

import argparse
import getpass
import json
import locale
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional, Tuple

DEFAULT_URL = "http://localhost:2283"
# The same file and format as DEFAULT_KEY_FILE and cmd_save_key in import_library.py:
# the key on one line, then a newline.
DEFAULT_KEY_FILE = Path.home() / ".homecloud" / "immich-api-key"
KEY_NAME = "Homecloud"
DEFAULT_NAME = "Admin"
MIN_PASSWORD = 8
TRIES = 3
DIALOG_SECONDS = 600
DEFAULT_WAIT = 300

# Runs with the question, the default answer and "hidden" or "plain" as arguments. Prints
# "ok:" and the answer, or "gave-up". Cancel makes osascript fail with error -128.
APPLESCRIPT = (
    "on run argv",
    "set question to item 1 of argv",
    "set defaultAnswer to item 2 of argv",
    'set isHidden to ((item 3 of argv) is "hidden")',
    "try",
    "activate",
    "end try",
    "with timeout of %d seconds" % (DIALOG_SECONDS + 60),
    "set r to display dialog question default answer defaultAnswer hidden answer isHidden "
    'with title "Homecloud" buttons {"Cancel", "OK"} default button "OK" '
    'cancel button "Cancel" giving up after %d' % DIALOG_SECONDS,
    "end timeout",
    'if gave up of r then return "gave-up"',
    'return "ok:" & (text returned of r)',
    "end run",
)


# ----------------------------------------------------------------------------- output

def say(text: str = "") -> None:
    print(text, flush=True)


class Stop(Exception):
    """Ends the program with a message and an exit code."""

    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


# ------------------------------------------------------------------------------- API

class ApiError(Exception):
    def __init__(self, status: int, method: str, path: str, message: str) -> None:
        super().__init__(f"{method} /api{path} failed with HTTP {status}: {message}")
        self.status = status
        self.message = message


class Unreachable(Exception):
    pass


SESSION = object()  # "use the sign-in or key this Api holds"


def error_message(err: urllib.error.HTTPError) -> str:
    try:
        raw = err.read(4000).decode("utf-8", "replace")
    except OSError:
        return err.reason if isinstance(err.reason, str) else ""
    try:
        message = json.loads(raw).get("message")
    except (ValueError, AttributeError):
        return raw.strip()[:200]
    if isinstance(message, list):
        return "; ".join(str(m) for m in message)[:300]
    return str(message or "")[:300]


class Api:
    """Small client for the Immich REST API (base URL + /api)."""

    def __init__(self, url: str) -> None:
        url = url.strip().rstrip("/")
        if url.endswith("/api"):
            url = url[: -len("/api")]
        if not re.match(r"^https?://[^/\s]+", url):
            raise Stop(f"The address must start with http:// or https://, like {DEFAULT_URL}.", 2)
        self.url = url
        self.base = url + "/api"
        self.auth: Optional[Tuple[str, str]] = None  # ("bearer", token) or ("key", key)
        # Immich runs on this computer or on the tailnet, so ignore proxy settings.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, method: str, path: str, body=None, auth=SESSION, timeout: float = 60):
        headers = {"Accept": "application/json"}
        auth = self.auth if auth is SESSION else auth
        if auth:
            kind, secret = auth
            if kind == "bearer":
                headers["Authorization"] = "Bearer " + secret
            else:
                headers["x-api-key"] = secret
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with self.opener.open(req, timeout=timeout) as resp:
                raw = resp.read()
                status = resp.status
        except urllib.error.HTTPError as err:
            raise ApiError(err.code, method, path, error_message(err)) from None
        except (urllib.error.URLError, OSError) as err:
            raise Unreachable(str(getattr(err, "reason", err))) from None
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            raise ApiError(status, method, path, "the answer is not JSON") from None


def wait_for_server(api: Api, seconds: float, sleep: Callable[[float], None] = time.sleep) -> None:
    deadline = time.monotonic() + seconds
    told = False
    while True:
        try:
            answer = api.call("GET", "/server/ping", auth=None, timeout=5)
            if isinstance(answer, dict) and answer.get("res") == "pong":
                return
            raise Stop(f"{api.url} answered, but not like Immich. Check the address.", 1)
        except ApiError as err:
            if err.status < 500:
                raise Stop(f"{api.url} answered, but not like Immich (HTTP {err.status}). "
                           "Check the address.", 1) from None
        except Unreachable:
            pass
        if time.monotonic() >= deadline:
            raise Stop(f"Immich did not answer at {api.url} within {int(seconds)} seconds. Make sure "
                       "Docker Desktop is running and Immich has started, then run this command again.", 1)
        if not told:
            say(f"Waiting for Immich to answer at {api.url} ...")
            told = True
        sleep(2)


def server_version(api: Api) -> str:
    try:
        v = api.call("GET", "/server/version", auth=None)
        return f"v{v['major']}.{v['minor']}.{v['patch']}"
    except (ApiError, KeyError, TypeError):
        return "(unknown version)"


def has_admin(api: Api) -> Optional[bool]:
    """True or False from ServerConfigDto.isInitialized; None when the server does not say."""
    try:
        config = api.call("GET", "/server/config", auth=None)
    except ApiError as err:
        if err.status in (404, 405, 410):
            return None
        raise
    flag = config.get("isInitialized") if isinstance(config, dict) else None
    return flag if isinstance(flag, bool) else None


# ------------------------------------------------------------------------- questions

def stdin_is_tty() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def dialog(osascript: str, question: str, default: str = "", hidden: bool = False) -> Optional[str]:
    """Shows a Mac window with a text field. Returns the answer, or None when no window
    can be shown. Only the question, the default answer and the word hidden or plain go
    on osascript's command line; the answer comes back on its standard output."""
    cmd = [osascript]
    for line in APPLESCRIPT:
        cmd += ["-e", line]
    cmd += [question, "" if hidden else default, "hidden" if hidden else "plain"]
    try:
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=DIALOG_SECONDS + 120)
    except subprocess.TimeoutExpired:
        raise Stop("The Homecloud window got no answer in time. Run the same command again.", 2) from None
    except OSError:
        return None
    if proc.returncode != 0:
        if b"-128" in proc.stderr:
            raise Stop("You chose Cancel, so the setup stopped. Run the same command again when "
                       "you are ready.", 2)
        return None
    out = proc.stdout.decode("utf-8", "replace")
    if out.endswith("\n"):
        out = out[:-1]
    if out == "gave-up":
        raise Stop(f"The Homecloud window closed after {DIALOG_SECONDS // 60} minutes without an "
                   "answer. Run the same command again.", 2)
    if out.startswith("ok:"):
        return out[3:]
    return None


class Asker:
    """Asks the person: a Mac window first, else the terminal. --password-stdin wins for the
    password."""

    def __init__(self, password_stdin: bool) -> None:
        self.password_stdin = password_stdin
        self.osascript = shutil.which("osascript")
        self._stdin_password: Optional[str] = None

    def stdin_password(self) -> str:
        if self._stdin_password is None:
            stream = getattr(sys.stdin, "buffer", None)
            data = stream.readline() if stream is not None else sys.stdin.readline().encode("utf-8")
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = data.decode(locale.getpreferredencoding(False), "replace")
            if text.startswith("﻿"):
                text = text[1:]
            text = text.rstrip("\r\n")
            if not text:
                raise Stop("--password-stdin was given, but no password came in on standard input.", 2)
            self._stdin_password = text
        return self._stdin_password

    def text(self, question: str, default: str = "") -> str:
        if self.osascript:
            answer = dialog(self.osascript, question, default, hidden=False)
            if answer is not None:
                return answer.strip()
        if not self.password_stdin and stdin_is_tty():
            say(question)
            answer = input(f"Email [{default}]: " if default else "Email: ").strip()
            return answer or default
        raise Stop("There is no window or terminal to ask for the email address. Run the command "
                   "again with --email followed by the address.", 2)

    def secret(self, question: str) -> str:
        if self.osascript:
            answer = dialog(self.osascript, question, "", hidden=True)
            if answer is not None:
                return answer
        if stdin_is_tty():
            say(question)
            return getpass.getpass("Password (it stays hidden): ")
        raise Stop("There is no window or terminal to ask for the Immich password. Run the command "
                   "in your own Terminal window, or pass the password with --password-stdin.", 2)


def looks_like_email(text: str) -> bool:
    return re.fullmatch(r"[^@\s]+@[^@\s.][^@\s]*", text) is not None


def ask_email(asker: Asker, question: str, default: str = "") -> str:
    note = ""
    for _ in range(TRIES):
        email = asker.text(note + question, default)
        if looks_like_email(email):
            return email
        note = "That email address does not look right.\n\n"
        default = email
    raise Stop("No valid email address after 3 tries. Run the same command again, or add "
               "--email followed by the address.", 2)


def ask_new_password(asker: Asker, email: str) -> str:
    if asker.password_stdin:
        password = asker.stdin_password()
        if len(password) < MIN_PASSWORD:
            raise Stop(f"The password is too short. Use at least {MIN_PASSWORD} characters. "
                       "Nothing was created.", 2)
        return password
    note = ""
    for _ in range(TRIES):
        password = asker.secret(
            f"{note}Pick a password for your Immich account {email}.\n\nUse at least "
            f"{MIN_PASSWORD} characters. You type it again on your iPhone. Homecloud does not save it.")
        if len(password) < MIN_PASSWORD:
            note = f"That password is too short. Use at least {MIN_PASSWORD} characters.\n\n"
            continue
        if asker.secret("Type the same password again.") != password:
            note = "The two passwords were different. Pick one again.\n\n"
            continue
        return password
    raise Stop("No password was set after 3 tries. Run the same command again.", 2)


# ------------------------------------------------------------------------- accounts

def login(api: Api, email: str, password: str) -> dict:
    """Signs in and keeps the session token in api.auth (memory only)."""
    answer = api.call("POST", "/auth/login", body={"email": email, "password": password}, auth=None)
    token = answer.get("accessToken") if isinstance(answer, dict) else None
    if not isinstance(token, str) or not token:
        raise Stop("Immich accepted the sign-in but sent no session. Run the same command again.", 1)
    api.auth = ("bearer", token)
    return {"email": answer.get("userEmail") or email, "isAdmin": answer.get("isAdmin") is True}


def logout(api: Api) -> None:
    if api.auth and api.auth[0] == "bearer":
        try:
            api.call("POST", "/auth/logout", timeout=10)
        except (ApiError, Unreachable):
            pass
    api.auth = None


def sign_up(api: Api, email: str, name: str, password: str) -> None:
    try:
        api.call("POST", "/auth/admin-sign-up",
                 body={"email": email, "name": name, "password": password}, auth=None)
    except ApiError as err:
        if err.status == 400:
            raise Stop(f"Immich did not create the account: {err.message}. Run the same command "
                       "again.", 1) from None
        raise


def new_admin(api: Api, asker: Asker, args) -> dict:
    say("Immich has no account yet. Creating the admin account.")
    email = args.email or ask_email(
        asker, "Homecloud is setting up Immich, your photo library on this computer.\n\n"
               "Type an email address for your Immich account. You sign in with it on your iPhone.")
    password = ask_new_password(asker, email)
    sign_up(api, email, args.name, password)
    say(f"Created the admin account {email}.")
    return login(api, email, password)


def existing_admin(api: Api, asker: Asker, args) -> dict:
    tries = 1 if asker.password_stdin else TRIES
    email = args.email or ""
    note = ""
    for _ in range(tries):
        if not args.email:
            email = ask_email(asker, note + "Immich already has an admin account. Type its email "
                                            "address.", email)
            question = "Type the Immich password for " + email + "."
        else:
            question = note + "Type the Immich password for " + email + "."
        password = asker.stdin_password() if asker.password_stdin else asker.secret(question)
        try:
            return login(api, email, password)
        except ApiError as err:
            if err.status not in (400, 401):
                raise
        note = "Immich did not accept that email and password. Try again.\n\n"
    raise Stop(f"Immich did not accept the email and password for {email}. Run the same command "
               "again and type the password of your Immich admin account.", 1)


def unknown_admin(api: Api, asker: Asker, args) -> dict:
    """The server does not say whether it has an account: sign in, else create it."""
    email = args.email or ask_email(
        asker, "Type the email address of your Immich account. If you have none yet, type a new "
               "one. You sign in with it on your iPhone.")
    password = ask_new_password(asker, email)
    try:
        return login(api, email, password)
    except ApiError as err:
        if err.status not in (400, 401):
            raise
    sign_up(api, email, args.name, password)
    say(f"Created the admin account {email}.")
    return login(api, email, password)


# ------------------------------------------------------------------------------ key

def read_key(path: Path) -> Optional[str]:
    try:
        key = path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, NotADirectoryError):
        return None
    except (OSError, UnicodeDecodeError):
        return None
    return key or None


def key_user(api: Api, key: str) -> Optional[dict]:
    """The user of a key that works and has every permission, else None."""
    try:
        me = api.call("GET", "/users/me", auth=("key", key))
        info = api.call("GET", "/api-keys/me", auth=("key", key))
    except ApiError as err:
        if err.status in (400, 401, 403, 404):
            return None
        raise
    if not isinstance(me, dict) or not isinstance(info, dict):
        return None
    if "all" not in (info.get("permissions") or []):
        return None
    return me


def create_key(api: Api) -> str:
    made = api.call("POST", "/api-keys", body={"name": KEY_NAME, "permissions": ["all"]})
    secret = made.get("secret") if isinstance(made, dict) else None
    if not isinstance(secret, str) or not secret:
        raise Stop("Immich made the API key but did not send it back. Run the same command again.", 1)
    if key_user(api, secret) is None:
        raise Stop("Immich made the API key, but it does not work. Run the same command again.", 1)
    return secret


def save_key(path: Path, key: str) -> None:
    """Writes the key like import_library.py save-key: mode 0600, folder 0700."""
    folder = path.parent
    created = not folder.exists()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if created or folder == DEFAULT_KEY_FILE.parent:
        try:
            os.chmod(folder, 0o700)
        except OSError:
            pass
    tmp = folder / f".{path.name}.{os.getpid()}.tmp"
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(key + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


# ------------------------------------------------------------------- server settings

def set_machine_learning(api: Api, enabled: bool) -> bool:
    """Changes only machineLearning.enabled. Returns False when it already had that value."""
    for path in ("/admin/config", "/system-config"):
        try:
            config = api.call("GET", path)
        except ApiError as err:
            if err.status in (404, 405):
                continue
            raise
        break
    else:
        raise Stop("This Immich has no settings page that Homecloud knows. Open Immich in a browser "
                   "and switch machine learning in Administration > Settings > Machine Learning.", 1)
    ml = config.get("machineLearning") if isinstance(config, dict) else None
    if not isinstance(ml, dict) or not isinstance(ml.get("enabled"), bool):
        raise Stop("Immich's settings have no machine learning switch where Homecloud expects it. "
                   "Switch it in Administration > Settings > Machine Learning.", 1)
    if ml["enabled"] is enabled:
        return False
    ml["enabled"] = enabled
    saved = api.call("PUT", path, body=config)
    saved_ml = saved.get("machineLearning") if isinstance(saved, dict) else None
    if not isinstance(saved_ml, dict) or saved_ml.get("enabled") is not enabled:
        raise Stop("Immich did not keep the machine learning setting. Run the same command again.", 1)
    return True


def finish_welcome(api: Api, is_admin: bool) -> None:
    """Marks the welcome screens (admin and user onboarding) as done, where the API has them."""
    steps = [("PUT", "/users/me/onboarding")]
    if is_admin:
        steps.insert(0, ("POST", "/system-metadata/admin-onboarding"))
    for method, path in steps:
        try:
            api.call(method, path, body={"isOnboarded": True})
        except ApiError as err:
            if err.status in (404, 405):
                continue
            say(f"Note: Immich did not mark its welcome screens as done (HTTP {err.status}). "
                "You may see them once if you open Immich in a browser.")
            return
    say("Marked Immich's welcome screens as done.")


# ----------------------------------------------------------------------------- main

def run(args) -> int:
    api = Api(args.url)
    key_file = Path(args.key_file).expanduser()
    if args.name is not None and not args.name.strip():
        raise Stop("--name needs a name, like Admin.", 2)
    asker = Asker(args.password_stdin)
    wait_for_server(api, args.wait)
    say(f"Immich {server_version(api)} answers at {api.url}.")
    admin = has_admin(api)
    key_ok = False
    try:
        if admin is False:
            user = new_admin(api, asker, args)
        else:
            saved = read_key(key_file)
            found = key_user(api, saved) if saved else None
            if found is not None and args.email and str(found.get("email", "")).lower() != args.email.lower():
                say(f"The saved API key belongs to {found.get('email')}, not {args.email}.")
                found = None
            if found is not None:
                api.auth = ("key", saved)
                user = {"email": found.get("email") or "", "isAdmin": found.get("isAdmin") is True}
                key_ok = True
                say(f"The saved API key works for {user['email']}, so no password is needed.")
            else:
                if saved:
                    say("The saved API key does not work here. Signing in to make a new one.")
                user = existing_admin(api, asker, args) if admin else unknown_admin(api, asker, args)
        if not key_ok:
            say(f"Signed in as {user['email']}.")

        if args.ml:
            wanted = args.ml == "on"
            word = "on" if wanted else "off"
            if not user["isAdmin"]:
                say(f"Note: {user['email']} is not an Immich admin, so machine learning stays as it is.")
            elif set_machine_learning(api, wanted):
                say(f"Turned machine learning {word}.")
            else:
                say(f"Machine learning was already {word}.")

        finish_welcome(api, user["isAdmin"])

        if not key_ok:
            secret = create_key(api)
            save_key(key_file, secret)
            say(f'Created the API key "{KEY_NAME}" and saved it to {key_file}. '
                "Only your user account can read that file.")
    finally:
        logout(api)
    say(f"Email: {user['email']}")
    say("Result: OK")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Set up Immich without its web page: first admin account, settings and API key.",
        epilog="Details: scripts/immich/README.md")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"Immich address (default {DEFAULT_URL})")
    parser.add_argument("--email", default=None,
                        help="email of the Immich account; asked in a window or the terminal if left out")
    parser.add_argument("--name", default=DEFAULT_NAME, help=f"name of a new admin account (default {DEFAULT_NAME})")
    parser.add_argument("--ml", choices=("off", "on"), default=None,
                        help="turn machine learning off or on in the server settings (default: leave it)")
    parser.add_argument("--password-stdin", action="store_true",
                        help="read the password from standard input (one line)")
    parser.add_argument("--key-file", default=str(DEFAULT_KEY_FILE),
                        help="where to save the API key (default ~/.homecloud/immich-api-key, "
                             "the file import_library.py reads)")
    parser.add_argument("--wait", type=float, default=DEFAULT_WAIT,
                        help=f"seconds to wait for Immich to answer (default {DEFAULT_WAIT})")
    return parser


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except Stop as stop:
        say(str(stop))
        return stop.code
    except Unreachable:
        say(f"Immich stopped answering at {args.url}. Make sure Docker Desktop is running, then run "
            "the same command again.")
        return 1
    except ApiError as err:
        say(f"Immich refused a request: {err}. Run the same command again. If it fails again, "
            "restart Immich in Docker Desktop.")
        return 1
    except KeyboardInterrupt:
        say("Stopped. Run the same command again to continue.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
