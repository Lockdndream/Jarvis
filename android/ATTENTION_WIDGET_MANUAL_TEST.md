# Manual Acceptance Checklist — Milestone 9B.3 / ADR-015 (Attention Widget)

## Prerequisites

- A paired Android device (or emulator with a paired Jarvis server reachable on the LAN).
- `PresenceService` running (companion started from `SettingsActivity`).
- Server-side ability to create an `AttentionRequest` on demand (e.g. trigger a worker question/permission request that reaches `attention_manager.py`) and to resolve/cancel it, for the state-transition checks below.
- `adb logcat -s JarvisCompanion` open in a terminal to cross-check `ATTENTION_SNAPSHOT_APPLIED` / `ATTENTION_EVENT_APPLIED` / `ATTENTION_COMMAND_SENT` telemetry lines against what's observed on-screen.

---

## 1. Adding the widget

1. Long-press the home screen → Widgets → find "Jarvis Companion" → drag the Attention widget onto the home screen.
2. Verify it renders immediately (no crash, no blank/placeholder-forever state) — should show a connection-state line, a body line, and a last-contact line.
3. Verify the widget is resizable per its declared `resizeMode` (horizontal and vertical) and doesn't clip/overlap text at its minimum size (250×100dp).

## 2. Connected, no outstanding items

1. With the companion connected and no outstanding `AttentionRequest`, verify: connection line reads "Connected", body reads "No outstanding items", the Dismiss button is hidden (not just disabled — actually gone).
2. Verify the Refresh button is present and tapping it doesn't crash or change anything (nothing to refresh to).

## 3. Connected, one outstanding item

1. Trigger a server-side `AttentionRequest` (e.g. a worker question).
2. Within a few seconds (push-driven, not the 30-minute OS floor), verify the widget updates without any manual interaction: body line shows the count and the item's type/summary, Dismiss button becomes visible and enabled.
3. Cross-check `adb logcat`: an `ATTENTION_EVENT_APPLIED` (or `ATTENTION_SNAPSHOT_APPLIED` on first connect) line should appear at roughly the same time the widget visibly updated.

## 4. Multiple outstanding items

1. Trigger a second `AttentionRequest` while the first is still outstanding.
2. Verify the widget's body line shows the updated count (2) and the *most recently updated* item's summary (not the first one created) — `outstanding` is most-recently-updated-first.
3. Tap the widget body → verify `AttentionActivity` opens showing **both** items in a list (the widget itself never shows more than one).

## 5. Dismiss from the widget

1. With one outstanding item shown, tap Dismiss.
2. Verify `adb logcat` shows an `ATTENTION_COMMAND_SENT` line with the correct `attentionRequestId` and `phrase=later`.
3. Verify the server actually defers/handles the item (check server-side logs or the PWA's own view of the same `AttentionRequest`).
4. Verify the widget's display updates to reflect the new state once the server's resulting `attention_*` event round-trips back — this is not instantaneous (no optimistic UI), so allow a few seconds.

## 6. Dismiss with two items — correct item targeted (regression check for the FLAG_UPDATE_CURRENT fix)

1. With two outstanding items (item A first, item B second — B is shown, since most-recent-first), tap Dismiss.
2. Verify `adb logcat`'s `ATTENTION_COMMAND_SENT` line names **item B's** ID, not item A's or a stale ID from an earlier render.
3. This specifically exercises a real bug that was found and fixed during code review (a missing `PendingIntent.FLAG_UPDATE_CURRENT` would have caused the Dismiss button to keep firing with whichever item's ID was current the *first* time the widget rendered, never updating after) — do not skip this check.

## 7. Refresh button

1. With an outstanding item shown, tap Refresh.
2. Verify no network request is made (check `adb logcat` / server access logs — Refresh must not trigger any request; it only re-renders already-known state).
3. Verify the display is unchanged (nothing new happened) or, if state changed between the last push update and this tap, the display catches up.

## 8. Tap-to-open and focused item

1. With one outstanding item shown, tap the widget body (not Dismiss/Refresh).
2. Verify `AttentionActivity` opens and the row matching the widget's shown item is visually highlighted (background tint).
3. From within `AttentionActivity`, tap that row's own Dismiss button — verify it works the same way as the widget's Dismiss (same `sendAttentionCommand` call, same server effect).

## 9. Disconnected / reconnecting states

1. Turn off Wi-Fi (or otherwise break connectivity) while the widget shows an outstanding item.
2. Verify the connection-state line updates to "Reconnecting…" (or "Disconnected" once retries are exhausted) — the item list is *not* cleared during a transient reconnect (per ADR-015, the mirror survives a reconnect gap and self-corrects via the next `pending_attention` snapshot).
3. Verify the Dismiss button becomes disabled inside `AttentionActivity` while disconnected (the widget itself has no enable/disable state for Dismiss — only visibility when the list is empty; confirm this matches the design, not a bug).
4. Restore connectivity, verify reconnection, and verify the widget/activity both resync correctly (no duplicate items, no stuck "Reconnecting…" text).

## 10. Permanent failure (re-pair required)

1. Force a permanent disconnect (e.g. regenerate the server's TLS certificate so the pinned fingerprint no longer matches — see the existing cert-mismatch test procedure from Milestone 9B.1).
2. Verify the connection-state line reads "Not connected — re-pair required".
3. Verify the outstanding list is cleared (per the `attentionRepository.clear()` call on `FAILED_PERMANENT`) rather than showing stale data indefinitely.

## 11. Service not running

1. Stop the companion (Settings → Stop companion) or force-stop the app.
2. Verify the widget does not crash — `PresenceService.activeClient` is null, so Dismiss should be a safe no-op (verify tapping it does nothing harmful, no crash, ideally also reflected as "Disconnected" if `connectionState` was reset).

## 12. Multiple widget instances

1. Add a second instance of the same widget to the home screen (or to a second home screen page).
2. Verify both instances render independently and both update on the same push-driven trigger.
3. Trigger Dismiss from instance #1 — verify instance #2 is unaffected by instance #1's `PendingIntent` (no cross-instance interference — this exercises the per-instance `widget://.../$appWidgetId` URI disambiguation).

## 13. Device reboot / process death

1. With an outstanding item shown on the widget, force-stop the app (simulating process death) or reboot the device.
2. Immediately after (before the app/service restarts), verify the widget still shows its last-known content on the home screen (RemoteViews content persists visually even though the process is gone) — this is expected, not a bug (see ADR-015 Tradeoffs).
3. Once the companion reconnects (may require manually reopening the app, per the existing no-`BOOT_COMPLETED`-receiver limitation from Milestone 9B.1), verify the widget refreshes to accurate current state via the next `pending_attention` snapshot.
