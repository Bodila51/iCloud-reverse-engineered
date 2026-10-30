"""Tests for scripts/immich/bootstrap.py against a small mock of the Immich API (v3.3.5 shapes)."""

import copy
import importlib.util
import io
import json
import os
import socket
import stat
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "immich" / "bootstrap.py"


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bs = load("immich_bootstrap", "scripts/immich/bootstrap.py")
imp = load("import_library", "scripts/immich/import_library.py")

PASSWORD = "correct horse 9"
CONFIG = {
    "machineLearning": {"enabled": True, "urls": ["http://immich-machine-learning:3003"],
                        "clip": {"enabled": True, "modelName": "ViT-B-32__openai"},
                        "facialRecognition": {"enabled": True, "minFaces": 3}},
    "job": {"thumbnailGeneration": {"concurrency": 3}},
    "trash": {"enabled": True, "days": 30},
}


class MockImmich:
    """Answers like Immich v3.3.5 for the endpoints bootstrap.py uses, and records requests."""

    def __init__(self):
        self.users = {}       # email -> user dict with "password"
        self.tokens = {}      # session token -> email
        self.keys = {}        # key secret -> {"id", "name", "permissions", "email"}
        self.config = copy.deepcopy(CONFIG)
        self.admin_onboarded = False
        self.user_onboarded = set()
        self.requests = []    # (method, path, headers, body)
        self.has_admin_config = True   # False: /admin/config answers 404 (older servers)
        self.has_server_config = True  # False: /server/config answers 404 (future servers)
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code, body=None):
                raw = b"" if body is None else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _handle(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw) if raw else None
                path = self.path.split("?")[0]
                mock.requests.append((self.command, path, dict(self.headers), body))
                code, answer = mock.route(self.command, path, self.headers, body)
                self._send(code, answer)

            do_GET = do_POST = do_PUT = do_DELETE = _handle

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    # --------------------------------------------------------------------- helpers
    def add_admin(self, email="jane@example.com", password=PASSWORD, admin=True):
        self.users[email] = {"id": str(uuid.uuid4()), "email": email, "name": "Admin",
                             "password": password, "isAdmin": admin}

    def add_key(self, email, permissions=("all",)):
        secret = "key-" + uuid.uuid4().hex
        self.keys[secret] = {"id": str(uuid.uuid4()), "name": "Homecloud",
                             "permissions": list(permissions), "email": email}
        return secret

    def public_user(self, email):
        u = self.users[email]
        return {"id": u["id"], "email": u["email"], "name": u["name"], "isAdmin": u["isAdmin"]}

    def who(self, headers):
        auth = headers.get("Authorization") or ""
        if auth.startswith("Bearer ") and auth[7:] in self.tokens:
            return self.tokens[auth[7:]], "session", None
        key = headers.get("x-api-key")
        if key and key in self.keys:
            return self.keys[key]["email"], "key", key
        return None, None, None

    def secrets(self):
        return [PASSWORD] + list(self.tokens) + list(self.keys) + self.issued_tokens

    issued_tokens = []

    # ---------------------------------------------------------------------- routes
    def route(self, method, path, headers, body):
        if path == "/api/server/ping" and method == "GET":
            return 200, {"res": "pong"}
        if path == "/api/server/version" and method == "GET":
            return 200, {"major": 3, "minor": 3, "patch": 5, "prerelease": None}
        if path == "/api/server/config" and method == "GET":
            if not self.has_server_config:
                return 404, {"message": "Cannot GET /api/server/config"}
            return 200, {"isInitialized": bool(self.users), "isOnboarded": self.admin_onboarded,
                         "loginPageMessage": "", "trashDays": 30}
        if path == "/api/auth/admin-sign-up" and method == "POST":
            if not isinstance(body, dict) or set(body) != {"email", "name", "password"}:
                return 400, {"message": ["body must be SignUpDto"], "statusCode": 400}
            if self.users:
                return 400, {"message": "The server already has an admin", "statusCode": 400}
            self.add_admin(body["email"], body["password"])
            self.users[body["email"]]["name"] = body["name"]
            return 201, self.public_user(body["email"])
        if path == "/api/auth/login" and method == "POST":
            if not isinstance(body, dict) or set(body) != {"email", "password"}:
                return 400, {"message": ["body must be LoginCredentialDto"], "statusCode": 400}
            user = self.users.get(body["email"])
            if not user or user["password"] != body["password"]:
                return 401, {"message": "Incorrect email or password", "statusCode": 401}
            token = "tok-" + uuid.uuid4().hex
            self.tokens[token] = user["email"]
            self.issued_tokens.append(token)
            return 201, {"accessToken": token, "userId": user["id"], "userEmail": user["email"],
                         "name": user["name"], "isAdmin": user["isAdmin"], "isOnboarded": False,
                         "profileImagePath": "", "shouldChangePassword": False}

        email, kind, key = self.who(headers)
        if email is None:
            return 401, {"message": "Authentication required", "statusCode": 401}
        user = self.users[email]
        if path == "/api/auth/logout" and method == "POST":
            if kind == "session":
                del self.tokens[headers["Authorization"][7:]]
            return 200, {"successful": True, "redirectUri": "/auth/login"}
        if path == "/api/users/me" and method == "GET":
            return 200, self.public_user(email)
        if path == "/api/api-keys/me" and method == "GET":
            if kind != "key":
                return 403, {"message": "Not an API key", "statusCode": 403}
            k = self.keys[key]
            return 200, {"id": k["id"], "name": k["name"], "permissions": k["permissions"]}
        if path == "/api/api-keys" and method == "POST":
            if (not isinstance(body, dict) or not body.get("permissions")
                    or set(body) - {"name", "permissions"}):
                return 400, {"message": ["body must be ApiKeyCreateDto"], "statusCode": 400}
            secret = self.add_key(email, body["permissions"])
            self.keys[secret]["name"] = body.get("name", "API Key")
            k = self.keys[secret]
            dto = {"id": k["id"], "name": k["name"], "permissions": k["permissions"],
                   "createdAt": "2026-10-30T00:00:00.000Z", "updatedAt": "2026-10-30T00:00:00.000Z"}
            return 201, dict(dto, secret=secret, apiKey=dto)
        if path == "/api/users/me/onboarding" and method == "PUT":
            if body != {"isOnboarded": True}:
                return 400, {"message": ["body must be OnboardingDto"], "statusCode": 400}
            self.user_onboarded.add(email)
            return 200, {"isOnboarded": True}

        admin_paths = ("/api/admin/config", "/api/system-config", "/api/system-metadata/admin-onboarding")
        if path in admin_paths and not user["isAdmin"]:
            return 403, {"message": "Forbidden", "statusCode": 403}
        if path == "/api/admin/config" and not self.has_admin_config:
            return 404, {"message": "Cannot %s /api/admin/config" % method}
        if path in ("/api/admin/config", "/api/system-config"):
            if method == "GET":
                return 200, copy.deepcopy(self.config)
            if method == "PUT":
                if not isinstance(body, dict) or set(body) != set(self.config):
                    return 400, {"message": ["body must be the whole AdminConfigDto"], "statusCode": 400}
                self.config = copy.deepcopy(body)
                return 200, copy.deepcopy(self.config)
        if path == "/api/system-metadata/admin-onboarding" and method == "POST":
            if body != {"isOnboarded": True}:
                return 400, {"message": ["body must be AdminOnboardingUpdateDto"], "statusCode": 400}
            self.admin_onboarded = True
            return 204, None
        return 404, {"message": "Cannot %s %s" % (method, path)}

    def calls(self, method, path):
        return [r for r in self.requests if r[0] == method and r[1] == path]


