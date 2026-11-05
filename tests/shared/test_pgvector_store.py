import pytest

from oncall_rag.shared.adapters.vectorstore.pgvector import connect_pgvector_store
from oncall_rag.shared.errors import ConfigError


async def test_dimension_above_the_hnsw_limit_is_a_config_error() -> None:
    # The check runs before connecting, so the URL is never used.
    with pytest.raises(ConfigError, match="EMBEDDING_DIM must be at most 2000"):
        async with connect_pgvector_store("postgresql://unused", dimension=5000, pool_max_size=1):
            pass
