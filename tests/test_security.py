import hashlib
import hmac
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from config import Settings, get_settings
from main import app

PUSH_PAYLOAD = {
    "ref": "refs/heads/main",
    "before": "0000000000000000000000000000000000000000",
    "after": "abc1234abc1234abc1234abc1234abc1234abc123",
    "repository": {"name": "test-repo", "clone_url": ""},
    "pusher": {"name": "Test User", "email": "test@example.com"},
    "commits": [
        {
            "id": "abc1234abc1234abc1234abc1234abc1234abc123",
            "timestamp": "2026-01-01T00:00:00Z",
            "message": "test commit",
            "author": {"name": "Test User", "email": "test@example.com"},
            "added": [],
            "removed": [],
            "modified": ["README.md"],
        }
    ],
    "head_commit": None,
}

SECRET = "test-secret-1234"


def _sign(payload: dict, secret: str) -> str:
    body = json.dumps(payload, separators=(",", ":")).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def _settings_with_secret(secret: str = SECRET) -> Settings:
    return Settings(webhook_secret=secret)


def _settings_no_secret() -> Settings:
    return Settings(webhook_secret="")


@pytest.fixture
def client():
    get_settings.cache_clear()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    get_settings.cache_clear()


def post(client, payload=None, headers=None):
    body = json.dumps(payload or PUSH_PAYLOAD, separators=(",", ":"))
    base_headers = {"Content-Type": "application/json", "X-GitHub-Event": "push"}
    if headers:
        base_headers.update(headers)
    return client.post("/git/push", content=body, headers=base_headers)


class TestNoSecretConfigured:
    def test_accepts_without_signature(self, client):
        with patch("security.get_settings", return_value=_settings_no_secret()):
            r = post(client)
        assert r.status_code == 202

    def test_accepts_with_any_signature(self, client):
        with patch("security.get_settings", return_value=_settings_no_secret()):
            r = post(client, headers={"X-Hub-Signature-256": "sha256=whatever"})
        assert r.status_code == 202


class TestSecretConfigured:
    def test_accepts_valid_signature(self, client):
        payload = PUSH_PAYLOAD
        sig = _sign(payload, SECRET)
        with patch("security.get_settings", return_value=_settings_with_secret()):
            r = post(client, headers={"X-Hub-Signature-256": sig})
        assert r.status_code == 202

    def test_rejects_missing_signature(self, client):
        with patch("security.get_settings", return_value=_settings_with_secret()):
            r = post(client)
        assert r.status_code == 401
        assert "Missing" in r.json()["detail"]

    def test_rejects_wrong_signature(self, client):
        with patch("security.get_settings", return_value=_settings_with_secret()):
            r = post(client, headers={"X-Hub-Signature-256": "sha256=deadbeef"})
        assert r.status_code == 401
        assert "Invalid" in r.json()["detail"]

    def test_rejects_signature_from_different_secret(self, client):
        sig = _sign(PUSH_PAYLOAD, "wrong-secret")
        with patch("security.get_settings", return_value=_settings_with_secret()):
            r = post(client, headers={"X-Hub-Signature-256": sig})
        assert r.status_code == 401

    def test_rejects_tampered_payload(self, client):
        sig = _sign(PUSH_PAYLOAD, SECRET)
        tampered = {**PUSH_PAYLOAD, "ref": "refs/heads/evil"}
        with patch("security.get_settings", return_value=_settings_with_secret()):
            r = post(client, payload=tampered, headers={"X-Hub-Signature-256": sig})
        assert r.status_code == 401