@pytest.fixture
def immich():
    MockImmich.issued_tokens = []
    server = MockImmich()
    yield server
    server.close()


@pytest.fixture
def no_dialog(tmp_path, monkeypatch):
    """No osascript on PATH, so a Mac running the tests shows no windows."""
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))


def run(immich, tmp_path, monkeypatch, capsys, *extra, stdin=None):
    key_file = tmp_path / "home" / ".homecloud" / "immich-api-key"
    if stdin is not None:
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(stdin.encode("utf-8"))))
    else:
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"")))
    code = bs.main(["--url", immich.url, "--key-file", str(key_file), "--wait", "5"] + list(extra))
    out = capsys.readouterr().out
    for secret in immich.secrets():
        assert secret not in out
    return code, out, key_file


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


# ------------------------------------------------------------------------------ tests

def test_new_install_creates_admin_turns_ml_off_and_saves_key(immich, tmp_path, monkeypatch, capsys, no_dialog):
    code, out, key_file = run(immich, tmp_path, monkeypatch, capsys,
                              "--email", "jane@example.com", "--ml", "off", "--password-stdin",
                              stdin=PASSWORD + "\n")
    assert code == 0, out
    lines = out.strip().splitlines()
    assert lines[-1] == "Result: OK"
    assert lines[-2] == "Email: jane@example.com"

    signup = immich.calls("POST", "/api/auth/admin-sign-up")
    assert [r[3] for r in signup] == [{"email": "jane@example.com", "name": "Admin", "password": PASSWORD}]
    assert immich.users["jane@example.com"]["isAdmin"]

    # Machine learning: only the one switch changed, through /admin/config.
    puts = immich.calls("PUT", "/api/admin/config")
    expected = copy.deepcopy(CONFIG)
    expected["machineLearning"]["enabled"] = False
    assert [r[3] for r in puts] == [expected]
    assert not immich.calls("PUT", "/api/system-config")

    assert immich.admin_onboarded and "jane@example.com" in immich.user_onboarded

    made = immich.calls("POST", "/api/api-keys")
    assert [r[3] for r in made] == [{"name": "Homecloud", "permissions": ["all"]}]
    secret = key_file.read_text(encoding="utf-8")
    assert secret.endswith("\n") and secret.strip() in immich.keys
    assert immich.keys[secret.strip()]["name"] == "Homecloud"
    assert mode(key_file) == 0o600
    assert mode(key_file.parent) == 0o700
    assert [p.name for p in key_file.parent.iterdir()] == ["immich-api-key"]  # no temp file left

    # The setup session was signed out, and the password never went into a URL or a header.
    assert immich.tokens == {}
    for method, path, headers, body in immich.requests:
        assert PASSWORD not in path and PASSWORD not in json.dumps(headers)

    # import_library.py finds the key in the same place and accepts it.
    assert imp.DEFAULT_KEY_FILE == bs.DEFAULT_KEY_FILE
    assert imp.read_key_file(key_file) == secret.strip()
    me, missing = imp.check_key(imp.Api(immich.url, imp.read_key_file(key_file)))
    assert me["email"] == "jane@example.com" and missing == []


