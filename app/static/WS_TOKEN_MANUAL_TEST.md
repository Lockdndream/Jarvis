# Manual Test Plan — Milestone 9B.2 / ADR-014 (WS Token Auth)

## Prerequisites

- Server running with `JARVIS_API_TOKEN` **unset** (default local dev mode).
- Browser DevTools open (F12), **Network** tab, filter: `ws` / `websocket` / `api/ws-token`.

---

## 1. Fresh page load — token fetch on first connect

1. Clear all site data (Application > Storage > Clear site data) or open an incognito/private window.
2. Load the app.
3. **Network tab** — verify a `POST /api/ws-token` request appears **before** the WebSocket upgrade request.
4. Verify the POST request body is `{"client_id": "<uuid string>"}`.
5. Verify the response body contains `token` (a JWT string), `expires_at` (unix epoch seconds), `expires_in` (seconds).
6. **WS frames tab** (or the WS entry in Network) — verify the WebSocket connection URL contains `?token=<the JWT>`.
7. Verify the WebSocket connects successfully (status `101`), the status dot shows green "Connected".

## 2. Token reuse on reconnect (within expiry window)

1. After step 1, trigger a reconnect by closing the WS from the server or killing the server process and restarting it.
2. Wait for the 3-second reconnect timer.
3. **Network tab** — verify **no** new `POST /api/ws-token` request appears (the cached token is reused).
4. Verify the WebSocket reconnects successfully with the same `?token=` value.

## 3. Token refresh when near expiry (< 120 s remaining)

1. Wait until the token's `expires_at` is less than 120 seconds away (or short-circuit by editing `wsTokenExpiresAt` in the DevTools console to a near-future value).
2. Trigger a reconnect.
3. **Network tab** — verify a new `POST /api/ws-token` request appears.
4. Verify the WebSocket connects with the **new** token.

## 4. Token fetch failure — graceful retry

1. Stop the server entirely.
2. Load the app (or trigger a reconnect).
3. Verify the status dot shows red "Error".
4. **Network tab** — verify the `POST /api/ws-token` request fails (no response / connection refused).
5. Wait 3 seconds — verify the client retries: another `POST /api/ws-token` appears.
6. Start the server again — verify the next retry succeeds and the WebSocket connects.

## 5. WS close code 4001 (expired token) — cache cleared, fresh token fetched

1. Set a breakpoint or inject a server-side response that closes the WS with code `4001` after connection.
2. After the WS closes with code `4001`:
   - Verify `wsToken` is cleared (check `wsToken` in console — should be `null`).
   - Verify the 3-second reconnect timer fires.
   - Verify a **new** `POST /api/ws-token` request appears (fresh token is fetched).
3. Verify the WebSocket reconnects successfully.

## 6. WS close code 4002 (invalid/malformed token) — cache cleared, fresh token fetched

1. Same as step 5 but with close code `4002`.
2. Verify the same behavior: cache cleared, fresh token fetched, reconnect succeeds.

## 7. WS close code 4003 (no credential — JARVIS_API_TOKEN set server-side)

1. Set `JARVIS_API_TOKEN` on the server and restart.
2. Load the app.
3. Verify the WS closes with code `4003`.
4. Verify `wsToken` is **not** cleared (cache is left intact — the problem is the server requires a credential the client doesn't have, not a bad token).
5. Verify the reconnect timer fires and the client retries (expected: loops with 4003, which is the known limitation documented in the task).

## 8. Persisted clientId across page loads

1. After step 1, reload the page.
2. **Network tab** — verify the `POST /api/ws-token` body contains the **same** `client_id` as the previous load (check Application > Local Storage > `jarvis_client_id`).
3. Verify the token is fetched and the WS connects successfully.

## 9. No clientId (localStorage blocked)

1. In DevTools, go to Application > Storage and check "Block third-party cookies" or set the console to disable localStorage (`Object.defineProperty(window, 'localStorage', { get: function() { throw new Error('no ls'); } })`).
2. Reload the page.
3. Verify the WS connects **without** a `?token=` parameter (no clientId available → fallback to bare `/ws`).
4. If the server `JARVIS_API_TOKEN` is unset, the connection should succeed.