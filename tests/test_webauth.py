"""Проверка подписи initData Telegram Mini App."""
import hashlib
import hmac
import json
from urllib.parse import urlencode

from app.webauth import MAX_AGE_SECONDS, validate_init_data

TOKEN = "123:test-token"
NOW = 1_700_000_000.0


def make_init_data(token: str = TOKEN, auth_date: int = int(NOW), user: dict | None = None,
                   **extra) -> str:
    pairs = {"auth_date": str(auth_date), "query_id": "AAEtest", **extra}
    if user is None:
        user = {"id": 4242, "username": "tester", "first_name": "T"}
    pairs["user"] = json.dumps(user, separators=(",", ":"), ensure_ascii=False)
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


def test_valid_signature_returns_user():
    data = validate_init_data(make_init_data(), TOKEN, now=NOW)
    assert data == {"tg_id": 4242, "username": "tester"}


def test_tampered_pair_is_rejected():
    signed = make_init_data()
    bad = signed.replace("auth_date=1700000000", "auth_date=1700000001", 1)
    assert validate_init_data(bad, TOKEN, now=NOW) is None


def test_wrong_token_is_rejected():
    assert validate_init_data(make_init_data(), "999:other-token", now=NOW) is None


def test_expired_auth_date_is_rejected():
    old = make_init_data(auth_date=int(NOW) - MAX_AGE_SECONDS - 1)
    assert validate_init_data(old, TOKEN, now=NOW) is None


def test_future_auth_date_is_rejected():
    future = make_init_data(auth_date=int(NOW) + 3600)
    assert validate_init_data(future, TOKEN, now=NOW) is None


def test_missing_hash_is_rejected():
    assert validate_init_data("auth_date=1700000000&user=%7B%7D", TOKEN, now=NOW) is None
    assert validate_init_data("", TOKEN, now=NOW) is None


def test_missing_or_bad_user_is_rejected():
    assert validate_init_data(make_init_data(user={}), TOKEN, now=NOW) is None
    assert validate_init_data(make_init_data(user={"id": "42"}), TOKEN, now=NOW) is None


def test_user_without_username_is_ok():
    data = validate_init_data(make_init_data(user={"id": 7}), TOKEN, now=NOW)
    assert data == {"tg_id": 7, "username": None}