def test_second_run_keeps_the_working_key_and_asks_nothing(immich, tmp_path, monkeypatch, capsys, no_dialog):
    run(immich, tmp_path, monkeypatch, capsys, "--email", "jane@example.com", "--password-stdin",
        stdin=PASSWORD + "\n")
    key_file = tmp_path / "home" / ".homecloud" / "immich-api-key"
    before = key_file.read_text()
    immich.requests.clear()

    code, out, _ = run(immich, tmp_path, monkeypatch, capsys, "--ml", "off")  # no stdin, no email
    assert code == 0, out
    assert "no password is needed" in out
    assert out.strip().splitlines()[-1] == "Result: OK"
    assert key_file.read_text() == before
    assert not immich.calls("POST", "/api/auth/login")
    assert not immich.calls("POST", "/api/api-keys")
    assert immich.config["machineLearning"]["enabled"] is False  # done with the saved key
    assert len(immich.keys) == 1

    immich.requests.clear()
    code, out, _ = run(immich, tmp_path, monkeypatch, capsys, "--ml", "off")
    assert code == 0 and "Machine learning was already off." in out
    assert not immich.calls("PUT", "/api/admin/config")


def test_existing_admin_signs_in_and_replaces_a_key_that_fails(immich, tmp_path, monkeypatch, capsys, no_dialog):
    immich.add_admin()
    key_file = tmp_path / "home" / ".homecloud" / "immich-api-key"
    key_file.parent.mkdir(parents=True)
    key_file.write_text("old-key-that-immich-does-not-know\n")

    code, out, _ = run(immich, tmp_path, monkeypatch, capsys,
                       "--email", "jane@example.com", "--password-stdin", stdin=PASSWORD + "\r\n")
    assert code == 0, out
    assert "does not work here" in out
    assert not immich.calls("POST", "/api/auth/admin-sign-up")
    assert [r[3] for r in immich.calls("POST", "/api/auth/login")] == [
        {"email": "jane@example.com", "password": PASSWORD}]
    assert key_file.read_text().strip() in immich.keys
    assert mode(key_file) == 0o600
    assert immich.tokens == {}


