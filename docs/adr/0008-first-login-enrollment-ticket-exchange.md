# ADR-0008: First-Login Enrollment — Opaque Ticket Exchange (mirror of TOTP-login `session_ticket`)

- **Status**: Proposed
- **Date**: 2026-05-19
- **Deciders**: architect (proposed), venawaziwoco83@gmail.com (accepted)
- **Depends on**: ADR-0003 (Authentication & Session Lifecycle — §3 TOTP enrollment, §5b forced password change, §7 access JWT incl. mandatory `sid`, §12 generic-401, §13 session revocation, §14 Bearer-required state-changing routes, AAL2 line ~26), ADR-0004 (Two-tier administration — §1 privilege-separation/BFF chokepoint, §3 bootstrap forced first-login, §5 auto-invite OOB OTP)
- **References**:
  - `services/auth-service/src/auth_service/application/use_cases/start_login.py` (enrollment_token + session_ticket issuance, lines ~129–156 — both write `totp:login:pending:<token> = str(user_id)`, the confusion surface)
  - `services/auth-service/src/auth_service/application/use_cases/verify_totp.py` (the WORKING opaque-ticket→resolve→issue pattern this ADR mirrors; the enumerated session-mint sequence lines ~73–204)
  - `services/auth-service/src/auth_service/application/use_cases/confirm_totp_enrollment.py` (today emits `TotpEnrolled` + `BackupCodesGenerated`, NO `LoggedIn`, NO token deps)
  - `services/auth-service/src/auth_service/application/use_cases/change_password.py` (forced path lines ~85–108 — `PasswordChanged` + `UserActivated` + session mint)
  - `services/auth-service/src/auth_service/infrastructure/redis/pending_totp_store.py` (the reused opaque-ticket store; pending-SECRET store prefix is `totp:pending:<user_id>`, ticket store is `totp:login:pending:<ticket>`; scope discriminator added by this ADR)
  - `shared/src/lotsman_shared/logging.py` (`redact_sensitive_fields` lines ~38–66 — TOP-LEVEL keys only, no recursion; extended by MF-3)
  - `services/web-bff/src/web_bff/api/deps.py` (`_verify_access_jwt` / `RequireAccessClaims` — the failing gate, lines 79–161; UNCHANGED for non-enrollment routes)
  - `web/src/features/auth/types.ts` (`LoginFirstLoginResponse.next_step: "enroll_totp"` union literal — the SPA contract that does NOT change)
  - CHANGELOG.md [1.13.1] (the already-live BFF `next_step` regression fix this ADR builds on top of)

