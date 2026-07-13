# Independent Review: android-presence-spike

**Reviewer**: Independent (read-only)  
**Date**: 2026-07-12  
**Scope**: All 10 areas requested  

---

## 1. AndroidManifest.xml correctness

**Finding: Minor observation — redundant `ensureNotificationChannel` calls on every notification build, but not a manifest issue.**

**Declared permissions:**
- `INTERNET` — justified (WebSocket outbound).
- `ACCESS_NETWORK_STATE` — justified (`ConnectivityManager.registerNetworkCallback`).
- `POST_NOTIFICATIONS` — justified (foreground notification on API 33+).
- `FOREGROUND_SERVICE` — justified (foreground service on API 28+).
- `FOREGROUND_SERVICE_DATA_SYNC` — justified (declared `foregroundServiceType="dataSync"` on API 34+).

**Foreground service declaration** (`AndroidManifest.xml:26-29`):  
`android:foregroundServiceType="dataSync"` is correct for a WebSocket transport service and matches the `FOREGROUND_SERVICE_DATA_SYNC` permission. Service is `android:exported="false"` as expected.

**No `android:supportsRtl` or `android:configChanges` issues** — not applicable for a disposable spike.

---

## 2. Foreground-service correctness for Android 13 (API 33)

**Finding: Real bug/risk — no graceful degradation if `POST_NOTIFICATIONS` is denied, leading to a crash.**

`PresenceService.kt:80`: `startForeground(NOTIFICATION_ID, buildNotification(connected = false))` is correctly called inside `onStartCommand`. `onStartCommand` returns `START_STICKY` (`PresenceService.kt:96`), which is appropriate.

**Critical issue:** `MainActivity.kt:32-33` requests `POST_NOTIFICATIONS` via `registerForActivityResult` with a **no-op callback**. If the user denies the permission dialog (or revokes the permission later via Settings), the next call to `startForeground()` at `PresenceService.kt:80` will throw a `SecurityException` (Android 13+ behavior for apps targeting API 33+, which this does — `targetSdk = 34`). There is no try/catch, no fallback, no grace period. The service crashes outright.

For a disposable spike this may be tolerable (the test protocol presumably grants the permission), but it is a real crash risk that would prevent the spike from collecting any data if the permission is missing.

**No issue:** The notification object itself is correctly built with `setOngoing(true)`, `setSmallIcon`, channel ID. `startForeground` is called before any work that depends on it.

---

## 3. Notification channel

**Finding: No issue found.**

`PresenceService.kt:148-158`: Channel created with `IMPORTANCE_LOW` — correct for a persistent status notification that should not make sound or appear as a heads-up. Guarded by `Build.VERSION.SDK_INT >= Build.VERSION_CODES.O`. `createNotificationChannel` is idempotent; calling it repeatedly is harmless.

**Minor observation:** `ensureNotificationChannel()` is called on every `buildNotification()` call rather than once in `onCreate()`. This is wasteful but not a bug — the SDK handles it.

---

## 4. Lifecycle (onCreate/onStartCommand/onDestroy/onTaskRemoved)

**Finding: Real bug/risk — `onTaskRemoved` calls `super.onTaskRemoved()` which (by default) calls `stopSelf()`, contradicting the developer's stated intent.**

`PresenceService.kt:117-125`:  
The comment at line 121-122 explicitly states: "The service is NOT stopped here; this only records that it happened, so the survival protocol can tell the three cases apart in the log."

However, `super.onTaskRemoved(rootIntent)` at line 124 calls `android.app.Service.onTaskRemoved()`, whose default implementation calls `stopSelf()`. This **does** stop the service. This is a factual error in the comment and a behavioral mismatch: the service **is** stopped when the user swipes from Recents, which means the survival spike is not observing the outcome it thinks it is. The `TASK_REMOVED` telemetry event fires, but it is immediately followed by `SERVICE_DESTROYED` — indistinguishable from the user explicitly tapping Stop. This confounds the very experiment the spike is designed to run.

**Fix (noted for the implementer):** Either omit the `super` call (to keep the service alive as the comment claims) or correct the comment to acknowledge the stop. For a spike measuring survival, the former is likely the intended behavior.