def test_existing_admin_key_without_all_permissions_is_replaced(immich, tmp_path, monkeypatch, capsys, no_dialog):
    immich.add_admin()
    weak = immich.add_key("jane@example.com", permissions=("asset.read",))
    key_file = tmp_path / "home" / ".homecloud" / "immich-api-key"
    key_file.parent.mkdir(parents=True)
    key_file.write_text(weak + "\n")
    code, out, _ = run(immich, tmp_path, monkeypatch, capsys,
                       "--email", "jane@example.com", "--password-stdin", stdin=PASSWORD)
    assert code == 0, out
    new = key_file.read_text().strip()
    assert new != weak and immich.keys[new]["permissions"] == ["all"]


def test_wrong_password_fails_and_saves_nothing(immich, tmp_path, monkeypatch, capsys, no_dialog):
    immich.add_admin()
    code, out, key_file = run(immich, tmp_path, monkeypatch, capsys,
                              "--email", "jane@example.com", "--password-stdin", stdin="wrong password\n")
    assert code == 1
    assert "did not accept the email and password" in out
    assert "Result: OK" not in out
    assert "wrong password" not in out
    assert not key_file.exists()
    assert not immich.calls("POST", "/api/api-keys")


def test_short_new_password_is_refused(immich, tmp_path, monkeypatch, capsys, no_dialog):
    code, out, key_file = run(immich, tmp_path, monkeypatch, capsys,
                              "--email", "jane@example.com", "--password-stdin", stdin="short\n")
    assert code == 2 and "at least 8 characters" in out
    assert immich.users == {} and not key_file.exists()


def test_falls_back_to_system_config_and_to_sign_in_or_create(immich, tmp_path, monkeypatch, capsys, no_dialog):
    immich.has_admin_config = False   # an older server
    immich.has_server_config = False  # a future server that no longer says isInitialized
    code, out, _ = run(immich, tmp_path, monkeypatch, capsys, "--email", "jane@example.com",
                       "--ml", "off", "--password-stdin", stdin=PASSWORD + "\n")
    assert code == 0, out
    assert "jane@example.com" in immich.users
    assert [r[3]["machineLearning"]["enabled"] for r in immich.calls("PUT", "/api/system-config")] == [False]


def test_no_window_and_no_terminal_stops_with_a_plain_sentence(immich, tmp_path, monkeypatch, capsys, no_dialog):
    code, out, key_file = run(immich, tmp_path, monkeypatch, capsys, "--email", "jane@example.com")
    assert code == 2 and "--password-stdin" in out
    assert immich.users == {} and not key_file.exists()


