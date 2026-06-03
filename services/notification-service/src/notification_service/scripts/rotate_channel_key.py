# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""rotate_channel_key — CLI to re-encrypt all channel configs.

Usage:
    docker compose exec notification-svc \
        python -m notification_service.scripts.rotate_channel_key \
        --old-key <BASE64_OLD_KEY> \
        --new-key <BASE64_NEW_KEY>

Exit codes:
    0 — success; prints "Re-encrypted N channel configs"
    1 — error (old key wrong, no DB, etc.); prints error to stderr

See ADR-0004 §4 and the operations runbook §key-rotation for the
full runbook including how to update CHANNEL_ENC_KEY in .env afterwards.
"""

from __future__ import annotations

import argparse
import asyncio
import sys


async def main(old_key: str, new_key: str) -> None:
    """Wire database and outbox then run RotateChannelKey."""
    import os

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from notification_service.application.use_cases.rotate_channel_key import RotateChannelKey
    from notification_service.infrastructure.db.repositories import (
        SqlaCredentialRepository,
        SqlaEventOutbox,
    )

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL environment variable is required", file=sys.stderr)
        sys.exit(1)

    engine = create_async_engine(database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    try:
        async with session_factory() as session, session.begin():
            use_case = RotateChannelKey(
                credential_repo=SqlaCredentialRepository(session),
                outbox=SqlaEventOutbox(session),
            )
            try:
                count = await use_case.execute(old_key=old_key, new_key=new_key)
            except RuntimeError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                sys.exit(1)

        print(f"Re-encrypted {count} channel configs")
    finally:
        await engine.dispose()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Re-encrypt all notification channel configs with a new CHANNEL_ENC_KEY."
    )
    parser.add_argument(
        "--old-key",
        required=True,
        help=(
            "Current Fernet key (base64-urlsafe, 32 bytes). "
            "Generate with: python -c "
            "'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
        ),
    )
    parser.add_argument(
        "--new-key",
        required=True,
        help="New Fernet key to re-encrypt with.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(main(old_key=args.old_key, new_key=args.new_key))