**Other lifecycle findings: No issue.**
- `onCreate` registers both `networkCallback` and `screenStateReceiver`; `onDestroy` unregisters both with proper try/catch for `IllegalArgumentException` (already-unregistered case). No double-registration risk — `connectionClient` is lazily created only when null.
- `onDestroy` calls `connectionClient?.disconnect()` then nulls the reference. Correct.
- No `stopForeground()` call in `onDestroy()` — the framework handles foreground-state cleanup when the service stops, so this is acceptable (the notification is removed by the system).

---

## 5. Secrets

**Finding: No issue found.**

- `TestConnectionClient.kt:24` — `TEST_WS_URL = "wss://192.168.1.27:8443/ws"`: a LAN IP address, explicitly called a dev-only placeholder in the README and in the inline comment. Not a production credential.
- `app/src/main/res/raw/jarvis_lan_cert.pem` — a copied TLS certificate for the LAN test server. This is the same mkcert-issued dev cert used by the browser-based PWA. No production secret.
- No API keys, tokens, passwords, or production credentials anywhere in the directory.

---

## 6. Logging

**Finding: No issue found — logging is appropriately scoped.**

All logging passes through `TelemetryRecorder.record()`, which logs event names and the optional `detail` string. The only reference to message content is in `TestConnectionClient.kt:122`:

```kotlin
telemetry.record(TelemetryRecorder.WS_FRAME_RECEIVED, "bytes=${text.length} generation=$generation")
```

This logs **byte length only**, never the message content. All other detail strings are metadata: generation numbers, reconnect counts, WebSocket close codes/reasons, exception class names, heartbeat timing. No credentials, no PII, no message payload is ever written to the log.

---

## 7. Permissions

**Finding: No issue found — all declared permissions are justified and used.**

| Permission | Used in | Justification |
|---|---|---|
| `INTERNET` | `OkHttpClient` (WebSocket) | Required for any network I/O |
| `ACCESS_NETWORK_STATE` | `ConnectivityManager.registerNetworkCallback` | Required for network availability callbacks |
| `POST_NOTIFICATIONS` | `startForeground()` + `NotificationManager.notify()` | Required for foreground notification on API 33+ |
| `FOREGROUND_SERVICE` | `AndroidManifest.xml` foreground service declaration | Required for any foreground service on API 28+ |
| `FOREGROUND_SERVICE_DATA_SYNC` | `AndroidManifest.xml` `foregroundServiceType="dataSync"` | Required for dataSync-type foreground service on API 34+ |

No unused or unnecessary permissions.

---

## 8. Coroutine/thread/Handler cleanup

**Finding: No issue found — cleanup is thorough and correct.**

**`TestConnectionClient.disconnect()` (`TestConnectionClient.kt:85-95`):**
1. Sets `stopped = true` — gates all subsequent `scheduleReconnect()` calls.
2. Calls `generationTracker.startNewGeneration()` — invalidates all in-flight callbacks from the current connection.
3. Removes both `heartbeatRunnable` and `reconnectRunnable` from `mainHandler`.
4. Calls `webSocket?.close(1000, ...)` — initiates clean close. The subsequent `onClosed`/`onFailure` callback will fire on OkHttp's dispatcher thread, but `stillCurrent()` returns `false` (step 2), so the callback is harmlessly ignored.
5. Nulls `webSocket`.

**Handler lifecycle:**
- `heartbeatRunnable` is posted in `onOpen` (`TestConnectionClient.kt:136`) and removed in `onClosed`, `onFailure`, and `disconnect`. No double-post risk because `onOpen` fires at most once per connection generation, and each generation gets a fresh `ConnectionListener`.
- `reconnectRunnable` is posted in `scheduleReconnect` and removed in `disconnect`.

**Generation checking applied consistently to all four callbacks:**
- `onOpen` (`TestConnectionClient.kt:126`) — checked ✓
- `onClosed` (`TestConnectionClient.kt:140`) — checked ✓
- `onFailure` (`TestConnectionClient.kt:148`) — checked ✓
- `onMessage` (`TestConnectionClient.kt:121`) — checked ✓

