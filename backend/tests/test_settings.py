"""設定（`pocket_api.settings`）のテスト。"""

import pytest

from pocket_api.settings import Settings


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        # Render の接続文字列はドライバを書かない。SQLAlchemy は psycopg2 を探しに行く
        (
            "postgresql://user:pass@dpg-xxx/pocket",
            "postgresql+psycopg://user:pass@dpg-xxx/pocket",
        ),
        (
            "postgres://user:pass@host:5432/pocket",
            "postgresql+psycopg://user:pass@host:5432/pocket",
        ),
        (
            "postgresql+psycopg://pocket:pocket@db:5432/pocket",
            "postgresql+psycopg://pocket:pocket@db:5432/pocket",
        ),
    ],
)
def test_database_url_uses_psycopg3(given: str, expected: str) -> None:
    assert Settings(database_url=given).database_url == expected


def test_cors_origins_can_be_a_comma_separated_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example, https://b.example")
    assert Settings(_env_file=None).cors_origins == ["https://a.example", "https://b.example"]


def test_cors_origins_still_accept_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", '["https://a.example"]')
    assert Settings(_env_file=None).cors_origins == ["https://a.example"]
