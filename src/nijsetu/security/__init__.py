"""Storage-encryption primitives: a cryptographic domain distinct from
`phase2.keys` (pseudonymization). See `secure_store` for the implementation."""

from __future__ import annotations

from .secure_store import (
    SecureStore,
    SecureStoreError,
    decrypt_blob,
    derive_storage_key,
    encrypt_blob,
    ram_backed_tempdir,
)

__all__ = [
    "SecureStore",
    "SecureStoreError",
    "decrypt_blob",
    "derive_storage_key",
    "encrypt_blob",
    "ram_backed_tempdir",
]
