"""Проверка подписи initData Telegram Mini App.

Алгоритм (https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app):
data-check-string — пары key=value кроме hash, отсортированные по ключу, через "\\n";
secret = HMAC_SHA256(key="WebAppData", msg=BOT_TOKEN); подпись = HMAC_SHA256(secret, data-check-string).
"""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

MAX_AGE_SECONDS = 86400  # initData старше суток не принимаем
FUTURE_SKEW_SECONDS = 60


def validate_init_data(init_data: str, bot_token: str, now: float | None = None,
                       max_age: int = MAX_AGE_SECONDS) -> dict | None:
    """Проверить подпись initData. Возвращает {"tg_id", "username"} или None."""
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calculated = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, received_hash):
        return None

    if now is None:
        now = time.time()
    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError:
        return None
    if now - auth_date > max_age or auth_date > now + FUTURE_SKEW_SECONDS:
        return None

    try:
        user = json.loads(pairs.get("user", ""))
    except (json.JSONDecodeError, TypeError):
        return None
    tg_id = user.get("id") if isinstance(user, dict) else None
    if not isinstance(tg_id, int) or isinstance(tg_id, bool):
        return None
    username = user.get("username")
    return {"tg_id": tg_id, "username": username if isinstance(username, str) else None}
