"""Tests for the stateless magpie/Google Flow bridge. No network is used."""

import base64
import json
import os
from pathlib import Path

import pytest

from gflow.account_info import AccountInfoError
from gflow.api.models import Asset, AssetType
from gflow.auth import browser_auth
from gflow.auth import recaptcha as recaptcha_module
from gflow.magpie_bridge import (BridgeError, _auth_cookie_header, _cdp_shell_emails,
                                 browser_login_start, browser_login_wait,
                                 configure_account, cookies_from, identity, image,
                                 image_bytes, main, media_type_of, model_of, run,
                                 usage, validate, video)


COOKIES = "SID=synthetic; HSID=synthetic; SAPISID=synthetic"


class FakeClient:
    def __init__(self, assets):
        self.assets = assets
        self.requests = []
        self.closed = False

    def generate_image(self, req):
        self.requests.append(req)
        return self.assets

    def save_image(self, asset, output_path):
        Path(output_path).write_bytes(asset.raw["downloaded"])
        return Path(output_path)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True


def asset(**raw):
    return Asset(id="asset-1", name="image-1", asset_type=AssetType.IMAGE,
                 prompt="a fox", model="NARWHAL", raw=raw)


import hashlib


def safe_account(account):
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in account)[:48]
    digest = hashlib.sha256(account.encode("utf-8")).hexdigest()[:12]
    return f"{safe}_{digest}"


def make_request(**overrides):
    return {
        "command": "image",
        "account": "gflow#acct1",
        "state_dir": "/tmp/gflow-magpie-test-state",
        "headers": {"x-gflow-cookies": COOKIES},
        "prompt": "a fox in snow",
        "n": 2,
        "size": "1024x1024",
        **overrides,
    }


def request(**overrides):
    return make_request(**overrides)


