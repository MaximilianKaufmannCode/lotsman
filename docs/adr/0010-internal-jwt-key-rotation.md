# ADR-0010: Internal JWT Key Rotation — Dual-Key Verify, Single-Key Issue

- **Status**: Proposed
- **Date**: 2026-05-22
- **Deciders**: architect (proposed), venawaziwoco83@gmail.com (pending)
- **Depends on**: ADR-0002 (Service boundaries — internal HS256 JWT defined here), ADR-0003 (Authentication & Session Lifecycle — access RS256 JWT, distinct from internal)
- **References**:
  - `shared/src/lotsman_shared/auth/internal_jwt.py` (current single-key encode/decode)
  - `infra/compose.prod.yml` env vars `INTERNAL_JWT_KEY_AUTH`, `INTERNAL_JWT_KEY_REGISTRY`, `INTERNAL_JWT_KEY_NOTIFICATION`, `INTERNAL_JWT_KEY_AUDIT`, `INTERNAL_JWT_KEY_SYSTEM_CONTROL`

## Context

Лоцман has **5 separate internal HS256 signing keys**, one per service that issues internal JWTs:

| Key env var | Issued by | Verified by |
|---|---|---|
| `INTERNAL_JWT_KEY_AUTH` | auth-svc | web-bff, registry-svc, notification-svc, audit-svc |
| `INTERNAL_JWT_KEY_REGISTRY` | registry-svc | audit-svc (event sourcing through outbox) |
| `INTERNAL_JWT_KEY_NOTIFICATION` | notification-svc | audit-svc |
| `INTERNAL_JWT_KEY_AUDIT` | audit-svc | (consumer-only, no upstream verifier) |
| `INTERNAL_JWT_KEY_SYSTEM_CONTROL` | system-control | web-bff |

Tokens are short-lived (`INTERNAL_JWT_TTL_SECONDS = 60`). Each service today does:

```python
# pseudocode of shared/src/lotsman_shared/auth/internal_jwt.py — simplified
def encode(claims: dict, key: str) -> str:
    return jwt.encode(claims, key, algorithm="HS256")

def decode(token: str, key: str) -> dict:
    return jwt.decode(token, key, algorithms=["HS256"])
```

This is a **single-key** design: encode with $K$, decode with $K$. To rotate $K \to K'$ requires:

1. Generate $K'$.
2. Put $K'$ into `.env` (replacing $K$).
3. Restart **every** container that issues OR verifies tokens signed by this key — **simultaneously**.
4. Any token issued with $K$ that has not yet been verified at restart-time → fails on next verification → cascades as a 5xx error to the user.

Because TTL is 60 s, the window of broken tokens is bounded — but during the restart, in-flight requests fail. There is no documented procedure, no migration script, and no test that rotation works. **The keys have never been rotated** since the original deploy (2026-05-14). If any key is suspected compromised, today the response is a coordinated restart with user-visible 5xx for ~the restart duration.

This is acceptable only because keys haven't actually leaked. The plan must support **planned** rotation (annual hygiene) and **emergency** rotation (after a suspected leak) without user-visible downtime.

### What this ADR is NOT

- Not about the **RS256 access JWT** (ADR-0003 §7) — that key (`jwt-private.pem`) is RSA and a separate concern. Public-key crypto has its own rotation pattern (publish new public key in JWKS, rotate private). Out of scope here.
- Not about the **per-channel admin token encryption keys** (`CHANNEL_ENC_KEY`, `TOTP_ENC_KEY`, `LOTSMAN_SIGNED_URL_KEY`) — those encrypt data at rest and need a different rotation pattern (re-encrypt rows on rotation). Separate future ADR.

### Threat model

- **T1 (primary):** internal HS256 key leaks (e.g. `.env` read by a non-root process before the P0 chmod 0600 fix from production-security-audit-2026-05-22). Attacker mints valid internal JWTs as long as the key is live. **Mitigation:** rotate within minutes of suspicion; only `K'`-signed tokens are valid after rotation completes.
- **T2:** developer error — accidental commit of `.env` to git. Same response as T1.
- **T3:** weak key generation — `INTERNAL_JWT_KEY_*` are 64 hex chars (256 bits) per `.env` inspection. HMAC-SHA256 with a 256-bit key is fine; no weakness here. NOT in scope.

## Decision

**Adopt dual-key rotation with a `key_id` (kid) header and a server-side key set.** Verifiers maintain `current` + `previous` keys for a bounded overlap window; issuer signs only with `current`. Rollover happens by promoting `next → current → previous → discard` over time, with NO simultaneous restart.

### Design

#### D1. Per-service key set, not single key

Each service that **issues or verifies** an internal JWT for a given role maintains a small set of keys:

