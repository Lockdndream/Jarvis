# ADR-014: Unified WebSocket Authentication (Short-Lived Signed Tokens)

## Status

Accepted

## Date

2026-07-13 (Milestone 9B.2 — final infrastructure task)

## Context

ADR-011 gave the Android companion a real `/ws` authentication path
(`Authorization: Bearer <JARVIS_API_TOKEN>`), but explicitly disclosed
that this cannot work for the browser PWA — the JavaScript `WebSocket`
API cannot set arbitrary handshake headers, only `Authorization` on
regular HTTP requests. That gap was tracked as part of TD-018 and named
in ADR-011's own Future Revisit Conditions. Milestone 9B.1's own Phase 2
design comparison evaluated three options (Authorization header, secure
cookie, short-lived signed token) and recommended the token approach
(Option C) as the only one that works uniformly across browser, Android,
and future ESP32 clients without the WebSocket-hijacking-adjacent CSRF
risk cookies carry. That recommendation is now accepted and implemented.

## Problem

How does every client type — browser, Android, and a future ESP32 device
— authenticate a `/ws` connection using one consistent mechanism, given a
browser cannot set custom WebSocket handshake headers, without weakening
the security this project already has (TOFU certificate pinning,
HTTPS-only, the existing `JARVIS_API_TOKEN` shared secret)?

## Decision

**A short-lived, server-signed JWT (HS256), fetched via an authenticated
REST call and passed as a `?token=` WebSocket URL query parameter,
verified server-side before `ws.accept()` completes — with the existing
Authorization-header path kept as a deprecated, still-supported migration
path.**

- `app/integrations/ws_tokens.py`: `issue_ws_token(subject, ttl_seconds)`
  / `verify_ws_token(token)`, HS256 only (no `alg: none`, no asymmetric
  algorithm — both are known JWT implementation pitfalls this explicitly
  avoids). Claims are exactly `sub`/`iat`/`exp` — no capability info (that
  already exists separately via `device_status`, ADR-012, and would be
  redundant here). Signing secret: `JARVIS_WS_TOKEN_SECRET` if set, else a
  process-lifetime-only random secret generated once at import — tokens
  don't need to survive a server restart, because WebSocket connections
  don't either.
- **Token lifetime: 900 seconds (15 minutes) by default**
  (`JARVIS_WS_TOKEN_TTL_SECONDS`, configurable). Justification: short
  enough to bound real exposure if a token leaks into a log line or
  browser history; long enough that this project's own reconnect backoff
  (`BackoffPolicy`, capped at 60 seconds between attempts) doesn't need a
  fresh REST round-trip on every single reconnect — only roughly every 15
  minutes of connectivity does.
- `POST /api/ws-token` (protected by the existing `_require_api_token`
  dependency — no-op when `JARVIS_API_TOKEN` is unset, a real check when
  set, unchanged gate from ADR-011) issues a token. Accepts an optional
  `client_id` in the request body (Android sends its stable
  `DeviceIdentity`; a browser can omit it and receive a server-generated
  one) as the `sub` claim — informational only, nothing server-side makes
  an authorization decision based on its value today.
- `/ws`'s handshake check: if a `?token=` query param is present, it is
  **always** verified via `verify_ws_token()` regardless of whether
  `JARVIS_API_TOKEN` is set — a client that bothered to fetch a real
  signed token gets real signature/expiry verification either way, not a
  silent pass-through. Only when no token param is present does the
  handshake fall back to the deprecated Authorization-header check
  (unchanged behavior from ADR-011, itself a no-op when
  `JARVIS_API_TOKEN` is unset).
