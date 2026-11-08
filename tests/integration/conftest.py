"""A fresh PostgreSQL database per test, on the server INTEGRATION_DATABASE_URL points to.

The URL must name the `postgres` maintenance database. It is used only to create and drop
the per-test databases, so the application's database is never touched.
"""

import uuid
from collections.abc import AsyncIterator, Iterator

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from oncall_rag.shared.adapters.vectorstore.pgvector import PgVectorStore, connect_pgvector_store

MAINTENANCE_DATABASE = "postgres"
TEST_DATABASE_PREFIX = "oncall_rag_test_"
# Small enough that the expected ranking can be read off the vectors.
TEST_DIMENSION = 3
TEST_POOL_SIZE = 2


class IntegrationSettings(BaseSettings):
    """From the environment only: `.env` holds the application's database, not a test server."""

    model_config = SettingsConfigDict(extra="ignore", hide_input_in_errors=True)

    integration_database_url: SecretStr | None = None
    # Set by GitHub Actions; there the tests must run, never be skipped.
    ci: bool = False


@pytest.fixture(scope="session")
def server_url() -> str:
    settings = IntegrationSettings()
    if settings.integration_database_url is None:
        if settings.ci:
            pytest.fail("INTEGRATION_DATABASE_URL must be set in CI")
        pytest.skip("set INTEGRATION_DATABASE_URL to a PostgreSQL server with pgvector")
    url = settings.integration_database_url.get_secret_value()
    if conninfo_to_dict(url).get("dbname") != MAINTENANCE_DATABASE:
        pytest.fail(
            "INTEGRATION_DATABASE_URL must point to the `postgres` maintenance database "
            "of a throwaway server"
        )
    return url


@pytest.fixture
def database_url(server_url: str) -> Iterator[str]:
    """A new, empty database, dropped after the test."""
    name = f"{TEST_DATABASE_PREFIX}{uuid.uuid4().hex}"
    identifier = sql.Identifier(name)
    with psycopg.connect(server_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(identifier))
    try:
        yield make_conninfo(server_url, dbname=name)
    finally:
        with psycopg.connect(server_url, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(identifier))


@pytest.fixture
async def store(database_url: str) -> AsyncIterator[PgVectorStore]:
    """A store on an initialized, empty index of TEST_DIMENSION-dimensional vectors."""
    async with connect_pgvector_store(
        database_url, dimension=TEST_DIMENSION, pool_max_size=TEST_POOL_SIZE
    ) as connected:
        await connected.initialize()
        yield connected
