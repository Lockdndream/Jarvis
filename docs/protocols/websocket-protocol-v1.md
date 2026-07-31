# Jarvis WebSocket Protocol — v1

This is the wire-level reference for `/ws`, the one real-time channel
every Jarvis client (browser PWA, Android companion, and — per ADR-014 —
any future client) connects through. It complements, not replaces,
`ARCHITECTURE.md` (which explains *why* the system is shaped this way) and
the ADRs (which capture individual decisions) — this document is
specifically "what goes over the wire."

**v1** covers the protocol as of Milestone 9B.2. There is no v0 document;
this is the first time the wire format has been written down in one place
rather than left implicit in `app/main.py` and each client's own code.

---

## 1. Connecting

```
wss://<host>:<port>/ws?token=<signed-token>
```

TLS is required (`wss://`, never `ws://` — see ARCHITECTURE.md §12).
Certificate trust is out of this protocol's scope: the Android companion
uses trust-on-first-use pinning (ADR-011); a browser relies on normal
system/browser TLS trust, which is why the server's certificate must be
one the browser already trusts (e.g. via mkcert's root CA, `certs/`).

### 1.1 Authentication (ADR-014)

Two mechanisms exist. **Every client should use §1.1.1** — §1.1.2 exists
only for already-paired clients that haven't migrated yet, and is
deprecated.

#### 1.1.1 Signed token (current, required for new clients)

1. `POST /api/ws-token`, JSON body `{"client_id": "<string, optional>"}`.
   If the server has `JARVIS_API_TOKEN` configured, this call itself
   requires `Authorization: Bearer <JARVIS_API_TOKEN>`; if unset, no
   extra header is needed. `client_id` becomes the token's `sub` claim —
   informational only, nothing server-side makes an authorization
   decision based on it. If omitted, the server generates one.
2. Response: `{"token": "<JWT>", "expires_at": <unix epoch seconds>, "expires_in": <seconds>}`.
   Default lifetime is 900 seconds (15 minutes — see ADR-014 for why),
   configurable server-side via `JARVIS_WS_TOKEN_TTL_SECONDS`.
3. Connect to `/ws?token=<the JWT>`, URL-encoded.
4. Before the token expires, repeat step 1–3 for the *next* connection
   attempt — a client should treat "less than ~2 minutes remaining"
   (this project's own clients use a 120-second safety margin) as reason
   to fetch a fresh token rather than risk connecting with one that
   expires mid-handshake.

The token is a JWT (HS256), claims exactly `sub`/`iat`/`exp` — no
capability or device-state information (that travels separately, see
§2.1). Server-signed with `JARVIS_WS_TOKEN_SECRET` if set, otherwise a
random secret generated once per server-process lifetime (tokens don't
need to survive a server restart, because WebSocket connections don't
either).

#### 1.1.2 Legacy Authorization header (deprecated, still supported)

`Authorization: Bearer <JARVIS_API_TOKEN>` as a WebSocket handshake
header. **Browsers cannot do this** — the JavaScript `WebSocket`
constructor has no way to set custom headers — which is the entire
reason §1.1.1 exists. Only usable by native clients (Android). Checked
only when no `?token=` query parameter is present at all.

#### 1.1.3 No authentication (default deployment)

If `JARVIS_API_TOKEN` is unset (the project's current default — TD-018),
both mechanisms above are no-ops and any client on the LAN may connect.
A client that *does* present a valid `?token=`, even in this mode, still
gets real verification — the token path never silently passes through
unchecked.

### 1.2 Rejection — WebSocket close codes

Unlike a normal HTTP rejection, the server always completes the
WebSocket handshake (`accept()`) before closing with one of these codes
— sending a close code before `accept()` was found (ADR-012) to be
silently discarded by the ASGI server, always producing a bare HTTP 403
instead, which cannot carry a specific reason.

| Code | Meaning | Client should |
|---|---|---|
| `4001` | `TOKEN_EXPIRED` — token's signature is valid but `exp` has passed | Fetch a fresh token (§1.1.1) and reconnect. Not a permanent failure. |
| `4002` | `TOKEN_INVALID` — bad signature, malformed, or tampered token | Fetch a fresh token and reconnect. Not a permanent failure — the pairing credential itself may still be valid. |
| `4003` | No credential presented at all (`JARVIS_API_TOKEN` is set, no `?token=` and no `Authorization` header) | Permanent until re-paired/reconfigured — this is a config problem, not something a retry fixes. |
| `4004` | `Authorization` header present but wrong (legacy path only) | Permanent until re-paired. |

A connection that succeeds sends no special frame — the client just
starts receiving the messages in §2.2.

---

## 2. Messages

All frames are JSON text frames, `{"type": "...", ...}`. A message with
an unrecognized `type` is silently ignored server-side — this is by
design (e.g. the app-level heartbeat frame below), not an error.

### 2.1 Client → Server

| Type | Fields | Purpose |
|---|---|---|
| `conversation_init` | `conversation_id` (nullable) | Establish or resume a conversation identity (Milestone 6 Phase 7). |
| `user_message` | `content`, `conversation_id`, `bound_attention_request_id` (optional) | A typed/spoken message to the Supervisor. |
| `voice_session_open` | `conversation_id` (optional), `attention_request_id` (optional) | Begin a voice session (Milestone 8). |
| `voice_session_transcript` | `voice_session_id`, `transcript` | A recognized utterance within an open voice session. |
| `voice_session_audio` | `voice_session_id` | **Text frame** (header only). Signals the client is about to send the raw audio bytes for [voice_session_id] as the immediate next binary frame on this same connection. Audio format is WAV (RIFF/WAVE), 16kHz mono 16-bit PCM. |
| `voice_session_close` | `voice_session_id` | End a voice session. |
| `*(binary frame)*` | *(n/a — raw bytes)* | **Binary frame** immediately following a `voice_session_audio` text frame. Contains the full utterance's audio as a WAV file (44-byte RIFF/WAVE header + PCM data). No JSON framing — the preceding `voice_session_audio` header carries the session association. |
| `device_status` | `device_id`, `capabilities`, `pairing_state`, `battery_optimization_exempt`, `notification_permission_granted`, `connection_generation` | Capability advertisement / state sync (ADR-012). Fire-and-forget, no reply. Sent once per successful (re)connect by the Android companion. In-memory only server-side, not yet read by anything (groundwork for future multi-device routing). |
| `heartbeat` | *(none)* | App-level liveness signal from the Android companion (`{"type":"heartbeat"}`), sent every 30s while connected. The server does not act on it — it exists for the *client's* own telemetry (proving the send queued successfully) and is otherwise a no-op frame, matching this protocol's "don't invent server-side handling without a demonstrated need" discipline (Milestone 9B.0). |
| `permission_response` | `attention_request_id`, `decision` (`"approve"` \| `"reject"`) | **Interaction Layer v1.** Structured phone-to-server permission resolution for a specific AttentionRequest. Reuses the existing `_resolve_bound_command` path — the `decision` string ("approve" or "reject") is routed as `user_message` with `bound_attention_request_id` set, flowing into the same grammar (PERMISSION attention_type) that voice transcripts already use. Server replies with `permission_response_ack`. This is the missing structured response for `attention_created` (PERMISSION), which already broadcasts to the phone with `attention_request_id` — no separate `permission_request` push type is needed.

### 2.2 Server → Client

Sent automatically on connect, in this order: `history`, `running_tasks`
(if any), `pending_questions` (if any), `pending_notifications` (if any),
`pending_attention` (if any), `opencode_status`.

Sent in response to client messages or server-side events:
`conversation_ready`, `voice_session_opened`, `voice_session_response`,
`voice_session_error`, `voice_session_closed`, `user_message` (echo),
`supervisor_thinking`, `supervisor_message`, plus the various
`attention_*`/`notification`/`opencode_task_*` broadcast events described
in `ARCHITECTURE.md` (not re-enumerated here — those are business-logic
events, not part of the connection/auth protocol this document covers).

#### 2.2.2 New Server → Client messages (Interaction Layer v1)

These are additive Interaction Layer message types, documented here
together rather than spreading across §2.2's existing entries.

| Type | Direction | Fields | Description |
|---|---|---|---|
| `conversation_turn` | S → C | `role` (always `"user"` for v1), `content`, `voice_session_id`, `conversation_id`, `timestamp` | Emitted between STT resolution and supervisor invocation for a voice turn, so the phone can show "here's what Jarvis heard" before the answer arrives. The `conversation_id` may be `null` on the first turn of a session before `_process_transcript` assigns one (known v1 limitation). An assistant-role variant is not needed — `voice_session_response` already carries the assistant's text and already reaches the phone. |
| `thinking_update` | S → C | `action` (`"tool_call"`), `status` (`"started"` \| `"completed"`), `summary`, `detail` (nullable, short human-readable result synopsis on completion), `conversation_id`, `trace_id`, `timestamp` | Emitted for each tool call the supervisor executes, alongside the existing dashboard-only `supervisor_tool_call` broadcast. On `"started"`, `detail` is `null`. On `"completed"`, `detail` is a bounded (≤200 char) human-legible synopsis derived from the tool result — never raw `args` or a full untruncated result. No `thinking_update` is sent for plan-step-level activity (out of scope for v1). |
| `permission_response_ack` | S → C | `attention_request_id`, `response`, `conversation_id` | Sent in reply to a `permission_response` after the supervisor resolves the permission. Reuses `_resolve_bound_command` — the `response` field carries its result text. |

> **Deliberate omission:** no new `permission_request` push type was added.
> `attention_created` (ATTENTION_TYPE=PERMISSION) already broadcasts to the
> phone with `attention_request_id` and a lock-screen-safe `summary` whenever
> a PERMISSION-type AttentionRequest is created. Adding a second,
> differently-shaped push for the same event would put two representations
> of one event on the wire.

#### 2.2.1 `trace_id` (ADR-020, additive)

`voice_session_response` and `supervisor_message` each carry a `trace_id`
field: the server-minted correlation id for the turn that produced this
response, or `null` if the reply never reached a full Supervisor turn
(e.g. a stale-attention early reply). No client currently reads this
field — it exists so a future client can correlate its own local state
with the server's execution record for that turn (Owner Experience
Milestone 1). Not currently accepted from the client on any inbound
message; see ADR-020 for why `client_request_id` (§2.1's
`voice_session_open`, unchanged here) was deliberately not extended to
every message type yet.

---

## 3. What this document does not cover

- Application-level meaning of `AttentionRequest`/`VoiceSession`/task
  state machines — see `ARCHITECTURE.md` §5–8.
- REST endpoints other than `/api/ws-token` (push subscription, settings,
  deep-link resolution) — see `app/main.py` directly, they're simple.
- Capability semantics inside `device_status` — see ADR-012.

## References

- ADR-011 (Android Companion Pairing Security Model)
- ADR-012 (Device State-Sync and Capability-Advertisement Protocol)
- ADR-014 (Unified WebSocket Authentication)
- `app/main.py` (`websocket_endpoint`, `_resolve_ws_close_code`,
  `issue_ws_token_endpoint`)
- `app/integrations/ws_tokens.py`
