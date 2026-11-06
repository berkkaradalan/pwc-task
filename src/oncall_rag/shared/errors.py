"""Exceptions for rules of this application that no library expresses."""


class ReindexRequiredError(Exception):
    """Raised when the index was built with another embedding model or vector dimension."""


class ProviderResponseError(Exception):
    """Raised when a provider answers, but the answer is unusable (empty, wrong vector length)."""
