/* Jarvis service worker (Milestone 7).
   Scope: "/" (served from GET /sw.js, not /static/sw.js — see app/main.py).

   Responsibilities:
   1. Minimal app-shell caching so the UI itself can load offline/on a
      flaky connection. Never caches WebSocket traffic or /api/* responses
      — those must always be live, never stale.
   2. Web Push: display a notification when a push event arrives.
   3. notificationclick: route to the correct deep link inside the app.

   Never caches or logs raw audio, API keys, or push payload contents
   beyond what's needed to render the notification itself (Phase 15).
*/

// Bump this whenever any SHELL_FILES content changes — a byte-level diff
// of sw.js itself is what makes the browser notice an update is available
// (cache-first serving of app.js/style.css otherwise means edits to those
// files alone go unnoticed indefinitely; real-phone finding, Phase 18).
const CACHE_NAME = "jarvis-shell-v4";
const SHELL_FILES = ["/", "/static/style.css", "/static/app.js", "/manifest.json"];

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then(function (cache) {
        return cache.addAll(SHELL_FILES);
      })
      .then(function () {
        return self.skipWaiting();
      })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches
      .keys()
      .then(function (names) {
        return Promise.all(
          names
            .filter(function (n) {
              return n !== CACHE_NAME;
            })
            .map(function (n) {
              return caches.delete(n);
            })
        );
      })
      .then(function () {
        return self.clients.claim();
      })
  );
});

self.addEventListener("fetch", function (event) {
  var url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.pathname.indexOf("/api/") === 0 || url.pathname === "/ws") {
    return; // never intercept API calls or the WebSocket upgrade
  }
  if (SHELL_FILES.indexOf(url.pathname) === -1) {
    return; // only the fixed app-shell list is ever served from cache
  }
  event.respondWith(
    caches.match(event.request).then(function (cached) {
      var network = fetch(event.request)
        .then(function (resp) {
          if (resp && resp.ok) {
            caches.open(CACHE_NAME).then(function (cache) {
              cache.put(event.request, resp.clone());
            });
          }
          return resp;
        })
        .catch(function () {
          return cached;
        });
      return cached || network;
    })
  );
});

self.addEventListener("push", function (event) {
  var data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = {};
  }
  var title = data.title || "Jarvis";
  var body = data.body || "Jarvis has an update for you.";
  event.waitUntil(
    self.registration.showNotification(title, {
      body: body,
      tag: data.notification_id || undefined,
      renotify: false,
      icon: "/static/icons/icon.svg",
      badge: "/static/icons/icon.svg",
      data: data,
    })
  );
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  var data = event.notification.data || {};
  var params = new URLSearchParams();
  if (data.notification_id) params.set("notification", data.notification_id);
  if (data.task_id) params.set("task", data.task_id);
  if (data.conversation_id) params.set("conversation", data.conversation_id);
  var targetUrl = "/" + (params.toString() ? "?" + params.toString() : "");

  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (clientsArr) {
      for (var i = 0; i < clientsArr.length; i++) {
        var client = clientsArr[i];
        if ("focus" in client) {
          if ("navigate" in client) client.navigate(targetUrl);
          return client.focus();
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(targetUrl);
      }
    })
  );
});
