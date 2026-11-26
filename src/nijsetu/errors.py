"""Internal exceptions. Messages must stay safe: reason codes, never raw content."""

from __future__ import annotations


class SafeError(Exception):
    """Base class: message is a safe reason code, detail is kept out of str()."""

    def __init__(self, reason_code: str, detail: str = ""):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.detail = detail


class UnsupportedFormatError(SafeError):
    pass


class LimitExceededError(SafeError):
    pass


class MalformedInputError(SafeError):
    pass


class ProcessingBlockedError(SafeError):
    """Raised when the run cannot complete safely (exit code 3)."""