def test_configure_account_is_private_and_per_account(monkeypatch, tmp_path):
    monkeypatch.delenv("GFLOW_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    configure_account({"account": "gflow#acct1", "state_dir": str(tmp_path)})
    first = Path(os.environ["GFLOW_HOME"])
    assert first == tmp_path / "accounts" / safe_account("gflow#acct1")
    assert first.is_dir()
    assert oct(first.stat().st_mode & 0o777) == "0o700"
    assert os.environ["GFLOW_NONINTERACTIVE"] == "1"
    configure_account({"account": "gflow#acct2", "state_dir": str(tmp_path)})
    assert Path(os.environ["GFLOW_HOME"]) == tmp_path / "accounts" / safe_account("gflow#acct2")
    home_root = tmp_path / "custom-home"
    configure_account({"account": "gflow#acct1", "home": str(home_root)})
    assert Path(os.environ["GFLOW_HOME"]) == home_root / "accounts" / safe_account("gflow#acct1")
    configure_account({"account": "a" * 100 + "#x", "state_dir": str(tmp_path)})
    assert Path(os.environ["GFLOW_HOME"]).name == safe_account("a" * 100 + "#x")


def test_auth_cookie_header_keeps_only_google_domains():
    assert _auth_cookie_header([
        {"name": "SID", "value": "a", "domain": ".google.com"},
        {"name": "NID", "value": "b", "domain": "flow.google.com"},
        {"name": "MAGPIE", "value": "c", "domain": "example.test"},
        {"name": "SID", "value": "duplicate", "domain": ".google.com"},
    ]) == "SID=a; NID=b"


def test_cookies_are_read_from_header_and_never_from_a_fake_key():
    assert cookies_from(request()) == COOKIES
    with pytest.raises(BridgeError, match="missing account cookies"):
        cookies_from({"headers": {}, "cookies": ""})
    with pytest.raises(BridgeError, match="missing account cookies"):
        cookies_from({"headers": {}, "cookies": COOKIES})
    with pytest.raises(BridgeError, match="invalid account cookies"):
        cookies_from({"headers": {"x-gflow-cookies": "not-a-cookie"}})


def fake_account_info(cookies, credits_required=False):
    assert cookies == COOKIES
    return {"email": "a@example.test", "name": "Account A", "credits": 42}


def test_validate_uses_flow_account_shell(monkeypatch):
    monkeypatch.setattr("gflow.magpie_bridge.read_account_info", fake_account_info)
    result = validate(request(command="validate"))
    assert result == {"authenticated": True, "email": "a@example.test",
                      "name": "Account A", "credits": 42, "warning": ""}


def test_identity_command_does_not_require_credits(monkeypatch):
    def partial(cookies, credits_required=False):
        assert cookies == COOKIES
        assert credits_required is False
        return {"email": "a@example.test", "name": "Account A",
                "credits": None, "error": "Flow account shell did not include an authenticated session token"}

    monkeypatch.setattr("gflow.magpie_bridge.read_account_info", partial)
    result = identity(request(command="identity"))
    assert result == {"authenticated": True, "email": "a@example.test", "name": "Account A"}


def test_usage_returns_flow_credit_balance(monkeypatch):
    monkeypatch.setattr("gflow.magpie_bridge.read_account_info", fake_account_info)
    result = usage(request(command="usage"))
    assert result == {"authenticated": True, "email": "a@example.test", "credits": 42}


def test_image_returns_base64_and_forwards_generation_fields(monkeypatch):
    png = b"\x89PNG\r\n\x1a\nimage-data"
    assets = [
        asset(encodedImage=base64.b64encode(png).decode(), prompt="revised fox"),
        asset(downloaded=png + b"-second"),
    ]
    clients = []

    def make_client(cookies, **kwargs):
        client = FakeClient(assets)
        clients.append((client, cookies, kwargs))
        return client

    monkeypatch.setattr("gflow.magpie_bridge.FlowClient", make_client)
    result = image(request(seed=42, created=123))
    assert result["created"] == 123
    assert len(result["data"]) == 2
    assert base64.b64decode(result["data"][0]["b64_json"]) == png
    assert base64.b64decode(result["data"][1]["b64_json"]) == png + b"-second"
    assert result["data"][0]["revised_prompt"] == "revised fox"
    assert result["data"][0]["media_type"] == "image/png"
    assert result["data"][1]["media_type"] == "image/png"
    assert result["revised_prompt"] == "revised fox"
    assert clients[0][1] == COOKIES
    assert clients[0][2] == {"debug": False, "interactive_auth": False}
    req = clients[0][0].requests[0]
    assert (req.prompt, req.model, req.aspect_ratio, req.seed, req.num_images) == (
        "a fox in snow", "nano-banana-2.1", "square", 42, 2)
    assert clients[0][0].closed is True


def test_video_bridges_magpie_account_and_persists_workflow(monkeypatch, tmp_path):
    clients = []

    class VideoClient(FakeClient):
        def __init__(self, cookies, **kwargs):
            super().__init__([])
            clients.append(self)
            self.cookies = cookies
            self.kwargs = kwargs
            self._workflow_id = ""
            self._primary_media_id = ""
            self.generated = []
            self.saved = []

        def generate_video(self, req):
            self.generated.append(req)
            self._workflow_id = "workflow-test-1"
            return [Asset(id="media-test-1", name="op-test-1", asset_type=AssetType.VIDEO,
                          prompt=req.prompt, url="https://example.test/video.mp4")]

        def wait_for_video(self, media_ids, timeout=300):
            assert media_ids == ["media-test-1"]
            assert timeout == 120
            return [Asset(id="media-test-1", asset_type=AssetType.VIDEO, prompt="a fox",
                          url="https://example.test/video.mp4", raw={"secret": "not exported"})]

        def save_video(self, asset, output_path):
            self.saved.append((asset, Path(output_path)))
            target = Path(output_path)
            target.write_bytes(b"video-test")
            return target

    monkeypatch.setenv("GFLOW_HOME", str(tmp_path))
    monkeypatch.setattr("gflow.magpie_bridge.FlowClient", VideoClient)
    result = video(request(prompt="a fox", aspect_ratio="16:9", wait=True,
                           timeout=120, seed=7, output=str(tmp_path / "fox.mp4")))
    assert result["operations"] == ["media-test-1"]
    assert result["new_media_ids"] == ["media-test-1"]
    assert result["waited"] is True
    assert result["workflow_id"] == "workflow-test-1"
    assert Path(result["saved_files"][0]).read_bytes() == b"video-test"
    assert "raw" not in result["videos"][0]
    assert len(clients) == 1
    assert clients[0].cookies == COOKIES
    assert clients[0].kwargs == {"debug": False, "interactive_auth": False}
    assert clients[0].closed is True
    req = clients[0].generated[0]
    assert (req.prompt, req.aspect_ratio, req.seed) == ("a fox", "16:9", 7)
    assert clients[0].saved[0][1] == tmp_path / "fox.mp4"
    state_files = list(tmp_path.glob("video-state-*.json"))
    assert len(state_files) == 1
    state = json.loads(state_files[0].read_text())
    assert state["account"] == "gflow#acct1"
    assert state["workflow_id"] == "workflow-test-1"
    assert state["last_media_ids"] == ["media-test-1"]


@pytest.mark.parametrize("content,expected", [
    (b"\xff\xd8\xff\xe0jpeg-data", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\npng-data", "image/png"),
    (b"RIFF1234WEBPwebp", "image/webp"),
    (b"unknown", "application/octet-stream"),
])
def test_media_type_of_detects_real_cdn_format(content, expected):
    assert media_type_of(content) == expected

@pytest.mark.parametrize("raw,expected", [
    (None, "nano-banana-2.1"),
    ("nano-banana-pro", "nano-banana-pro"),
    ("gflow/nano-banana-2-lite", "nano-banana-2-lite"),
])
def test_model_of_accepts_registered_ids(raw, expected):
    payload = {} if raw is None else {"model": raw}
    assert model_of(payload) == expected


@pytest.mark.parametrize("raw", ["imagen-4", "NARWHAL", "other/nano-banana-pro", ""])
def test_model_of_rejects_unregistered_ids(raw):
    with pytest.raises(BridgeError, match="unknown Flow image model"):
        model_of({"model": raw})


@pytest.mark.parametrize("bad,message", [
    ({"prompt": ""}, "prompt is required"),
    ({"n": 0}, "n must be"),
    ({"n": True}, "n must be"),
    ({"size": "999x999"}, "unsupported size"),
    ({"seed": "x"}, "seed must be"),
    ({"model": "imagen-4"}, "unknown Flow image model"),
])
def test_image_rejects_bad_requests(bad, message):
    with pytest.raises(BridgeError, match=message):
        image(request(**bad))


def test_image_bytes_rejects_invalid_base64():
    with pytest.raises(BridgeError, match="invalid image base64"):
        image_bytes(asset(encodedImage="not-base64!"), FakeClient([]))


def test_main_reads_stdin_and_writes_json(monkeypatch, capsys):
    monkeypatch.setattr("gflow.magpie_bridge.run", lambda req: {"ok": True, "command": req["command"]})
    monkeypatch.setattr("sys.stdin", type("Stdin", (), {"read": lambda self, n: json.dumps({"command": "validate"})})())
    assert main([]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "command": "validate"}


def test_main_reports_error_without_cookies(monkeypatch, capsys):
    payload = json.dumps({"command": "validate", "headers": {}})
    monkeypatch.setattr("sys.stdin", type("Stdin", (), {"read": lambda self, n: payload})())
    assert main([]) == 401
    assert "missing account cookies" in json.loads(capsys.readouterr().out)["error"]["message"]


def test_gflow_home_uses_override(monkeypatch, tmp_path):
    monkeypatch.setenv("GFLOW_HOME", str(tmp_path / "account"))
    assert browser_auth.gflow_home() == tmp_path / "account"
    monkeypatch.delenv("GFLOW_HOME")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert browser_auth.gflow_home() == tmp_path / ".gflow"


def test_noninteractive_recaptcha_never_auto_launches(monkeypatch, tmp_path):
    monkeypatch.setenv("GFLOW_NONINTERACTIVE", "1")
    provider = recaptcha_module.RecaptchaProvider(cookies=COOKIES)
    called = []
    monkeypatch.setattr(recaptcha_module, "RecaptchaError", type("RecaptchaError", (Exception,), {}))
    monkeypatch.setattr(recaptcha_module.RecaptchaProvider, "_auto_launch_chrome",
                        lambda self: called.append("launch") or 9222)
    monkeypatch.setattr("gflow.auth.browser_auth.get_saved_cdp_port", lambda: None)
    with pytest.raises(Exception, match="never launches a browser"):
        provider._connect()
    assert called == []
    assert tmp_path.exists()


class FakeCDP:
    instances = []

    def __init__(self, ws_url):
        self.ws_url = ws_url
        self.sent = []
        self.closed = False
        self.state = {
            "Storage.getCookies": {"cookies": []},
            "Target.getTargets": {"targetInfos": []},
        }
        FakeCDP.instances.append(self)

    def send(self, method, params=None, session_id=None):
        self.sent.append((method, params, session_id))
        result = self.state.get(method, {})
        if method == "Target.attachToTarget":
            result = {"sessionId": "session-1"}
        if method == "Runtime.evaluate" and isinstance(params, dict):
            expression = str(params.get("expression") or "")
            token = expression.split("t='", 1)[-1].split("'", 1)[0] if "t='" in expression else ""
            result = cdp_email_result(token)
        return result

    def close(self):
        self.closed = True


def login_cdp_state(url="https://flow.google.com/", title="Google Flow"):
    return {
        "Storage.getCookies": {"cookies": [
            {"name": "SID", "value": "synthetic", "domain": ".google.com"},
            {"name": "HSID", "value": "synthetic", "domain": ".google.com"},
            {"name": "SAPISID", "value": "synthetic", "domain": ".google.com"},
        ]},
        "Target.getTargets": {"targetInfos": [
            {"type": "page", "url": url, "title": title, "targetId": "page-1"},
        ]},
        "Runtime.evaluate": {"result": {"value": json.dumps({
            "t": "", "emails": ["a@example.test"],
        })}},
    }


def verified_shell_state(base_state):
    """Add a shell that carries SNlM0e so wait's cookie verification passes.

    ``browser_login_wait`` now re-reads cookies and requires the Flow shell to
    carry ``SNlM0e`` before saving, because Google rotates session cookies
    right after sign-in and the rotated-out values serve a reduced shell.
    """
    def fake_read_identity(cookies):
        return {"email": "a@example.test", "name": "Account A",
                "values": {"SNlM0e": "AFAKEAT"}}
    return base_state, fake_read_identity


def cdp_email_result(token):
    return {"result": {"value": json.dumps({"t": token, "emails": ["a@example.test"]})}}


def test_cdp_page_filter_selects_this_launch_instead_of_an_unrelated_tab(monkeypatch):
    marker = "data:text/html,gflow-login-synthetic"
    targets = [
        {"type": "page", "url": "https://flow.google.com/", "webSocketDebuggerUrl": "ws://unrelated"},
        {"type": "page", "url": marker, "webSocketDebuggerUrl": "ws://owned"},
    ]

    class Response:
        def read(self):
            return json.dumps(targets).encode()

    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: Response())
    assert browser_auth._wait_for_cdp_page(9231, target_url=marker) == "ws://owned"
    assert browser_auth._wait_for_cdp_page(9231) == "ws://unrelated"


def test_cdp_page_filter_rejects_a_listener_without_the_launch_marker(monkeypatch):
    ticks = iter([0, 0.01, 0.1])

    class Response:
        def read(self):
            return json.dumps([
                {"type": "page", "url": "https://flow.google.com/", "webSocketDebuggerUrl": "ws://unrelated"}
            ]).encode()

    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: Response())
    monkeypatch.setattr(browser_auth.time, "time", lambda: next(ticks))
    monkeypatch.setattr(browser_auth.time, "sleep", lambda seconds: None)
    with pytest.raises(browser_auth.AuthError, match="CDP not available"):
        browser_auth._wait_for_cdp_page(9231, timeout=0.05, target_url="data:text/html,gflow-login-synthetic")


