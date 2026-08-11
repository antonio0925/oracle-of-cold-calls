"""The password gate. Every UI route writes to production HubSpot."""
from unittest.mock import patch

import pytest

import app as app_module

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def password_set():
    with patch.object(app_module.config, "SUMMIT_PASSWORD", PASSWORD):
        yield


def test_unauthenticated_root_redirects_to_login(client, password_set):
    resp = client.get("/")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/login")


def test_wrong_password_is_rejected(client, password_set):
    resp = client.post("/login", data={"password": "wrong"})
    assert resp.status_code == 401
    with client.session_transaction() as sess:
        assert "summit_auth" not in sess


def test_right_password_sets_session_and_reaches_root(client, password_set):
    resp = client.post("/login", data={"password": PASSWORD})
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")

    with client.session_transaction() as sess:
        assert sess["summit_auth"] is True

    root = client.get("/")
    assert root.status_code == 200
    assert b"SUMMIT" in root.data


def test_empty_password_config_fails_closed(client):
    # An unset password must never authenticate an empty form field.
    with patch.object(app_module.config, "SUMMIT_PASSWORD", ""):
        resp = client.post("/login", data={"password": ""})
    assert resp.status_code == 503
    with client.session_transaction() as sess:
        assert "summit_auth" not in sess


def test_login_page_is_reachable_without_a_session(client, password_set):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert b"SUMMIT" in resp.data


def test_api_route_returns_401_json_not_a_redirect(client, password_set):
    # The UI fetches these. An HTML redirect would surface as a parse error.
    resp = client.get("/api/dispositions")
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "Not authenticated"


def test_webhook_stays_reachable_without_a_browser_session(client, password_set):
    # The signal webhook has its own X-API-Key auth. The cookie gate must not
    # shadow it, or the 401 would come from the wrong layer.
    resp = client.post("/api/webhook/signal", json={})
    assert resp.status_code != 302
    assert resp.get_json()["error"] != "Not authenticated"


def test_healthz_is_public(client, password_set):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_logout_clears_the_session(client, password_set):
    client.post("/login", data={"password": PASSWORD})
    resp = client.get("/logout")
    assert resp.status_code == 302
    with client.session_transaction() as sess:
        assert "summit_auth" not in sess
