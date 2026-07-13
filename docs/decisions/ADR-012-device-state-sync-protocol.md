# ADR-012: Device State-Sync and Capability-Advertisement Protocol

## Status

Accepted

## Date

2026-07-13 (Milestone 9B.2 — Persistent Presence and Communication Layer)

## Context

Milestone 9B.1 built a production Android companion that connects, pairs,
and reconnects, but the server has no visibility into *what kind* of
client just connected, or what it can do — every WebSocket client looks
identical to `ConnectionManager`. Milestone 9B.2's stated goal is to make
the companion "a robust, long-running communication endpoint" and
explicitly names "capability advertisement" and "state synchronization"
as required deliverables, framed as groundwork for a later
multi-device-routing decision (not built yet, and not this ADR's concern).

Separately, Milestone 9B.1's real-device validation (item 5B) found that
`CompanionWebSocketClient` retried forever against a permanently-failing
certificate mismatch (TD-021) — the connection state machine had no way
to distinguish "will succeed if I just wait" from "will never succeed
without user action."

## Problem

How does the Android companion tell the server what it is and what it
supports, in a way that (a) requires no new server-side reasoning or
persistence, (b) doesn't fork the existing `/ws` JSON protocol's
conventions, and (c) closes TD-021 by giving the client's own connection
state machine a principled way to stop retrying when retrying cannot
help?

## Decision

**A single `device_status` JSON message, sent once per successful
(re)connect, stored in-memory only per-connection on the server; and a
`DisconnectReason` classification that marks certain reasons as
permanent, moving the client to a distinct `FAILED_PERMANENT` state
instead of continuing to back off.**

- `core.DeviceStatus` (Android): `device_id`, `capabilities` (`voice`,
  `wakeword`, `widget`, `notifications`, `foreground_service` — all
  booleans, fixed at build time via `DeviceCapabilities.CURRENT`),
  `pairing_state`, `battery_optimization_exempt`,
  `notification_permission_granted`, `connection_generation`. Hand-built
  JSON (matching the existing heartbeat frame's style), not
  `org.json.JSONObject` — every field is a boolean/int/UUID-backed string,
  so general string escaping was never actually needed, and this sidesteps
  `org.json` being Android-stubbed (not the real implementation) in local
  JVM unit tests.
- `app/main.py`'s `/ws` loop accepts `{"type": "device_status", ...}` and
  stores it via a new `ConnectionManager.set_device_status(ws, data)` —
  the exact same per-connection dict pattern already used for connection
  IDs (`_ids`). No new DB table, no reasoning about the contents, no reply
  sent. This is a communication primitive (the milestone's own framing),
  not routing logic — nothing reads this data yet.
- `network.DisconnectReason` (Android): `NETWORK`, `AUTH`, `CERTIFICATE`,
  `SERVER_DOWN`, `TOKEN_EXPIRED`, `USER_STOPPED`, `UNKNOWN`, `NONE`, each
  produced by `DisconnectClassifier` from an actual observed signal (WS
  close code, HTTP status, or `Throwable` type/message) — never guessed.
  `AUTH`, `CERTIFICATE`, and `TOKEN_EXPIRED` are `isPermanent`;
  `CompanionWebSocketClient` stops scheduling a reconnect for these and
  transitions to `ConnectionState.FAILED_PERMANENT` instead (TD-021 fix).
- A real, load-bearing finding during this design: `app/main.py`'s
  pre-accept `ws.close(code=1008)` does **not** actually send that close
  code over the wire — uvicorn's ASGI websocket implementation discards
  it and always rejects with a plain HTTP 403 before the handshake
  completes (confirmed by reading uvicorn's source, not assumed).
  `DisconnectClassifier` checks for HTTP 403/401 *and* WS close code 1008,
  so it works correctly against both the real current behavior and any
  future server change that sends a genuine close frame.

## Alternatives Considered

**Persist device status to a new database table.** Rejected: nothing
reads this data yet (no multi-device routing exists), and per-connection
in-memory state is the correct durability level for "what is this live
connection" — a DB table would imply a durability guarantee (survives
server restart, queryable history) this milestone doesn't need and adds
schema/migration surface for a feature not yet built. Revisit when
multi-device routing is actually designed.

**A REST endpoint for capability reporting instead of a WS message.**
Rejected: capability/state can change per-connection (e.g. reconnecting
after granting battery-exemption), and the WS connection is already the
one channel guaranteed to exist exactly when a device is live — a
separate REST call adds a second round-trip and a correlation problem
(which connection does this REST call's device belong to?) that sending
it over the WS connection itself doesn't have.

**Use `org.json.JSONObject` for serialization.** Rejected: `org.json.*` is
part of the Android SDK, which unit tests only get a stub of (throws or
returns defaults) unless a real implementation is added as a test
dependency. `DeviceStatus`'s fields have no free-text/user-controlled
content requiring general JSON escaping, so hand-built string
concatenation (with `\`/`"` escaping, defensively, even though `deviceId`
is always a UUID today) is simpler, dependency-free, and trivially
unit-testable with exact-string assertions.

**Treat every disconnect reason as retryable (status quo before this
ADR).** Rejected: directly caused TD-021 — a real, observed bug where the
companion retried forever against a certificate the pairing was
deliberately re-confirmed to reject.

## Consequences

The server now has a place to look (`ConnectionManager._device_status`)
for what a connected device is and can do, without yet acting on it —
laying the groundwork the milestone asked for without building routing
logic prematurely. The Android client's reconnect loop is now correct
for the TD-021 case: a permanently-failing condition surfaces as
`FAILED_PERMANENT` (UI-visible: "re-pairing required") rather than
looping forever.

## Positive Outcomes

- TD-021 is closed with a real fix, not just documented as a known gap.
- `DisconnectClassifier` is fully unit-tested (11 tests) against real
  exception types/messages actually observed on real hardware (Milestone
  9B.1's Wi-Fi-drop `SocketException: Software caused connection abort`,
  the real `SSLHandshakeException` fingerprint-mismatch message).
- The uvicorn HTTP-403-not-close-code-1008 finding is a genuine
  correction to Milestone 9B.1's own (incorrect) assumption about how its
  auth rejection reaches the client — caught by reading the actual
  library source rather than re-asserting the earlier comment.

## Tradeoffs

- `device_status` is fire-and-forget — the server never confirms receipt,
  and a device has no way to know if its status was actually stored (only
  that the `send()` call queued successfully, which OkHttp's WebSocket API
  cannot distinguish from "sent and silently dropped server-side"). This
  mirrors the heartbeat frame's existing tradeoff and doesn't matter yet
  because nothing depends on this data being received.
- In-memory-only storage means device status is lost on every server
  restart and never available to a second process — an accepted
  limitation given nothing reads it yet, but real if multi-device routing
  arrives before this is revisited.

## Future Revisit Conditions

Revisit persistence (in-memory → DB or shared cache) when multi-device
routing is actually designed and needs to survive a server restart or be
visible across processes. Revisit `TOKEN_EXPIRED`'s current
never-actually-produced status if `JARVIS_API_TOKEN` ever gains a real
expiry mechanism (today's binary Bearer-token check cannot distinguish
"wrong" from "expired").

## References

- `ARCHITECTURE.md` §9 (Android Companion)
- `SESSION.md`, Milestone 9B.1 (TD-021 found), Milestone 9B.2 (this
  decision)
- `docs/TECHNICAL_DEBT.md` TD-021 (closed by this ADR)
- ADR-010 (Evidence-Based Engineering — the uvicorn source-reading
  finding), ADR-011 (Android Companion Pairing Security Model — the
  certificate-mismatch condition this ADR's `CERTIFICATE` reason
  classifies)

## Related Milestones

Milestone 9B.1, Milestone 9B.2

## Related Source Files

- `android/app/src/main/java/com/jarvis/companion/network/`
  (`DisconnectReason.kt`, `DisconnectClassifier.kt`,
  `CompanionWebSocketClient.kt`)
- `android/app/src/main/java/com/jarvis/companion/core/`
  (`DeviceStatus.kt`, `DeviceCapabilities.kt`, `ConnectionState.kt`)
- `app/connection_manager.py` (`set_device_status`, `get_device_status`)
- `app/main.py` (`device_status` handler, `_websocket_authorized` comment)
- `android/app/src/test/java/com/jarvis/companion/network/DisconnectClassifierTest.kt`
- `android/app/src/test/java/com/jarvis/companion/core/DeviceStatusTest.kt`
- `tests/test_connection_manager.py`, `tests/test_device_status.py`