def test_browser_login_start_opens_flow_chrome_with_isolated_profile(monkeypatch, tmp_path):
    configure_account({"account": "gflow#button", "state_dir": str(tmp_path)})
    seen = {}
    monkeypatch.setattr("gflow.auth.browser_auth.kill_auth_browser", lambda: seen.setdefault("killed", True))
    monkeypatch.setattr("gflow.auth.browser_auth.clear_cdp_port", lambda: seen.setdefault("cleared", True))
    monkeypatch.setattr("gflow.auth.browser_auth._get_chrome_path", lambda: "/fake/Chrome")
    monkeypatch.setattr("gflow.auth.browser_auth._read_devtools_active_port",
                        lambda profile_dir, timeout=20.0: seen.setdefault("profile_dir", profile_dir) and 9231)
    monkeypatch.setattr("gflow.auth.browser_auth._find_free_port", lambda: 9231)
    monkeypatch.setattr("gflow.auth.browser_auth.wait_for_cdp_ready", lambda port, timeout=10.0: True)
    monkeypatch.setattr("gflow.auth.browser_auth._wait_for_cdp_page",
                        lambda port, timeout=30, target_url=None: seen.setdefault("target", target_url) and "ws://local/page")
    monkeypatch.setattr("gflow.auth.browser_auth.save_cdp_port", lambda port: seen.setdefault("port", port))
    monkeypatch.setattr("gflow.auth.proxy_ext.get_chrome_proxy_args", lambda: [])

    class FakeCDP:
        def __init__(self, ws_url):
            seen["ws_url"] = ws_url

        def send(self, method, params):
            seen["navigation"] = (method, params)
            return {"frameId": "frame"}

        def close(self):
            seen["closed"] = True

    class FakePopen:
        def __init__(self, args, **kwargs):
            seen["args"], seen["kwargs"] = args, kwargs

    monkeypatch.setattr("gflow.auth.browser_auth._CDPConnection", FakeCDP)
    monkeypatch.setattr("subprocess.Popen", FakePopen)
    answer = browser_login_start({})

    assert answer == {"url": "https://flow.google.com/", "cdp_port": 9231, "opened": True}
    assert seen["killed"] and seen["cleared"] and seen["port"] == 9231
    assert seen["args"][0] == "/fake/Chrome"
    assert "--remote-debugging-port=9231" in seen["args"]
    assert "--remote-debugging-port=0" not in seen["args"]
    assert seen["target"].startswith("data:text/html,gflow-login-")
    assert seen["target"] in seen["args"]
    assert seen["ws_url"] == "ws://local/page"
    assert seen["navigation"] == ("Page.navigate", {"url": "https://flow.google.com/"})
    assert seen["closed"] is True
    assert str(tmp_path / "accounts" / safe_account("gflow#button") / "chrome-profile") in " ".join(seen["args"])
    assert "--disable-features=ChromeWhatsNewUI" in seen["args"]
    assert "https://flow.google.com/" not in seen["args"]
    assert seen["kwargs"]["start_new_session"] is True
    assert COOKIES not in " ".join(seen["args"])


