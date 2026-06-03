# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""CLI: bootstrap_admin — create/recover the first Space-admin user.

Usage (inside container):
    python -m auth_service.scripts.bootstrap_admin --email <e> --full-name "<n>"

Or via Makefile:
    make admin-create EMAIL=admin@org.local FULL_NAME="Иван Петров"

Exit codes:
    0 — success
    1 — user error (validation failure, user has active TOTP)
    2 — unexpected error

SECURITY: The OTP value is printed to stdout ONLY.
          It MUST NOT appear in any log output.
          structlog is configured with a filter that would redact it anyway,
          but the use case itself never passes the OTP to any logger.
"""

from __future__ import annotations

import argparse
import asyncio
import sys


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bootstrap_admin",
        description="Bootstrap the first (or recovery) Space-admin user.",
    )
    p.add_argument("--email", required=True, help="Admin email address")
    p.add_argument("--full-name", required=True, help="Admin full name")
    return p


async def _run(email: str, full_name: str) -> None:
    """Wire dependencies and execute the BootstrapAdmin use case."""
    import redis.asyncio as aioredis
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from auth_service.application.dto import BootstrapAdminCommand
    from auth_service.application.use_cases.bootstrap_admin import (
        BootstrapAdmin,
        UserHasActiveTotpError,
    )
    from auth_service.config import get_settings
    from auth_service.domain.errors import AuthDomainError
    from auth_service.infrastructure.db.repositories import (
        SqlaEventOutbox,
        SqlaUserRepository,
    )
    from auth_service.infrastructure.password import Argon2PasswordHasher
    from auth_service.infrastructure.redis.bootstrap_otp_store import RedisBootstrapOtpStore

    settings = get_settings()

    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    redis_client: aioredis.Redis = aioredis.from_url(  # type: ignore[no-untyped-call]
        settings.redis_url, decode_responses=False
    )

    try:
        async with session_factory() as session, session.begin():
            use_case = BootstrapAdmin(
                user_repo=SqlaUserRepository(session),
                hasher=Argon2PasswordHasher(),
                otp_store=RedisBootstrapOtpStore(redis_client),
                outbox=SqlaEventOutbox(session),
            )
            cmd = BootstrapAdminCommand(email=email, full_name=full_name)

            try:
                result = await use_case.execute(cmd=cmd)
            except UserHasActiveTotpError as exc:
                print(exc.default_message, file=sys.stderr)
                sys.exit(1)
            except AuthDomainError as exc:
                print(str(exc), file=sys.stderr)
                sys.exit(1)

        # Print to stdout — outside the transaction so we only print on commit success.
        separator = "=" * 60
        print(separator)
        print(f"User created/updated: {result.user_id}")
        print(f"Email: {result.email}")
        print("Role: admin")
        print(f"One-time OTP (TTL 24h): {result.oob_otp}")
        print()
        print("Pass this OTP to the user out-of-band (call, Signal). Do NOT log it.")
        print(separator)

    except (UserHasActiveTotpError, AuthDomainError):
        # Already handled above; re-raised only if something went wrong in teardown.
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        print(f"Unexpected error: {exc}", file=sys.stderr)
        sys.exit(2)
    finally:
        await redis_client.aclose()
        await engine.dispose()


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    asyncio.run(_run(email=args.email, full_name=args.full_name))


if __name__ == "__main__":
    main()
