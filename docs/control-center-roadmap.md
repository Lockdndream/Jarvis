# Jarvis Control Center — Long-Term Roadmap

This roadmap prioritizes by product value — "does this materially
improve daily operational awareness" — not by engineering novelty.
Nothing here is scheduled; each version is a coherent bundle to pick up
when there's a real reason to, not a deadline commitment. Per the
governing product mode, do not implement any of this speculatively.

## Version 1.0 — Current implementation (this milestone)

Already built, reviewed, hardened, and documented: the 10 panels
(System Health, Live Activity Feed, Current Conversation, Running
Tasks, Attention Center, Event Timeline, Decision Stream, Connectivity
Monitor, Background Services, Recent Alerts), the observer protocol,
the snapshot bootstrap endpoint, and the authentication/data-exposure/
performance contracts in `control-center-observer-protocol-v1.md`.
Not yet merged into `develop` — see the merge plan.

## Version 1.1 — Small improvements

Prioritized highest among future work because each item closes a gap
in the *current* panels rather than adding new ones — the highest
value-to-risk ratio available.

1. **Fix TD-024** (explicit event envelope instead of the `content ??
   data` implicit convention) — the next time any new observer event
   type is added, this should be done first, not worked around again.
2. **Fix TD-025** (turn history instead of a single `currentTurn` slot)
   — low cost, removes a real (if currently rare) correctness gap in
   "Current Conversation."
3. **Historical timeline** — the Live Activity Feed currently shows
   only what happened since the dashboard connected (plus whatever the
   snapshot bootstrapped). A simple "load earlier" against the existing
   `events` table (already queried by the snapshot endpoint) would let
   a user answer "what happened while I wasn't watching" without
   needing to already suspect something and go query `jarvis.db` by
   hand — directly closing the exact gap that motivated this whole
   subsystem in the first place (ADR-018's stale-task-data incident).

## Version 1.2 — Operational improvements

Prioritized second because these make the *existing* panels more
trustworthy and usable day-to-day, rather than adding new
observation surfaces.

1. **Searchable events** — a text filter over the historical timeline
   (once 1.1 delivers it). High value once history exists at all;
   low value before that, hence sequenced after it.
2. **Diagnostics mode** — a panel/toggle that surfaces the raw,
   already-collected Android-side diagnostics (`WakeWordDiagnostics`,
   `VoiceDiagnostics` — see ADR-017) alongside the server-side view,
   for the specific case of "something looks wrong and I need more
   than the operational summary." This is explicitly a deeper, more
   technical view than the rest of the dashboard's mission-control
   framing, and should be visually distinct as an escalation path, not
   the default view.
3. **Notification center** — a panel reflecting the existing
   `notifications` table/lifecycle (already a first-class concept in
   `app/notifications.py`) the same way Attention Center reflects
   `attention_manager.py`. Deferred behind 1.1's items because the
   underlying data model already exists and isn't going anywhere;
   there's no urgency cost to sequencing it here.
4. **AI decision summaries** — a short, plain-language rollup of what
   the Supervisor has decided over a session ("started 2 OpenCode
   tasks, answered 1 question, deferred 1 attention item") rather than
   requiring someone to read the raw Decision Stream turn-by-turn.
   Must be built from the same observable-execution data the Decision
   Stream already renders (Contract 7) — never chain-of-thought,
   never a new LLM call summarizing reasoning that was never captured
   in the first place.

## Version 2.0 — Major architectural evolution

Everything here is a genuine architecture change, not an incremental
addition, and should go through the same review discipline (initial
design → independent review → security/performance review → hardening)
this subsystem itself went through — not be added casually because the
panel scaffolding already exists.

1. **Multi-device support.** The current protocol implicitly assumes
   one phone. Supporting several devices observably (which device did
   what) is a real data-model question — device identity would need to
   become a first-class dimension of every relevant event, not just an
   afterthought field. Highest product value in this tier if Jarvis
   ever genuinely becomes multi-device; zero value, and real design
   risk if attempted, before that's a real need.
2. **Multiple coding agents / coding-agent monitoring.** Today OpenCode
   is the only delegated coding agent. If a second one is ever
   integrated, the Decision Stream and Running Tasks panels' current
   assumption of "one kind of delegated work" would need to generalize
   — which agent, not just which task. Sequenced behind multi-device
   because it's a narrower, more self-contained version of the same
   underlying question ("which of several things did this").
3. **Task analytics** (duration trends, success/failure rates over
   time, cost tracking against `docs/decisions/ADR-013-standing-delegation-workforce-cost-policy.md`).
   Real product value once enough historical data exists (depends on
   1.1's historical timeline landing first) but is genuinely a
   different kind of feature — aggregation and trend-reporting, not
   live observability — and should be evaluated as its own scoped
   decision rather than assumed to belong in this subsystem by default.
4. **Event replay** (re-driving the dashboard's rendering against
   historical events for a specific past window, e.g. "show me exactly
   what the dashboard would have shown during last Tuesday's failure").
   High diagnostic value, meaningfully more complex than it sounds
   (requires the event history to be replayable in order, which the
   append-only model supports in principle but has never been tested
   for), and only worth building once a real incident has actually
   needed it and a simpler tool (reading `events` by hand) proved
   insufficient.
5. **Remote controls** (taking an action — approving a permission,
   answering a question — from the dashboard itself, not just the
   phone). Explicitly the largest departure from this subsystem's
   founding decision (ADR-018: "read-only... cannot affect... the
   product," Contract 2). Would require its own ADR revisiting that
   decision directly, its own security review, and should not be
   assumed to be a natural extension just because the underlying
   `resolve_permission`/`answer_question` tools already exist for the
   Supervisor to call — a human acting through the dashboard is a
   different trust boundary than the Supervisor acting autonomously.
6. **Plugin monitoring / distributed execution / performance
   dashboards.** Listed for completeness; none of these have a
   concrete driving need in the current single-user, single-laptop
   deployment, and speculatively designing for them now would violate
   this project's own stated engineering principles (build for the
   product that exists, not a hypothetical one). Revisit only if the
   deployment model itself changes.
7. **Audit history.** Distinct from the historical timeline (1.1) in
   intent — audit history implies a durability/tamper-evidence
   guarantee ("this record cannot have been altered after the fact"),
   which the current append-only-but-still-just-a-local-SQLite-table
   model does not actually provide. Only worth pursuing if a real
   trust requirement emerges (e.g. multi-user, or Jarvis acting with
   consequences someone else needs to verify) — not for a single-user
   personal system with no such requirement today.

## What's deliberately not on this roadmap

Anything not listed above is not rejected — it simply has no evidence
yet that it's worth building. Per this project's engineering
principles, the next addition to this roadmap should come from a real,
encountered gap (the way the entire Control Center itself originated
from three real incidents), not from a brainstorm of what a dashboard
could theoretically do.
