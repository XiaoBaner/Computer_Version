"""Errors shared by the ASR stream and its backends."""

from __future__ import annotations


class AsrError(Exception):
    """Base error for the ASR module.

    ``retryable`` tells the integration layer whether a retry can help.
    ``fatal`` means the same stream cannot recover and should stay disabled
    while the user continues with ``input.text``.
    """

    def __init__(self, message: str, *, retryable: bool = False, fatal: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.fatal = fatal


class AsrUnavailable(AsrError):
    """The recogniser cannot be loaded or failed while decoding."""