def test_server_that_never_answers_times_out(tmp_path, capsys, no_dialog):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    code = bs.main(["--url", "http://127.0.0.1:%d" % port, "--wait", "0",
                    "--key-file", str(tmp_path / "k")])
    out = capsys.readouterr().out
    assert code == 1 and "did not answer" in out and "Docker Desktop" in out


FAKE_OSASCRIPT = r'''#!{python}
import json, sys
state = json.load(open({state!r}))
with open({log!r}, "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\n")
answer = state["answers"].pop(0)
json.dump(state, open({state!r}, "w"))
if answer is None:
    sys.stderr.write("execution error: User canceled. (-128)\n")
    sys.exit(1)
sys.stdout.write("ok:" + answer + "\n")
'''


def fake_osascript(tmp_path, answers):
    bindir = tmp_path / "fake-bin"
    bindir.mkdir()
    state, log = tmp_path / "osa-state.json", tmp_path / "osa-log.jsonl"
    state.write_text(json.dumps({"answers": answers}))
    exe = bindir / "osascript"
    exe.write_text(FAKE_OSASCRIPT.format(python=sys.executable, state=str(state), log=str(log)))
    exe.chmod(0o755)
    return bindir, log


def test_mac_dialogs_ask_twice_and_never_put_the_password_on_a_command_line(immich, tmp_path, capsys):
    other = "another pass 7"
    bindir, log = fake_osascript(tmp_path, [
        "jane@example.com",
        PASSWORD, other,        # different: asked again
        PASSWORD, PASSWORD,
    ])
    home = tmp_path / "home"
    env = {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", ""), "HOME": str(home),
           "USERPROFILE": str(home)}
    proc = subprocess.run([sys.executable, str(SCRIPT), "--url", immich.url, "--wait", "5"],
                          env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    out = proc.stdout
    assert proc.returncode == 0, out + proc.stderr
    assert out.strip().splitlines()[-1] == "Result: OK"
    for secret in [PASSWORD, other] + list(immich.keys) + MockImmich.issued_tokens:
        assert secret not in out and secret not in proc.stderr

    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(calls) == 5
    for argv in calls:
        assert all(PASSWORD not in a and other not in a for a in argv)
        assert argv[:-3:2] == ["-e"] * len(bs.APPLESCRIPT)
    assert [argv[-1] for argv in calls] == ["plain", "hidden", "hidden", "hidden", "hidden"]
    assert "The two passwords were different" in calls[3][-3]
    assert immich.users["jane@example.com"]["password"] == PASSWORD

    key_file = home / ".homecloud" / "immich-api-key"   # the default place
    assert key_file.read_text().strip() in immich.keys
    assert mode(key_file) == 0o600 and mode(key_file.parent) == 0o700


def test_cancel_in_the_mac_window_stops_without_changes(immich, tmp_path, monkeypatch, capsys):
    bindir, _ = fake_osascript(tmp_path, ["jane@example.com", None])
    monkeypatch.setenv("PATH", str(bindir))
    code, out, key_file = run(immich, tmp_path, monkeypatch, capsys)
    assert code == 2 and "Cancel" in out
    assert immich.users == {} and not key_file.exists()


def test_existing_admin_wrong_password_in_window_then_right_one(immich, tmp_path, monkeypatch, capsys):
    immich.add_admin()
    bindir, log = fake_osascript(tmp_path, ["jane@example.com", "nope nope", "jane@example.com", PASSWORD])
    monkeypatch.setenv("PATH", str(bindir))
    code, out, key_file = run(immich, tmp_path, monkeypatch, capsys, "--ml", "off")
    assert code == 0, out
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert "did not accept" in calls[2][-3]
    assert calls[2][-2] == "jane@example.com"  # the email is offered again as the default answer
    assert immich.config["machineLearning"]["enabled"] is False
    assert key_file.read_text().strip() in immich.keys
