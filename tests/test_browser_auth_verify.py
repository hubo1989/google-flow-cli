"""Regression for the CDP cookie verifier's User-Agent binding."""

import json

from gflow.auth import browser_auth


class FakeCDP:
    def send(self, method, params=None, session_id=None):
        assert method == "Runtime.evaluate"
        return {"result": {"value": json.dumps({"at": "AT", "bl": "BL", "fsid": "FSID"})}}


def test_verify_cookies_via_page_sends_user_agent(monkeypatch):
    captured = []

    class FakeResp:
        status_code = 200
        text = ")]}'\n[[\"wrb.fr\",\"nzlxg\",\"[25050,2]\",null,null,null,\"generic\"]]\n"

    def fake_post(url, **kwargs):
        captured.append((url, kwargs))
        return FakeResp()

    monkeypatch.setattr(browser_auth.requests, "post", fake_post)
    result = browser_auth._verify_cookies_via_page(FakeCDP(), "SID=x")
    assert result == {"verified": True}
    assert captured
    assert captured[0][1]["headers"]["User-Agent"].startswith("Mozilla/5.0")
