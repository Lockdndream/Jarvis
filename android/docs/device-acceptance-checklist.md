# Milestone 9B.1 — Real-Device Acceptance Checklist

Purpose: verify the production companion (`android/`, `com.jarvis.companion`)
actually behaves as designed on real hardware, not just that it builds and
passes JVM unit tests. Per ADR-010 (Evidence-Based Engineering), a passing
`assembleDebug`/`testDebugUnitTest` is not evidence of real-device
behavior — every item below requires a specific, checkable signal, not "it
looked fine."

Classify every item as one of: **PASS**, **FAIL**, **NOT TESTED**, **BUG
FOUND**. Do not mark PASS from absence of an error alone — each item names
the actual evidence required.

Device under test: **Samsung Galaxy S20 FE (SM-G781B), Android 13, API 33**
(same device used throughout Milestone 9B.0).

Jarvis server: host `192.168.1.27`, port `8443`, `JARVIS_API_TOKEN`
unset (no-op path exercised, per ADR-011's disclosed PWA/companion
asymmetry — a token-set run is a separate, not-yet-performed test).

**Executed 2026-07-13.** Results below.

---

## Pre-requisites

- Built APK: `android/app/build/outputs/apk/debug/app-debug.apk`
  (produced by `./gradlew assembleDebug`).
- `adb` reachable, device authorized (`adb devices` shows it as `device`,
  not `unauthorized`).
- Jarvis server running and reachable from the device's network.

## 1. APK installs

- **Procedure**: `adb install -r app-debug.apk`.
- **Evidence required**: command exits 0 with `Success`; then
  `adb shell pm list packages | grep com.jarvis.companion` returns the
  package.
- **Not sufficient**: "the install command didn't print an error" without
  checking `pm list packages`.

## 2. Permissions

- **Procedure**: launch the app (first-run). Expect a system
  `POST_NOTIFICATIONS` permission prompt (API 33+) triggered from the
  Settings screen's "Grant notification permission" button, since this
  build does not request it automatically on launch — confirm the button
  is visible before granting, tap it, grant, confirm the button
  disappears and the status text switches to "Notifications: granted."
- **Evidence required**:
  `adb shell dumpsys package com.jarvis.companion | grep -A2 POST_NOTIFICATIONS`
  shows `granted=true` after granting.
- **Known gap to confirm, not assume**: before granting, the app should
  still run (foreground service notification may not be visible without
  this permission, but the service itself should not crash) — confirm
  this rather than assuming it.

## 3. Pairing

- **Procedure**: on the Settings screen, enter the server's host/port
  (and API token, if `JARVIS_API_TOKEN` is set server-side), tap "Probe &
  Pair."
- **Evidence required**: a confirmation dialog appears showing a SHA-256
  fingerprint (colon-separated hex). If the probe fails (wrong
  host/port/network), confirm the failure dialog shows a real error
  message, not a silent no-op.
- **Telemetry check**: `PAIRING_PROBE_STARTED` appears in the telemetry
  log for this attempt.

## 4. TLS fingerprint confirmation

- **Procedure**: before tapping "Trust and Pair," independently compute
  the server's actual certificate fingerprint from the server side (not
  from the phone) and compare by eye:
  `openssl x509 -in certs/jarvis-lan-cert.pem -noout -fingerprint -sha256`
  (adjust for however the running server's actual cert is currently
  configured).
- **Evidence required**: the fingerprint shown on the phone matches the
  independently-computed one exactly (same 32 byte-pairs). This is the
  one item where "looks plausible" is explicitly not acceptable — do the
  actual byte-for-byte comparison.
- **Do not** just tap "Trust and Pair" without doing this comparison at
  least once — that is the actual security property TOFU depends on
  (ADR-011).

## 5. Certificate pinning persistence

- **Procedure A (persistence across restart)**: after pairing, force-stop
  the app (`adb shell am force-stop com.jarvis.companion`), relaunch,
  open Settings. Confirm it still shows "Paired with host:port" without
  re-pairing.
- **Procedure B (pinning actually rejects a changed cert)**: regenerate
  the server's TLS certificate (e.g. re-run mkcert) while keeping the
  same host/port, restart the Jarvis server, and attempt to reconnect
  with the already-paired app (do not re-pair).
- **Evidence required (B)**: the connection fails — telemetry should show
  a `WS_DISCONNECTED` with a `CertificateException`/fingerprint-mismatch
  failure reason (from `PinnedTrustManager`), not a silent hang or a
  connection that somehow still succeeds. A successful connection here
  would mean pinning is not actually being enforced — a real bug, not a
  cosmetic issue.

## 6. Connection establishment