> **Revision history**
> - 2026-05-19 (rev. 1, original draft): opaque-ticket exchange mirroring `verify_totp`; D1–D7.
> - 2026-05-19 (rev. 2): revised per pre-implementation security review verdict **BLOCK** (1 Critical + 2 High + 4 lower). Decision section rewritten to close **MF-1..MF-7** as numbered, testable normative requirements (scope-discriminator on the Redis ticket value + store API change; enumerated enroll-only terminal session-mint; body-secret no-log invariant + recursion of `redact_sensitive_fields`; post-resolution `has_totp_enrolled` re-check; exclusive ticket-derived user binding; ticket-holder-controls-authenticator threat statement; uniform generic-401). Direction (opaque ticket, auth-svc resolution, body-carried, 300 s TTL, Options A/B/C rejected) **confirmed sound by security and unchanged**. §8 ruling re-affirmed: PATCH → `v1.13.2`.
> - 2026-05-19 (rev. 3, **this revision**): **F-008/F-010 spec-correctness fixes per security re-review (APPROVE-WITH-NOTES)** — the BLOCK is cleared; these are the only remaining pre-implementation requirements and need no further security cycle. **F-008 (High, spec-correctness):** corrected the over-broad "the `IssueSession` extraction is behaviour-preserving / same events" claim wherever it swept in the forced `change_password` path (D5.4.1, D6, INV-1, INV-6, Versioning, Implementation handoff). The forced `change_password` path emits **no** `LoggedIn` and writes **no** `last_login_at` today; routing it through `IssueSession` **intentionally ADDS** `auth.user.logged_in.v1` + `last_login_at` as a deliberate, audited audit-completeness improvement (`actor_id = resolved user_id`, in the caller's existing transaction) — explicitly NOT behaviour-preservation, NOT a regression. `verify_totp` is now described as the only genuinely behaviour-preserving call site (it already emits both). INV-6 made *more* precise: `QA` now EXPECTS the added event and MUST update existing `ChangePassword` outbox tests. Versioning rationale no longer leans on "behaviour-preserving refactor"; PATCH → `v1.13.2` / `### Fixed` is **unchanged**, re-justified as repair of a never-working path + internal Redis-value/store-API change + an additive emit of an *already-existing, already-consumed* internal auth→audit event (no public/SPA contract change, no Alembic migration). **F-010 (Low):** D3a.3 replaced the non-normative "e.g., 6 levels" with a fixed `MAX_REDACT_DEPTH = 8` constant + an explicit named cycle-safety mechanism (visited-`id()` set plus a strict depth bound that elides-by-redacting at the cap), made deterministic for a QA boundary test. Direction, security model, MF-1..MF-7 closures, the sequence diagram's security semantics, and Status (**Proposed**) are unchanged.

## Context

First-login TOTP enrollment and the forced first-login password change have **never worked** in any deployment of Лоцман. The defect is original to the first auth commit (`c171edd`) and is now blocking real PreProd users: `user1@example.com` and `user2@example.com` have OOB OTPs issued (per ADR-0004 §5 auto-invite) but cannot get past the login screen because every enrollment-phase request returns 401. CHANGELOG `[1.13.1]` fixed a *different, adjacent* bug (BFF was returning `next_step:"first_login_required"` instead of the SPA-contract literal `"enroll_totp"`, regression `a2945f7`); that fix is live on PreProd but only un-hangs the SPA so it now *attempts* the enrollment calls — which then fail with the 401 this ADR addresses. The two bugs were always stacked; `[1.13.1]` exposed this one.

The root cause has been diagnosed and verified against live source on 2026-05-19. It is a **token-type mismatch across a trust boundary**:

1. `StartLogin` (`start_login.py:~132`), when `not user.has_totp_enrolled`, issues an **opaque** token: `enrollment_token = secrets.token_urlsafe(32)`, stores `enrollment_token → user.id` in Redis via `pending_totp_store.set_ticket(token, user.id)` (5-min TTL), and returns `LoginPendingEnrollDTO`. This is structurally identical to the TOTP-login branch 13 lines below it (`session_ticket = PendingTotpLoginTicket.generate(...)` → same `pending_totp_store`), which **works**.
2. The SPA (`AuthProvider.tsx:~180`) does `applyToken(res.enrollment_token)` and `FirstLoginPage` then calls the three enrollment-phase endpoints with `Authorization: Bearer <enrollment_token>`.
3. In the BFF, all three handlers — `enroll_totp` (`web-bff/api/v1/auth.py:286`), `confirm_totp_enrollment` (`:309`), and the first-login branch of `change_password` (`:614`) — depend on `RequireAccessClaims`. That dependency calls `deps.py::_verify_access_jwt` (line 79), which `jwt.decode(...)` the bearer as an **RS256 JWT** (ADR-0003 §7). An opaque `token_urlsafe(32)` string is not a JWT → PyJWT raises *"Not enough segments"* → the BFF returns **401** before any auth-service call is made.

So all **three** enrollment-phase routes fail identically, for the same reason. The opaque enrollment token is being fed into a gate that only accepts RS256 access JWTs. By contrast, the TOTP-login path never hits `RequireAccessClaims`: its opaque `session_ticket` travels in the **request body** to `/totp/verify`, which is an *anonymous* BFF route (`auth_client.verify_totp` mints an internal JWT with `_ANON_ACTOR_ID`), and **auth-svc** resolves the ticket via `pending_totp_store` inside the `VerifyTotp` use case. The enrollment path tried to carry an opaque token through the *access-JWT* lane instead of the *ticket-exchange* lane. That is the entire bug.

This ADR ratifies the correct resolution model **before any auth code is touched**, so that `backend` implements against a fixed contract rather than improvising one. The constraint is sharp: do **not** promote the enrollment token to a full access JWT (see Alternatives — security reasoning). Mirror the proven `verify_totp` opaque-exchange instead.

**Pre-implementation security review (rev. 1 → rev. 2).** The original draft's *direction* (opaque ticket, resolved in auth-service, body-carried, mirror of `/totp/verify`, 300 s TTL; Options A/B/C correctly rejected) was reviewed and **confirmed sound**. However the review returned an overall verdict of **BLOCK** — **1 Critical + 2 High + 4 lower** findings — because the original D1 and D5 were *under-specified at exactly the points where the design is load-bearing*. The Critical: `start_login.py` writes the **identical** Redis value (`totp:login:pending:<token> = str(user_id)`) for *both* the enrollment ticket (`start_login.py:~134`) and the TOTP-login `session_ticket` (`start_login.py:~147`). After this ADR routes the enrollment ticket through the same store **and** the same generic resolver, the two ticket classes become *cryptographically and structurally indistinguishable*. A login `session_ticket` replayed to `/auth/totp/enroll` (or vice-versa) would resolve to a valid `user_id` and the route would proceed — the *entire* MFA gate for every account would rest on a code convention ("we only call this resolver from these three routes"), not an enforced discriminator. The two High findings: the enroll-only terminal branch mints a real session but the ADR said only "mirror `verify_totp`" without enumerating the steps (audit/`sid` correctness risk), and the body-carried secret has no no-log invariant while `redact_sensitive_fields` does not recurse into request-body dicts. This revision (rev. 2) closes **MF-1..MF-7** in the Decision section so that every fix is a normative, testable line `backend` builds against and `QA`/`security` can re-review against — *without* changing the (sound) direction.

## Decision

**The first-login enrollment token is an opaque, server-resolved ticket — not an access JWT — and is resolved in `auth-service` exactly the way `verify_totp` resolves its `session_ticket` today.** No new token type, no new store, no new Redis key family, no DB migration, no SPA change.

Concretely:

> **Normative-requirement legend.** Every line tagged **[MF-n]** is a binding, testable acceptance criterion that closes the correspondingly-numbered pre-implementation security finding. `backend` MUST satisfy all of them; `QA` MUST have at least one test per tag; `security` re-reviews against this list. "MUST"/"MUST NOT"/"MUST NEVER" are RFC 2119 normative.

### D1. Token format stays opaque — but the Redis value gains a code-enforced scope discriminator **[MF-1, Critical]**

`enrollment_token` stays exactly as `start_login.py:~132` mints it: `secrets.token_urlsafe(32)` (256-bit opaque), stored in the **existing** `RedisPendingTotpLoginStore` (`pending_totp_store`, key family `totp:login:pending:<ticket>`, TTL 300 s). We deliberately keep enrollment tickets in the *same store class* so there is one ticket-resolution component to reason about, audit, and test. The bug being fixed is never about the token's bytes — it is about which lane carries it and who resolves it. **But the original draft's "value = `str(user_id)`, same for both ticket classes" is a Critical defect and is hereby superseded:** because `start_login.py` writes the *identical* value shape for the enrollment ticket (`:~134`) and the TOTP-login `session_ticket` (`:~147`), an indistinguishable value means a login ticket replayed to an enrollment route (or vice-versa) resolves to a real `user_id` and proceeds. The MFA gate of every account would then rest on a calling convention, not an enforced check. The following are normative:

- **D1.1 [MF-1]** The Redis ticket value MUST be a JSON object carrying a scope discriminator, **not** a bare UUID string:

  ```json
  { "uid": "<user-uuid-canonical-string>", "scope": "enroll" | "login" }
  ```

  `scope` is a closed enum of exactly `"enroll"` and `"login"`. `start_login.py` writes `scope:"enroll"` at `:~134` (the `not user.has_totp_enrolled` branch) and `scope:"login"` at `:~147` (the TOTP-enrolled branch). The key family is unchanged (`totp:login:pending:<ticket>`); the *value* is the discriminator carrier. (Per-scope key sub-prefixes — `totp:login:pending:enroll:<tok>` vs `:login:<tok>` — were considered as the alternative discriminator placement and are an acceptable equivalent **only if** the store also rejects a token presented under the wrong sub-prefix; the JSON-value form is the chosen primary because it keeps a single `_key()` and makes the discriminator impossible to drop silently when adding a new caller.)

- **D1.2 [MF-1]** `RedisPendingTotpLoginStore` API changes (signature is normative):
  - `async def set_ticket(self, ticket_id: str, user_id: uuid.UUID, scope: TicketScope) -> None` — `scope` is a required, non-defaulted parameter (so a new caller cannot forget it; `TicketScope` is a `StrEnum` with members `ENROLL = "enroll"`, `LOGIN = "login"`). It serialises `{"uid": str(user_id), "scope": scope.value}` via `json.dumps` and `SET … EX 300`.
  - `async def get_user_id(self, ticket_id: str, *, expected_scope: TicketScope) -> uuid.UUID | None` — `expected_scope` is a required keyword-only parameter. It returns the `uid` **only if** the stored, parsed `scope` equals `expected_scope`; otherwise (missing key, malformed JSON, `uid` not a UUID, **or scope mismatch**) it returns `None`. There is no overload that resolves without an `expected_scope`.
  - `delete_ticket(self, ticket_id: str)` is unchanged (key is scope-agnostic; deletion does not need the discriminator).
  - **Backward-read tolerance is explicitly NOT provided.** There is no legacy value to migrate (the path has never worked; no live ticket of this kind exists), so a bare-string value MUST be treated as malformed → `None`. This keeps the parser strict and removes a confusion vector.

- **D1.3 [MF-1]** The three enrollment routes MUST resolve via `get_user_id(token, expected_scope=TicketScope.ENROLL)` and MUST reject (generic 401, see D5.7) any ticket whose stored scope is not `enroll`. `verify_totp` MUST resolve via `get_user_id(cmd.ticket_id, expected_scope=TicketScope.LOGIN)` and MUST reject any ticket whose stored scope is not `login`. **Cross-scope rejection is bidirectional and mandatory**; it is the enforced replacement for the original draft's weaker invariant ("at least one non-enrollment route rejects the ticket"). A login ticket presented to any enrollment route, and an enrollment ticket presented to `/totp/verify`, both yield the same generic 401 with no scope/user-existence signal.

- **D1.4 [MF-1]** `QA` obligation: a `session_ticket` (scope `login`) POSTed to each of the three enrollment routes → generic 401, ticket NOT consumed, no secret written; an `enrollment_token` (scope `enroll`) POSTed to `/totp/verify` → generic 401, ticket NOT consumed, no session minted. (See INV-3.)

### D2. Resolution happens in auth-service, NOT in the BFF

The opaque ticket is carried to auth-service and resolved there via `pending_totp_store.get_user_id(ticket, expected_scope=TicketScope.ENROLL)` (per D1.2/D1.3). The BFF does **not** resolve the ticket and does **not** mint a "narrowly-scoped internal JWT from a resolved actor" for these routes.

Rationale (this is the load-bearing architectural choice):

- **Consistency with the working mirror.** `verify_totp` already resolves its opaque ticket *in auth-service* (`verify_totp.py:73`, `user_id = await self.pending_totp_store.get_user_id(cmd.ticket_id)`). The BFF's `auth_client.verify_totp` passes `_ANON_ACTOR_ID` precisely *because there is no resolved actor yet*. Pre-enrollment, there is likewise **no resolved actor** — the user has no session, no access JWT, and (for forced-password-change) possibly not even TOTP. Asking the BFF to resolve the ticket would require giving the BFF read access to auth-service's Redis ticket store — a direct violation of ADR-0002 database/store-per-service and the ADR-0004 §1 privilege-separation principle. The BFF must remain the *chokepoint*, not a *participant* in auth-service's internal credential state.
- **The BFF chokepoint principle is preserved, not weakened.** ADR-0004 (and the BFF-chokepoint design it inherits) says the BFF is where *resolved* identity is translated into internal JWTs for downstream calls. It does **not** say the BFF must resolve *every* credential. The login → `/totp/verify` path is the existing, accepted precedent: opaque pre-session ticket flows *through* the BFF (anonymous lane) and auth-service owns resolution. Enrollment is the same class of flow (pre-session credential exchange) and must use the same lane. Introducing a *second* resolution mechanism (BFF-resolves-then-mints) for a flow that is structurally identical to one we already have (auth-svc-resolves) would be exactly the "wrong coupling / duplicated mechanism" the project conventions invariant warns against.
- **Single-host blast radius.** If the BFF could resolve enrollment tickets it would need either (a) the auth Redis ACL credentials, or (b) a new auth-service "resolve this ticket for me" endpoint that returns a `user_id` to the BFF — which is just `verify_totp`'s anti-pattern inverted and leaks pre-enrollment identity to the gateway. Keeping resolution in auth-service keeps the un-enrolled-user identity entirely inside the auth trust boundary.

**Therefore the three enrollment-phase BFF routes move from the `RequireAccessClaims` lane to the anonymous-ticket lane, identical to how `/totp/verify` is wired.**

### D3. The ticket carries in the request BODY, not the Authorization header

On every enrollment-phase hop the ticket travels as a JSON body field, mirroring `verify_totp` (which carries `session_ticket` in the body). The Authorization header is **not** used for the enrollment lane at all — that header is reserved for RS256 access JWTs (ADR-0003 §7) and feeding an opaque value into it is the precise cause of this bug.

Wire contract (BFF ↔ auth-service uses the existing internal-JWT envelope with `_ANON_ACTOR_ID`, exactly like `verify_totp`):

| Hop | SPA → BFF (body field) | BFF → auth-svc (body field) | auth-svc identity source (the ONLY one — MF-5) |
|---|---|---|---|
| `POST /api/v1/auth/totp/enroll` | `{ "enrollment_token": "<opaque>" }` | `{ "enrollment_token": "<opaque>" }` | `get_user_id(token, expected_scope=ENROLL)` |
| `POST /api/v1/auth/totp/enroll/confirm` | `{ "enrollment_token": "<opaque>", "code": "123456" }` | `{ "enrollment_token": "<opaque>", "code": "123456" }` | `get_user_id(token, expected_scope=ENROLL)` |
| `POST /api/v1/auth/password/change` (first-login branch only) | `{ "enrollment_token": "<opaque>", "new_password": "…" }` | `{ "enrollment_token": "<opaque>", "new_password": "…" }` | `get_user_id(token, expected_scope=ENROLL)` |

Note: this means the **SPA already sends what it needs** in two of three cases without code change — see §D7. The SPA currently *also* puts the token in the `Authorization` header (via `applyToken`); auth-svc and BFF will simply ignore the header for these routes and read the body. The SPA's `FirstLoginPage` already includes the enrollment token in the request body for these calls (it round-trips the value it received as `enrollment_token`); the BFF passes the body through. (Verified contract; see §D7 and Non-regression for the exact zero-change assertion.)

### D3a. The enrollment token is a body-carried secret and MUST NEVER be logged **[MF-3, High]**

Moving the ticket into the request body (D3) makes it a secret that traverses three hops (SPA → BFF → auth-svc). Today `redact_sensitive_fields` (`shared/src/lotsman_shared/logging.py:~47–66`) iterates **only top-level `event_dict` keys** — it does not recurse into nested dict/list values. A structured log call that binds a request body as a *nested* value (e.g. `log.info("enroll_request", body={"enrollment_token": "..."})`) would leak the live ticket in plaintext, defeating the 300 s TTL while the ticket is alive. The following are normative:

- **D3a.1 [MF-3]** `enrollment_token` MUST NEVER be logged at any hop — SPA, web-bff, or auth-service — at any level (including debug). It MUST NOT appear in request-trace logs, access logs, error contexts, or exception messages.
- **D3a.2 [MF-3]** Request **bodies** for the three enrollment routes (`/auth/totp/enroll`, `/auth/totp/enroll/confirm`, `/auth/password/change` first-login branch) MUST NOT be logged as a whole, in either web-bff or auth-service, because the body contains the live ticket (and, for confirm/change-password, the TOTP code / new password).
- **D3a.3 [MF-3]** `redact_sensitive_fields` MUST be extended to **recurse into nested `dict` and `list` values** so that a sensitive key (matched by the existing `_SENSITIVE_KEY_RE`) is redacted at any depth, not only at the top level. The regex MUST additionally match `enrollment_token` (it already matches the substring `token`, so `enrollment_token` is covered by the current pattern — `QA` MUST assert this with an explicit nested-dict test, e.g. `{"body": {"enrollment_token": "x"}}` → redacted). Recursion MUST be depth-bounded and cycle-safe to avoid a logging-path DoS, with **deterministic** behaviour so `QA` can write a boundary test:
  - **Fixed maximum recursion depth: `MAX_REDACT_DEPTH = 8`** (a module-level constant in `shared/src/lotsman_shared/logging.py`). Depth is counted from the top-level `event_dict` as depth 0; the function recurses into `dict`/`list` children only while the current depth `< MAX_REDACT_DEPTH`.
  - **Bound behaviour (normative, deterministic):** when the depth bound is reached, the function MUST NOT recurse further into that branch; it MUST replace the entire still-nested value at the bound with the redaction sentinel (`***REDACTED***`) — i.e. elide-by-redacting rather than recurse — so the original value can never leak past the depth cap. (Redacting the over-deep subtree wholesale is the safe default: a sensitive key buried below the cap is thus still never emitted in plaintext.)
  - **Cycle safety (normative):** the recursion MUST be protected by a `visited` set of object `id()`s for container values (`dict`/`list`) on the current descent path; a container whose `id()` is already in `visited` MUST be rendered as the redaction sentinel (or a fixed `"<cycle>"` marker) and MUST NOT be recursed into. The depth cap and the visited-set are independent safeguards; either alone terminates the recursion, together they make it total and deterministic.
  - `QA` boundary test: a payload nested exactly `MAX_REDACT_DEPTH` levels deep with a sensitive key at the boundary is redacted; a payload nested `MAX_REDACT_DEPTH + 1` levels deep has its over-deep subtree replaced by the sentinel (no plaintext leak, no crash); a self-referential dict (`d["self"] = d`) is redacted/marked and terminates without `RecursionError`.
  This is a `shared/` change and therefore in-scope for the §8 version bump (still PATCH — internal, see Versioning).
- **D3a.4 [MF-3]** FastAPI 422 validation responses for these three routes MUST NOT echo the offending input value. The Pydantic models for these routes MUST be configured so request-validation errors do not include the submitted `enrollment_token`/`new_password` in the error payload (custom 422 handler scoped to these routes, or `model_config` that strips `input` from the error context). `QA` asserts: a malformed body to each route returns 422 whose JSON contains no substring of the submitted token.

### D3b. User identity is derived EXCLUSIVELY from ticket resolution **[MF-5, Medium]**

On the three enrollment routes the acting `user_id` MUST be obtained **only** from `pending_totp_store.get_user_id(token, expected_scope=ENROLL)`. The following identity sources MUST be unreachable (not merely unused) on these three routes:

- **D3b.1 [MF-5]** Any `actor.actor_id` from the internal-JWT envelope — **including the `_ANON_ACTOR_ID`** sentinel that the anonymous BFF lane carries. The auth-service handlers for these three routes MUST NOT accept an `actor`/`RequireActor` dependency at all (matching `verify_totp`, which takes no actor). There is no code path on these routes where an actor identity can be read.
- **D3b.2 [MF-5]** Any body field that names a user/email (no `email`, `user_id`, `username`, or hint field is read for identity — the Pydantic request models for these routes MUST NOT declare such a field; if present for other reasons it MUST NOT influence identity resolution).
- **D3b.3 [MF-5]** Any `Authorization`-header identity. The header is ignored for these three routes (D3); it MUST NOT be decoded, trusted, or used to derive `user_id` even as a fallback.
- **D3b.4 [MF-5]** `QA` obligation: a request to each route with a *valid enrollment ticket for user A* but a body `email`/`user_id` for user B, and separately with an internal envelope carrying a non-`_ANON` actor, MUST act on **user A only** (the ticket-resolved id) — proving the alternate inputs are inert.

### D4. Ticket scope — EXACTLY three operations, nothing else

The resolved `user_id` from an enrollment ticket authorizes **only**:

1. `POST /auth/totp/enroll` — generate pending secret + otpauth URL.
2. `POST /auth/totp/enroll/confirm` — verify first code, persist `totp_secret_enc`, issue backup codes.
3. `POST /auth/change-password` **forced-first-login branch only** (the branch that returns a fresh access+refresh pair — `web-bff/api/v1/auth.py:642–648`).

The ticket grants access to **no other endpoint**. It is not an access JWT, carries no `role`, no `sid`, and the gate that lets it in (auth-service ticket resolution) exists only on these three endpoints. Any other route continues to require a real RS256 access JWT via the unchanged `RequireAccessClaims` / `RequireActor` gates.

### D5. Reusable across the flow, single-use at the terminal step; TTL stays 300 s

The flow is inherently multi-call and includes **human QR-scan time**: `enroll` → user opens authenticator app and scans QR → `enroll/confirm` → forced `change-password`. Therefore:

- The ticket is **reusable** across `enroll` and `enroll/confirm` (and the forced `change-password` if it follows). It is *not* deleted on `enroll` or on a *failed* `enroll/confirm` (wrong 6-digit code must be retryable without restarting login).
- The ticket is **consumed (deleted) exactly once, at the terminal successful step**:
  - If the account requires both TOTP enrollment and a forced password change: the ticket is deleted at the end of the successful forced `change-password` (the step that mints the real session).
  - If the account requires TOTP enrollment only (no forced password change — e.g. admin TOTP-reset of a user who already has a password): the ticket is deleted at the end of successful `enroll/confirm`, which then issues the real access+refresh pair. The exact steps are enumerated in **D5.4 [MF-2]** below — "mirror `verify_totp`" alone is NOT an acceptable spec.
  - If the account requires a forced password change only (no TOTP enrollment — pure ADR-0003 §5b admin password reset of a TOTP-enrolled user): the existing `must_change_password` access-JWT-gated path is unaffected by this ADR; that user already has TOTP, logs in via `/totp/verify`, gets a real (restricted) access token, and `RequireAccessClaims` works normally. **This ADR's ticket lane applies only when the user has no TOTP yet** (the `not user.has_totp_enrolled` branch of `start_login`) — re-checked per D5.3 [MF-4].
- **TTL stays 300 s (5 min), unchanged.** The 5-minute window is per the existing store and ADR-0003 §3 (pending-secret TTL is also 5 min). The window covers QR-scan time for a prepared user. If the ticket expires mid-flow (user walked away), the user re-enters email + OOB OTP at `/login`, `start_login` mints a fresh enrollment ticket, and the pending TOTP secret (also 5-min TTL, ADR-0003 §3) is regenerated on the next `enroll` call. We explicitly do **not** lengthen the TTL: a longer-lived pre-enrollment credential is a larger window for an intercepted OOB OTP + ticket to be abused, and 5 minutes is sufficient for the documented happy path (the OOB OTP itself has a 10-min TTL per ADR-0004 §5, so the constraint is the ticket, deliberately the tighter of the two).

#### D5.3 — The enrollment lane applies ONLY to users without TOTP, re-checked AFTER ticket resolution **[MF-4, High/AAL2]**

`start_login` chooses the enrollment branch on `not user.has_totp_enrolled` (`start_login.py:~130`). That check at ticket-mint time is **insufficient**: a ticket minted before the user enrolled, then replayed after a concurrent enroll/confirm (or after an admin re-enrolls them by another path) could overwrite a *live* TOTP secret of an already-enrolled account. The following are normative:

- **D5.3.1 [MF-4]** All three enrollment routes (`enroll`, `enroll/confirm`, forced `change-password` first-login branch) MUST, **after** resolving the ticket to `user_id` and loading the user, re-check `not user.has_totp_enrolled` (auth-service side, against the freshly-loaded DB row — not a cached value, not the `start_login`-time decision).
- **D5.3.2 [MF-4]** If the resolved user **already** has TOTP enrolled, the route MUST reject with the generic 401 (D5.7), **MUST NOT** write or overwrite `totp_secret_enc`, MUST NOT generate a pending secret, MUST NOT issue backup codes, MUST NOT change the password, and MUST NOT consume the ticket as a "success". (Whether the now-pointless ticket is deleted on this rejection is an implementation choice; security-relevant requirement is no state mutation + generic 401.)
- **D5.3.3 [MF-4]** `QA` obligation: resolve a valid enrollment ticket for a user who (in a concurrent step) became TOTP-enrolled → all three routes return generic 401 and `totp_secret_enc` / password are byte-identical before and after the rejected call.

#### D5.4 — Enumerated steps of the enroll-only terminal branch (TOTP enrollment, NO forced password change) **[MF-2, High]**

This is the branch where `enroll/confirm` is the terminal step and must itself mint the real session (admin TOTP-reset of a user who already has a valid password, so `must_change_password` is false). Today `ConfirmTotpEnrollment` emits `TotpEnrolled` + `BackupCodesGenerated` and has **no** session, **no** `LoggedIn`, **no** JWT dependencies. Adding a session mint here is new behaviour and MUST be specified step-by-step, not as "like verify_totp":

- **D5.4.1 [MF-2] — Implementation shape decision.** The session-minting logic MUST be extracted into a **shared session-issuing collaborator** rather than copy-pasted from `verify_totp`. Name it `IssueSession` (an application-layer collaborator/use-case under `auth_service.application.use_cases.issue_session`), encapsulating steps D5.4.4–D5.4.8 below. `VerifyTotp` (steps 5–11), `ChangePassword` forced path (`change_password.py:~91–108`), and the new enroll-only terminal branch all call `IssueSession` so the *session + JWT + outbox* sequence has exactly one implementation. This is preferred over faithful replication because the security risk in MF-2 is precisely *divergence* of three hand-written session mints (e.g. one forgets the `LoggedIn` emit or fabricates the `sid`). **The behaviour-preservation claim is call-site-specific and MUST NOT be generalised:**
  - **`verify_totp` — genuinely behaviour-preserving.** `verify_totp` (`verify_totp.py:~172–188`) **already** emits `LoggedIn` (`auth.user.logged_in.v1`) and **already** sets `user.last_login_at` today. Routing it through `IssueSession` emits the same events in the same transaction with the same `actor_id` — this is the *only* genuinely behaviour-preserving call site (INV-1 holds verbatim — same events, same transaction).
  - **forced `change_password` — INTENTIONALLY ADDITIVE, not behaviour-preserving.** The current forced-password-change path (`change_password.py:~87–108`) today emits `PasswordChanged` + `UserActivated` (+ `SessionRevoked` ×N) and mints a session + access JWT, but emits **NO `LoggedIn`** and writes **NO `last_login_at`**. Routing this path through `IssueSession` **deliberately ADDS `auth.user.logged_in.v1` + `last_login_at`** to that path. This is an **intentional, audited audit-completeness improvement**, NOT behaviour preservation and NOT a regression: the forced-password-change session-mint was previously under-audited (a real session was created with no `LoggedIn` event and no `last_login_at` stamp), and unifying it through `IssueSession` repairs that audit gap. The added `LoggedIn` carries `actor_id = resolved user_id` and `session_id = session.id`, emitted **inside the caller's existing `db.begin()` transaction** (the same transaction as `PasswordChanged` / `UserActivated`). This is a deliberate side effect, expected by INV-6 (QA MUST update existing `ChangePassword` outbox tests to assert the added `LoggedIn`; its appearance MUST NOT be treated as a regression).
- **D5.4.2 [MF-2] — Step 1: resolve + scope-check + user load.** `user_id = get_user_id(token, expected_scope=TicketScope.ENROLL)`; `None` → generic 401 (D5.7). Load `user = user_repo.get_by_id(user_id)`; `None` → generic 401.
- **D5.4.3 [MF-2] — Step 2: MF-4 re-check + verify TOTP vs pending secret.** Apply D5.3.1 (`not user.has_totp_enrolled`, else generic 401, no mutation). Then the existing `ConfirmTotpEnrollment` body runs: fetch pending secret from `RedisTotpEnrollmentStore` (key `totp:pending:<user_id>`); `None` → `TotpEnrollmentExpiredError`; verify `cmd.code` against pending secret; invalid → `TotpInvalidError`, **ticket NOT deleted, pending key NOT deleted** (retry preserved — `confirm_totp_enrollment.py:52`).
- **D5.4.4 [MF-2] — Step 3: persist secret + backup codes (existing, in `db.begin()`).** Encrypt and write `user.totp_secret_enc`; delete the pending Redis key; regenerate the 10 argon2id-hashed backup codes; this all stays exactly as `confirm_totp_enrollment.py:55–78`.
- **D5.4.5 [MF-2] — Step 4: lockout re-check.** Before minting, `CheckLockout(attempts_repo).execute(email=user.email)`; if locked → record `LOCKED` attempt + generic 401 (mirrors `verify_totp.py:82–92`). (Anti-replay period-index from `verify_totp.py:134–148` is **TOTP-login-specific** and does NOT apply here — the confirm code was just verified against the *pending enrollment* secret, not a logged-in TOTP; do not add `totp_used_repo` to this branch.)
- **D5.4.6 [MF-2] — Step 5: `Session.create` + `session_repo.add`.** Generate opaque refresh (`secrets.token_urlsafe(32)`), `refresh_hash = sha256`; `Session.create(user_id=user.id, refresh_hash=…, user_agent=cmd.user_agent, ip_address=cmd.ip_address, ttl_seconds=session_ttl_seconds)`; `session_repo.add(session)` (mirrors `verify_totp.py:150–162`).
- **D5.4.7 [MF-2] — Step 6: mint RS256 access JWT with a REAL `sid`.** `jwt_issuer.issue(user_id=user.id, email=user.email, role=user.role, session_id=session.id)`. The `sid` claim MUST be the real `auth.sessions.id` just persisted in D5.4.6 (ADR-0003 §7 requires `sid`; ADR-0003 §13 requires it map to a revocable row). A fabricated/placeholder `sid` is forbidden (this is exactly the Option-A defect we rejected — it MUST NOT re-enter via this branch).
- **D5.4.8 [MF-2] — Step 7: `last_login_at` + emit `auth.user.logged_in.v1` in the SAME transaction.** Set `user.last_login_at = now(UTC)`; `user_repo.update(user)`. Emit, in the **same `db.begin()` transaction** as D5.4.4's writes, the `LoggedIn` event (`auth.user.logged_in.v1`) with `actor_id = resolved user_id` and `session_id = session.id` (mirrors `verify_totp.py:172–188`). The pre-existing `TotpEnrolled` + `BackupCodesGenerated` events (`confirm_totp_enrollment.py:81–82`) MUST still be emitted in this transaction (INV-6) — the terminal branch now emits **three** events: `TotpEnrolled`, `BackupCodesGenerated`, `LoggedIn`, all `actor_id = user_id`, all in one transaction.
- **D5.4.9 [MF-2] — Step 8: consume ticket + record success + return tokens.** Record `SUCCESS` login attempt; `pending_totp_store.delete_ticket(token)` (terminal consume — exactly once); return `{ backup_codes, access_token, refresh_token }` so the BFF can set the refresh cookie and the SPA `applyToken`s the real access JWT.

For the **both-TOTP-and-forced-password-change** account, `enroll/confirm` does NOT mint a session (it returns `{backup_codes}` only, ticket alive); the forced `change_password` first-login branch is the terminal step and calls the same `IssueSession` collaborator (D5.4.5–D5.4.9) after `PasswordChanged` + `UserActivated` (`change_password.py:~85–89`), then deletes the ticket. `actor_id` for `PasswordChanged`/`UserActivated`/`LoggedIn` is the resolved `user_id` (per D6).

#### D5.6 — Threat statement: the ticket-holder controls which authenticator is enrolled **[MF-6, Medium]**

It must be stated plainly, not implied: during the 300 s enrollment window, **whoever holds the valid enrollment ticket chooses which TOTP authenticator gets bound to the account.** There is no out-of-band confirmation that the enrolling device belongs to the legitimate user — the OOB OTP at `/login` is the only prior factor, and it has already been spent to mint the ticket. Therefore the *only* controls protecting account takeover during this window are **(1) the 300 s TTL and (2) ticket secrecy**. This is what makes MF-1 (no cross-scope confusion that could yield a valid ticket) and MF-3 (the ticket is never logged) *load-bearing*, not nice-to-have. Two normative consequences:

- **D5.6.1 [MF-6] — Per-ticket failed-confirm cap (hardening, ADOPTED — not deferred).** `enroll/confirm` MUST enforce a per-ticket cap of **5 failed TOTP-code attempts**, tracked in Redis keyed on the ticket (e.g. `totp:login:pending:attempts:<ticket>`, same 300 s TTL as the ticket, `INCR`+`EXPIRE`). On the 6th failed confirm the ticket is invalidated (deleted) and the route returns the generic 401; the user must restart at `/login`. Rationale for adopting rather than deferring: without a cap, an attacker who obtains a leaked ticket (the very scenario this section is about) has the full 300 s to brute-force the 6-digit confirm against an attacker-chosen pending secret they generated via `enroll` — but more importantly the cap also bounds an attacker brute-forcing confirm to lock a victim out of their own short window. The counter is per-ticket (not per-user) so it does not become a denial-of-service lever against the account beyond the 5-minute window the ticket already bounds. (The existing per-email lockout, ADR-0003 §12, still applies on top via the D5.4.5 lockout re-check.)
- **D5.6.2 [MF-6]** This residual risk (ticket-holder picks the authenticator) is explicitly **accepted** for our threat model (2–4 internal users, VPN-only, OOB OTP delivered to a corp channel) and is the reason the TTL is deliberately the *tighter* of the two pre-enrollment windows (ticket 5 min < OOB OTP 10 min). It MUST NOT be "fixed" by lengthening the TTL.

#### D5.7 — Uniform generic-401 on the enrollment lane **[MF-7, Low]**

Every ticket-invalid outcome on the three enrollment routes — unknown ticket, expired ticket, scope mismatch (D1.3), already-enrolled user (D5.3.2), failed-confirm cap exceeded (D5.6.1), user row not found — MUST return the **exact same generic 401 response shape** that `verify_totp`'s ticket-invalid path returns today (ADR-0003 §12 generic-401 principle: `InvalidCredentialsError` → uniform body). No distinct message, no `attempts_remaining` field, no differentiation between "ticket bad" vs "user state bad", no signal of user existence or TOTP-enrollment status. The single permitted distinguishable response on these routes is the in-flow wrong-TOTP-code-at-confirm case (`TotpInvalidError` → the standard validation message), which by design does not reveal ticket or user state and leaves the ticket reusable for retry (within the D5.6.1 cap).

### D6. Outbox audit events preserved (the project conventions / ADR-0003)

Resolution-lane change does **not** alter what *existing* events are emitted, and the new session mint adds `LoggedIn` only where a session is actually created. Inside the auth-service use cases (which already run inside `async with db.begin()`):

- `enroll/confirm` (non-terminal, both-TOTP-and-password account) continues to emit exactly `TotpEnrolled` + `BackupCodesGenerated` in the same DB transaction (existing `ConfirmTotpEnrollment` outbox publish — unchanged; no `LoggedIn` here because no session is minted).
- `enroll/confirm` (**terminal**, enroll-only account, D5.4): emits `TotpEnrolled` + `BackupCodesGenerated` **plus** `auth.user.logged_in.v1` (`LoggedIn`), all three in the **same transaction** as the secret-persist + session-insert (D5.4.8). This `LoggedIn` is new and is emitted *only* on this terminal branch, via the shared `IssueSession` collaborator (D5.4.1).
- forced `change-password` success continues to emit `auth.user.password_changed.v1` + `auth.user.activated.v1` (+ any existing `auth.session.revoked.v1` ×N) and, via the same `IssueSession` collaborator, **additionally and intentionally** emits `auth.user.logged_in.v1` and sets `user.last_login_at` — all in one transaction (`change_password.py:~87–108`, refactored to call `IssueSession`). **The `LoggedIn` event and `last_login_at` write are NEW on this path** (the forced-password-change path emits neither today — see D5.4.1): they are a deliberate, audited additive change to this existing internal auth→audit outbox stream (an audit-completeness improvement closing a previously under-audited session mint), **not** a behaviour-preserving refactor and **not** a regression. `actor_id = resolved user_id`, `session_id = session.id`, in the caller's existing transaction.

**Normative:** the outbox `actor_id` for **every** event on these self-service routes (`TotpEnrolled`, `BackupCodesGenerated`, `PasswordChanged`, `UserActivated`, `LoggedIn`, any `SessionRevoked`) MUST be the **ticket-resolved `user_id`** (the user acting on their own account), never `_ANON_ACTOR_ID` and never any other identity (consistent with D3b/MF-5 and how `verify_totp` sets `actor_id=user.id`). `QA` asserts every emitted envelope's `actor_id == resolved user_id` (INV-6).

### D7. The SPA requires NO change — and why

`web/src/features/auth/types.ts` defines `LoginFirstLoginResponse = { next_step: "enroll_totp"; enrollment_token: string }`. `AuthProvider.tsx:178–181` keys on the literal `"enroll_totp"` and stores the value via `applyToken(res.enrollment_token)`. `FirstLoginPage` already submits the enrollment token to the three endpoints. Because:

- the response shape from `/login` is unchanged (still `{ next_step:"enroll_totp", enrollment_token:"…" }` — this is the `[1.13.1]` contract, already live);
- the SPA already sends the enrollment token value to all three enrollment-phase endpoints;
- the BFF will accept the token from the request body (where the SPA already includes it for these calls) and ignore the now-irrelevant `Authorization` header for these three routes only;

the SPA bundle, types, `AuthProvider`, `AuthGuard`, and `FirstLoginPage` are **untouched**. `backend` must NOT modify anything under `web/`. This is a backend-only contract correction. (If, during implementation, `backend` finds the SPA puts the ticket *only* in the Authorization header for one of the three calls and not in the body, the correct fix is still backend-only: the BFF reads the bearer value from the Authorization header *for these three routes only*, treats it as an opaque ticket — NOT a JWT — and forwards it as the `enrollment_token` body field downstream. Either way `web/` is not edited. The header-as-opaque-pass-through option is explicitly the fallback; body is the primary contract.)

## Token-exchange sequence

```mermaid
sequenceDiagram
    autonumber
    actor U as User (invited, has OOB OTP)
    participant SPA as React SPA
    participant B as web-bff
    participant A as auth-service
    participant R as Redis (auth: totp:login:pending:*)
    participant P as Postgres (auth)

    Note over U,SPA: Step 0 — login with OOB OTP as password
    U->>SPA: email + OOB OTP
    SPA->>B: POST /api/v1/auth/login {email,password}
    B->>A: POST /auth/login (X-Internal-Token, _ANON actor)
    A->>P: verify OOB-OTP hash; user.has_totp_enrolled == false
    A->>A: enrollment_token = secrets.token_urlsafe(32)
    A->>R: SET totp:login:pending:<tok> = {"uid":user.id,"scope":"enroll"} EX 300  [MF-1]
    A-->>B: 200 {enrollment_token}
    B-->>SPA: 200 {next_step:"enroll_totp", enrollment_token}
    SPA->>SPA: applyToken(enrollment_token); status=first-login-required

    Note over U,SPA: Step 1 — enroll (opaque ticket in BODY, anon lane, NEVER logged [MF-3])
    SPA->>B: POST /api/v1/auth/totp/enroll {enrollment_token}
    B->>A: POST /auth/totp/enroll {enrollment_token} (X-Internal-Token, _ANON actor)
    A->>R: GET totp:login:pending:<tok>; parse JSON; assert scope=="enroll" [MF-1]
    Note over A: invalid/expired/scope-mismatch -> generic 401 [MF-7]
    A->>P: load user; assert NOT user.has_totp_enrolled [MF-4]
    Note over A: user_id ONLY from ticket — actor/_ANON/body-email unreachable [MF-5]
    A->>R: SET totp:pending:<user_id> = secret  EX 300  (ADR-0003 §3)
    A-->>B: 200 {secret_b32, otpauth_url}
    B-->>SPA: 200 {secret_b32, otpauth_url}
    SPA->>U: render QR client-side
    U->>U: scan QR in authenticator app (human time, within 5-min TTL)

    Note over U,SPA: Step 2 — confirm (ticket reused, not yet consumed)
    SPA->>B: POST /api/v1/auth/totp/enroll/confirm {enrollment_token, code}
    B->>A: POST /auth/totp/enroll/confirm {enrollment_token, code} (anon lane)
    A->>R: GET ...<tok>; scope=="enroll"? [MF-1]; failed-confirm cap<5? [MF-6]
    A->>P: load user; assert NOT has_totp_enrolled [MF-4]
    A->>A: verify code vs totp:pending:<user_id> secret
    alt code invalid
        A->>R: INCR totp:login:pending:attempts:<tok> (cap 5) [MF-6]
        A-->>SPA: 400 generic (ticket NOT deleted — retry allowed within cap)
    else code valid
        A->>P: BEGIN; write totp_secret_enc; insert 10 backup codes
        A->>P: INSERT auth.outbox (TotpEnrolled + BackupCodesGenerated)
        alt account also needs forced password change
            A->>P: COMMIT
            A-->>B: 200 {backup_codes}   (ticket still alive — NOT terminal)
            B-->>SPA: 200 {backup_codes}
            Note over SPA: Step 3 — forced change-password (ticket reused, TERMINAL)
            SPA->>B: POST /api/v1/auth/password/change {enrollment_token, new_password}
            B->>A: POST /auth/change-password {enrollment_token, new_password} (anon lane)
            A->>R: GET ...<tok>; scope=="enroll"? [MF-1]
            A->>P: load user; assert NOT has_totp_enrolled [MF-4]
            A->>P: BEGIN; argon2id set password; clear must_change_password
            A->>A: IssueSession collaborator [MF-2]: lockout re-check
            A->>P: INSERT auth.sessions (refresh_hash, +TTL)  -> REAL sid
            A->>A: mint RS256 access JWT (sid=sessions.id, role, 15m) [MF-2 D5.4.7]
            A->>P: set user.last_login_at
            A->>P: INSERT auth.outbox (PasswordChanged + UserActivated + LoggedIn) actor_id=user_id [D6]
            A->>P: COMMIT
            A->>R: DEL totp:login:pending:<tok>   (consume — terminal step)
            A-->>B: 200 {access_token, refresh_token}
            B-->>SPA: 200 {access_token} + Set-Cookie refresh=...
        else TOTP-enrollment only (no forced password change) — TERMINAL [MF-2 D5.4]
            A->>A: IssueSession collaborator [MF-2 D5.4.5]: lockout re-check
            A->>P: INSERT auth.sessions (refresh_hash, +TTL) -> REAL sid [D5.4.6]
            A->>A: mint RS256 access JWT (sid=sessions.id, role, 15m) [D5.4.7]
            A->>P: set user.last_login_at [D5.4.8]
            A->>P: INSERT auth.outbox (TotpEnrolled + BackupCodesGenerated + LoggedIn) actor_id=user_id [D5.4.8/D6]
            A->>P: COMMIT
            A->>R: DEL totp:login:pending:<tok>   (consume — terminal step [D5.4.9])
            A-->>B: 200 {backup_codes, access_token, refresh_token}
            B-->>SPA: 200 {backup_codes, access_token} + Set-Cookie refresh=...
        end
        SPA->>SPA: applyToken(real access JWT); status=authenticated
    end
```

## Security notes

- **No privilege widening.** The resolved ticket authorizes exactly the three enrollment-phase operations because the resolution code only exists on those three auth-service endpoints. It is not a bearer credential for the rest of the API. This is strictly *narrower* than the rejected full-JWT alternative.
- **No pre-session capability.** Until the terminal step issues a real RS256 access JWT (with `sid`, `role`, ADR-0003 §7), the user holds **no** session and can reach **no** access-gated route. `RequireAccessClaims` and `RequireActor` are unchanged and continue to reject opaque tokens everywhere else — which is correct.
- **Tight TTL retained (300 s).** Deliberately the tighter of the two pre-enrollment windows (OOB OTP is 10 min per ADR-0004 §5; ticket is 5 min). A stolen OOB-OTP-plus-ticket pair has at most a 5-minute abuse window, and abuse still cannot proceed past enroll/confirm without the user's authenticator (TOTP code) at the confirm step.
- **Single-use at terminal step** prevents a leaked ticket from being replayed to mint a *second* session after the legitimate user finishes. Reuse across enroll/confirm is bounded by the same 5-minute TTL and does not widen scope (same three endpoints, same `user_id`).
- **Identity stays inside the auth trust boundary.** The BFF never learns the un-enrolled user's `user_id` from the ticket (it forwards an opaque blob). Pre-enrollment identity resolution is confined to auth-service + its own Redis — consistent with ADR-0002 store-per-service and ADR-0004 §1.
- **No cross-scope confusion [MF-1].** The Redis ticket value carries an enforced `scope` discriminator (D1.1) checked by `get_user_id(token, expected_scope=…)` (D1.2); a login ticket cannot be replayed to an enrollment route or vice-versa. This closes the Critical: the MFA gate no longer rests on a calling convention. The two ticket classes are now distinguishable by an enforced field, not only by which function happens to read them.
- **Ticket-holder controls the authenticator (accepted residual) [MF-6].** During the 300 s window, whoever holds the ticket chooses the bound TOTP device; the only controls are TTL + ticket secrecy + the per-ticket 5-attempt confirm cap (D5.6.1). This is why MF-1/MF-3 are load-bearing and is explicitly accepted for our 2–4-user VPN-only threat model (D5.6.2).
- **No secret in logs [MF-3].** The body-carried `enrollment_token` is never logged at any hop; `redact_sensitive_fields` recurses into nested dict/list (D3a.3); 422 responses on these routes do not echo input (D3a.4).
- **Exclusive ticket binding [MF-5].** `user_id` derives solely from ticket resolution; `_ANON` actor, body email/hint, and Authorization identity are unreachable on these routes (D3b).
- **No enumeration / uniform 401 [MF-7].** Every ticket-invalid outcome (unknown, expired, scope-mismatch, already-enrolled, cap-exceeded, user-missing) returns the *same* generic 401 as `verify_totp` (ADR-0003 §12). Wrong TOTP at confirm returns the standard validation error without revealing whether the ticket or the code was the problem.
- **Outbox integrity + intentional audit-completeness gain (the project conventions) [MF-2, D6].** Existing audit events remain emitted in the same DB transaction. The terminal enroll-only branch additionally emits `LoggedIn`; the forced `change_password` path — which today emits `PasswordChanged`/`UserActivated` but **no** `LoggedIn` and **no** `last_login_at` — now *intentionally* also emits `auth.user.logged_in.v1` + writes `last_login_at` via the shared `IssueSession` collaborator (a deliberate, audited improvement closing a previously under-audited session mint — see D5.4.1/D6, not a regression). `verify_totp`'s emitted events are unchanged (it already emits `LoggedIn` — INV-1). Every `actor_id` is the resolved `user_id`.

## Invariants / non-regression

The following MUST hold after `backend`'s change. Each is a named test obligation for `QA`.

- **INV-1 (TOTP-login behaviour-preserved).** `start_login` TOTP branch → `/totp/verify` with `session_ticket` in body → `VerifyTotp` resolves via `pending_totp_store` → tokens issued. The only change to this path is the `expected_scope=TicketScope.LOGIN` resolve argument (MF-1, D1.3) and the internal extraction of its session-mint into the shared `IssueSession` collaborator (D5.4.1). This extraction is **genuinely behaviour-preserving for `verify_totp`** — and `verify_totp` is the **only** call site for which that claim holds — because `verify_totp` *already* emits `LoggedIn` (`auth.user.logged_in.v1`) and *already* sets `user.last_login_at` today; `IssueSession` emits the identical events in the identical transaction with the identical `actor_id`. (Contrast: the forced `change_password` path does NOT emit `LoggedIn`/`last_login_at` today, so its `IssueSession` adoption is intentionally additive, not behaviour-preserving — see D5.4.1 / INV-6.) Regression test: existing TOTP login of an enrolled user still succeeds end-to-end and emits exactly the same outbox events as before the extraction.
- **INV-2 (Access-JWT routes untouched).** `RequireAccessClaims` (`deps.py:79`) and `RequireActor` keep RS256-decoding the Authorization bearer for *every other* route. No access-gated endpoint starts accepting opaque tokens. Regression test: a valid RS256 access JWT still authorizes `/api/v1/registry/*` etc.; an opaque string in Authorization on those routes still 401s.
- **INV-3 (Three routes + bidirectional scope rejection) [MF-1].** Ticket resolution is added to exactly `enroll`, `enroll/confirm`, and forced `change-password`. Negative tests assert: (a) the enrollment ticket is rejected on a non-enrollment route (e.g. `GET /api/v1/auth/sessions/me`); (b) a `scope:"login"` `session_ticket` is rejected (generic 401) on each of the three enrollment routes with no state mutation; (c) a `scope:"enroll"` ticket is rejected (generic 401) on `/totp/verify` with no session minted. (Strengthened from the original "at least one non-enrollment route" to cross-scope, both directions.)
- **INV-4 (No DB migration).** Zero Alembic migrations. No new columns, no new tables. The reused store is the existing `RedisPendingTotpLoginStore` (value-shape change + new `scope` param only — internal Redis/store change, NOT a schema/migration; INV-4 holds). CI `import-linter` and migration-diff stay green with no new revision.
- **INV-5 (Zero SPA change).** No file under `web/` is modified. `web/src/features/auth/types.ts` `next_step:"enroll_totp"` union is the unchanged contract. The scope discriminator is auth-svc-internal (Redis value + store API) and is NOT a public/SPA contract. Asserted by: `git diff --stat` on the fix commit shows no `web/` paths.
- **INV-6 (Outbox preserved + intentional additive `LoggedIn` on forced change-password + correct actor) [MF-2, MF-5, D6].** Non-terminal enroll-confirm still emits exactly `TotpEnrolled` + `BackupCodesGenerated`; the terminal enroll-only branch additionally emits `LoggedIn`; forced change-password emits `PasswordChanged` + `UserActivated` (+ any existing `SessionRevoked` ×N) **plus a NEW, intentional `auth.user.logged_in.v1` and a NEW `last_login_at` write** (the forced-password-change path emits neither today — D5.4.1/D6). All in-transaction (the project conventions). Every emitted envelope's `actor_id == resolved user_id` (never `_ANON`, never any other identity). **QA obligation (NOT a relaxation — this invariant is made *more* precise):** `QA` MUST **update the existing `ChangePassword` outbox tests** to *expect* the added `LoggedIn` event and `last_login_at` on the forced change-password path; the appearance of `auth.user.logged_in.v1` on that path **MUST NOT be flagged as a regression** — it is the deliberate audit-completeness improvement specified in D5.4.1/D6. `verify_totp`'s outbox events are unchanged (INV-1, behaviour-preserving there). New test asserts: the terminal enroll-only `LoggedIn`, the forced-change-password additive `LoggedIn` + `last_login_at`, and `actor_id == resolved user_id` on every envelope.
- **INV-7 (Ticket lifecycle + cap) [MF-6, MF-7].** Ticket survives `enroll` and a *failed* `enroll/confirm` (within the 5-attempt cap); is deleted exactly once at the terminal successful step; an expired/unknown/scope-mismatched/cap-exceeded ticket yields the **same generic 401** as `verify_totp`'s ticket-invalid path (no distinct message, no `attempts_remaining`, no user-existence signal). 6th failed confirm invalidates the ticket.
- **INV-8 (Already-enrolled re-check) [MF-4].** Each of the three routes, given a valid enrollment ticket whose resolved user has since become TOTP-enrolled, returns generic 401 with `totp_secret_enc` and `password_hash` byte-identical before/after (no overwrite).
- **INV-9 (No secret in logs / no 422 echo / deterministic depth bound) [MF-3].** With `redact_sensitive_fields` applied, a structured log call binding `{"body": {"enrollment_token": "<v>"}}` redacts `<v>` (nested-recursion test); no log line at any service contains the live ticket; a malformed body to each of the three routes returns a 422 whose JSON contains no substring of the submitted token/password. Deterministic-bound tests (D3a.3, `MAX_REDACT_DEPTH = 8`): a payload nested exactly 8 levels redacts at the boundary; a payload nested 9 levels has the over-deep subtree replaced by `***REDACTED***` (no plaintext leak, no crash); a self-referential container terminates without `RecursionError`.
- **INV-10 (Exclusive ticket binding) [MF-5].** A request with a valid enrollment ticket for user A plus a body `email`/`user_id` for user B (and separately with a non-`_ANON` envelope actor) acts on user A only; the alternate inputs are provably inert.

## Alternatives considered

### Option A — Escalate the enrollment token to a full RS256 access JWT (REJECTED)

Make `start_login` mint a real RS256 access JWT for the un-enrolled user so the existing `RequireAccessClaims` gate "just works" with no lane change.

- **Pro**: Smallest diff — only `start_login` changes; BFF and the three handlers are untouched because `RequireAccessClaims` would succeed.
- **Pro**: One token type in the system instead of "JWT + ticket".
- **Con (fatal, security)**: An RS256 access JWT per ADR-0003 §7 **requires a `sid` claim** linked to `auth.sessions.id`. Pre-enrollment there is **no session** — the user has not completed MFA. We would have to either (a) fabricate a fake `sid`, breaking the §13 forced-revocation invariant (a `sid` that maps to no session row cannot be revoked, and the `lockout` Redis kill-switch keys on `sub`/`sid`), or (b) create a real session for a user who has not presented a second factor — directly violating ADR-0003 AAL2 (TOTP mandatory for all roles, the project conventions #8). Both are unacceptable.
- **Con (fatal, security)**: A valid access JWT authorizes **every access-gated endpoint** (`RequireAccessClaims` is the universal gate). An un-enrolled, un-MFA'd user would gain the full API surface for the JWT's 15-minute life. The whole point of the enrollment phase is that the user has **not** yet authenticated to AAL2. This is a privilege-escalation-by-design.
- **Con**: It contradicts the working `verify_totp` precedent, which deliberately does *not* issue an access JWT until *after* the second factor is verified. Mirroring the broken idea instead of the working one.
- **Why rejected**: Trades a token-lane bug for an authentication-bypass. Explicitly forbidden by the task constraint and by ADR-0003 §7/§13/AAL2. The correct fix is to mirror the credential-exchange that already works, not to manufacture a session that does not exist.

### Option B — BFF resolves the opaque ticket, then mints a narrowly-scoped internal JWT (REJECTED)

The BFF reads the opaque ticket, looks up `user_id` (via auth Redis or a new auth-service "resolve" endpoint), then mints an internal JWT scoped to the three enrollment endpoints and calls auth-service with a resolved actor.

- **Pro**: Keeps the BFF as the place where identity → internal-JWT translation happens (a literal reading of "BFF chokepoint").
- **Pro**: auth-service's three endpoints could keep their current `RequireActor` dependency unchanged.
- **Con**: To resolve the ticket the BFF needs either auth-service's Redis ACL (violating ADR-0002 store-per-service — the BFF would read another service's private credential store) **or** a new auth-service endpoint that returns a pre-enrollment `user_id` to the gateway (leaking un-enrolled identity outside the auth trust boundary, ADR-0004 §1).
- **Con**: Introduces a *second* ticket-resolution mechanism for a flow structurally identical to `/totp/verify`, which already resolves in auth-service. Two mechanisms for one problem class — the "wrong coupling > duplication" smell; the project conventions "no shared/duplicated mechanism where one correct one exists".
- **Con**: More moving parts (new endpoint or new ACL grant) for zero functional benefit over Option chosen.
- **Why rejected**: Inconsistent with the verified working mirror (`verify_totp` resolves in auth-service via `_ANON` BFF lane). The Decision (D2) keeps resolution in auth-service precisely so there is exactly one pre-session ticket-resolution path. Documented here because it is the tempting "respect the chokepoint literally" path; the principled reading is that the chokepoint translates *resolved* identity, and the existing precedent for *unresolved* pre-session tickets is auth-service-side resolution.

### Option C — New dedicated enrollment token type + new Redis key family + new store class (REJECTED)

Introduce `EnrollmentTicket` value object, a `RedisEnrollmentStore`, key family `enroll:ticket:*`, separate from the TOTP-login ticket store.

- **Pro**: Clean separation of concerns; enrollment tickets and login tickets cannot collide conceptually.
- **Pro**: Independent TTL tuning per flow.
- **Con**: `start_login` already stores the enrollment token in `pending_totp_store`. A parallel store is net-new code (class, port, wiring, tests) for a flow that is otherwise identical to the existing one — pure duplication.
- **Con**: Two stores = two resolution code paths = two things to keep correct, audit, and reason about. The bug we are fixing is *too many lanes*, not too few stores.
- **Con**: Both tickets are 256-bit opaque, both 5-min TTL, both map to a `user_id`, both are pre-session — divergent TTL/semantics are not required today.
- **Updated note (rev. 2, post-MF-1):** the security review's Critical did identify a *real* confusion risk that the rev.-1 "same store, same bare value" draft had. The chosen resolution is **not** Option C (separate store/class — still rejected as duplication) but the **in-value scope discriminator (D1.1) plus a mandatory `expected_scope` on resolution (D1.2/D1.3)**. This gives Option C's "the two ticket classes cannot collide" property *without* a second store/port/wiring — a single enforced field on the existing store. The store-level enforcement is exactly the "one correct mechanism" Option C's Pro wanted, achieved by hardening the one store rather than cloning it.
- **Why rejected**: YAGNI + "duplication < wrong coupling but a *needless* second mechanism is still cost". The hardened single store (D1) is exactly right; reuse it. If a future requirement genuinely needs divergent TTLs or semantics, a new ADR introduces the split then.

## Versioning

### §8 ruling: this lands as a **PATCH** → **`v1.13.2`**, CHANGELOG section **`### Fixed`**

This is the deliberate, written reconciliation against the project conventions **Re-affirmed unchanged at rev. 2** after incorporating the security MUST-FIX items: security confirmed the diagnosis and cure direction are sound, and the MF-1..MF-7 additions do **not** change that this is a bug fix of a path that has never worked. Specifically, the rev.-2 additions are still PATCH-class:

- The **scope discriminator (MF-1)** changes the *internal* auth-svc Redis value shape and the `RedisPendingTotpLoginStore` Python API. It is **not** a public/SPA contract change (the SPA never sees the Redis value; the `/login` and three enrollment request/response bodies are unchanged) and it is **not** a DB schema change (Redis value, no Alembic). It is a security hardening of a never-working internal path — textbook PATCH ("security-fix без изменения публичного контракта").
- The **`IssueSession` extraction (MF-2)** has two distinct version-relevant parts, neither of which is a MAJOR/MINOR trigger:
  - For `verify_totp` it is a genuinely behaviour-preserving internal refactor (it already emits `LoggedIn` + `last_login_at`; same events, same transaction — INV-1).
  - For the forced `change_password` path it **intentionally ADDS `auth.user.logged_in.v1` + a `last_login_at` write** (that path emits neither today — D5.4.1/D6). This is *not* claimed as behaviour-preserving; it is justified for PATCH as **an additive emit on an *existing internal* auth→audit outbox stream**: the event type `auth.user.logged_in.v1` **already exists** and is **already consumed** by `audit-service` (it is emitted today by `verify_totp` and the terminal enroll-only branch), so no new event contract, no new consumer, and **no schema/Alembic migration** is introduced. It repairs a previously under-audited (never-`LoggedIn`-emitting) session mint. It is a private auth→audit internal stream, **not** a public/SPA contract — the SPA never observes outbox events — so the §8 "breaking change in inter-service contracts" MAJOR trigger does not fire, and "new use case / feature" MINOR does not fire (no new user-facing capability; the user story already existed and was shipped-as-broken). The terminal enroll-only `LoggedIn` is likewise additive on a never-working path. Net: an internal, additive, contract-compatible audit event → PATCH-class, no MAJOR/MINOR trigger.
- The **`redact_sensitive_fields` recursion (MF-3)** is a `shared/` logging hardening (defensive, no API surface change).
- No new microservice, no service-boundary change, no data migration, no destructive change (INV-4/INV-5 explicitly re-affirmed: **zero `web/` change, zero Alembic migration**).

Therefore the ruling stands: **PATCH → `v1.13.2`, CHANGELOG `### Fixed`.**

**The §8 MAJOR trigger does NOT fire.** §8 lists "breaking change in inter-service contracts" as MAJOR. This change does **not** break an inter-service contract:

- It does **not** change the `/login` response shape (still `{next_step:"enroll_totp", enrollment_token}` — the `[1.13.1]` contract, already live on PreProd).
- It does **not** change any *existing, working* contract. The enrollment-phase contract it touches has **never functioned** (broken since `c171edd`). You cannot "break" a contract that has never produced a successful response in any environment; there is no client, deployment, or stored state relying on the current 401 behaviour. Fixing a never-working path is the textbook definition of a bug fix, not a breaking change.
- It does **not** change the *public* SPA-facing contract at all (INV-5: zero `web/` change; the SPA already sends the token).
- There is **no data migration** and **no destructive change** (INV-4: zero Alembic revisions, reuses the existing Redis store).
- No microservice is added, split, or removed; service boundaries are unchanged.

What it *is*, precisely: "защита от/исправление сломанного флоу без изменения публичного контракта" — the exact wording of the §8 **PATCH** definition ("Любое исправление найденного бага: фикс падения … security-fix без изменения публичного контракта"). All three failing routes return 401 today; after the fix they work. That is a fix, not a feature: the user story ("new user enrolls TOTP on first login") already existed and was *shipped-as-broken* in `v1.0.0` (`the auth feature`, ADR-0003) — we are repairing delivered-but-non-functional behaviour, which §8 explicitly classes as PATCH ("фикс падения"), not MINOR ("новая фича / use case"). The fix also includes an *internal* Redis-value/store-API change (MF-1) and the *intentional additive* `auth.user.logged_in.v1` + `last_login_at` on the existing (under-audited) forced-change-password path (D5.4.1/D6); per the MF-2 bullet above, an additive emit of an *already-existing, already-consumed* internal auth→audit event with no new contract and no Alembic migration is contract-compatible and does **not** trip the MAJOR (inter-service contract break) or MINOR (new user-facing capability) triggers. PATCH stands; the version string and CHANGELOG `### Fixed` classification are **unchanged**.

**Reconciliation with the in-flight version timeline:**

- `web/package.json` is currently `1.13.1`; CHANGELOG `[1.13.1]` (2026-05-19) is the staged-but-relevant BFF `next_step` one-liner now live on PreProd.
- The user-pre-approved 2-commit landing was: (1) `feat` → `v1.13.0` bundling the unrelated invite-pipeline WIP (already in CHANGELOG `[1.13.0]`, 2026-05-14); (2) `fix` → `v1.13.1` (the `next_step` regression, CHANGELOG `[1.13.1]`). Both are accounted for.
- **This enrollment fix is the *next* commit after `1.13.1` and lands as its own PATCH: `v1.13.2`.** It is logically continuous with `1.13.1` (both repair the same broken first-login journey — `1.13.1` un-hung the SPA, `1.13.2` makes the calls the un-hung SPA now fires actually succeed) but is a distinct, separately-tagged fix because it is a distinct root cause in a different layer (`1.13.1` = BFF response literal; `1.13.2` = enrollment ticket resolution lane across BFF + auth-svc).
- **Exact target version string: `1.13.2`.** Bump `web/package.json` `version` to `1.13.2` in the same commit as the auth code change. Add CHANGELOG section `## [1.13.2] — <commit date>` with a `### Fixed` subsection (NOT `### Added` — nothing new is offered to the user; a broken capability is repaired). Recommended (PATCH tags are optional per §8 but advised here given PreProd users are blocked): `git tag -a v1.13.2 HEAD -m "fix(auth): first-login enrollment opaque-ticket exchange (ADR-0008)"`.

CHANGELOG `### Fixed` entry should name: the three previously-401 routes, the root cause (opaque token fed into RS256 access-JWT gate since `c171edd`), the fix (mirror `verify_totp` opaque-ticket resolution in auth-service; BFF moves the three routes to the anonymous-ticket lane), the security hardening folded in (scope-discriminator on the Redis ticket value preventing login/enroll ticket confusion; nested-secret log redaction; post-resolution already-enrolled re-check; per-ticket confirm-attempt cap), and explicitly note "SPA bundle not affected; no DB migration". Use `### Fixed` (and a `### Security` sub-bullet for the MF-1 confusion-class hardening is acceptable but not a separate version).

## Implementation handoff

**Scope is backend-only. Zero `web/` edits. Zero Alembic migrations.** (INV-4, INV-5.)

### `backend` — auth-service

Exact files/functions to change (this list is normative; rev. 2 expands it for MF-1/MF-2/MF-4):

- **`TicketScope` enum (new).** Add a `StrEnum` `TicketScope { ENROLL = "enroll", LOGIN = "login" }` in `auth_service.domain.value_objects` (or alongside `PendingTotpLoginTicket` in `domain.entities`). Pure, no deps.
- **`services/auth_service/infrastructure/redis/pending_totp_store.py` — `RedisPendingTotpLoginStore` (MUST change; supersedes the rev.-1 "do not change" line).** Implement D1.2 verbatim: `set_ticket(ticket_id, user_id, scope: TicketScope)` writes `json.dumps({"uid": str(user_id), "scope": scope.value})`; `get_user_id(ticket_id, *, expected_scope: TicketScope)` parses JSON, returns `uid` UUID **only if** `scope == expected_scope`, else `None` (also `None` for missing/malformed/bare-string/legacy). Update the `application.ports.RedisPendingTotpLoginStore` Protocol signatures to match. This is a Redis-value/API change, **not** an Alembic migration (INV-4 holds).
- **`services/auth_service/application/use_cases/start_login.py`** — the only change here: pass `scope=TicketScope.ENROLL` at `:~134` and `scope=TicketScope.LOGIN` at `:~147` to `set_ticket(...)`. (Logic otherwise unchanged.)
- **`services/auth_service/application/use_cases/verify_totp.py:73`** — change the resolve call to `get_user_id(cmd.ticket_id, expected_scope=TicketScope.LOGIN)` (MF-1, D1.3). Behaviour otherwise preserved (INV-1).
- **New: enrollment-ticket resolver dependency.** Add an auth-svc API dependency `resolve_enrollment_ticket` (in `auth_service.api.v1.deps` or the auth router module) that: reads `enrollment_token` from the request body, calls `get_user_id(token, expected_scope=TicketScope.ENROLL)`, loads the user, applies the MF-4 `not user.has_totp_enrolled` re-check (D5.3), and raises the generic `InvalidCredentialsError` (→ uniform 401, D5.7) on any failure. **It takes no `actor`/`RequireActor` dependency** (MF-5, D3b.1). All three endpoints depend on this resolver and on nothing that exposes an actor.
- **New: `IssueSession` collaborator (MF-2, D5.4.1).** `auth_service.application.use_cases.issue_session.IssueSession` encapsulating D5.4.5–D5.4.8 (lockout re-check → `Session.create` + `session_repo.add` → mint RS256 JWT with real `sid` → set `last_login_at` → emit `LoggedIn` in the caller's transaction). Refactor `VerifyTotp` (steps 5–11) and `ChangePassword` forced path (`change_password.py:~91–108`) to call it. **Behaviour-impact is call-site-specific (D5.4.1):** for `VerifyTotp` this is behaviour-preserving (it already emits `LoggedIn` + sets `last_login_at` — INV-1). For the `ChangePassword` forced path this **intentionally ADDS** `auth.user.logged_in.v1` + `last_login_at` (that path emits neither today) — a deliberate, audited additive change, **NOT** behaviour-preserving and **NOT** a regression (INV-6; `QA` MUST update existing `ChangePassword` outbox tests to assert the added event).
- **`services/auth_service/api/v1/auth.py`**
  - `enroll_totp_endpoint` (line ~351): drop `RequireActor`; use `resolve_enrollment_ticket`; pass resolved `user_id` to the enroll use case. Do **not** delete the ticket here (non-terminal).
  - `confirm_totp_enrollment_endpoint` (line ~378): drop `RequireActor`; use `resolve_enrollment_ticket`; pass `user_id` into `ConfirmTotpEnrollmentCommand`. On wrong code: INCR the per-ticket confirm-attempt counter (MF-6, D5.6.1), generic 400, ticket/pending preserved (until cap). On success: if the account also needs forced password change → return `{backup_codes}`, ticket alive (non-terminal). Else (enroll-only terminal) → call `IssueSession` (D5.4.4–D5.4.9) **inside the same `db.begin()`** as the secret-persist, then `delete_ticket`.
  - `change_password` first-login branch: accept `enrollment_token` in the body; use `resolve_enrollment_ticket`; apply MF-4 re-check; on success call the shared `IssueSession`, then `delete_ticket` (terminal). Normal (re-MFA) path unchanged.
  - Preserve all existing `async with db.begin()` blocks and the existing `TotpEnrolled`/`BackupCodesGenerated`/`PasswordChanged`/`UserActivated` (+ any existing `SessionRevoked`) outbox publishes verbatim (INV-6) — these existing events are unchanged. The `user_id` *source* changes (resolved ticket, never `actor.actor_id`/`_ANON`). **Additionally:** on the forced `change_password` terminal path the shared `IssueSession` call **intentionally adds** an `auth.user.logged_in.v1` emit + a `last_login_at` write that this path does NOT produce today (D5.4.1/D6) — this is a deliberate, in-transaction additive audit improvement, not a regression; do not suppress it.
- **`confirm_totp_enrollment.py` / `change_password.py`** — accept `user_id` from the resolver; emit events with `actor_id = user_id` (already do); add the `LoggedIn` emit via `IssueSession` only on the terminal branches (D6).
- Do **not** change `RequireActor` itself or any endpoint *outside* these three using it (INV-2, INV-3).

### `backend` — web-bff

- `services/web-bff/src/web_bff/api/v1/auth.py`
  - `enroll_totp` (line ~286): remove the `RequireAccessClaims` dependency for this route. Read `enrollment_token` from the request body (fallback: from the `Authorization: Bearer` value treated as an *opaque string*, NOT decoded as JWT — see §D7). Forward to auth-svc via the **anonymous** client lane (mint internal JWT with `_ANON_ACTOR_ID`, exactly as `verify_totp` BFF handler does), passing `{enrollment_token}` in the body. The handler MUST NOT log the request body / `enrollment_token` (MF-3, D3a.1/D3a.2).
  - `confirm_totp_enrollment` (line ~309): same — drop `RequireAccessClaims`, forward `{enrollment_token, code}` via the anon lane; no body/token logging.
  - `change_password` (line ~614): for the first-login path, drop the `RequireAccessClaims` requirement when an `enrollment_token` is present in the body; forward `{enrollment_token, new_password}` via the anon lane; no body/token/password logging. The normal (already-authenticated) password-change path keeps `RequireAccessClaims` unchanged (INV-2).
  - For these three routes, configure 422 handling so validation errors do not echo the submitted body (MF-3, D3a.4).
- `services/web-bff/src/web_bff/infrastructure/clients/auth_client.py`
  - `enroll_totp` / `confirm_totp_enrollment` / `change_password` (lines ~117, ~132, ~200): add an enrollment-ticket variant (or parameterize) so they call the auth-svc routes with `actor_id=_ANON_ACTOR_ID`, `role=_ANON_ROLE`, and the `enrollment_token` in the JSON body — modeled exactly on `verify_totp` (`auth_client.py:55`). Do not introduce a new client class. Do not log the outgoing body (MF-3).
- `services/web-bff/src/web_bff/api/deps.py`: **no change for non-enrollment routes** — `_verify_access_jwt` / `RequireAccessClaims` / `RequireActor` stay exactly as-is and continue to gate every other route (INV-2). The three enrollment routes simply do not depend on them anymore; the dependency code itself is untouched.

### `backend` — shared (MF-3)

- `shared/src/lotsman_shared/logging.py` — `redact_sensitive_fields` (lines ~47–66): make it **recurse into nested `dict` and `list` values** so a key matched by `_SENSITIVE_KEY_RE` (which already matches `enrollment_token` via the `token` alternative) is redacted at any depth, not only top-level (D3a.3). Recursion MUST be depth-bounded by the fixed module-level constant **`MAX_REDACT_DEPTH = 8`** (top-level `event_dict` = depth 0; recurse only while depth `< MAX_REDACT_DEPTH`; at the bound, replace the still-nested value wholesale with `***REDACTED***` rather than recursing) **and** cycle-safe via a `visited` set of container `id()`s on the descent path (an already-visited container → sentinel/`"<cycle>"`, no recursion) — exactly per D3a.3 (deterministic so QA can boundary-test). This is a `shared/` change; it ships in the same `v1.13.2` commit (still PATCH — internal logging hardening, see Versioning rev.-2 note). Add unit tests: `{"body": {"enrollment_token": "secret"}}` → nested value `***REDACTED***`; payload nested exactly `MAX_REDACT_DEPTH` deep redacts at the boundary; `MAX_REDACT_DEPTH + 1` deep elides-by-redacting (no leak, no crash); self-referential dict terminates without `RecursionError`.

### `backend` — versioning (mandatory, same commit)

- Bump `web/package.json` `version`: `1.13.1` → `1.13.2`.
- Prepend CHANGELOG.md section `## [1.13.2] — <date>` with a `### Fixed` subsection per the Versioning section above.
- Tag: `git tag -a v1.13.2 HEAD -m "fix(auth): first-login enrollment opaque-ticket exchange (ADR-0008)"`.

### `QA`

- One test per invariant **INV-1 … INV-10** (named above). Critical paths:
  - E2E: invited user (OOB OTP issued, no TOTP) → login → enroll → confirm → forced change-password → lands authenticated. Run against the PreProd-equivalent fixture for `user1@example.com` / `user2@example.com` shape (no TOTP, OOB OTP set). On the forced change-password terminal step, assert the outbox transaction emits `PasswordChanged` + `UserActivated` **plus the newly-added `auth.user.logged_in.v1`** and that `user.last_login_at` is now set (D5.4.1/D6/INV-6) — these two additions are EXPECTED, not regressions.
  - **Updated existing test (INV-6, D5.4.1):** the pre-existing `ChangePassword`-forced-path outbox test(s) MUST be updated to *expect* `auth.user.logged_in.v1` (+ `last_login_at`) in addition to `PasswordChanged`/`UserActivated`. A test that fails because `LoggedIn` newly appears on this path is asserting stale behaviour and MUST be amended, not treated as a defect.
  - E2E enroll-only terminal branch (MF-2): user with valid password + admin TOTP-reset (no forced password change) → login → enroll → confirm → confirm itself returns `{backup_codes, access_token}` + refresh cookie; assert a real `auth.sessions` row exists, the JWT `sid` equals that row id, and the transaction emitted `TotpEnrolled` + `BackupCodesGenerated` + `LoggedIn` all with `actor_id == user_id` (INV-6).
  - Cross-scope (MF-1, INV-3): `session_ticket` (scope `login`) → each of the 3 enrollment routes → generic 401, no mutation; `enrollment_token` (scope `enroll`) → `/totp/verify` → generic 401, no session.
  - Negative: enrollment ticket on `GET /api/v1/auth/sessions/me` → 401; opaque string in `Authorization` on `/api/v1/registry/*` → still 401 (INV-2/INV-3).
  - Ticket lifecycle + cap (MF-6/MF-7, INV-7): failed `enroll/confirm` does not consume the ticket; 5 retries allowed, 6th invalidates the ticket; expired/unknown/scope-mismatch all → the *same* generic 401 shape.
  - Already-enrolled re-check (MF-4, INV-8): valid ticket whose user became TOTP-enrolled → all 3 routes generic 401, `totp_secret_enc`/`password_hash` byte-identical before/after.
  - Logging (MF-3, INV-9): nested-dict redaction unit test; no service log line contains a live ticket; malformed body → 422 with no token/password substring.
  - Exclusive binding (MF-5, INV-10): ticket for user A + body hint / non-`_ANON` actor for user B → acts on A only.
  - `git diff --stat` of the fix commit contains no `web/` path and no `alembic/versions/*` (INV-4, INV-5).

### `Documentation`

- Add to `docs/architecture/auth.md`: the enrollment opaque-ticket lane as a sibling of the TOTP-login `session_ticket` lane; note both resolve in auth-service and travel the anonymous BFF lane (body field), and that the `Authorization` header is reserved for RS256 access JWTs only.
- Regenerate `docs/api/auth.yaml` so the three enrollment-phase request bodies show the `enrollment_token` field.

### `security`

- **Re-review this revised ADR before implementation starts** (the BLOCK verdict lifts only on sign-off of rev. 2): confirm MF-1..MF-7 are each closed by a concrete normative line and that the scope-discriminator value schema + store API (D1.1/D1.2) are unambiguous.
- Confirm post-implementation that: no access-gated route accepts an opaque token (INV-2/INV-3); cross-scope tickets are rejected both directions (MF-1/INV-3); the un-enrolled user holds no session until the terminal step and the terminal `sid` is a real `auth.sessions.id` (MF-2/INV-6, ADR-0003 §7/§13); already-enrolled users cannot have a secret overwritten (MF-4/INV-8); the ticket is never present in any log and 422s do not echo it (MF-3/INV-9); identity is ticket-only (MF-5/INV-10); the confirm cap and uniform-401 hold (MF-6/MF-7/INV-7). Re-affirm ADR-0003 §7/§13/AAL2 are intact (Option A was rejected specifically to protect these).