- **Close codes**, sent after `ws.accept()` — not before. ADR-012 already
  found that a pre-accept `ws.close()` discards its `code` argument
  entirely (uvicorn always sends a bare HTTP 403). Sending the rejection
  *after* accepting means a real WebSocket close frame is sent, letting
  the client's `onClosed(code, ...)` distinguish the reason directly,
  which the required `TOKEN_EXPIRED`/`TOKEN_INVALID`/`TOKEN_MISSING`/
  `AUTH_FAILED` taxonomy needs and a bare HTTP 403 cannot provide:
  - `4001` — `TOKEN_EXPIRED` (signature valid, `exp` has passed)
  - `4002` — `TOKEN_INVALID` (bad signature, malformed, tampered payload)
  - `4003` — `TOKEN_MISSING` (no token param and no Authorization header)
  - `4004` — `AUTH_FAILED` (Authorization header present but wrong —
    the legacy path's existing failure mode)
  Codes 4000–4999 are reserved for application use by RFC 6455.
- Android's `DisconnectReason`/`DisconnectClassifier` (ADR-012) map these
  close codes directly. `TOKEN_EXPIRED` and `TOKEN_INVALID` are **not**
  `isPermanent` — unlike `AUTH`/`CERTIFICATE`, a client that can just
  fetch a fresh token doesn't need a human to re-pair; the client
  transparently refreshes the token and retries. `TOKEN_MISSING` and
  `AUTH_FAILED` remain permanent (something is wrong with the pairing
  config itself, not just an expired credential).
- **Migration**: the Authorization-header path is kept, working, and
  explicitly marked deprecated (a warning-level server log line on use).
  Not removed — Android's already-paired installs and any other existing
  client keep working unmodified until they're updated to fetch tokens.
- Certificate pinning and HTTPS-only operation are unaffected — the token
  fetch itself is a regular HTTPS POST to the same pinned server,
  verified by the same `PinnedTrustManager` (Android) / browser TLS trust
  the WebSocket connection itself already relies on. This ADR adds a
  credential type, not a new trust boundary.

## Alternatives Considered

**Secure cookie (Option B).** Rejected in the original Phase 2 comparison
and reaffirmed here: a browser automatically attaches cookies to *any*
WebSocket handshake to the same origin, including one initiated by a
malicious page the user's browser happens to have open — a real,
documented WebSocket-hijacking-via-cookie risk class that a header or
query-param token doesn't have (a hostile page can trigger a same-origin
WebSocket connection, but it cannot read or forge this app's signed
token, which lives in JS-managed state, not an ambient browser-attached
credential).

**Keep the Authorization-header path as the only mechanism (status quo).**
Rejected: this is the exact asymmetry this ADR closes — it is
structurally impossible for a browser to do, not merely inconvenient.

**Encode capability/device info into the token's claims.** Rejected:
`device_status` (ADR-012) already carries this, sent over the now-open
WebSocket connection itself; duplicating it into the token would be two
sources of truth for the same information and a larger, more
attack-surface-relevant token payload than necessary ("do not include
unnecessary information," per this milestone's own requirements).

**A longer token lifetime (e.g. hours) to minimize REST round-trips.**
Rejected: this project's real reconnect cadence (heartbeat every 30s,
backoff capped at 60s) means a 15-minute token already avoids a
round-trip on nearly every reconnect; a much longer lifetime would trade
away the "short-lived" security property for a benefit (fewer REST calls)
that 15 minutes already mostly captures.

**Remove the Authorization-header path immediately instead of
deprecating it.** Rejected, explicitly, by this task's own requirement to
provide a migration path — removing a working credential path the moment
a new one lands is unnecessary churn for zero security benefit (both
paths are equally strong when `JARVIS_API_TOKEN` is set; the new path's
advantage is *browser reachability*, not *strength*).

## Consequences

Every client type now has exactly one way to authenticate a `/ws`
connection that actually works for it: fetch a token via `/api/ws-token`,
connect with `?token=`. The browser PWA gains the auth path it
structurally could never have had via headers. Android migrates to the
same mechanism, with its existing Authorization-header path staying as a
working fallback during the transition. A future ESP32 companion can use
the identical REST-then-query-param flow — no browser-specific
workaround needed for it, since ESP32 HTTP/WS client libraries handle
custom query parameters as easily as headers.

## Positive Outcomes

