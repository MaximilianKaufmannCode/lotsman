# Deployment guide

Лоцман is designed to run **on-premise / on a private network**, behind your own
reverse proxy with TLS. This guide covers a single-host Docker Compose
deployment, which is sufficient for the small teams Лоцман targets.

> For local development use `make dev` instead — see the
> [README](../../README.md#quickstart-5-minutes).

## 1. Prerequisites

- A Linux host with **Docker 26+** and Compose v2.
- A DNS name pointing at the host (e.g. `lotsman.example.com`) and a TLS
  certificate for it (Let's Encrypt or your corporate CA).
- Outbound access to your SMTP/EWS server and, if used, the Telegram Bot API and
  Dion API.

## 2. Get the code

```bash
git clone https://github.com/MaximilianKaufmannCode/lotsman.git
cd lotsman
```

## 3. Generate secrets

**Never commit secrets.** Generate them on the host.

RS256 key pair for access-token signing:

```bash
mkdir -p infra/secrets
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out infra/secrets/jwt-private.pem
openssl rsa -pubout -in infra/secrets/jwt-private.pem -out infra/secrets/jwt-public.pem
```

Strong random values for the remaining secrets:

```bash
openssl rand -hex 32   # POSTGRES_PASSWORD / per-service DB passwords
openssl rand -hex 32   # INTERNAL_JWT_SECRET (service-to-service)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # TOTP_ENC_KEY
```

## 4. Configure `.env`

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:

| Variable | Notes |
|---|---|
| `WEB_BFF_URL` | Public URL, e.g. `https://lotsman.example.com` — used in email/calendar deep-links |
| `POSTGRES_PASSWORD` and per-service DB passwords | from step 3 |
| `INTERNAL_JWT_SECRET` | from step 3 |
| `TOTP_ENC_KEY` | Fernet key from step 3 |
| `JWT_PRIVATE_KEY_PATH` / `JWT_PUBLIC_KEY_PATH` | paths to the keys from step 3 |
| SMTP / Telegram / Dion credentials | for the channels you enable |

Every variable is documented inline in `.env.example`.

## 5. Start the stack and run migrations

```bash
docker compose -f infra/compose.prod.yml --env-file .env up -d --build

for s in auth registry notification audit; do
  docker compose -f infra/compose.prod.yml run --rm ${s}-svc alembic upgrade head
done
```

## 6. Reverse proxy & TLS

Terminate TLS at Nginx (or your existing proxy) and forward `/api/*` to `web-bff`
and everything else to the static SPA. A reference config lives in
[`infra/nginx/`](../../infra/nginx/). Keep the application services bound to the
private network — only the proxy should be reachable from outside.

## 7. First administrator

Create the first admin user (the seed/bootstrap procedure is described in the
`auth-service` README). The user completes TOTP enrollment on first login; MFA is
mandatory for every role.

## 8. Observability (optional)

```bash
docker compose -f infra/compose.observability.yml --env-file .env up -d
```

Brings up Prometheus, Grafana, Loki, and Promtail. Restrict access to these to
operators only.

## 9. Backups

- **PostgreSQL** — schedule `pg_dump` (or use your platform's backup tooling).
  The audit log is append-only and month-partitioned; include all schemas.
- **Secrets** — back up `infra/secrets/` (the JWT key pair) separately and
  securely; losing the private key invalidates all issued tokens.

## Upgrades

```bash
git pull
docker compose -f infra/compose.prod.yml --env-file .env up -d --build
for s in auth registry notification audit; do
  docker compose -f infra/compose.prod.yml run --rm ${s}-svc alembic upgrade head
done
```

Review `CHANGELOG.md` for breaking changes before upgrading across a MAJOR
version.