def test_browser_login_start_fails_when_chrome_never_exposes_a_port(monkeypatch, tmp_path):
    configure_account({"account": "gflow#button", "state_dir": str(tmp_path)})
    monkeypatch.setattr("gflow.auth.browser_auth.kill_auth_browser", lambda: None)
    monkeypatch.setattr("gflow.auth.browser_auth.clear_cdp_port", lambda: None)
    monkeypatch.setattr("gflow.auth.browser_auth._get_chrome_path", lambda: "/fake/Chrome")
    monkeypatch.setattr("gflow.auth.browser_auth._find_free_port", lambda: 9231)
    def no_login_page(*args, **kwargs):
        raise browser_auth.AuthError("Chrome CDP not available")

    monkeypatch.setattr("gflow.auth.browser_auth._wait_for_cdp_page", no_login_page)
    saved = {}
    process_state = {}
    monkeypatch.setattr("gflow.auth.browser_auth.save_cdp_port",
                        lambda port: saved.setdefault("port", port))
    monkeypatch.setattr("gflow.auth.proxy_ext.get_chrome_proxy_args", lambda: [])

    class FakePopen:
        def __init__(self, args, **kwargs):
            pass

        def poll(self):
            return None

        def terminate(self):
            process_state["terminated"] = True

        def wait(self, timeout):
            process_state["waited"] = True
            return 0

    monkeypatch.setattr("subprocess.Popen", FakePopen)

    with pytest.raises(BridgeError, match="did not expose a debugging port"):
        browser_login_start({})
    assert process_state["terminated"] and process_state["waited"]
    # A dead port must never be persisted: that is what made browser_login_wait
    # report "login browser is not running" while Chrome was open.
    assert saved == {}


