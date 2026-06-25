# Architecture Decision Records

ADRs capture significant architectural decisions: what was chosen, what was rejected, and why. Once **Accepted**, an ADR is immutable — a changed decision is recorded as a *new* ADR that supersedes it. While still **Proposed**, an ADR may be revised in place (some records carry a `revision` note). The table below mirrors the `Status` header of each file.

| ID | Title | Status | Date |
|---|---|---|---|
| [0001](0001-tech-stack.md) | Tech Stack Selection | Accepted | 2026-05-06 |
| [0002](0002-service-boundaries.md) | Service Boundaries and Inter-Service Contracts | Accepted | 2026-05-06 |
| [0003](0003-authentication-and-session-lifecycle.md) | Authentication and Session Lifecycle | Accepted | 2026-05-06 |
| [0004](0004-two-tier-administration.md) | Two-tier administration (Super-admin / Space-admin) | Accepted (§1–§2 superseded by 0006) | 2026-05-07 |
| [0005](0005-exchange-calendar-integration.md) | Exchange Calendar Integration | Proposed (revision 2) | 2026-05-08 |
| [0006](0006-super-admin-role-and-system-panel.md) | Super-admin Role and System Panel | Proposed | 2026-05-08 |
| [0007](0007-flexible-document-fields.md) | Flexible Document Fields | Proposed | 2026-05-08 |
| [0008](0008-first-login-enrollment-ticket-exchange.md) | First-Login Enrollment — Opaque Ticket Exchange | Proposed | 2026-05-19 |
| [0009](0009-system-control-privilege-reduction.md) | system-control Privilege Reduction | Proposed | 2026-05-22 |
| [0010](0010-internal-jwt-key-rotation.md) | Internal JWT Key Rotation | Proposed | 2026-05-22 |
| [0011](0011-notifications-expansion.md) | Notifications Expansion — events, all-users reminders, per-user prefs, in-app center | Proposed | 2026-06-01 |

## Workflow

1. `architect` agent drafts a new ADR (`Status: Proposed`) when a non-trivial architectural choice is required.
2. The orchestrator surfaces it to the user for review.
3. Once user approves, status moves to `Accepted` and implementation begins.
4. To revise, write a new ADR with `Supersedes ADR-NNNN`; mark the old one `Superseded by ADR-MMMM`.

## How to draft a new ADR

Use this when you need to record a decision yourself (without going through `/feature`):

1. Pick the next sequential ID from the table above (currently `0012`).
2. Create `docs/adr/<ID>-<kebab-title>.md`.
3. Use the template in [TEMPLATE.md](TEMPLATE.md). At minimum fill in:
   - `Status: Proposed`
   - `Date: YYYY-MM-DD`
   - `Deciders:` your name / agent name
   - `Context` — what forced this decision
   - `Decision` — what you chose, stated plainly
   - `Consequences` — positive, negative, neutral / follow-ups
   - `Alternatives considered` — at least one, with reasons for rejection
4. Add a row to the index table in this file.
5. Open a PR. The `architect` or `review` agent reviews the ADR before it is merged.
6. Once merged, change `Status: Proposed` → `Status: Accepted`. Implementation may begin.

**Never edit an accepted ADR.** If the decision changes, write a new ADR with `Supersedes: ADR-NNNN` in the header and update the old ADR's status to `Superseded by ADR-MMMM`.

## Template

See the template in [TEMPLATE.md](TEMPLATE.md).
