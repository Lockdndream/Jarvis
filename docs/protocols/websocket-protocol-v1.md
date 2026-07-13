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
| `voice_session_close` | `voice_session_id` | End a voice session. |
| `device_status` | `device_id`, `capabilities`, `pairing_state`, `battery_optimization_exempt`, `notification_permission_granted`, `connection_generation` | Capability advertisement / state sync (ADR-012). Fire-and-forget, no reply. Sent once per successful (re)connect by the Android companion. In-memory only server-side, not yet read by anything (groundwork for future multi-device routing). |
| `heartbeat` | *(none)* | App-level liveness signal from the Android companion (`{"type":"heartbeat"}`), sent every 30s while connected. The server does not act on it — it exists for the *client's* own telemetry (proving the send queued successfully) and is otherwise a no-op frame, matching this protocol's "don't invent server-side handling without a demonstrated need" discipline (Milestone 9B.0). |

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