def mock_browser_cdp(monkeypatch, state, identity=None):
    FakeCDP.instances.clear()
    if identity is None:
        identity = lambda cookies: {  # noqa: E731
            "email": "a@example.test", "name": "Account A",
            "values": {"SNlM0e": "AFAKEAT"}}
    monkeypatch.setattr("gflow.magpie_bridge.read_identity", identity)
    monkeypatch.setattr("gflow.auth.browser_auth.read_saved_cdp_port", lambda: 9231)
    monkeypatch.setattr("gflow.auth.browser_auth._wait_for_browser_cdp",
                        lambda port, timeout: "ws://127.0.0.1:9231/browser")
    monkeypatch.setattr("gflow.auth.browser_auth._CDPConnection", FakeCDP)
    monkeypatch.setattr("gflow.magpie_bridge.time.sleep", lambda seconds: None)

    def make_cdp(ws_url):
        cdp = FakeCDP(ws_url)
        cdp.state = state
        return cdp

    monkeypatch.setattr("gflow.auth.browser_auth._CDPConnection", make_cdp)


def test_browser_login_wait_preserves_cookies_when_identity_read_fails(monkeypatch):
    mock_browser_cdp(monkeypatch, login_cdp_state())

    def unavailable(cookies):
        raise AccountInfoError("Flow account shell did not include an authenticated session token")

    monkeypatch.setattr("gflow.magpie_bridge.read_identity", unavailable)
    answer = browser_login_wait({"timeout_seconds": 10})
    assert answer["authenticated"] is True
    assert answer["cookies"] == COOKIES
    assert answer["email"] == "a@example.test"  # recovered from the live page
    assert answer["name"] == ""


