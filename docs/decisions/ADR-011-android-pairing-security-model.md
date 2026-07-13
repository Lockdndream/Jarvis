# ADR-011: Android Companion Pairing Security Model

## Status

Accepted

## Date

2026-07-12 (Milestone 9B.1 — Android Native Companion, Production Foundation)

## Context

Milestone 9B.0's disposable spike hardcoded a single LAN IP and pinned its
certificate statically via `network_security_config.xml`, deliberately —
that was sufficient for a technical-risk spike testing one known machine.
Milestone 9B.1 builds the production companion, which must let a user pair
with *their* Jarvis server at a LAN address not known at build time, while
still meeting the project's standing constraints (ADR-001, ARCHITECTURE.md
§12): no cloud account, no internet discovery, LAN-only, and the server's
certificate is self-signed (mkcert, per-machine — `certs/`, gitignored),
so there is no public CA to validate against.

Separately, this milestone found that `/ws` — unlike the push-subscription
REST endpoints — never checked `JARVIS_API_TOKEN` at all (TD-018 was aware
auth was optional/absent by default, but `/ws` specifically had no wiring
to the existing gate). A companion sending a pairing token to a server that
never checks it would be security theater, not a real boundary.

## Problem

How does a native Android client establish a trusted, authenticated
connection to a self-signed, runtime-discovered LAN server — with no cloud
account, no CA, and no discovery protocol — in a way that's both real (an
attacker on the LAN can't silently intercept it after the fact) and doesn't
require extending the existing `/ws` JSON protocol?

## Decision

**Trust-on-first-use (TOFU) certificate pinning, confirmed by the user, plus
a now-server-enforced bearer token — the same security model as SSH host
keys, not a certificate-authority model.**

- The user manually enters the server's host/port (no discovery, no QR
  code, no cloud account — ADR-002/ADR-008's LAN-only model, unchanged).
- The app performs a one-shot TLS handshake against that host
  (`pairing.PairingClient`, using a `DiscoveryTrustManager` that trusts
  anything *only* for this single probe) and computes the presented leaf
  certificate's SHA-256 fingerprint (`pairing.CertFingerprint`).
