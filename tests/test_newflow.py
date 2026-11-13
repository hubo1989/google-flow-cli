"""Tests for the new Angular frontend's batchexecute image RPCs."""

import json
from unittest.mock import MagicMock, patch

import pytest

from gflow.api import newflow
from gflow.api.models import AssetType


def _rpc_reply(rpcid, payload):
    return f")]}}'\n\n200\n[[\"wrb.fr\",\"{rpcid}\",{json.dumps(json.dumps(payload))},null,null,null,\"generic\"]]\n"


class FakeCDP:
    def __init__(self, ws_url):
        self.ws_url = ws_url
        self.calls = []
        self.closed = False

    def send(self, method, params=None, session_id=None):
        self.calls.append((method, params))
        if method == "Runtime.evaluate":
            expr = params.get("expression", "")
            if "typeof g.execute" in expr or "ok: true" in expr or "g.execute(" in expr:
                value = json.dumps({"ok": True, "token": "TOKEN"})
            else:
                value = json.dumps({"ready": True, "at": True})
            return {"result": {"value": value}}
        return {}

    def close(self):
        self.closed = True


@pytest.fixture
def newflow_env(monkeypatch, tmp_path):
    session = ("SID=fresh", {"at": "AT", "bl": "BL", "fsid": "FSID"})
    monkeypatch.setattr(newflow, "_cdp_page_session", lambda: session)
    monkeypatch.setattr(newflow, "_page_ws_url", lambda: "ws://example/page")
    monkeypatch.setattr(newflow, "_CDPConnection", FakeCDP)
    monkeypatch.setattr(newflow, "gflow_home", lambda: tmp_path)
    monkeypatch.setattr(newflow, "_rpc_headers", lambda cookies: {"Cookie": cookies})
    monkeypatch.setattr(newflow, "_proxy_for", lambda: None)
    posts = []

    def fake_post(url, **kwargs):
        posts.append((url, kwargs))
        if "rpcids=jHPbke" in url:
            return MagicMock(status_code=200, text=_rpc_reply("jHPbke", ["11111111-1111-1111-1111-111111111111", ["gflow magpie"]]))
        media = ["media-1", None, "session-1", None, None, None,
                 [[None, 4242, None, None, None, None, 1, "a fox"]], None,
                 "https://flow-content.google/image/media-1?Signature=sig", 2,
                 [None, None, [["a fox"]]], None, None, "media-1"]
        payload = [[media, None, [768, 1376]]]
        return MagicMock(status_code=200, text=_rpc_reply("ogiZ0b", payload))
    monkeypatch.setattr(newflow.requests, "post", fake_post)
    return posts


@pytest.mark.parametrize("model,wire", [
    ("nano-banana-pro", "GEM_PIX_2"),
    ("nano-banana-2.1", "BELUGA"),
    ("nano-banana-2-lite", "HARBOR_SEAL"),
])
def test_generate_images_sends_selected_model(newflow_env, model, wire):
    assets = newflow.generate_images(model=model, prompt="a fox", aspect_ratio="landscape", seed=4242)
    assert len(assets) == 1
    asset = assets[0]
    assert asset.asset_type == AssetType.IMAGE
    assert asset.url.startswith("https://flow-content.google/image/")
    assert (asset.width, asset.height) == (768, 1376)
    assert asset.raw["fifeUrl"] == asset.url
    ogi = [p for p in newflow_env if "rpcids=ogiZ0b" in p[0]][0]
    body = ogi[1]["data"]
    assert wire in body
    assert "%2Fproject%2F11111111-1111-1111-1111-111111111111" in ogi[0]


def test_generate_images_rejects_unknown_model(newflow_env):
    with pytest.raises(newflow.NewFlowError, match="unknown Flow image model"):
        newflow.generate_images(model="imagen-4", prompt="x", aspect_ratio="square")


def test_generate_images_requires_live_page(monkeypatch):
    monkeypatch.setattr(newflow, "_cdp_page_session", lambda: None)
    with pytest.raises(newflow.NewFlowUnavailable):
        newflow.generate_images(model="nano-banana-2.1", prompt="x", aspect_ratio="landscape")


def test_generate_images_surfaces_unusual_activity(newflow_env, monkeypatch):
    error_reply = (
        ")]}'\n[[\"wrb.fr\",\"ogiZ0b\",null,null,null,"
        "[7,null,[[\"type.googleapis.com/google.rpc.ErrorInfo\","
        "[\"PUBLIC_ERROR_UNUSUAL_ACTIVITY\"]]]],\"generic\"]]\n"
    )

    def fake_post(url, **kw):
        if "jHPbke" in url:
            return MagicMock(status_code=200, text=_rpc_reply(
                "jHPbke", ["11111111-1111-1111-1111-111111111111", ["x"]]))
        return MagicMock(status_code=200, text=error_reply)

    monkeypatch.setattr(newflow.requests, "post", fake_post)
    with pytest.raises(newflow.NewFlowError, match="UNUSUAL_ACTIVITY"):
        newflow.generate_images(model="nano-banana-2.1", prompt="x", aspect_ratio="landscape")


def test_xssi_payload_ignores_other_frames():
    text = ")]}'\n\n50\n[[\"di\",1]]\n200\n[[\"wrb.fr\",\"nzlxg\",\"[25050,2]\",null,null,null,\"generic\"]]\n"
    assert newflow._xssi_payload(text, "nzlxg") == [25050, 2]
