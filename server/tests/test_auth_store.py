import sqlite3

import pytest

from app.auth.store import AuthError, AuthStore


def test_password_and_token_are_not_persisted_as_plaintext(tmp_path):
    path = tmp_path / "auth.sqlite3"
    store = AuthStore(path)
    result = store.register("Tester", "test-only-password!")
    with sqlite3.connect(path) as db:
        hashed = db.execute("SELECT password_hash FROM users").fetchone()[0]
        digest = db.execute("SELECT digest FROM sessions").fetchone()[0]
    assert hashed.startswith("$argon2id$")
    assert "test-only-password!" not in path.read_bytes().decode("latin1")
    assert digest != result["access_token"]
    assert result["access_token"].encode() not in path.read_bytes()
    assert AuthStore(path).authenticate(result["access_token"])["username"] == "tester"


def test_expired_session_is_rejected(tmp_path, monkeypatch):
    store = AuthStore(tmp_path / "auth.sqlite3")
    monkeypatch.setattr("app.auth.store.time.time", lambda: 1000)
    result = store.register("tester", "test-only-password!")
    monkeypatch.setattr("app.auth.store.time.time", lambda: 87400)
    with pytest.raises(AuthError) as error:
        store.authenticate(result["access_token"])
    assert error.value.status == 401


def test_rate_limit_survives_store_restart_and_recovers(tmp_path, monkeypatch):
    path = tmp_path / "auth.sqlite3"
    monkeypatch.setattr("app.auth.store.time.time", lambda: 1000)
    AuthStore(path).throttle("ip", 1)
    with pytest.raises(AuthError) as error:
        AuthStore(path).throttle("ip", 1)
    assert error.value.status == 429
    monkeypatch.setattr("app.auth.store.time.time", lambda: 1900)
    AuthStore(path).throttle("ip", 1)


def test_bootstrap_never_promotes_existing_user(tmp_path):
    store = AuthStore(tmp_path / "auth.sqlite3")
    session = store.register("tester", "test-only-password!")
    with pytest.raises(ValueError):
        store.bootstrap_admin("tester", "other-test-password!")
    assert store.authenticate(session["access_token"])["role"] == "user"