- That fingerprint is shown to the user, who is expected to confirm it
  matches the certificate the server itself prints (the same manual
  verification model as `ssh`'s "the authenticity of host X can't be
  established" prompt). Once confirmed, the fingerprint is persisted
  (`pairing.PairingRepository`, backed by `core.SecureConfigStore` —
  `EncryptedSharedPreferences`).
- Every subsequent connection uses `pairing.PinnedTrustManager`, which
  trusts *only* a certificate matching the pinned fingerprint exactly —
  protecting against a later MITM even though the initial trust decision
  was manual.
- The paired API token is sent as a standard `Authorization: Bearer`
  header on the `/ws` WebSocket handshake. `app/main.py`'s `/ws` endpoint
  now actually checks it (`_websocket_authorized`, reusing the exact
  `JARVIS_API_TOKEN` env var and Bearer-format convention
  `_require_api_token` already used for the push endpoints) — a small,
  backward-compatible server change: a no-op when `JARVIS_API_TOKEN` is
  unset, identical to every deployment's behavior before this milestone.

## Alternatives Considered

**Ship a static trust anchor / bundle a CA the user installs.** Rejected:
this project has no CA infrastructure (ADR-001's laptop-as-brain model
doesn't include a cloud PKI), and building one would be significant new
infrastructure for a single-user, single-device pairing flow — a much
larger investment than the problem justifies.

**Trust any certificate on the LAN (no pinning at all).** Rejected,
directly: this is exactly the gap ADR-004/ADR-010 warn against — a
permissive default that is only as safe as everything else around it. A
LAN is not inherently trusted (a compromised or malicious device on the
same Wi-Fi network could otherwise impersonate the server).

**QR-code-based pairing (encode host/port/fingerprint/token in a QR the
server displays).** Considered as a nicer UX than manual entry + fingerprint
confirmation, but explicitly out of scope for this milestone (the user's
M9B.1 spec lists QR/widgets as later work requiring separate approval) —
noted as a natural follow-up, not rejected on merits.

**Extend the `/ws` JSON protocol with an explicit `auth` message type.**
Rejected: the existing `Authorization` header on the WebSocket upgrade
request is sufficient and standard (RFC 6455 handshake is a real HTTP
request), and Milestone 9B.0's explicit instruction not to extend the
protocol without a demonstrated deficiency still applies — there was none
here.

## Consequences

Pairing a new device requires one manual, human-verified trust decision
(confirming a fingerprint) — after that, every future connection is
cryptographically pinned without further user action. `/ws` gains a real
(if optional) authentication boundary it did not have before, closing part
of TD-018 for the specific case of the Android companion; the browser PWA
remains unauthenticated by default since browsers cannot set a custom
`Authorization` header on a WebSocket handshake (a real, disclosed
limitation — see Tradeoffs).

## Positive Outcomes

- No new server-side infrastructure (no CA, no account system) was needed
  to get a real, MITM-resistant trust model.
- The `/ws` auth check is proven backward-compatible: `tests/test_ws_auth.py`
  (4/4 passing) confirms unset `JARVIS_API_TOKEN` is still a total no-op,
  and existing conversation-continuity/notification tests
  (`tests/test_conversation_continuity.py`, `tests/test_notification_api.py`,
  21/21) show no regression.
- `network.PinnedTrustManager` and `pairing.CertFingerprint`'s matching
  logic are directly unit-tested (`PinnedTrustManagerTest`,
  `CertFingerprintTest`) without needing real device hardware for this
  part of the security boundary.

## Tradeoffs

- TOFU's security depends on the user actually verifying the fingerprint
  the first time, not just tapping through the confirmation dialog — the
  same known weakness SSH has always had. No stronger guarantee is
  possible without CA infrastructure this project has deliberately not
  built (see Alternatives Considered).
- If `JARVIS_API_TOKEN` is set to protect the Android companion's `/ws`
  connection, the browser PWA's own `/ws` connection breaks (browsers
  cannot set a custom header on a WebSocket handshake) — this is a real,
  disclosed gap, not yet resolved. Until it is, an operator who wants the
  companion's token enforced must accept losing PWA access, or leave the
  token unset (current default; no enforcement either way).
- If the server's certificate is ever legitimately regenerated (e.g. mkcert
  re-run), every already-paired device's pinned fingerprint stops matching
  and must be re-paired — an accepted cost of TOFU pinning with no
  automatic rotation mechanism in this milestone.

## Future Revisit Conditions

Revisit if a second authenticated client type (e.g. a browser-based PWA
requiring the same token) becomes a real requirement — the header-based
approach here would need a companion mechanism for browsers (e.g. a
short-lived cookie or query-param token, weighed against the security
cost of a token appearing in server logs/URLs). Revisit if QR-based pairing
is approved as a follow-up milestone — the underlying trust model
(TOFU + pinned fingerprint) would likely carry over unchanged; only the
host/port/token entry step would change.

## References

- `ARCHITECTURE.md` §12 (Security Model)
- `SESSION.md`, Milestone 9B.0 (spike's static per-IP pinning), Milestone
  9B.1 (this decision)
- `docs/TECHNICAL_DEBT.md` TD-018 (no authentication/TLS by default —
  partially addressed by this ADR for the Android companion specifically)
- ADR-001 (Laptop Remains the Brain), ADR-002 (Hybrid Android
  Architecture), ADR-004 (OpenCode Runtime Isolation — the project's other
  credential-boundary precedent), ADR-010 (Evidence-Based Engineering)

## Related Milestones

Milestone 9B.0, Milestone 9B.1

## Related Source Files

- `android/app/src/main/java/com/jarvis/companion/pairing/` (`PairingClient`,
  `PairingRepository`, `PairingConfig`, `PinnedTrustManager`,
  `DiscoveryTrustManager`, `CertFingerprint`)
- `android/app/src/main/java/com/jarvis/companion/core/SecureConfigStore.kt`
- `android/app/src/main/java/com/jarvis/companion/network/CompanionWebSocketClient.kt`
- `app/main.py` (`_websocket_authorized`, `/ws` handshake)
- `tests/test_ws_auth.py`
- `android/app/src/test/java/com/jarvis/companion/pairing/`
  (`PinnedTrustManagerTest`, `CertFingerprintTest`)