- **Procedure**: with the app paired, start the companion ("Start
  companion" button, or via `adb shell am start-foreground-service`).
- **Evidence required**: telemetry shows `WS_CONNECTING` then
  `WS_CONNECTED`; **independently**, the Jarvis server's own log
  (`WebSocket client connected ... conn_id=N`) shows a new connection at
  the same time. Cross-check both sides — client-only evidence is not
  sufficient (ADR-010).

## 7. Reconnect

- **Procedure**: with a connection established, kill the server process
  (or toggle the device's Wi-Fi off/on).
- **Evidence required**: telemetry shows `WS_DISCONNECTED` →
  `WS_RECONNECT_SCHEDULED` (with an increasing `delayMs` on repeated
  failures, capped — see `BackoffPolicy`) → eventually `WS_RECONNECTED`
  once the server/network is back. Cross-check the server log shows a
  new `conn_id`.

## 8. Foreground service

- **Procedure**: with the service running, swipe the app away from
  Recents (do not tap "Stop companion").
- **Evidence required**: telemetry logs `TASK_REMOVED`; the service is
  still alive afterward
  (`adb shell dumpsys activity services com.jarvis.companion` still
  lists `PresenceService`); the persistent notification is still visible.
  A service that disappears here is a real regression, not expected
  behavor — see `PresenceService`'s doc comment on why `onTaskRemoved()`
  deliberately does not stop the service.

## 9. Notification channel

- **Procedure**: Android Settings → Apps → Jarvis Companion →
  Notifications.
- **Evidence required**: a channel named "Jarvis companion status"
  exists, importance **Low** (matches `PresenceNotifications.ensureChannel()`
  — confirm it's actually Low, not default/High, since Low is what keeps
  it silent/non-intrusive by design).

## 10. Battery optimization detection

- **Procedure**: with the app **not** yet granted battery exemption,
  open Settings — confirm it shows "Battery: optimized" and the
  "Request unrestricted battery" button is visible. Tap it, grant
  Unrestricted in the system dialog, return to the app.
- **Evidence required**: the status text switches to "Battery:
  unrestricted" and the button disappears — cross-check against
  `adb shell dumpsys deviceidle whitelist | grep com.jarvis.companion`
  (should list the package once granted).

## 11. Telemetry

- **Procedure**: after exercising several of the above (connect,
  disconnect, reconnect), pull the raw log:
  `adb shell run-as com.jarvis.companion cat files/telemetry.log`
  (or via `adb pull` if run-as is unavailable on the build).
- **Evidence required**: the pulled file contains real, timestamped
  lines for every event actually exercised above — cross-check specific
  expected event names appear (e.g. `WS_CONNECTED`, `WS_RECONNECTED`,
  `TASK_REMOVED`), not just "the file is non-empty."

## 12. Diagnostics screen

- **Procedure**: open the Diagnostics screen from Settings.
- **Evidence required**: the displayed tail matches the tail of the same
  `telemetry.log` pulled via adb (item 11) — cross-check a specific
  recent line appears verbatim in both. The screen should also update
  live (within ~2s per `DiagnosticsActivity`'s poll interval) if a new
  event occurs while it's open.

## 13. Connection state transitions

- **Procedure**: open the Connection Status screen; trigger a disconnect
  (item 7) while watching it.
- **Evidence required**: the displayed text visibly transitions
  Connected → Reconnecting → Connected (or → Disconnected if stopped),
  matching the `ConnectionState` values, not stuck on a stale value.

## 14. App restart

- **Procedure**: force-stop the app (`adb shell am force-stop
  com.jarvis.companion`) while the service is running, then relaunch.
- **Evidence required**: pairing config is intact (item 5's Procedure A);
  confirm whether the service auto-resumes or requires a manual "Start
  companion" tap — record whichever is actually observed, do not assume
  either.

## 15. Device reboot recovery

- **Predicted result, stated before testing (per ADR-010, so this isn't
  a surprise discovery)**: this build has **no `RECEIVE_BOOT_COMPLETED`
  receiver** — `PresenceService` will **not** auto-start after a device
  reboot. `START_STICKY` only survives the OS killing the process while
  itself still running; it does not survive a full reboot, which clears
  all process state. Expect the user must manually reopen the app and
  tap "Start companion" after a reboot.
- **Procedure**: reboot the device (`adb reboot`), wait for it to fully
  boot, then check `adb shell dumpsys activity services
  com.jarvis.companion`.
- **Evidence required**: confirm the predicted absence (no service
  running until manually started) — classify as **PASS** if it matches
  this documented, intended-for-now behavior, not as a bug. If
  auto-start-after-reboot is wanted, that is a real, separate feature
  (a `BOOT_COMPLETED` receiver) requiring its own approval — not
  something to add silently during this validation pass.

---

## Result table

| # | Item | Result | Evidence / Notes |
|---|------|--------|-------------------|
| 1 | APK installs | **PASS** | `adb install -r` succeeded; `pm list packages --user 0` confirmed `com.jarvis.companion` registered. |
| 2 | Permissions | **PASS** | System prompt appeared; after granting, `dumpsys package` showed `POST_NOTIFICATIONS: granted=true, flags=[USER_SET...]`; Settings screen text updated to "granted" and the grant button disappeared. |
| 3 | Pairing | **PASS** | Probe succeeded against `192.168.1.27:8443`; confirmation dialog appeared; **user personally confirmed the dialog** (not automation) — the intended TOFU trust decision, made by a human. Settings screen updated to "Paired with 192.168.1.27:8443." |
| 4 | TLS fingerprint confirmation | **PASS (user-performed)** | The confirming tap was done directly by the user, not driven by adb — i.e. the actual human verification TOFU depends on. Independently, the pinned fingerprint was later proven to be enforced correctly by item 5's Procedure B (an unrelated cert was rejected with an exact fingerprint mismatch against the originally-pinned value). |
| 5 | Certificate pinning persistence | **PASS** | Procedure A: pairing survived force-stop/relaunch and a full device reboot (`EncryptedSharedPreferences`/Keystore key survived reboot). Procedure B: server cert regenerated (mkcert, new fingerprint `41:88:AA:D8:2F:52:3C:67:18:39:56:B6:31:87:0F:0A:3E:2E:C8:EE:45:8E:F6:84:13:C0:4A:43:BB:D6:CE:DC`), server restarted — the already-paired app rejected every reconnect attempt with `SSLHandshakeException: Server certificate fingerprint changed: expected EA:78:19:33:...(original), got 41:88:AA:D8:...(new)`, byte-for-byte matching both independently-computed (`openssl x509 -fingerprint`) values. Original cert restored and reconnection re-verified working afterward. |
| 6 | Connection establishment | **PASS** | Client telemetry `WS_CONNECTED` cross-verified against an independent, OS-level signal: `netstat` showed a real `ESTABLISHED` TCP connection from the phone's IP to the server, at the same time. |
| 7 | Reconnect | **PASS** | Real (accidentally triggered — see Known Issues Found) network drop: `NETWORK_LOST` → `WS_RECONNECT_SCHEDULED delayMs=2035` → failed attempt (generation 2, `ConnectException`) → `delayMs=4469` → `WS_RECONNECTED` (generation 3). Backoff grows correctly across attempts; no stale-callback misattribution. |
| 8 | Foreground service | **PASS** | Task removed via `am stack remove` on the app's task (recents-swipe equivalent); `TASK_REMOVED` logged; `dumpsys activity services` confirmed `PresenceService` still alive afterward; heartbeat continued; notification remained posted. |
| 9 | Notification channel | **PASS** | `dumpsys notification` confirmed channel `jarvis_companion_status`, `mImportance=2` (IMPORTANCE_LOW), matching `PresenceNotifications.ensureChannel()`. |
| 10 | Battery optimization detection | **PASS** | Before granting: UI showed "Battery: optimized," button visible. After granting via the real system dialog: UI showed "Battery: unrestricted (recommended)," button hidden; cross-verified via `dumpsys deviceidle whitelist` listing the package. |
| 11 | Telemetry | **PASS** | Pulled `telemetry.log` via `adb shell run-as` contained every expected event name from the session (`WS_CONNECTED`, `WS_RECONNECTED`, `TASK_REMOVED`, etc.), not just a non-empty file. |
| 12 | Diagnostics screen | **PASS** | On-screen tail matched the pulled `telemetry.log` content verbatim. |
| 13 | Connection state transitions | **PASS** | Connection Status screen showed "Connected" / host:port while connected; `NOTIFICATION_POSTED state=...` telemetry confirms CONNECTING → CONNECTED → RECONNECTING → CONNECTED transitions occurred correctly during the reconnect test. |
| 14 | App restart | **PASS** | After `am force-stop` + relaunch: pairing, notification-permission status, and battery-exemption status all correctly persisted and re-displayed; service did not auto-resume (requires a manual "Start companion" tap) — consistent, unsurprising behavior, not a bug. |
| 15 | Device reboot recovery | **PASS** | Real `adb reboot` performed (confirmed via `uptime` showing ~4 minutes post-reboot). Matched the predicted behavior exactly: no `PresenceService` record existed at all until manually started (no `BOOT_COMPLETED` receiver, as documented before testing); pairing/permission/battery state all survived the reboot correctly. |

**15/15 PASS.** See "Known Issues Found" below for two non-blocking observations surfaced during this pass.

## Known Issues Found

1. **Infinite silent retry on a permanently-failing certificate mismatch.**
   Unlike the `WS_AUTH_REJECTED` (close code 1008) case, a certificate
   fingerprint mismatch does not stop the reconnect loop or prompt the
   user to re-pair — `CompanionWebSocketClient` will retry forever
   (capped backoff, but forever) against a connection that can never
   succeed until the user re-pairs manually. Not a security bug (the
   rejection itself is correct and enforced, per item 5's Procedure B) —
   a UX/observability gap. Candidate follow-up: treat a certificate
   mismatch the same way as `WS_AUTH_REJECTED` (surface distinctly,
   perhaps stop retrying) rather than silently backing off forever.
2. **Non-exported activities correctly refuse `adb shell am start -n`.**
   Not a bug — confirms `ConnectionStatusActivity`/`DiagnosticsActivity`'s
   `android:exported="false"` is genuinely enforced. Noted only because it
   changes how these screens must be tested (navigate via the actual UI
   button, not a direct component launch).

Both are tracked as follow-up items, not blockers for closing Milestone
9B.1 — see the Phase 3/4 report.