def test_cdp_shell_emails_attaches_flow_target_session():
    class OnePageCDP:
        def __init__(self):
            self.sent = []

        def send(self, method, params=None, session_id=None):
            self.sent.append((method, params, session_id))
            if method == "Target.attachToTarget":
                assert params["targetId"] == "flow"
                assert params["flatten"] is True
                return {"sessionId": "session-1"}
            expression = str((params or {}).get("expression") or "")
            token = expression.split("t='", 1)[-1].split("'", 1)[0]
            return cdp_email_result(token)

    cdp = OnePageCDP()
    pages = [
        {"url": "https://accounts.google.com/", "targetId": "google"},
        {"url": "https://flow.google.com/", "targetId": "flow"},
    ]
    assert _cdp_shell_emails(cdp, pages) == ["a@example.test"]
    assert [call[0] for call in cdp.sent] == ["Target.attachToTarget", "Runtime.evaluate"]
    assert cdp.sent[1][2] == "session-1"


def test_browser_login_wait_returns_cookies_and_account_identity(monkeypatch):
    mock_browser_cdp(monkeypatch, login_cdp_state())
    answer = browser_login_wait({"timeout_seconds": 10})

    assert answer == {
        "authenticated": True,
        "cookies": COOKIES,
        "email": "a@example.test",
        "name": "Account A",
    }
    assert len(FakeCDP.instances) == 1
    cdp = FakeCDP.instances[0]
    assert cdp.closed is True
    assert [call[0] for call in cdp.sent if call[0] in ("Storage.getCookies", "Target.getTargets")] == ["Storage.getCookies", "Target.getTargets"]


def test_browser_login_wait_skips_deprecated_session_endpoint(monkeypatch):
    mock_browser_cdp(monkeypatch, login_cdp_state())
    called = []
    def deprecated(*args, **kwargs):
        called.append(args)
        raise BridgeError("Session endpoint returned 410: deprecated", 410)

    # browser_auth.refresh_access_token is the deprecated 410 endpoint's
    # helper; it must not be called by the browser login completion path.
    monkeypatch.setattr("gflow.auth.browser_auth.refresh_access_token", deprecated)
    answer = browser_login_wait({"timeout_seconds": 10})
    assert answer == {"authenticated": True, "cookies": COOKIES,
                      "email": "a@example.test", "name": "Account A"}
    assert called == []
    assert FakeCDP.instances[0].closed is True