```python
# shared/src/lotsman_shared/auth/internal_jwt.py — proposed
@dataclass(frozen=True)
class InternalJwtKey:
    kid: str              # opaque identifier, e.g. "auth-2026-05"
    secret: str           # 256-bit hex
    role: KeyRole         # "current" | "previous" | "next"

class InternalJwtKeySet:
    current:  InternalJwtKey         # the one that encode() uses
    previous: InternalJwtKey | None  # accepted for decode() only, during overlap
    next:     InternalJwtKey | None  # accepted for decode() ONLY if issuer has switched

    def encode(self, claims: dict) -> str:
        # always sign with current
        return jwt.encode({**claims}, self.current.secret, algorithm="HS256",
                          headers={"kid": self.current.kid})

    def decode(self, token: str) -> dict:
        # PyJWT API: peek header.kid → look up which key → verify
        kid = jwt.get_unverified_header(token)["kid"]
        key = self._lookup(kid)                  # raises if unknown kid
        return jwt.decode(token, key.secret, algorithms=["HS256"])
```

#### D2. Three-phase rollover

To rotate `K_old → K_new` on (say) the auth-issued key:

**Phase A — prepare (no downtime, no user impact):**
1. Generate `K_new` and a new `kid`, e.g. `auth-2026-08`.
2. Add **as `next`** to verifiers: `INTERNAL_JWT_KEY_AUTH_NEXT=<K_new>`, `INTERNAL_JWT_KEY_AUTH_NEXT_KID=auth-2026-08`. Issuer (auth-svc) keeps signing with `K_old`.
3. **Rolling restart** of all verifiers (web-bff, registry-svc, notification-svc, audit-svc) — *not* simultaneously, one at a time. Each one comes up knowing about both keys, but `K_old`-signed tokens still verify fine.

**Phase B — promote (atomic per-issuer):**
4. On the issuer (auth-svc), promote `next → current`: in `.env`, swap `INTERNAL_JWT_KEY_AUTH=<K_new>` (and matching `KID`); add `INTERNAL_JWT_KEY_AUTH_PREVIOUS=<K_old>` (and matching `KID`).
5. **Restart only the issuer.** From this moment, auth-svc signs all new tokens with `K_new`. In-flight `K_old`-signed tokens still verify (TTL bound: 60 s).

**Phase C — retire (no downtime, no user impact):**
6. Wait `≥ 2 × INTERNAL_JWT_TTL_SECONDS` (default = 120 s, generous). After this all `K_old` tokens are expired.
7. Remove `INTERNAL_JWT_KEY_AUTH_PREVIOUS` and matching `KID` from all `.env`. **Rolling restart** of verifiers.

Total downtime: **zero** (each container restart is ~5 s, and at no point is there a window where a valid in-flight token cannot be verified by *some* key).

#### D3. Env-var convention (normative)

```
INTERNAL_JWT_KEY_<ROLE>=<hex>                    # current — required
INTERNAL_JWT_KEY_<ROLE>_KID=<string>             # required, matches `kid` header

INTERNAL_JWT_KEY_<ROLE>_PREVIOUS=<hex>           # optional — present only during Phase B–C
INTERNAL_JWT_KEY_<ROLE>_PREVIOUS_KID=<string>

INTERNAL_JWT_KEY_<ROLE>_NEXT=<hex>               # optional — present only during Phase A
INTERNAL_JWT_KEY_<ROLE>_NEXT_KID=<string>
```

**[NR-1]** On startup, each service MUST refuse to start if `INTERNAL_JWT_KEY_<ROLE>_KID` is missing (we deliberately do not allow keys without `kid` — there is no migration path back to "header-less" tokens).

**[NR-2]** Decode MUST reject tokens with `kid` not in the active key-set with HTTP 401 (or the equivalent internal error in the consumer). It MUST NOT fall back to "try every key" — that defeats the purpose of `kid`.

**[NR-3]** Each issuer MUST sign with exactly one key (`current`) at a time. Verifier MAY accept up to three (`current` + `previous` + `next`).

**[NR-4]** Logging of rotation events MUST go through `audit-svc` with `event_type=security.internal_jwt.key_rotated.v1`, payload includes `role`, `previous_kid`, `new_kid`, `actor=ops`. NEVER log the secret itself (`redact_sensitive_fields` extended to scrub all `_KEY_*` env-shape strings).

#### D4. Backward compatibility — existing tokens have NO `kid`

Tokens issued today are signed with the single key and have no `kid` header. Cutover plan:

1. **Migration release** (`v1.X.0` minor): introduce key-set API but **default `kid` to `"default"`** when reading; issuer starts sending `kid: "default"`. Verifier accepts `kid: "default"` mapping to the single existing key. *No env-var changes yet.*
2. **First real rotation** (any time after migration release is live for 1 week): add `_NEXT`, do Phase A–C above with a new `kid`. After this rotation, `"default"` is gone and all tokens have meaningful kids.

