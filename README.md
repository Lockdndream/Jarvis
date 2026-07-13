# Jarvis

A personal AI assistant that runs on your laptop and is reachable from
your phone. The laptop holds all reasoning, state, and persistence; the
phone (browser today, a native Android companion in spike form) is a
thin client that lets you talk to it and be reached by it.

This README is the entry point. For *why* Jarvis is built the way it is,
see `ARCHITECTURE.md`. For the full chronological history of how it got
here, see `SESSION.md`. See §8 below for the complete documentation map.

---

## 1. Project Overview

Jarvis today is a FastAPI server (`app/main.py`) that a browser or a
phone connects to over WebSocket. It can:

- Hold a free-text conversation, routed through an LLM tool-call loop
  that can start and supervise real coding-agent work (via OpenCode),
  answer questions, and report status.
- Supervise long-running local subprocesses and OpenCode sessions,
  tracking their real, verified state — never guessing that something
  "probably" finished.
- Notice when something needs your attention (a question, a permission
  request, a failure, a proactive check-in) and decide, deterministically,
  whether and how to reach you.
- Hold a voice conversation with you — spoken input and spoken output —
  on the exact same conversational pipeline as typed text.
- Be reached back: Web Push notifications, a call-style "Jarvis is
  calling" UI, deep links from a system notification straight to the
  relevant item.
- Run as an installed Progressive Web App on a phone, with real HTTPS,
  a service worker, and reconnect-safe conversation continuity.

A disposable technical-risk spike for a native Android companion exists
(`spikes/android-presence/`) and has real physical-device evidence behind
it, but it is not production code — see §9.

## 2. Key Features

- **Supervisor** (`app/supervisor/`) — the conversational entry point.
  Routes free text through fast deterministic paths or an LLM tool-call
  loop with a small, bounded set of tools. Never executes work directly;
  always delegates to a supervised worker.
