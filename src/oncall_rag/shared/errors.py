"""Application exceptions. Adapters translate provider errors into these."""


class OncallRagError(Exception):
    """Base class for all expected application errors."""


class ConfigError(OncallRagError):
    """The settings are missing or invalid. The message never contains input values."""


class DocsDirectoryNotFoundError(OncallRagError):
    pass


class EmbedderUnavailableError(OncallRagError):
    """The embedding service could not be reached or kept failing."""


class EmbeddingError(OncallRagError):
    """The embedding service answered, but the request or response was invalid."""


class VectorStoreUnavailableError(OncallRagError):
    pass


class ReindexRequiredError(OncallRagError):
    """The stored index is incompatible with the current configuration."""


class EmbeddingModelMismatchError(ReindexRequiredError):
    def __init__(self, stored_models: set[str], configured_model: str) -> None:
        stored = ", ".join(sorted(stored_models))
        super().__init__(
            f"The index contains vectors from embedding model(s) [{stored}], "
            f"but EMBEDDING_MODEL is '{configured_model}'."
        )


class IndexSchemaMismatchError(ReindexRequiredError):
    def __init__(self, stored_dimension: int, configured_dimension: int) -> None:
        super().__init__(
            f"The index stores {stored_dimension}-dimensional vectors, "
            f"but EMBEDDING_DIM is {configured_dimension}."
        )
