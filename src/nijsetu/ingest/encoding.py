"""Strict encoding detection/decoding. No chardet guessing: BOM or declared only."""

from __future__ import annotations

import codecs

from ..errors import UnsupportedFormatError

_UTF16_BOMS = (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)


def detect_and_decode(raw: bytes, declared_encodings: tuple[str, ...] = ()) -> tuple[str, str]:
    """Returns (text, encoding_label). Raises UnsupportedFormatError if undecodable.

    Order: UTF-8 BOM, UTF-16 BOM, strict UTF-8, then any explicitly declared
    source encoding (never silently guessed).
    """
    if raw.startswith(codecs.BOM_UTF8):
        try:
            return raw[len(codecs.BOM_UTF8):].decode("utf-8"), "utf-8-sig"
        except UnicodeDecodeError as exc:
            raise UnsupportedFormatError("UNSUPPORTED_ENCODING", detail=str(exc)) from None
    if raw.startswith(_UTF16_BOMS):
        try:
            return raw.decode("utf-16"), "utf-16"
        except UnicodeDecodeError as exc:
            raise UnsupportedFormatError("UNSUPPORTED_ENCODING", detail=str(exc)) from None
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError as exc:
        for enc in declared_encodings:
            try:
                return raw.decode(enc), enc
            except (UnicodeDecodeError, LookupError):
                continue
        raise UnsupportedFormatError(
            "UNSUPPORTED_ENCODING", detail=str(exc)
        ) from None