This avoids a flag day. Verifiers built on the migration release tolerate header-less tokens for one TTL (`60 s`) at startup; logged as `internal_jwt_legacy_kid_used` with WARN level. Tokens minted by the *new* issuer **always** have `kid`.

#### D5. Emergency-rotation drill (operations)

A *fast* rotation for suspected compromise — skip Phase A:

1. Generate `K_new` and `kid_new`.
2. Set issuer's `.env`: `_CURRENT=K_new`, `_PREVIOUS=K_old`.
3. Restart issuer. *(User-visible: zero — in-flight 60 s tokens still verify with `K_old` via `_PREVIOUS`.)*
4. Wait 60 s.
5. Set ALL `.env` to remove `K_old` from `_PREVIOUS`. Rolling-restart everyone.

This compresses the safe procedure to ~2 min total. Trade-off: a verifier that hasn't yet picked up the new `_PREVIOUS` env will reject `K_old` tokens during step 3 — acceptable for emergency.

**[NR-5]** A runbook entry `docs/runbook/emergency-internal-jwt-rotation.md` MUST exist before this ADR is closed, including: which env vars to update, exact restart sequence, smoke-test commands. (`ops` deliverable in the implementation handoff.)

### Versioning

Shared library change in `lotsman_shared/auth/internal_jwt.py`, new env vars. Per the project conventions this is **MINOR** (`v1.13.x → v1.14.0`) — additive API and backward-compatible (D4). No breaking inter-service contract change because all current code paths continue to work after the migration release.

## Alternatives considered

### Option B — JWKS-style key publication via internal HTTP endpoint

Each issuer publishes `GET /internal/jwks` (token-authenticated) returning its current keys. Verifiers fetch periodically.

**Pros:** matches industry-standard OAuth2/OIDC JWKS pattern; rotation is "issuer changes its endpoint output", no env-var coordination.

**Cons:** introduces a circular dependency — to verify internal JWTs you need a working internal JWT to call `/internal/jwks`. Bootstrap problem. Also: extra runtime HTTP dependency, cache invalidation logic, ~200 LoC of new infra for a 4-service mesh. Tecnologically over-engineered for our scale.

**Verdict:** rejected — env-var-based key distribution is fine for 4 services on one box; revisit only if Лоцман grows to multi-host.

### Option C — Replace HS256 with RS256 for internal JWTs (single signing key, multiple public-key verifiers)

Use asymmetric keys: each issuer holds private, each verifier holds public.

**Pros:** verifiers can't forge tokens (today an `auth_app` reading `INTERNAL_JWT_KEY_AUTH` could mint auth tokens — a containment violation if a verifier service is compromised).

**Cons:** big change. 5 keypairs to generate and distribute. Larger tokens (HS256 ~250B, RS256 ~700B) → bandwidth impact on hot inter-service path. The threat being mitigated (compromised verifier forging issuer tokens) is **not in T1–T3**. Not worth the complexity for our threat model.

**Verdict:** rejected as primary; reconsidered if a future ADR finds verifier-forgery in the threat model.

### Option D — Status quo + better incident playbook

Do nothing structural; document the "stop all containers, change env, restart" emergency response.

**Pros:** zero engineering work.

**Cons:** user-visible 5xx during every rotation. Operators avoid rotating "because users complain", so keys age indefinitely. The actual outcome of Option D is **"never rotate"**.

**Verdict:** rejected — the failure mode of Option D is the situation we already have, which is the originating finding.

## Implementation plan handoff (for `backend`)

1. Extend `shared/src/lotsman_shared/auth/internal_jwt.py` with `InternalJwtKeySet` per §D1. Keep old `encode(token, key)` shim that wraps key-set with `kid="default"` for one release (D4).
2. Update each service's startup to read `_KID`, `_PREVIOUS`, `_NEXT` env vars and build the key set.
3. Add `WARN`-level log `internal_jwt_legacy_kid_used` for the cutover window.
4. Write `tests/contract/test_internal_jwt_rotation.py` covering:
   - Issuer signs with `current.kid`; verifier accepts.
   - Verifier accepts `previous.kid` token within TTL.
   - Verifier accepts `next.kid` token (forward compatibility for Phase A).
   - Verifier rejects unknown `kid`.
   - No-`kid` token is accepted only during the cutover release.
5. **`ops`** in parallel: write `docs/runbook/emergency-internal-jwt-rotation.md` per NR-5.
6. **First real rotation** (the test of the design) — pick `INTERNAL_JWT_KEY_AUDIT` (lowest blast radius, only audit-svc consumes) ~1 week after release. Document timing in CHANGELOG.

## Status

**Proposed.** Implementation triggered by operator approval. Estimated effort: **~6 hours** (shared lib + 5-service env wiring + tests + runbook). No data migration. No downtime in the steady-state cutover; one rolling restart per service in the migration release.