- Real, end-to-end browser validation (Playwright, real Jarvis server,
  real Chrome): a fresh page load fetches a real signed token via
  `POST /api/ws-token`, connects `wss://.../ws?token=...`, and the
  server's own log independently confirms the *exact same* JWT was
  accepted (`WebSocket ... [accepted]`, `WebSocket client connected`) —
  not inferred from the browser side alone. A same-page reconnect (`ws.close()`
  + the 3s timer) reused the cached token with zero new
  `POST /api/ws-token` calls, confirming the caching/reuse logic actually
  works, not just reads correctly in the source.
- The independent Reviewer of the `main.py` auth-handshake change found
  no bypass, no information disclosure, and confirmed the accept-then-close
  pattern has no exploitable race — and one of its specific claims (that
  Starlette's `QueryParams.get()` on a duplicate `?token=` returns the
  *first* value) was independently tested and found to actually return
  the *last* value. The overall "no bypass" conclusion held either way
  (both duplicate values still go through full verification), but this is
  recorded as a concrete example of why every review finding gets
  independently checked rather than accepted at face value (ADR-010).
- Delegated Android work (`WsTokenClient`) shipped with a real compile
  error (`assertThrows` cannot wrap a suspend call) that would have made
  its entire test file silently never run, plus two real robustness gaps
  (`invalidateCache()` bypassing the cache's concurrency protection; a
  malformed-but-200-OK response crashing with an uncaught exception
  instead of a clean error) — all three found by direct compilation and
  code review, not by trusting the Builder's own report, and fixed with
  regression tests added for each.

Full evidence trail: Milestone 9B.2's SESSION.md entry.

## Tradeoffs

- The signing secret is process-lifetime-only by default (regenerated on
  every server restart) — every already-connected client's *next*
  reconnect needs a fresh token, which is free (they're reconnecting
  anyway after a restart severs the TCP connection) but means a
  long-lived, persistent secret is opt-in (`JARVIS_WS_TOKEN_SECRET`), not
  the default.
- The PWA's `/api/ws-token` call is unauthenticated whenever
  `JARVIS_API_TOKEN` is unset (today's actual default deployment) —
  identical trust level to the WebSocket connection itself under that
  same default, not a new gap. If an operator sets `JARVIS_API_TOKEN`,
  the PWA has no credential-storage UI to present it with (unchanged
  limitation from TD-018) — this ADR closes the *mechanism* asymmetry
  (browsers can now use tokens at all), not the *credential-entry* gap,
  which remains open and disclosed.

## Future Revisit Conditions

Revisit the PWA credential-entry gap (above) if `JARVIS_API_TOKEN` is
ever actually enabled in a real deployment using both the PWA and the
Android companion. Revisit token replay protection (currently: expiry is
the only defense — a captured, unexpired token is usable until it
expires, same as any bearer credential) if a threat model beyond
"trusted LAN, single operator" is ever adopted.

## References

- `ARCHITECTURE.md` §12 (Security Model)
- `SESSION.md`, Milestone 9B.1 (auth asymmetry found, Phase 2 design
  comparison — Option C recommended), Milestone 9B.2 (ADR-012's uvicorn
  pre-accept-close finding this ADR reuses; this decision)
- `docs/TECHNICAL_DEBT.md` TD-018 (PWA/companion token asymmetry —
  mechanism gap closed by this ADR; credential-entry gap remains)
- ADR-011 (Android Companion Pairing Security Model — the
  Authorization-header mechanism this ADR deprecates but does not
  remove), ADR-012 (Device State-Sync Protocol — the close-code-timing
  finding and `DisconnectReason` classification this ADR extends)

## Related Milestones

Milestone 9B.1, Milestone 9B.2

## Related Source Files

- `app/integrations/ws_tokens.py` (`issue_ws_token`, `verify_ws_token`,
  `ws_token_ttl_seconds`)
- `app/main.py` (`POST /api/ws-token`, `/ws` handshake auth resolution)
- `android/app/src/main/java/com/jarvis/companion/network/`
  (`WsTokenClient.kt`, `DisconnectReason.kt`, `DisconnectClassifier.kt`,
  `CompanionWebSocketClient.kt`)
- `app/static/app.js` (PWA token fetch + WS connect)
- `docs/protocols/websocket-protocol-v1.md`
- `tests/test_ws_tokens.py`, `tests/test_ws_auth.py`
