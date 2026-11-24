from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    admin_ids: Annotated[list[int], NoDecode] = []
    channel_url: str = ""
    # Mini App: без miniapp_url веб-часть не стартует. dev_tg_id — обход авторизации, только локально.
    miniapp_url: str = ""
    miniapp_port: int = 8000
    dev_tg_id: int | None = None

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, v):
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [int(x) for x in v.split(",") if x.strip()]
        return v


def load_settings() -> Settings:
    return Settings()