All four guard with `stillCurrent()`, which logs a `WS_STALE_CALLBACK_IGNORED` event if stale.

**`@Volatile` on `ConnectionGenerationTracker.current`** (`ConnectionGenerationTracker.kt:16`): Correct — ensures cross-thread visibility since OkHttp callbacks run on OkHttp's internal dispatcher thread while `startNewGeneration()` is called from the main thread.

**Minor observation:** `reconnectRunnable` is not removed from the Handler before a new one is posted in `scheduleReconnect()`. In practice, `scheduleReconnect()` is only called from `onClosed`/`onFailure`, which fire at most once per connection, so overlapping schedules shouldn't occur. Not a real risk.

---

## 9. Reconnect/backoff logic

**Finding: Minor observation — jitter is applied after the `min()` cap, so the effective maximum backoff exceeds the declared cap by up to 499ms.**

`TestConnectionClient.kt:107`:
```kotlin
backoffMs = min(BACKOFF_MAX_MS, backoffMs * 2) + Random.nextLong(0, 500)
```

The `min()` caps `backoffMs * 2` at `BACKOFF_MAX_MS` (60,000ms). But the jitter `+ Random.nextLong(0, 500)` is applied **after** the cap, so the actual value can reach ~60,500ms. The comment on the same line says "capped at BACKOFF_MAX_MS" but the implementation exceeds it by up to ~500ms. Not a significant practical concern for a spike, but the stated behavior does not match the implementation.

**No issue found otherwise:**
- Backoff starts at `BACKOFF_INITIAL_MS` (2s), doubles each cycle, capped at ~60s.
- Backoff resets to `BACKOFF_INITIAL_MS` in `onOpen` (`TestConnectionClient.kt:128`) — correct.
- No runaway risk beyond the design intent: the service reconnects indefinitely with bounded per-attempt delay. For a survival spike, indefinite retry is the desired behavior.
- `stopped` flag gates `scheduleReconnect()` and the delayed `Runnable` body — correct.

---

## 10. Scope discipline

**Finding: No issue found — the codebase strictly respects the README scope boundary.**

Checked against `README.md` section "Scope boundary — do NOT build":
- No Hilt, Dagger, or dependency injection — only constructor injection. ✓
- No Room or database — telemetry is a flat text file. ✓
- No Navigation component — single activity with toggle buttons. ✓
- No wake-word code — not imported, not referenced, no audio permissions. ✓
- No LLM client or Jarvis reasoning of any kind. ✓
- No production companion architecture, no pairing/auth system, no widget, no call-style UI. ✓
- No secrets committed — only a dev placeholder URL and a LAN dev certificate. ✓
- `TestConnectionClient` connects to the Jarvis `/ws` endpoint but does not fork or extend the protocol — it speaks the same WebSocket protocol the real client uses. This is within scope.

**Dependencies (`app/build.gradle.kts:38-44`):** Only OkHttp (WebSocket transport), AppCompat + Material (UI skeleton), ConstraintLayout, and Core KTX. No framework-level dependencies.

---

## Summary

| Area | Verdict |
|---|---|
| 1. AndroidManifest.xml | Minor observation (redundant channel creation) |
| 2. Foreground-service (API 33) | **Real bug/risk** — crash if `POST_NOTIFICATIONS` denied |
| 3. Notification channel | No issue found |
| 4. Lifecycle | **Real bug/risk** — `onTaskRemoved` calls `super`, stopping the service despite comment saying it isn't stopped, confounding the survival experiment |
| 5. Secrets | No issue found |
| 6. Logging | No issue found |
| 7. Permissions | No issue found |
| 8. Handler/thread cleanup | No issue found |
| 9. Reconnect/backoff | Minor observation (jitter after cap, exceeding stated max by ~500ms) |
| 10. Scope discipline | No issue found |

**Two real bugs/risks identified, both in `PresenceService.kt`: one that changes the experimental outcome of the spike (`onTaskRemoved` / `super` call), and one that causes a hard crash on denied notification permission.**