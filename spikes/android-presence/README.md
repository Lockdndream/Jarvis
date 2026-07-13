# android-presence-spike (Milestone 9B.0)

Disposable technical-risk spike. Not production code. Do not import
anything from this directory into the main Jarvis app without an explicit
future milestone decision to promote it.

## Purpose

Answer two risk questions with physical-device evidence on a Samsung
Galaxy S20 FE (Android 13, One UI 5.1):

1. Can a correctly-implemented native foreground presence service survive
   extended screen-off operation and recover a WebSocket connection after
   disruption?
2. (Separate, later stream — do not start yet) Can a local/offline
   wake-word engine actually build, install, and run acceptably on this
   device?

This spike currently covers question 1 only (the "foundation" spike).
Wake-word integration is explicitly gated behind a separate research
decision and must not be started in this directory until told to.

## Scope boundary — do NOT build

- No production companion architecture, no final pairing/auth system, no
  widget, no wake-word UX, no call-style UI, no LLM client, no navigation
  framework, no dependency-injection framework, no database.
- No Jarvis reasoning of any kind. This app is a dumb presence probe.

## Minimum structure expected

- `app/` — a single minimal Android module
  - `MainActivity` — trivial launcher, starts/stops the service
  - `PresenceService` — Android 13-correct foreground service with a
    persistent notification; owns the WebSocket transport
  - `TestConnectionClient` — thin WebSocket client (connect, heartbeat,
    bounded-backoff reconnect) pointed at a Jarvis-compatible test endpoint
  - `TelemetryRecorder` — structured local event log with timestamps
    (SERVICE_CREATED, SERVICE_STARTED, SERVICE_RESTARTED_BY_OS [only when
    `onStartCommand` receives a null intent — the real signal that Android
    killed and restarted the service on its own, not a user-triggered
    start], FOREGROUND_ENTERED, WS_CONNECTING, WS_CONNECTED,
    WS_HEARTBEAT_SENT, WS_DISCONNECTED, WS_RECONNECT_SCHEDULED,
    WS_RECONNECTED [carries a cumulative `reconnectCount`], NETWORK_LOST,
    NETWORK_AVAILABLE, TASK_REMOVED [user swiped the app from Recents —
    distinct from an explicit Stop or an OS kill], SERVICE_DESTROYED, and
    SCREEN_OFF_OBSERVED/SCREEN_ON_OBSERVED only if legitimately observable
    — never fabricate lifecycle certainty from a missing event)
- No secrets committed here. Test-only auth (if any) must use a
  clearly-marked development placeholder, never a real credential.

## Status

Both this foundation spike and `docs/survival-test-protocol.md` were
written directly by Claude, not delegated — five separate OpenCode
delegation attempts across multiple free-tier models (default, coder-
specialized, small/less-popular, and the one model proven to work
earlier in the session) all failed to complete real work within their
20-minute budgets, due to persistent OpenRouter free-tier rate-limiting
that appeared to be a broad, systemic capacity issue that day rather than
specific to any one model. See SESSION.md Milestone 9B.0 for the full
delegation ledger. `assembleDebug` builds successfully (verified
2026-07-11) — not yet installed/run on the physical S20 FE.

## Build/run

```
# From this directory (spikes/android-presence), with Android Studio's
# bundled JBR as JAVA_HOME (adjust path if Android Studio is installed
# elsewhere):
$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"
.\gradlew.bat assembleDebug

# Install on the connected S20 FE (USB debugging enabled, RSA prompt
# accepted on-device):
adb install -r app\build\outputs\apk\debug\app-debug.apk

# Watch telemetry live:
adb logcat -s PresenceSpike

# Pull the on-device telemetry file after a test run:
adb shell run-as com.jarvis.presencespike cat files/telemetry.log
```

`TEST_WS_URL` in `TestConnectionClient.kt` is a dev-only placeholder
(`ws://10.0.2.2:8000/ws`, the Android-emulator alias for the host
machine's localhost) — override it to the real LAN HTTPS Jarvis endpoint
before testing against a real server from the physical phone.