- **OpenCode integration** (`app/integrations/`) — real coding-agent
  supervision over OpenCode's REST+SSE API, with an isolated runtime
  (never touches OpenCode Desktop's shared storage or credentials) and
  explicit, validated model pinning for every delegated call.
- **Attention system** (`app/attention_manager.py`,
  `app/interruption_policy.py`, `app/attention_scheduler.py`) — a full,
  deterministic state machine for "something needs the user," from
  first detection through deferred re-contact to resolution. No LLM
  involved in deciding whether or how urgently to interrupt you.
- **Voice sessions** (`app/voice_session_manager.py`) — lifecycle and
  state for a spoken conversation, including sessions Jarvis itself
  opens proactively. A thin pass-through to the same Supervisor logic
  typed text uses — no separate voice reasoning exists.
- **Browser client** (`app/static/`) — the current rich client: timeline,
  voice input/output, call-style attention UI, notifications, deep links.
- **Android companion spike** (`spikes/android-presence/`) — a
  disposable, real-device-validated technical spike proving a foreground
  presence service and wake-word feasibility; not production code.
- **Notifications** (`app/notifications.py`) — the single, deduplicated
  entry point for anything that becomes a user-visible notification.
- **Push** (`app/push.py`) — Web Push delivery (VAPID-based), with
  honestly-reported delivery certainty (never claims "delivered" when
  the platform can't confirm it).
- **PWA** (`app/static/manifest.json`, `app/static/sw.js`) — installable,
  offline-capable app shell, real HTTPS required for the underlying
  browser APIs.
- **Conversation management** (`app/database.py`, `conversation_id`
  handshake in `app/main.py`) — stable conversation identity across
  reconnects and turns.
- **Deterministic policies** (`app/attention_policy.py`,
  `app/interruption_policy.py`) — pure functions, no LLM, for anything
  safety-adjacent (should this notify, should this interrupt now).

## 3. Current Architecture

```
                 Laptop (the brain)
             Jarvis Supervisor + state
                    │
        ┌───────────┼────────────┐
        │           │            │
        ▼           ▼            ▼
   Browser PWA   Android      ESP Presence
  (implemented)  Companion    Device (future,
                 (spike only)  not built)
```

All reasoning, state machines, and persistence live on the laptop. Every
client — present or future — sends input and receives state; none of
them decide anything on Jarvis's behalf. See `ARCHITECTURE.md` for the
full component breakdown, state ownership tables, and the reasoning
behind this boundary.

## 4. Repository Layout

```
app/
  main.py                  FastAPI server, WebSocket endpoint, routing
  supervisor/               Conversational Supervisor, tool registry, LLM provider, project aliases
  integrations/             OpenCode adapter, server lifecycle, isolated runtime, SSE handling
  attention_manager.py      AttentionRequest state machine
  attention_policy.py       Deterministic notify-or-not policy
  attention_scheduler.py    DB-driven deferred re-contact scheduler
  interruption_policy.py    Deterministic interrupt-now-or-not policy
  voice_session_manager.py  Voice session lifecycle
  contact_channels.py       Contact channel abstraction (push, reserved future channels)
  notifications.py          Single notification-creation entry point
  push.py                   Web Push delivery
  deferral.py                Natural-language deferral parsing
  worker_events.py           Worker event normalization into attention
  task_manager.py             Local subprocess lifecycle
  connection_manager.py       WebSocket broadcast
  database.py                  SQLite schema, CRUD, reconciliation
  executor.py                  Slash-command routing
  protocol.py                  JARVIS_QUESTION: line parser
  workers/mock_worker.py        Deterministic mock interactive worker
  static/                       Browser client (HTML/CSS/JS, manifest, service worker)
docs/
  decisions/                 Architecture Decision Records (see §8)
  TECHNICAL_DEBT.md          Curated architectural debt register
  RELEASE_CHECKPOINT_M9B0.md Milestone 9B.0 close-out checkpoint
spikes/
  android-presence/          Disposable Android technical-risk spike (not production)
tests/                       pytest suite + gated real-integration/browser-validation scripts
scripts/                      Dev-cert and VAPID-key generation helpers
certs/                        Local HTTPS certificates (git-ignored, per machine)
```

## 5. Running Jarvis

```powershell
# 1. Create and activate a virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure .env (see §6 — at minimum, the LLM provider settings)

# 4. Start the server (plain HTTP — fine for basic text chat only)
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**Voice, push, and installing as a PWA all require a secure context**
(HTTPS, or `http://localhost`) — a plain `http://<laptop-ip>:8000` from a
phone will not unlock them:

```powershell
# Generate a local certificate (git-ignored, per machine) — prefer mkcert
# over the plain self-signed fallback to avoid a browser warning:
#   mkcert <laptop-ip> localhost 127.0.0.1
# or, without mkcert installed:
python scripts/generate_dev_cert.py

uvicorn app.main:app --host 0.0.0.0 --port 8443 --ssl-keyfile <key.pem> --ssl-certfile <cert.pem>
```

Then, from your phone's browser, visit `https://<laptop-ip>:8443` and
accept the certificate warning once (mkcert-issued certs won't show one).

**OpenCode**: Jarvis runs its own isolated `opencode serve` instance
automatically on startup — you do not need to start it yourself, and it
never touches OpenCode Desktop's shared storage or credentials (see
`ARCHITECTURE.md` §12, ADR-004). It needs a real OpenRouter API key (see
§6) to have any model to call.

**OpenRouter**: Jarvis's own conversational LLM and (by default)
delegated OpenCode work both use a free-tier OpenRouter model — see §6
for the exact variables and the free-only cost guard.

**Shutdown**: stop the `uvicorn` process normally (Ctrl+C or a graceful
kill). Jarvis's own owned OpenCode server is stopped cleanly as part of
its shutdown lifecycle. Avoid force-killing the Jarvis process itself —
a forced kill skips its own cleanup and can leave the isolated OpenCode
server (and, if you're mid-delegation, its subprocess) orphaned, requiring
manual cleanup (`netstat`/`taskkill` by the resolved PID — never by
process name, see `ARCHITECTURE.md` §16 ADR references).

## 6. Configuration

All configuration is via environment variables, typically in a `.env`
file at the repository root (loaded via `python-dotenv`).

### Required (for the Supervisor's conversational LLM to work at all)

| Variable | Purpose |
|---|---|
| `JARVIS_LLM_PROVIDER` | LLM provider identifier (e.g. `openrouter`) |
| `JARVIS_LLM_BASE_URL` | Base URL for the OpenAI-compatible chat completions API |
| `JARVIS_LLM_MODEL` | Model ID for the Supervisor's own conversational LLM |
| `JARVIS_LLM_API_KEY` | API key for the above |

### Strongly recommended

| Variable | Purpose |
|---|---|
| `JARVIS_LLM_FREE_ONLY` | `true`/`1`/`yes` enforces that `JARVIS_LLM_MODEL` must be `openrouter/free` or end in `:free` — the safety rail preventing accidental paid-model spend on the Supervisor's own LLM. Do not disable without a specific reason. |

### Optional

| Variable | Purpose |
|---|---|
| `JARVIS_VAPID_PUBLIC_KEY` / `JARVIS_VAPID_PRIVATE_KEY` / `JARVIS_VAPID_SUBJECT` | Web Push (generate via `scripts/generate_vapid_keys.py`); in-app/foreground notifications work without these |
| `JARVIS_API_TOKEN` | Opt-in bearer-token gate for push endpoints only — not a general authentication layer (see `ARCHITECTURE.md` §12, `docs/TECHNICAL_DEBT.md` TD-018) |
| `JARVIS_ATTENTION_RETRY_MINUTES` | Default re-contact retry interval for `InterruptionPolicy`; read once at process startup |
| `JARVIS_DEFAULT_SNOOZE_MINUTES` | Default snooze duration for vague deferral phrases ("later," "not now"); unset means Jarvis asks for clarification instead of guessing |
| `JARVIS_QUIET_HOURS` | Format `HH:MM-HH:MM` (server-local time, wraps past midnight), e.g. `22:00-07:00`; off by default |
| `JARVIS_NOTIFY_ON_COMPLETION` | Whether a completed task itself should notify (default: enabled) |
| `JARVIS_OPENCODE_PORT` | Port for the isolated OpenCode server (default `4097`) |
| `JARVIS_OPENCODE_RUNTIME_DIR` | Override the isolated OpenCode runtime directory (default `%LOCALAPPDATA%\JarvisOpenCodeRuntime`) |
| `JARVIS_OPENCODE_OPENROUTER_KEY` | Dedicated OpenRouter key for the isolated OpenCode runtime's own `auth.json`; falls back to `JARVIS_LLM_API_KEY` if unset |
| `JARVIS_OPENCODE_EXE` | Override the `opencode.exe` binary path |
| `JARVIS_OPENCODE_OWNER_MARKER` | Override the ownership-marker file path |
| `JARVIS_PROJECTS_FILE` | Override the safe-project-alias config file path (default `projects.json`) |
| `OPENCODE_SERVER_PASSWORD` / `OPENCODE_SERVER_USERNAME` | Basic auth credentials Jarvis uses against its own owned OpenCode server |

### Development

| Variable | Purpose |
|---|---|
| `JARVIS_TEST_MODE` | Set automatically by the test suite for faster mock-worker timing; not intended for manual use |
| `JARVIS_SUPERVISOR_ENABLED` | Set to anything other than `1` to disable the conversational Supervisor entirely (default: enabled) |

### Experimental / Temporary

| Variable | Purpose |
|---|---|
| **`JARVIS_OPENCODE_ALLOW_PAID`** | **Temporary development override, default `false`.** When `true`, permits *exactly one* explicitly-named paid model (`deepseek/deepseek-v4-flash` via `openrouter`) for delegated OpenCode worker sessions only — never the Supervisor's own LLM. Exists solely for sustained free-tier rate-limiting emergencies; requires deliberate, explicit re-approval each time it is enabled, and should be left `false` otherwise. See ADR-009 (`docs/decisions/ADR-009-cost-policy.md`). |
| `JARVIS_OPENCODE_MODEL_ID` / `JARVIS_OPENCODE_MODEL_PROVIDER_ID` | Override the default free model pinned for delegated OpenCode work; still validated free-only unless `JARVIS_OPENCODE_ALLOW_PAID` is also set. |

## 7. Testing

```powershell
pip install -r requirements.txt
pytest tests/ -v --tb=short
```

**Full automated suite**: **389 tests**, all passing as of Milestone
9B.0 (see `SESSION.md` Testing Status for the exact breakdown by
milestone). This is what CI/routine verification should run.

**Beyond `pytest tests/`**, several gated scripts require real
infrastructure and are run manually, not as part of routine testing:

| Script | Validates |
|---|---|
| `tests/m6_execution_probe.py` | Real OpenCode task execution against the actual isolated runtime — proves a delegated task genuinely executes work (filesystem evidence, exact content match), not just that an API call returned 200 |
| `tests/m7_browser_validate.py` | Real Playwright DOM validation of voice/TTS/notifications/deep-links/reconnect against a real running server |
| `tests/m8_browser_validate.py` | Real Playwright DOM validation of the attention lifecycle and voice-session UI |
| `tests/m5_validate2.py`, `tests/m5_scenarios.py` | Real end-to-end WebSocket scenarios against a real OpenCode server and a real free-tier LLM |
| `tests/browser_smoke.py` | Original Milestone-3-level browser smoke test |

**Real-phone validation** is not a script — it is manual, physical-device
testing (currently a Samsung Galaxy SM-G781B / Galaxy S20 FE, Android 13)
documented narratively in `SESSION.md` per milestone. This is how voice
accuracy, PWA installability, push delivery, and (Milestone 9B.0) native
foreground-service survival have all been verified — no simulated or
emulator-only result is trusted for these specific claims (see
`ARCHITECTURE.md` §14, ADR-010).

## 8. Documentation Map

| Document | Purpose |
|---|---|
| **README.md** (this file) | First read. What Jarvis is, how to run and test it, where to go next. |
| **`ARCHITECTURE.md`** | The authoritative architecture specification — *why* Jarvis is built this way: design principles, component ownership, state ownership, security model, failure philosophy. Read this before making any non-trivial change. |
| **`SESSION.md`** | Chronological engineering log — what happened, milestone by milestone, including every real bug found and fixed, every real-device test result, and the full historical Known Bugs/Limitations list. |
| **`docs/decisions/`** | Architecture Decision Records — the reasoning behind the load-bearing decisions, with genuine alternatives considered and honest tradeoffs. Start with `docs/decisions/README.md` for the system itself. |
| **`docs/TECHNICAL_DEBT.md`** | Curated register of structural debt requiring deliberate future scheduling — not a bug list. |
| **`docs/RELEASE_CHECKPOINT_M9B0.md`** | Milestone 9B.0 close-out: architecture freeze status, test results, real-device validation summary, risk assessment. |

## 9. Current Status

**Implemented and real-device-validated**: WebSocket conversation
interface; local task supervision; OpenCode integration (isolated
storage, isolated credentials, explicit model pinning); conversational
Supervisor with bounded tool-call loop; the full attention lifecycle
(`AttentionRequest`/`ContactAttempt`/`Notification`/`VoiceSession`,
deterministic policies, DB-driven scheduling); voice input/output on the
browser; Web Push (foreground/recently-backgrounded delivery); installed
PWA; deep linking; natural-language deferral parsing.

**Experimental / spiked, not production**: the Android presence
companion (`spikes/android-presence/`) — real physical-device evidence
exists for foreground-service survival and a real connection to the real
Jarvis server, but the code is explicitly classified disposable, not a
foundation to build on directly. No wake-word engine is integrated yet
(microWakeWord is the evidence-based recommendation, pending its own
build spike).

**Planned, not started**: production Android companion implementation
(Milestone 9B); a `VoiceSession` multi-client ownership guard (designed,
required before a second simultaneous voice-capable client can safely
connect); a separate `permissions` table; database table rotation.

**Not implemented, by design**: `NATIVE_ANDROID`/`PHONE_CALL`/`SMS`
contact channels are reserved names that intentionally raise rather than
silently no-op.

## 10. Known Limitations

The major, currently-active ones:

- **No authentication or TLS by default** on the WebSocket — anyone on
  the LAN can connect. Accepted under the current LAN-only, single-user
  deployment model; would need addressing before any remote-access
  scenario.
- **Background (Doze-idle) push delivery is unreliable** on real tested
  Android hardware, despite every standard mitigation — a platform
  limitation, not a Jarvis defect. Foreground/recently-active delivery
  works.
- **No remote/non-LAN connectivity story** — a phone leaving the laptop's
  Wi-Fi network loses connectivity entirely, with no fallback (tracked
  as "Transport Reachability").
- **iOS Safari is entirely untested** — only Android has real-device
  validation.
- **Unbounded growth** on several SQLite tables — no rotation or
  archiving exists yet.

For the complete, itemized list (56+ entries as of Milestone 9B.0), see
`SESSION.md`'s "Known Bugs, Limitations, and Technical Debt" section. For
the curated architectural subset with severity/ownership/risk, see
`docs/TECHNICAL_DEBT.md`.

## 11. Roadmap

**Current**: Milestone 9B.1 — not yet started. Architecture is frozen
(`ARCHITECTURE.md`, `docs/decisions/`); this milestone is expected to
begin production work on whichever of the pre-conditions (the
`VoiceSession` ownership guard, or the wake-word build spike) is
prioritized first.

**Future**: **M10** — carried-over cleanup items (separate permissions
table, table rotation, further real-device platform validation) as they
become load-bearing. **M10A — Remote Reachability Research** — the
Transport Reachability gap, deliberately not investigated before now.
**M11** — a future ESP32 presence device, once the Android companion has
proven out the presence/transport/attention model on a second real
device.

No further milestone history here — see `SESSION.md` for that.

## 12. Contributing

- **Architecture first.** Read `ARCHITECTURE.md` and the relevant ADRs in
  `docs/decisions/` before making a change that affects component
  ownership, state ownership, or a client/brain boundary. If your change
  reverses or contradicts an existing ADR, that decision needs revisiting
  explicitly, not silently overridden.
- **Evidence over assumptions.** Do not claim something works, survives,
  or completes without the specific evidence that would let someone else
  independently check it — an HTTP 200, elapsed silence, or confident-
  sounding prose is never sufficient proof on its own (see
  `ARCHITECTURE.md` §14, ADR-010).
- **Do not claim PASS without verification.** Real-device claims need
  real-device evidence; a delegated agent's own report of its work is
  independently spot-checked, not trusted automatically.
- **Update the architecture docs alongside any architecturally
  significant change** — a new ADR for a new load-bearing decision, an
  `ARCHITECTURE.md` update if a component's ownership or boundary
  changes, and a `SESSION.md` entry either way.
