# Security Policy

## Supported versions

Only the latest released minor of the `2.x` line receives security fixes.
The current release is **2.4.0** (source of truth: `web/package.json`).
Older minors are not maintained — upgrade to the latest `2.x` release.

| Version          | Supported |
|------------------|-----------|
| 2.4.x (latest)   | ✅        |
| < 2.4            | ❌        |

## Reporting a vulnerability

**Please do not report security vulnerabilities through public issues.**

Use one of these private channels:

- **Preferred:** GitHub private vulnerability reporting — open a draft advisory
  at <https://github.com/MaximilianKaufmannCode/lotsman/security/advisories/new>.
- **Email:** **maximilian.kaufmann@microcode.pro**.

Please include:

- a description of the issue and its impact;
- steps to reproduce (a proof-of-concept if possible);
- the affected component/version and any suggested remediation.

You will receive an acknowledgement within 3 business days and a status update
within 10 business days. We practice coordinated disclosure — please give us a
reasonable window to ship a fix before any public disclosure.

## Scope and hardening notes

Лоцман is an on-premise application meant to run behind your own reverse proxy
and network controls. The most security-relevant components are:

- `auth-service` — authentication, TOTP, JWT, RBAC;
- `web-bff` — the single MFA/authorization chokepoint and the owner of session
  cookies and CSRF semantics;
- inter-service internal-JWT propagation (see [docs/adr/](docs/adr/)).

When deploying:

- never commit real secrets — copy `.env.example` to `.env` and fill it in;
- generate your own RS256 JWT key pair and strong database credentials
  (see [docs/deployment/](docs/deployment/));
- terminate TLS at the proxy and keep the services on a private network.

---

_Last updated: 2026-06-25_
