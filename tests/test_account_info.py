"""Offline tests for the Flow account shell and ``nzlxg`` credit parser."""

import json
import urllib.parse

import pytest

from gflow import account_info


SHELL = (
    '<script>window.WIZ_global_data = {"oPEP7c":"a@example.test",'
    '"SNlM0e":"AFAKEAT","FdrFJe":"-1","cfb2h":"boq_test"};</script>'
)
SHELL_NO_TOKEN = (
    '<script>window.WIZ_global_data = {"oPEP7c":"a@example.test"};</script>'
)
RPC_REPLY = ")]}'\n[[\"wrb.fr\",\"nzlxg\",\"[42]\",null,null,null,\"generic\"]]\n"


class FakeResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


def test_wiz_values_and_email_from_shell():
    values = account_info._wiz_values(SHELL)
    assert values["oPEP7c"] == "a@example.test"
    assert values["SNlM0e"] == "AFAKEAT"
    assert values["FdrFJe"] == "-1"
    assert values["cfb2h"] == "boq_test"
    assert account_info._email_from_shell(SHELL) == "a@example.test"


def test_shell_uses_navigation_headers(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(200, SHELL)

    monkeypatch.setattr(account_info.requests, "get", fake_get)
    monkeypatch.setattr(account_info, "_proxy_for", lambda: None)
    account_info.read_identity("SID=synthetic")
    headers = calls[0][1]["headers"]
    assert headers["Sec-Fetch-Dest"] == "document"
    assert headers["Sec-Fetch-Mode"] == "navigate"
    assert headers["Sec-Fetch-Site"] == "none"
    assert headers["Sec-Fetch-User"] == "?1"
    assert headers["Upgrade-Insecure-Requests"] == "1"
    assert headers["Accept-Language"] == "en-US,en;q=0.9"


@pytest.mark.parametrize("create_ok", [True, False])
def test_cdp_session_recovers_a_closed_flow_tab(monkeypatch, create_ok):
    from gflow.auth import browser_auth

    calls, closed, waits = [], [], []

    class FakeCDP:
        def __init__(self, ws):
            self.ws = ws

        def send(self, method, params=None):
            calls.append((method, params))
            if method == "Target.createTarget":
                if not create_ok:
                    raise browser_auth.AuthError("target creation failed")
                return {"targetId": "flow"}
            if method == "Runtime.evaluate":
                assert params["awaitPromise"] is True
                return {"result": {"value": json.dumps({"at": "fresh-at", "bl": "build", "fsid": "session"})}}
            if method == "Network.getCookies":
                return {"cookies": [{"name": "SID", "value": "fresh-sid"}]}
            return {}

        def close(self):
            closed.append(self.ws)

    def wait_page(port, timeout=30, target_url=None):
        waits.append(target_url)
        if len(waits) == 1:
            raise browser_auth.AuthError("no page targets")
        return "ws://page"

    monkeypatch.setattr(browser_auth, "get_saved_cdp_port", lambda: 9231)
    monkeypatch.setattr(browser_auth, "_browser_cdp_ws_url", lambda port: "ws://browser")
    monkeypatch.setattr(browser_auth, "_wait_for_cdp_page", wait_page)
    monkeypatch.setattr(browser_auth, "_CDPConnection", FakeCDP)
    result = account_info._cdp_page_session()
    assert ("Target.createTarget", {"url": "https://flow.google.com/", "background": True}) in calls
    if create_ok:
        assert result[0] == "SID=fresh-sid"
        assert result[1]["SNlM0e"] == "fresh-at"
        assert waits == [None, "https://flow.google.com/"]
        assert closed == ["ws://browser", "ws://page"]
    else:
        assert result is None
        assert closed == ["ws://browser"]


def test_credits_from_reply_rejects_bad_shapes():
    assert account_info._credits_from_reply(RPC_REPLY) == 42
    assert account_info._wrb_payloads(RPC_REPLY) == [[42]]
    for bad in ("", "<html></html>", ")]}'\n[[\"wrb.fr\",\"nzlxg\",\"[\\\"x\\\"]\"]]\n"):
        with pytest.raises(account_info.AccountInfoError):
            account_info._credits_from_reply(bad)


def test_read_account_info_uses_nzlxg_with_shell_tokens(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(("GET", url, kwargs))
        assert kwargs["headers"]["Cookie"] == "SID=synthetic"
        return FakeResponse(200, SHELL)

    def fake_post(url, **kwargs):
        calls.append(("POST", url, kwargs))
        assert kwargs["headers"]["Cookie"] == "SID=synthetic"
        assert kwargs["headers"]["Sec-Fetch-Dest"] == "empty"
        assert kwargs["headers"]["Sec-Fetch-Mode"] == "cors"
        assert "Upgrade-Insecure-Requests" not in kwargs["headers"]
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        form = urllib.parse.parse_qs(kwargs["data"])
        assert query["rpcids"] == ["nzlxg"]
        assert query["bl"] == ["boq_test"]
        assert query["f.sid"] == ["-1"]
        assert form["at"] == ["AFAKEAT"]
        assert form["f.req"] == ['[[["nzlxg", "[]", null, "generic"]]]']
        return FakeResponse(200, RPC_REPLY)

    monkeypatch.setattr(account_info.requests, "get", fake_get)
    monkeypatch.setattr(account_info.requests, "post", fake_post)
    monkeypatch.setattr(account_info, "_proxy_for", lambda: None)
    info = account_info.read_account_info("SID=synthetic", credits_required=True)
    assert info == {"email": "a@example.test", "name": "", "credits": 42}
    assert [call[0] for call in calls] == ["GET", "POST"]
    assert "/_/AiSandboxAngularFrontend/data/batchexecute" in calls[1][1]


def test_identity_only_never_fetches_credits(monkeypatch):
    monkeypatch.setattr(account_info, "read_identity", lambda cookies: {"email": "a@example.test", "name": "A"})

    def forbidden_credits(*args, **kwargs):
        raise AssertionError("identity-only reads must not perform the credit RPC")

    monkeypatch.setattr(account_info, "read_credits", forbidden_credits)
    assert account_info.read_account_info("SID=synthetic") == {
        "email": "a@example.test", "name": "A", "credits": None,
    }


def test_read_identity_survives_shell_without_session_token(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(("GET", url, kwargs))
        return FakeResponse(200, SHELL_NO_TOKEN)

    def forbidden_post(url, **kwargs):
        raise AssertionError("nzlxg must not be called without SNlM0e")

    monkeypatch.setattr(account_info.requests, "get", fake_get)
    monkeypatch.setattr(account_info.requests, "post", forbidden_post)
    monkeypatch.setattr(account_info, "_proxy_for", lambda: None)
    monkeypatch.setattr(account_info, "_cdp_page_session", lambda: None)
    info = account_info.read_account_info("SID=synthetic")
    assert info["email"] == "a@example.test"
    assert info["credits"] is None
    assert "error" not in info
    assert [call[0] for call in calls] == ["GET"]
    with pytest.raises(account_info.CreditsUnavailable, match="authenticated session token"):
        account_info.read_account_info("SID=synthetic", credits_required=True)


def test_shell_redirect_is_actionable(monkeypatch):
    monkeypatch.setattr(account_info.requests, "get",
                        lambda url, **kwargs: FakeResponse(302, ""))
    monkeypatch.setattr(account_info, "_proxy_for", lambda: None)
    with pytest.raises(account_info.AccountInfoError, match="redirected the account shell to sign-in"):
        account_info.read_identity("SID=synthetic")


def test_read_account_info_rejects_invalid_cookie_header():
    with pytest.raises(account_info.AccountInfoError, match="missing account cookies"):
        account_info.read_account_info("")
