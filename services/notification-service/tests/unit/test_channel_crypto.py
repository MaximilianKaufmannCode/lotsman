# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for ChannelCipher — US-16.2 (round-trip, missing key)."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet


def test_encrypt_decrypt_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Encrypted config can be decrypted back to original dict."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("CHANNEL_ENC_KEY", key)

    from notification_service.infrastructure.channel_crypto import ChannelCipher

    cipher = ChannelCipher()
    config = {
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_user": "user",
        "smtp_password": "secret",
        "from_address": "noreply@example.com",
        "from_name": "Лоцман",
    }
    encrypted = cipher.encrypt(config)
    assert isinstance(encrypted, bytes)
    decrypted = cipher.decrypt(encrypted)
    assert decrypted == config


def test_encrypt_produces_opaque_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The encrypted bytes do not contain the plaintext password."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("CHANNEL_ENC_KEY", key)

    from notification_service.infrastructure.channel_crypto import ChannelCipher

    cipher = ChannelCipher()
    config = {"smtp_password": "my-secret-password"}
    encrypted = cipher.encrypt(config)
    assert b"my-secret-password" not in encrypted


def test_missing_key_raises_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """ChannelCipher raises RuntimeError if CHANNEL_ENC_KEY is missing (US-16.2)."""
    monkeypatch.delenv("CHANNEL_ENC_KEY", raising=False)

    # Force re-import so the class is not cached with the old env.
    import importlib

    import notification_service.infrastructure.channel_crypto as mod

    importlib.reload(mod)

    with pytest.raises(RuntimeError, match="CHANNEL_ENC_KEY is required"):
        mod.ChannelCipher()


def test_wrong_key_raises_on_decrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Decrypting with a different key raises an exception."""

    key_a = Fernet.generate_key().decode()
    key_b = Fernet.generate_key().decode()

    monkeypatch.setenv("CHANNEL_ENC_KEY", key_a)

    import importlib

    import notification_service.infrastructure.channel_crypto as mod

    importlib.reload(mod)
    cipher_a = mod.ChannelCipher()
    encrypted = cipher_a.encrypt({"x": 1})

    monkeypatch.setenv("CHANNEL_ENC_KEY", key_b)
    importlib.reload(mod)
    cipher_b = mod.ChannelCipher()

    with pytest.raises((ValueError, Exception)):  # InvalidToken from cryptography
        cipher_b.decrypt(encrypted)
