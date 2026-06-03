# Security Policy

## Supported versions

Security fixes are provided for the latest released version of Лоцман
(`lotsman`). Older versions are not maintained.

| Version | Supported |
|---------|-----------|
| latest  | ✅        |
| older   | ❌        |

## Reporting a vulnerability

**Please do not report security vulnerabilities through public issues.**

Email **security@lotsman.example.com** with:

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
