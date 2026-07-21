# Jarvis Control Center — Merge Plan

**Status: not yet executed.** This is a plan, produced ahead of the
actual merge per explicit instruction not to merge yet. It should be
followed, and can be revised, but nothing below has been run.

## Precondition — a real divergence, checked directly, not assumed

The worktree branch (`worktree-agent-af12d9edfe50c267c`) has never
committed anything — `git log` shows its `HEAD` is still exactly
`0c61baf`, the same commit `develop` was at when this worktree was
created. Every Control Center change (implementation, hardening, and
this documentation) exists purely as **uncommitted working-tree
changes** on top of that old commit.

Meanwhile, `develop` has since advanced to `ea9d422` ("Milestone 9B.10
RC validation: fix 5 real device-found defects") — five real,
independently-verified fixes to `WakeWordManager.kt`,
`VoiceSessionParser.kt`, `CompanionWebSocketClient.kt`,
`SpeechInputController.kt`, `VoiceActivity.kt`, and (relevantly) a
one-line logging addition to `app/integrations/opencode_supervisor.py`
(the `else:` branch in `_handle_activity()`).

**The Control Center's own changes never touch
`app/integrations/opencode_supervisor.py` at all** — confirmed via
`git status --short` in the worktree, which does not list that file.
There is therefore no real content conflict, only a base-commit
staleness to correct before merging.

## Files changed (Control Center only)

**Modified** (relative to `0c61baf`):
- `app/connection_manager.py`
- `app/main.py`
- `app/supervisor/supervisor.py`
- `app/voice_session_manager.py`
- `tests/test_connection_manager.py`
- `tests/test_supervisor.py`
- `tests/test_voice_session_manager.py`
- `docs/TECHNICAL_DEBT.md` (this documentation pass)
- `docs/decisions/README.md` (this documentation pass)

**New**:
- `app/static/dashboard/` (`index.html`, `dashboard.css`, `dashboard.js`)
- `scripts/dashboard_demo_seed.py`
- `open_control_center.bat`
- `tests/conftest.py`
- `tests/test_dashboard_observer_isolation.py`
- `docs/decisions/ADR-018-jarvis-control-center-observability-architecture.md`
- `docs/protocols/control-center-observer-protocol-v1.md`
- `docs/observability-strategy.md`
- `docs/control-center-roadmap.md`
- `docs/control-center-merge-plan.md` (this document)

Not touched by this feature at all: every Android file, every RC-fix
file from `ea9d422`, `app/integrations/opencode_supervisor.py`.

## Merge order

1. **Commit the worktree's current changes to its own branch**, in the
   worktree, against its current (stale) base — a normal commit,
   nothing merged anywhere yet. This turns "uncommitted changes on an
   old base" into "a real commit," which is what step 2 needs.
2. **Rebase that commit onto the current `develop` tip (`ea9d422`)**,
   from within the worktree: `git fetch` (if needed to ensure refs are
   current), then `git rebase develop`. Given zero file overlap (see
   above), this is expected to apply cleanly with no manual conflict
   resolution. If it does not apply cleanly, stop and treat that as new
   information — it would mean an assumption in this plan was wrong,
   not something to force through.
3. **Run the full verification checklist below against the rebased
   branch**, still inside the worktree, before touching `develop` at
   all.
4. **Only then**, fast-forward-merge the rebased branch into `develop`
   from the main working tree (`D:\Projects\Jarvis`) — a fast-forward
   should be possible precisely because step 2 already replayed the
   commit on top of `develop`'s current tip; if `develop` has moved
   again in the meantime, repeat step 2 rather than force a non-fast-
   forward merge.
5. **Re-run the full verification checklist a second time**, against
   the main working tree post-merge, as an independent confirmation —
   not a formality; this is what actually proves the merge itself
   didn't introduce anything, as distinct from proving the feature
   branch was correct in isolation.

## Validation checklist

Run in this order; stop and fix before proceeding if any step fails.

1. `pytest tests/` (full suite) — expect all worktree-baseline tests
   plus the new Control Center tests to pass. Last independently
   confirmed count: **488 passed, 0 failed** (worktree, pre-rebase).
2. `pytest tests/test_dashboard_observer_isolation.py tests/test_connection_manager.py -v` —
   the specific isolation-invariant tests (Contract 1/3), run in
   isolation for a clear pass/fail signal on the single most
   load-bearing guarantee this subsystem makes.
3. Manual auth check: set `JARVIS_API_TOKEN`, confirm
   `GET /api/dashboard/snapshot` returns 401 without a token, 200 with
   the correct one; confirm `dashboard.js`'s `/api/ws-token` and
   snapshot calls both succeed with the token present.
4. Manual live check: run `open_control_center.bat` (or the manual
   steps it wraps) against a real running server, confirm all 10
   panels render, confirm `scripts/dashboard_demo_seed.py` populates
   them with real and seeded data as expected.
5. Regression: full Android build (`./gradlew.bat testDebugUnitTest
   assembleDebug`) — expected to be completely unaffected (this
   feature touches no Android code), but confirm rather than assume,
   exactly as the hardening pass already did once.
6. `git status` on the main working tree post-merge — expect clean,
   with the merge commit as the only change, before considering the
   merge complete.

## Rollback strategy

Because the merge is a fast-forward of a single rebased branch (step 4
above), rollback is a plain `git reset --hard <pre-merge-develop-tip>`
on `develop` if a problem is found immediately after merging and before
any further commits land on top. Record the exact `develop` tip commit
hash immediately before merging specifically so this is possible without
guessing. If further commits have already landed on top of the merge by
the time a problem is found, prefer a targeted revert of the specific
files listed above over a broad `reset --hard`, to avoid discarding
unrelated work that happened afterward.

Because the feature is additive (no existing file's pre-existing
behavior was changed except by the two `has_observers()`-gated
early-returns in `supervisor.py`/`voice_session_manager.py`, both of
which are no-ops when the feature is entirely unused), a partial
rollback — reverting only the frontend or only specific backend
files — is not expected to be necessary; treat the feature as one unit
for rollback purposes.

## Post-merge verification

Beyond the validation checklist (which should already have been run
against the rebased branch before merging): confirm no phone or PWA
client's real-world behavior changed at all post-merge. Concretely,
if a real device is available, repeat a short slice of Milestone
9B.10's own RC rehearsal (wake word → spoken request → response) once
after merging, specifically to confirm the Control Center's presence
in the codebase has zero observable effect on the primary product when
no dashboard is connected — the entire point of Contract 6.

## Risk assessment

**Low overall risk**, for reasons specific to this feature, not risk
tolerance in general:

- Zero file-content overlap with anything `ea9d422` changed.
- The two touch points on load-bearing, phone-facing files
  (`supervisor.py`, `voice_session_manager.py`) were independently
  reviewed twice (initial architectural/security/performance review,
  then a hardening-verification pass) and diffed line-by-line against
  pre-existing behavior both times — confirmed additive, not
  behavior-changing, when unused.
- 488 tests passing in the worktree is from an independently-run
  suite, not a self-report — this plan's author ran it directly.
- The one genuine residual risk is process, not code: steps 1-2 above
  (commit, then rebase) have not actually been executed yet, so the
  "expected to apply cleanly" claim about the rebase is a considered
  prediction based on zero file overlap, not something already proven
  by running it. Treat step 2's outcome as the one open question this
  plan cannot close in advance.
