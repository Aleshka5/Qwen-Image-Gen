"""Seal provider secrets before they land in Mongo. The browser never receives them."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet


def _fernet(secret: str) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def seal(secret: str, value: str) -> str:
    return _fernet(secret).encrypt(value.encode("utf-8")).decode("ascii")


def open_secret(secret: str, token: str) -> str:
    return _fernet(secret).decrypt(token.encode("ascii")).decode("utf-8")


def hint(value: str) -> str:
    value = value.strip()
    if len(value) < 4:
        return "set"
    return f"····{value[-4:]}"
