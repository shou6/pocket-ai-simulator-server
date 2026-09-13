from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from pocket_api.db.base import Base
from pocket_api.db.models import *  # noqa: F403  モデルを metadata に登録する
from pocket_api.main import create_app
from pocket_api.settings import get_settings

TEST_SCHEMA = "pocket_test"
"""テスト専用のスキーマ。

**開発用のテーブルには触らない。** 以前は既定のスキーマに `create_all` / `drop_all` して
いたため、テストを流すたびに開発用のテーブルが消えていた（`alembic_version` は
残るので `alembic upgrade` でも復旧しない）。
規約どおりスキーマで分離する（`.claude/rules/backend.md`）。
"""


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture(scope="session")
def test_engine() -> Iterator[Engine]:
    """テスト専用スキーマにテーブルを作り、終わったらスキーマごと落とす。

    開発用のテーブル（既定のスキーマ）には一切触らない。
    """
    url = get_settings().database_url
    admin = create_engine(url)
    with admin.connect() as connection:
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {TEST_SCHEMA}"))
        connection.commit()

    engine = create_engine(url, connect_args={"options": f"-csearch_path={TEST_SCHEMA}"})
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE"))
            connection.commit()
        admin.dispose()


@pytest.fixture
def db_session(test_engine: Engine) -> Iterator[Session]:
    """1 テストにつき 1 トランザクション。終わったら必ず巻き戻す。

    テストどうしが互いのデータを見ないようにする（`.claude/rules/backend.md`）。
    """
    connection = test_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def api(db_session: Session) -> Iterator[TestClient]:
    """テスト用のセッションを使う API クライアント。

    テストが入れたデータ（まだコミットしていない）を API 側からも見えるようにする。
    """
    from pocket_api.api.deps import get_session

    app = create_app()
    app.dependency_overrides[get_session] = lambda: db_session
    with TestClient(app) as c:
        yield c
