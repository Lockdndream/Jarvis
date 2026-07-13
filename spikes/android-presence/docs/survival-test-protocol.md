# Extended Screen-Off Survival Test Protocol

Milestone 9B.0, Risk 2 (persistent presence). Target device: Samsung
Galaxy S20 FE, Android 13, One UI 5.1. Subject under test: `PresenceService`
in this spike (`spikes/android-presence/`), built from
`app-debug.apk`.

## UNKNOWN this experiment resolves

Whether a correctly-implemented native foreground presence service on the
S20 FE survives extended screen-off operation sufficiently for Jarvis
presence, and correctly restores its WebSocket transport after a real
disruption (screen-off itself, and separately a Wi-Fi interruption) —
as opposed to being killed, throttled into uselessness, or losing its
connection without ever recovering it. Milestone 9A's Test B only measured
~20 seconds of screen-off with the PWA still nominally foregrounded
pre-lock; this protocol measures a genuinely backgrounded, hours-scale
foreground-service scenario, which is a materially different claim.

## PASS criteria (defined now, before any run)

1. The service process is still alive at the end of the screen-off window
   (confirmed via `dumpsys`, not inferred from app icon or notification
   presence alone).
2. The persistent notification is still present and has not been silently
   dismissed or replaced by an OS "app inactive" style notification.
3. At least one `WS_CONNECTED` or `WS_RECONNECTED` telemetry event exists
   *after* the screen-off period began, i.e. the transport is not merely
   the same connection opened before the screen turned off with no
   evidence it survived — a fresh heartbeat/reconnect during the window
   counts as positive evidence.
4. For procedure D specifically: after Wi-Fi is restored, a
   `WS_RECONNECTED` event appears within a bounded time (see procedure D)
   — recovery must be observed, not assumed.
5. No unexplained gap in the telemetry log longer than the service's own
   heartbeat interval (30s) without a corresponding `WS_DISCONNECTED` /
   `WS_RECONNECT_SCHEDULED` pair explaining it — an unexplained gap means
   the *telemetry itself* may have stopped (a process freeze indistinguishable
   from a clean idle period), which must be treated as inconclusive, not PASS.

## FAIL criteria (defined now, before any run)

1. The service process is gone at the end of the window (`dumpsys` shows
   no matching process, or shows it in a crashed/dead state).
2. The persistent notification is gone without a corresponding
   `SERVICE_DESTROYED` telemetry event explaining a clean, intentional stop.
3. The telemetry log shows a `WS_DISCONNECTED` during the window with no
   subsequent `WS_RECONNECTED` before the window ends (for procedures A–C),
   or before the bounded recovery deadline (for procedure D).
4. The process is alive but the telemetry log is empty/frozen for the
   entire window with no explanation (distinguish from criterion 5 above:
   if this happens on *every* run, it is a FAIL, not merely inconclusive).

Any outcome that is neither a clean PASS nor a clean FAIL (e.g. telemetry
gap once but not on repeat) must be reported as such explicitly — do not
round an ambiguous result to PASS.

## Telemetry / evidence sources

| Source | Command | What it shows |
|---|---|---|
| On-device structured log (live) | `adb logcat -s PresenceSpike` | Every `TelemetryRecorder.record()` event, live, with timestamp |
| On-device structured log (pull) | `adb shell run-as com.jarvis.presencespike cat files/telemetry.log` | The full durable event log, readable after the screen-off window without keeping `adb logcat` attached the whole time |
| Process/service state | `adb shell dumpsys activity services com.jarvis.presencespike` | Whether the service is running, its process record, restart count |
| Battery/charging state | `adb shell dumpsys battery` | Charging state, level — observational only (see caveat below) |
| Doze/standby bucket | `adb shell dumpsys deviceidle` | Whether the device entered Doze during the window, and this app's standby bucket (`active`/`working_set`/`frequent`/`rare`) |
| Wi-Fi state | `adb shell dumpsys wifi \| grep -i "mNetworkInfo\|state"` (or `adb shell cmd wifi status` if available on this build) | Confirms actual Wi-Fi on/off state at each checkpoint, independent of what the test operator *intended* to do |
| Notification presence | Direct visual observation of the phone (user) | Distinguished explicitly from machine evidence — record as "observed by user," never conflated with `dumpsys`/logcat evidence |
| OEM battery optimization setting | Settings → Apps → Jarvis Presence Spike → Battery, or `adb shell dumpsys deviceidle whitelist` | Samsung's own unrestricted/optimized/restricted classification for this app |

Machine evidence (adb/dumpsys/logcat) and human observation (notification
visible, phone physically screen-off) must be recorded as clearly separate
columns in the results table for every procedure below — never merged into
one "looked fine" line.

## Procedures

For every procedure: before starting, confirm via `adb shell dumpsys
deviceidle whitelist | grep presencespike` whether the app is
battery-whitelisted, and record it. Do not change it mid-procedure.

### A — 20-minute screen-off baseline

