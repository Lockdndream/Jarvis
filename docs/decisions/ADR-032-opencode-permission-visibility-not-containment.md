# ADR-032: OpenCode Permission Visibility (Not Containment)

## Status

Accepted

## Date

2026-08-01 (TD-026 investigation, post-F1 feature roadmap)

## Context

TD-026 (Critical technical debt): during a walk-away-mode end-to-end test,
an OpenCode task assigned to `tests/test_projects/safe-test/` (a 9-file
sandbox) navigated to the live repository root — `.env`, `certs/`,
`jarvis.db`, and `projects.json` were all reachable — and ran the real
859-test pytest suite. Zero permission prompts fired. No log, no alert.

The working assumption going in was that `project_dir` provided some kind
of enforced isolation boundary (several `projects.json` entries' own
descriptions assert this, e.g. "excludes .env, projects.json, certs/,
jarvis.db"), and that fixing TD-026 meant building or configuring a
containment mechanism to make that assumption true. This ADR documents
why that assumption was false, what the actual root cause was, what was
tried, what worked, what didn't, and why the resulting fix is about
**correctness and visibility, not containment**.

## Investigation

A live-server investigation (not a code-reading exercise — every claim
below was verified against the actual running `opencode serve` instance
on port 4097, using disposable sessions created and cleaned up for the
purpose) found:

1. **Root cause**: `OpenCodeAdapter.create_session()` never sent OpenCode
   a `directory` parameter (`json={}`, no query param). Every OpenCode
   session therefore silently defaulted to the server subprocess's own
   working directory — the repository root — regardless of what
   `project_dir` Jarvis's own task records said. Confirmed live: every
   session in the server's own session list reported
   `"directory": "D:\\Projects\\Jarvis"`, no matter which project a task
   was nominally scoped to.

2. **The fix for (1) works**: passing `directory=<project_dir>` at
   session creation does rebind the session — confirmed live, a session
   created this way correctly reports its own sub-directory rather than
   the repo root.

3. **Binding alone is not containment.** With a session properly bound
   to the `safe-test` sandbox, asking it to read a file outside that
   sandbox — first a file inside the repo but outside the sandbox
   (`SESSION.md`), then a file entirely outside the git repository
   (`C:\Users\Admin\.gitconfig`) — completed both times with zero
   permission prompt and zero gate. OpenCode's `external_directory`
   permission, documented as defaulting to `"ask"` for exactly this case,
   did not fire at either distance in the installed version (1.15.10).

4. **Session-level permission overrides don't apply.** `PATCH
   /session/{id}` accepts a `permission` field, but testing showed it has
   no effect on `external_directory` — consistent with the official docs,
   which only describe `external_directory` as project/global-config
   configurable, never per-session.

5. **Explicit global-config permission rules were tried and made things
   worse.** Adding an explicit `read` permission block to the isolated,
   Jarvis-only OpenCode config (glob patterns for `.env`, `jarvis.db`,
   `projects.json`, `certs/**`) did not gate any of those files — but it
   **did disable `.env`'s own working built-in default protection**,
   which had gated correctly in three earlier observations before this
   config was added. In this OpenCode version, supplying an explicit
   `read` permission object appears to *replace* the built-in default
   ruleset rather than merge with it. The configuration was reverted and
   the server restarted, but the built-in `.env` default did not resume
   gating afterward — some state beyond the config file (not identified;
   not a plain file in the isolated state directory) appears to have
   been altered by the testing itself. This is disclosed plainly: **the
   investigation's own testing left the live system's `.env` protection
   in a degraded state that reverting the config file did not undo.**

6. **`bash` is completely ungated**, and it was the incident's actual
   vector — the real walk-away session ran `pytest tests/`, `pip install
   pytest-timeout`, `Get-ChildItem`, all `status: completed`, zero gate.
   Pattern-based permission rules match file paths, not shell command
   text, so no file-pattern configuration catches a shell command that
   reads a sensitive file indirectly (`cat .env`, `Get-Content .env`,
   etc.), and setting `bash: ask` is unusable under unattended/walk-away
   execution — there is no one to answer the prompt.

**Summary of what was tested**: five distinct containment angles
(directory rebinding, `external_directory` at two distances, session-level
permission override, explicit global sensitive-file patterns, and the
existing `.env` built-in default). One worked before testing began and no
longer does. None of the other four provide usable containment in this
version.

## Decision

**Fix the two genuine correctness bugs. Do not attempt to build or
configure containment that has been directly tested and shown not to
work.**

1. `OpenCodeAdapter.create_session()` now passes `directory=<project_dir>`
   at session creation, so a session's actual OpenCode-side workspace
   matches what Jarvis's own records say it is. This is a correctness
   fix — Jarvis's `project_dir` field stops being cosmetic — not a
   security boundary. Sessions are properly *scoped* by default now
   (relative-path tool calls resolve inside the assigned directory), but
   an absolute path or a shell command can still reach anywhere the
   OS-level process has access to.

2. `normalize_permission()` was reading the wrong keys entirely — it
   assumed a `{"action": ..., "path": ...}` shape that never matched
   OpenCode 1.15.10's real `PermissionRequest` schema
   (`{"permission": ..., "patterns": [...]}`, confirmed via the
   installed version's own OpenAPI spec). Every permission that *did*
   reach a user before this fix showed a blank action and empty path.
   Fixed to read the real schema, with the old shape kept as a fallback.

3. `_emit_permission()` (the handler that fires whenever OpenCode itself
   decides to queue a permission — for whatever reason, on whatever
   default or config governs that decision) now additionally logs at
   WARNING, stores an episodic memory entry (surfaced in catch-me-up),
   and broadcasts a `thinking_update` frame to the conversation UI — on
   top of the pre-existing `questions` DB row and `task_permission` WS
   broadcast used by the PWA's approve/deny buttons. This does not make
   Jarvis block anything; it makes sure that on the occasions OpenCode
   does ask, the request is impossible to miss across every surface
   (log, memory, PWA, voice/chat conversation view) instead of being
   visible only to the PWA.

**Explicitly not done**: no file-path allowlist/denylist enforcement, no
OS-level sandboxing (chroot, ACLs, containers — out of scope per the
original brief's "no over-engineering" instruction and disproportionate
for a single-user local system), no attempt to gate `bash`.

## What this prevents

- A session's default workspace is now the directory Jarvis actually
  assigned, not always the repo root — reduces the practical blast
  radius of ordinary (non-adversarial, non-absolute-path) tool use.
- When OpenCode's own permission system does decide to ask (its `.env`
  default, previously; whatever else may trigger it in a future version
  or configuration), the request now reaches the user correctly — real
  action, real path, on every surface — instead of a blank prompt only
  the PWA could see.

## What this does NOT prevent (stated plainly, per this project's
evidence-before-assumptions standard)

- **Absolute-path file access outside the assigned directory.** Nothing
  stops a tool call from reading, writing, or referencing an absolute
  path anywhere the OS-level `opencode serve` process (running as the
  same OS user as Jarvis itself) can reach. This is exactly what
  happened in the original incident.
- **`bash` in general.** Any shell command runs with zero gate,
  regardless of what it touches. This is a hole by design in the
  installed OpenCode version, not an oversight in this fix — the brief
  explicitly rules out disabling shell execution, and there is no
  configuration that gates command *effects* rather than command
  *text*.
- **Sensitive files specifically.** `.env`, `jarvis.db`, `projects.json`,
  and `certs/` have no enforced protection. `.env` had an apparent
  built-in default that this investigation's own testing degraded (see
  Investigation §5); the others never had one that testing could find.
- **A determined agent** with shell access can trivially defeat any
  future path-pattern-based rule (`cd`, environment-variable expansion,
  base64-encoding a file's contents into a "harmless" tool call,
  reading via a symlink, etc.). No configuration explored here changes
  that.

## Alternatives Considered

**OS-level sandboxing (chroot, Windows job objects / restricted tokens,
containers).** Rejected for this pass per the original brief's explicit
scope ("not enterprise-grade sandboxing... single-user local system") and
because it is disproportionate engineering effort relative to the actual
risk profile (one trusted user, one machine, no multi-tenancy). Revisit
if this system is ever used in a context with untrusted task sources.

**Jarvis-side path interception/blocking.** Rejected: investigated and
found there is no interception point. Jarvis only learns about an
OpenCode action when OpenCode itself chooses to surface it (a queued
permission or a terminal message). There is no hook to inspect or veto a
tool call before it executes. Jarvis-side work is therefore necessarily
about visibility (logging, memory, broadcast) and relay (the existing
approve/deny flow), not enforcement.

**Ship the explicit sensitive-file permission config anyway, on the
theory that it's better than nothing.** Rejected after direct testing
showed it replaces rather than augments the built-in default and
provides zero measured protection for the patterns tried, while actively
degrading the one thing that had been working. Shipping a configuration
that looks like protection but isn't would be worse than shipping
nothing — it would create false confidence in exactly the failure mode
TD-026 already demonstrated.

## Future Revisit Conditions

- A future OpenCode version ships `external_directory` enforcement that
  actually fires for reads (not just the documented default that didn't
  hold up under direct testing) — revisit whether project-scoped
  containment becomes achievable via config alone.
- Walk-away/unattended task volume or risk profile increases enough to
  justify OS-level sandboxing despite the effort — e.g. if tasks begin
  running with less-trusted instructions, or the household/environment
  changes such that "single-user local system" no longer holds.
- The `.env` built-in default's degradation is ever explained or
  reproduced deliberately — worth understanding whether it's a
  config-merge bug, a persisted-grant mechanism, or something else,
  since the same mechanism could silently affect other defaults too.

## References

- `docs/TECHNICAL_DEBT.md` (TD-026, updated status and full incident
  history).
- `docs/decisions/ADR-004-opencode-runtime-isolation.md` (storage and
  credential isolation — a different, narrower axis than this ADR;
  ADR-004 never claimed workspace/file-access containment, and this ADR
  does not change or supersede it).
- `docs/decisions/ADR-030-interaction-layer.md` (the conversation UI /
  `thinking_update` mechanism this work now also uses for permission
  visibility).

## Related Milestones

TD-026 investigation and fix, 2026-08-01.

## Related Source Files

- `app/integrations/opencode_adapter.py` (`create_session` — directory
  binding fix).
- `app/integrations/opencode_events.py` (`normalize_permission` — real
  schema fix).
- `app/integrations/opencode_supervisor.py` (`_emit_permission` —
  logging, memory, `thinking_update` additions).
- `projects.json` (per-project descriptions should be read as intent,
  not enforcement — several currently overstate what is actually
  guaranteed; worth revising their wording in a documentation pass, not
  addressed by this ADR).