def test_browser_login_wait_reopens_flow_after_connection_error(monkeypatch):
    state = login_cdp_state(url="chrome-error://chromewebdata/",
                            title="Connection failed (-100)")
    mock_browser_cdp(monkeypatch, state)

    def send(self, method, params=None):
        self.sent.append((method, params))
        if method == "Target.createTarget":
            self.state = login_cdp_state()
        return self.state.get(method, {})

    monkeypatch.setattr(FakeCDP, "send", send)
    answer = browser_login_wait({"timeout_seconds": 10})
    assert answer["authenticated"] is True
    assert answer["cookies"] == COOKIES
    assert ("Target.createTarget", {"url": "https://flow.google.com/"}) in FakeCDP.instances[0].sent


def test_browser_login_wait_waits_through_google_challenge(monkeypatch):
    state = login_cdp_state(
        url="https://accounts.google.com/v3/signin/challenge/pk?continue=https%3A%2F%2Fflow.google.com%2F",
        title="Sign in")
    mock_browser_cdp(monkeypatch, state)

    def send(self, method, params=None):
        self.sent.append((method, params))
        if method == "Target.getTargets" and len(self.sent) > 3:
            self.state = login_cdp_state()
        return self.state.get(method, {})

    monkeypatch.setattr(FakeCDP, "send", send)
    answer = browser_login_wait({"timeout_seconds": 10})
    assert answer["authenticated"] is True
    assert not any(method == "Target.createTarget" for method, _ in FakeCDP.instances[0].sent)


def test_browser_login_wait_reports_timeout_without_credentials(monkeypatch):
    state = login_cdp_state()
    state["Storage.getCookies"] = {"cookies": []}
    mock_browser_cdp(monkeypatch, state)
    with pytest.raises(BridgeError, match="Google sign-in cookies are not available yet"):
        browser_login_wait({"timeout_seconds": 10})
    assert FakeCDP.instances[0].closed is True


def test_read_devtools_active_port_reads_chrome_chosen_port(tmp_path):
    (tmp_path / browser_auth.DEVTOOLS_ACTIVE_PORT).write_text("65296\n/devtools/browser/abc\n")
    assert browser_auth._read_devtools_active_port(tmp_path, timeout=0.5) == 65296


def test_read_devtools_active_port_waits_for_a_late_file(tmp_path):
    path = tmp_path / browser_auth.DEVTOOLS_ACTIVE_PORT

    import threading

    def write_later():
        import time as _time
        _time.sleep(0.15)
        path.write_text("61234\n/devtools/browser/abc\n")

    thread = threading.Thread(target=write_later)
    thread.start()
    try:
        assert browser_auth._read_devtools_active_port(tmp_path, timeout=3.0) == 61234
    finally:
        thread.join()


def test_read_devtools_active_port_ignores_a_malformed_file(tmp_path):
    (tmp_path / browser_auth.DEVTOOLS_ACTIVE_PORT).write_text("not-a-port\n")
    assert browser_auth._read_devtools_active_port(tmp_path, timeout=0.3) is None


def test_get_saved_cdp_port_requires_cdp_http_not_just_tcp(monkeypatch, tmp_path):
    configure_account({"account": "gflow#port", "state_dir": str(tmp_path)})
    browser_auth.save_cdp_port(9231)
    # Chrome binds the socket before its CDP handler answers; a bare TCP
    # accept therefore must not count as "the login browser is running".
    monkeypatch.setattr("gflow.auth.browser_auth.wait_for_cdp_ready",
                        lambda port, timeout=1.5: False)
    assert browser_auth.get_saved_cdp_port() is None
    monkeypatch.setattr("gflow.auth.browser_auth.wait_for_cdp_ready",
                        lambda port, timeout=1.5: True)
    assert browser_auth.get_saved_cdp_port() == 9231


