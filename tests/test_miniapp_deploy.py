"""Выкладка Mini App: конфигурация compose и Caddyfile."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _compose() -> dict:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def test_caddy_service_config():
    caddy = _compose()["services"]["caddy"]
    assert caddy["image"].startswith("caddy:")
    assert "miniapp" in caddy["profiles"]  # без профиля не стартует
    assert "80:80" in caddy["ports"] and "443:443" in caddy["ports"]
    # домен пробрасывается в контейнер из .env, остальные секреты — нет
    assert caddy["environment"]["MINIAPP_URL"].startswith("${MINIAPP_URL")
    assert "env_file" not in caddy


def test_caddyfile_proxies_to_bot():
    text = (ROOT / "Caddyfile").read_text(encoding="utf-8")
    assert "{$MINIAPP_URL}" in text
    assert "reverse_proxy bot:8000" in text


def test_bot_publishes_web_port_only_on_localhost():
    ports = _compose()["services"]["bot"]["ports"]
    assert "127.0.0.1:8000:8000" in ports  # для туннеля; наружу не светится


def test_compose_is_valid():
    compose = _compose()
    assert {"bot", "backup", "caddy"} <= set(compose["services"])
