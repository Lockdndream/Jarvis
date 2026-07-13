# ADR-004: OpenCode Runtime Isolation

## Status

Accepted

## Date

2026-07-09 (Milestone 6.1), extended 2026-07-11 (Milestone 9B.0 —
credential isolation added as a second, distinct axis)

## Context

Milestone 6 built real OpenCode integration but hit a hard blocker: every
API-submitted prompt against the shared OpenCode Desktop database
(`~/.local/share/opencode/opencode.db`) failed with a
`session_message.seq NOT NULL` constraint violation, before any model
call. Investigation traced this to cross-version-incompatible rows in
that shared database — Jarvis's managed CLI and OpenCode Desktop's own
bundled CLI were different versions writing incompatible assumptions into
the same file. Separately, during Milestone 6.1's own investigation, an
individual-environment-variable diagnostic command accidentally mutated
the shared database directly (`session_message` row count dropped from
59 to 0) — a real, disclosed incident, not a hypothetical risk.
Separately again, Milestone 9B.0 found that storage isolation alone did
not prevent an ambient, Jarvis-unrelated `OPENAI_API_KEY` on the host
machine from reaching the isolated subprocess and being used for a real
paid model call.

## Problem

How should Jarvis's own OpenCode usage coexist with OpenCode Desktop
(a separate application on the same machine, with its own real,
user-owned conversation history) without either corrupting the other or
leaking credentials between them?

## Decision

A Jarvis-owned `opencode serve` instance runs against a fully isolated
runtime directory (`%LOCALAPPDATA%\JarvisOpenCodeRuntime` by default,
override via `JARVIS_OPENCODE_RUNTIME_DIR`) — separate `data`, `config`,
`cache`, and `state` subdirectories mapped to
`XDG_DATA_HOME`/`XDG_CONFIG_HOME`/`XDG_CACHE_HOME`/`XDG_STATE_HOME`
respectively, verified experimentally against the installed CLI's own
`debug paths` introspection (not officially documented, but deterministic
and repeatable). This is applied only on the owned-spawn path — external-
attach (an already-running, independently-owned OpenCode server) is never
isolated or modified.

This isolation is **storage isolation**, and it is necessarily
insufficient by itself: Milestone 9B.0 established a second, orthogonal
axis, **credential isolation** — the owned-spawn subprocess environment
is built from an explicit, minimal, OS-essential allowlist
(`isolated_subprocess_env()`/`_OS_ESSENTIAL_ENV_VARS`), never the full
inherited ambient environment. The isolated runtime's own provider
credential comes only from its own isolated `auth.json`, provisioned
once from `JARVIS_OPENCODE_OPENROUTER_KEY`/`JARVIS_LLM_API_KEY`, never
from Desktop's credentials or the ambient environment.

## Alternatives Considered

**Upgrade Jarvis's managed OpenCode CLI to match Desktop's version.**
Rejected: outside Jarvis's control (Desktop's own upgrade cadence is
independent), and does not address the underlying incompatibility
mechanism, which was never fully root-caused (the direct evidence trail
was lost in the Phase 1 incident before deeper forensics could be done —
disclosed as a genuine, unresolved evidentiary gap, not hidden).

**Pin Jarvis's CLI to Desktop's exact version.** Rejected: couples
Jarvis's own reliability to Desktop's version forever, with no
independent upgrade path, for a problem isolation solves without that
coupling.

**Find and execute a supported migration of the shared storage.**
Rejected: no supported migration path was found, and any migration
attempt risks the shared database's real, user-owned history — a real
Desktop app conversation history is not Jarvis's to gamble with, however
elegant a shared-storage fix might otherwise be.

**Continue using shared storage, treat the constraint violation as
something to work around per-call.** Rejected: already proven broken;
this was the status quo Milestone 6 inherited and could not make work.

**For credential isolation specifically: filter known-bad environment
variables by name (a denylist) rather than allowlisting.** Rejected: a
denylist can only exclude variables someone thought to name in advance;
the actual incident (an ambient `OPENAI_API_KEY` no one anticipated)
demonstrates exactly why a denylist is the wrong shape for a security
boundary — an allowlist fails closed, a denylist fails open.

Scored against these alternatives on a five-axis matrix (safety,
data-loss risk, session preservation, reliability, maintainability),
isolated storage won on every axis but one ("no new directory concept to
reason about") — see `SESSION.md` Milestone 6.1 for the full matrix.

## Consequences

Every future change to how Jarvis spawns or configures an owned OpenCode
server must go through `isolated_subprocess_env()`/
`isolated_env_overrides()`, never a direct `{**os.environ, ...}` or
equivalent — this is now a standing implementation constraint, not just a
one-time fix. Desktop OpenCode's storage is verified byte-identical
before/after essentially every real OpenCode-touching test run recorded
in `SESSION.md` from Milestone 6.1 onward — this verification habit
itself is a direct consequence of this ADR and should continue for any
future OpenCode-adjacent work.

## Positive Outcomes

- Real execution proven for the first time in the M5→M6 investigation,
  twice (fresh spawn and restart/reuse), immediately after this fix
  (Milestone 6.1 Phase 5/11).
- Zero further Desktop storage mutations recorded across every
  subsequent milestone's real OpenCode testing (Milestone 7 through
  9B.0), each independently hash-verified.
- The credential-isolation extension closed a real, previously-silent
  cost-boundary gap (a paid `gpt-5.3-chat-latest` call via an inherited
  key) discovered by direct log inspection, not by an external report —
  the isolation habit this ADR established is what made the gap
  *findable* at all.

## Tradeoffs

- Jarvis's own isolated OpenCode database is a second store that will
  independently accumulate data over time — the same unbounded-growth
  concern that already applies to `jarvis.db`'s own tables now applies to
  this store too, unaddressed as of this ADR.
- The exact mechanism of the original Phase 1 incident (why a partial-
  environment-variable `debug paths` call mutated the shared database)
  remains unconfirmed — a disclosed, accepted gap in understanding, not
  something this decision resolves.
- Running the real `opencode.exe` binary against the shared/default path
  for any reason (even read-only inspection) now carries a demonstrated
  mutation risk and must be avoided — a real, ongoing operational
  constraint on anyone debugging OpenCode behavior on this machine.

## Future Revisit Conditions

Revisit if OpenCode itself ships an officially-documented, supported
mechanism for storage isolation (the current approach relies on
experimentally-verified, undocumented `XDG_*` behavior) — an official
mechanism would be preferred once available. Revisit the credential-
isolation allowlist specifically if a legitimate new OS-level variable is
found to be necessary for the subprocess to function correctly on a
platform other than Windows.

## References

- `ARCHITECTURE.md` §12 (Security Model)
- `SESSION.md`, Milestone 6 (Phase 1/3, original blocker), Milestone 6.1
  (isolation fix, full decision matrix), Milestone 9B.0 (credential
  isolation extension, real incident evidence), Architecture Decisions
  #10–12, #29

## Related Milestones

Milestone 6, Milestone 6.1, Milestone 9B.0

## Related Source Files

- `app/integrations/opencode_server.py`
  (`resolve_runtime_dir`/`isolated_env_overrides`/
  `ensure_isolated_runtime_provisioned`/`isolated_subprocess_env`/
  `_OS_ESSENTIAL_ENV_VARS`)
- `app/integrations/opencode_adapter.py` (`validate_opencode_model`,
  explicit model pinning — see ADR-009)
- `tests/test_opencode_isolation.py`