def test_kill_auth_browser_waits_for_the_port_to_close(monkeypatch, tmp_path):
    configure_account({"account": "gflow#kill", "state_dir": str(tmp_path)})
    browser_auth.save_cdp_port(9231)
    probes = []

    monkeypatch.setattr("gflow.auth.browser_auth.get_saved_cdp_port", lambda: 9231)

    def fake_ready(port, timeout=0.3):
        probes.append(port)
        return len(probes) < 3  # two more answers, then the browser is gone

    monkeypatch.setattr("gflow.auth.browser_auth.wait_for_cdp_ready", fake_ready)
    monkeypatch.setattr("gflow.auth.browser_auth.clear_cdp_port", lambda: None)

    class FailingUrlopen:
        def __init__(self, *args, **kwargs):
            raise OSError("browser already gone")

    monkeypatch.setattr("urllib.request.urlopen", FailingUrlopen, raising=False)

    browser_auth.kill_auth_browser(timeout=5.0)
    # It must poll until the endpoint stops answering instead of returning
    # immediately and letting the next launch race the profile lock.
    assert len(probes) >= 3


def test_browser_login_wait_uses_raw_port_while_cdp_is_still_starting(monkeypatch, tmp_path):
    """A just-launched Chrome must not read as "login browser is not running".

    ``get_saved_cdp_port`` probes CDP with a short timeout, so gating the wait
    on it would fail for a browser that is merely still starting.  The wait
    must read the persisted port and let ``_wait_for_browser_cdp`` do the
    waiting.
    """
    configure_account({"account": "gflow#warmup", "state_dir": str(tmp_path)})
    browser_auth.save_cdp_port(9231)
    monkeypatch.setattr("gflow.auth.browser_auth.wait_for_cdp_ready",
                        lambda port, timeout=1.5: False)  # CDP not up yet
    monkeypatch.setattr("gflow.auth.browser_auth._wait_for_browser_cdp",
                        lambda port, timeout: "ws://127.0.0.1:9231/browser")
    monkeypatch.setattr("gflow.auth.browser_auth._CDPConnection", FakeCDP)
    monkeypatch.setattr("gflow.magpie_bridge.read_identity",
                        lambda cookies: {"email": "a@example.test", "name": "Account A"})
    monkeypatch.setattr("gflow.magpie_bridge.time.sleep", lambda seconds: None)
    state = login_cdp_state()
    FakeCDP.instances.clear()

    class WarmingCDP(FakeCDP):
        def __init__(self, ws_url):
            super().__init__(ws_url)
            self.sent = []

        def send(self, method, params=None, session_id=None):
            self.sent.append((method, params))
            return state.get(method, {})

    monkeypatch.setattr("gflow.auth.browser_auth._CDPConnection", WarmingCDP)
    answer = browser_login_wait({"timeout_seconds": 10})
    assert answer["authenticated"] is True
    assert answer["cookies"] == COOKIES


def test_browser_login_wait_rereads_cookies_until_shell_carries_token(monkeypatch):
    """Rotated-out cookies serve a reduced shell; the wait must not save them.

    Google rotates ``__Secure-*PSIDTS`` right after sign-in completes.  The
    first CDP read may therefore still hold valid-looking but dead cookies;
    wait must keep re-reading until the Flow shell verifies (SNlM0e present)
    and then save the fresh header.
    """
    attempts = {"identity": 0, "reads": 0}

    def flaky_identity(cookies):
        attempts["identity"] += 1
        if attempts["identity"] == 1:
            raise AccountInfoError("Flow account shell did not include an authenticated session token")
        return {"email": "a@example.test", "name": "Account A",
                "values": {"SNlM0e": "AFAKEAT"}}

    mock_browser_cdp(monkeypatch, login_cdp_state(), identity=flaky_identity)

    def state(_cdp):
        attempts["reads"] += 1
        return [
            {"name": "SID", "value": f"fresh{attempts['reads']}", "domain": ".google.com"},
            {"name": "HSID", "value": "synthetic", "domain": ".google.com"},
            {"name": "SAPISID", "value": "synthetic", "domain": ".google.com"},
        ], [{"type": "page", "url": "https://flow.google.com/", "targetId": "page-1"}]

    monkeypatch.setattr("gflow.magpie_bridge._cdp_browser_state", state)
    answer = browser_login_wait({"timeout_seconds": 30})
    assert answer["authenticated"] is True
    # The saved header must be the re-read one, not the first snapshot.
    assert answer["cookies"] == "SID=fresh2; HSID=synthetic; SAPISID=synthetic"
    assert answer["email"] == "a@example.test"
