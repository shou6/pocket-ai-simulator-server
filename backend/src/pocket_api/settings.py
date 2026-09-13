"""環境変数から読み込むアプリケーション設定。"""

import json
from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_DRIVERLESS_SCHEMES = ("postgres://", "postgresql://")


class Settings(BaseSettings):
    """`.env` または環境変数から読み込む設定値。"""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    database_url: str = "postgresql+psycopg://pocket:pocket@localhost:5433/pocket"
    # ホストのブラウザから見えるオリジンは 8434（compose が 5173 を公開している先）。
    # コンテナ内のブラウザ用に 5173 も残す。
    # 環境変数では JSON 配列でもカンマ区切りでも書ける（Render のダッシュボードではカンマ区切り）。
    cors_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:8434",
        "http://localhost:5173",
    ]

    @field_validator("database_url")
    @classmethod
    def _use_psycopg3(cls, value: str) -> str:
        """ドライバを書いていない接続文字列を psycopg（3）に向ける。

        Render などが渡す `postgresql://…` のままだと、SQLAlchemy は入っていない psycopg2 を探す。
        """
        for scheme in _DRIVERLESS_SCHEMES:
            if value.startswith(scheme):
                return "postgresql+psycopg://" + value.removeprefix(scheme)
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        text = value.strip()
        if text.startswith("["):
            return json.loads(text)
        return [origin.strip() for origin in text.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """設定をシングルトンとして返す。"""
    return Settings()