1. Launch the app, tap Start, confirm `SERVICE_CREATED` →
   `SERVICE_STARTED` → `FOREGROUND_ENTERED` → `WS_CONNECTED` all appear
   within a few seconds via `adb logcat -s PresenceSpike`.
2. Record: battery % (before), charging state, Wi-Fi state, OEM battery
   setting, timestamp.
3. Turn the screen off (power button). Do not touch the device for 20
   minutes.
4. At the 20-minute mark, without unlocking, run `adb shell dumpsys
   activity services com.jarvis.presencespike` from the host machine
   (adb over USB survives screen-off) to confirm process state without
   waking the device.
5. Unlock the device, pull the telemetry log, record: battery % (after,
   observational only), final connection state, any `WS_DISCONNECTED`/
   `WS_RECONNECTED` pairs and their durations, notification still present
   (visual).
6. Apply PASS/FAIL criteria above.

### B — 1-hour screen-off

Identical steps to A, but the screen-off window is 1 hour. Additionally,
run the `dumpsys activity services` and `dumpsys deviceidle` checks at the
20-minute and 40-minute marks (not just the end) via adb over USB, without
waking the device, to see whether the app's standby bucket demotes over
time and whether that correlates with any connection change.

### C — Multi-hour (3+ hours) screen-off

Identical steps to A/B, scaled to 3+ hours if practical to fully complete
in one session; if not practical, run as long as is practical and report
the actual duration achieved rather than rounding up. Check `dumpsys
deviceidle` at roughly hourly intervals via adb over USB (does not wake
the device). This procedure is the one most likely to actually reach
Android's Doze "idle maintenance window" cycling — record whenever a
maintenance window appears to have occurred (a burst of connectivity taken
by the OS) if visible in the telemetry.

### D — Screen-off with Wi-Fi interruption/recovery

1. Same setup as A. Let the service run screen-off for 5 minutes to
   establish a stable baseline connection.
2. Without unlocking the screen, disable Wi-Fi from the notification
   shade or via `adb shell svc wifi disable` (adb over USB does not
   require the screen to be on). Record the exact time.
3. Confirm via telemetry (`adb logcat -s PresenceSpike`, still attached
   over USB) that `NETWORK_LOST` and a `WS_DISCONNECTED`/
   `WS_RECONNECT_SCHEDULED` sequence appear.
4. Wait 5 minutes with Wi-Fi off, screen still off.
5. Re-enable Wi-Fi (`adb shell svc wifi enable`). Record the exact time.
6. Set a bounded recovery deadline of 2 minutes from re-enabling Wi-Fi
   (generous relative to the service's own 60s backoff cap). Confirm
   `NETWORK_AVAILABLE` and `WS_RECONNECTED` both appear within that
   deadline — this is the PASS criterion #4 check specifically.
7. Unlock, pull the full telemetry log, apply PASS/FAIL criteria.

### E — Server restart while screen off (run once, after A succeeds)

A more representative failure mode than a stable server the whole time:
Jarvis itself will be restarted during real development/upgrades while
the companion is asleep, not just the network flapping.

1. Start the service, confirm `WS_CONNECTED` (screen still on).
2. Turn the screen off.
3. On the laptop, stop and restart the real Jarvis server process (the
   same `uvicorn app.main:app --host 0.0.0.0 --port 8443 --ssl-keyfile
   ... --ssl-certfile ...` command already documented in the repo root
   README.md) — a real restart, not a network interruption.
4. Record the exact time the server was stopped, and the exact time it
   finished starting up again (`Uvicorn running on https://0.0.0.0:8443`
   in its own log, plus the isolated OpenCode server's own healthy-startup
   line, since Jarvis's lifespan starts that automatically).
5. Without unlocking the phone, confirm via `adb logcat -s PresenceSpike`
   (already attached over Wi-Fi/USB) that `WS_DISCONNECTED` appeared
   around step 3, and `WS_RECONNECTED` appears within a bounded time
   after the server is healthy again (use the same 2-minute bound as
   procedure D's Wi-Fi check, generous relative to the 60s backoff cap).
6. Cross-check against the server's own log (`WebSocket client connected
   (N total)`) for an independent, server-side timestamp of the
   reconnection — do not rely on the phone's telemetry alone for this one,
   since it's specifically testing whether the *server* recognizes the
   reconnect, not just whether the phone *attempted* one.
7. Record whether `SERVICE_RESTARTED_BY_OS` appears anywhere in the
   phone's telemetry during this window — it should not, since this
   experiment restarts the *server*, not the phone's process; if it does
   appear, that's a separate, real finding (Android killed the service
   independently of anything this experiment intentionally did) and must
   be reported as such, not folded into the "expected" reconnect story.

## Battery caveat

Battery percentage before/after each procedure is recorded as
**observational data only**. A single uncontrolled run (variable ambient
temperature, other apps' background activity, cellular signal strength,
screen-off duration measured only to the nearest procedure boundary, no
control group) must never be reported as a general claim about this
approach's battery cost — it is one data point about one run on one
device on one day, not a benchmark. Any future claim like "this uses X%
battery per hour" requires a dedicated, controlled, repeated-measurement
study, which this protocol does not attempt.
