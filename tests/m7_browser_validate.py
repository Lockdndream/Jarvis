"""Milestone 7 Phase 17: Playwright DOM validation for voice/TTS/
notification/deep-link/reconnect behavior.

Real browser APIs for speech recognition/synthesis require actual
microphone hardware and platform TTS voices that are not present in this
headless CI-style environment, so — per the task spec's explicit guidance
("use controlled mocks for deterministic browser tests... perform explicit
manual real-device validation for the remainder") — SpeechRecognition and
speechSynthesis are replaced with deterministic fakes via
page.add_init_script() before each page loads. Everything else (WebSocket
flow, DOM rendering, notification dedup, deep links, settings endpoints,
service worker registration) is exercised for real against a real running
Jarvis server.

Not covered here — requires a real phone: real microphone audio, real
platform TTS voices, real backgrounded push delivery. See SESSION.md
Milestone 7 Phase 18 for the honest real-phone acceptance report.
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from playwright.async_api import async_playwright

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SERVER_URL = "http://127.0.0.1:8000"
MS = 1000

passed = 0
failed = 0
errors = []

SPEECH_MOCK_INIT_SCRIPT = """
(() => {
  window.__recognitionStartCount = 0;
  window.__ttsCalls = [];

  class FakeSpeechRecognition {
    constructor() {
      window.__lastRecognitionInstance = this;
      this.lang = "en-US";
      this.interimResults = false;
      this.maxAlternatives = 1;
      this.onstart = null; this.onresult = null; this.onerror = null; this.onend = null;
    }
    start() {
      window.__recognitionStartCount += 1;
      var self = this;
      setTimeout(function () { if (self.onstart) self.onstart(); }, 5);
    }
    abort() {
      if (this.onend) this.onend();
    }
    stop() { this.abort(); }
  }
  window.SpeechRecognition = FakeSpeechRecognition;
  window.webkitSpeechRecognition = FakeSpeechRecognition;

  window.SpeechSynthesisUtterance = function (text) {
    this.text = text; this.onend = null; this.onerror = null;
  };
  // window.speechSynthesis is a spec'd getter-only accessor on Window in
  // real Chromium — a plain assignment silently no-ops, leaving the real
  // (voiceless-in-headless) implementation in place. Object.defineProperty
  // is required to actually replace it.
  var fakeSynth = {
    speaking: false,
    cancel: function () {
      window.__ttsCalls.push({ type: "cancel" });
      this.speaking = false;
    },
    speak: function (utter) {
      window.__ttsCalls.push({ type: "speak", text: utter.text });
      this.speaking = true;
      var self = this;
      setTimeout(function () {
        self.speaking = false;
        if (utter.onend) utter.onend();
      }, 15);
    },
  };
  Object.defineProperty(window, "speechSynthesis", { value: fakeSynth, configurable: true, writable: true });
})();
"""

# Real headless Chromium ships a native (if non-functional without a mic)
# SpeechRecognition/webkitSpeechRecognition — so "unsupported browser" can't
# be exercised by simply omitting the mock above; it must be forced off.
UNSUPPORTED_SPEECH_INIT_SCRIPT = """
(() => {
  Object.defineProperty(window, "SpeechRecognition", { value: undefined, configurable: true });
  Object.defineProperty(window, "webkitSpeechRecognition", { value: undefined, configurable: true });
})();
"""


def ok(msg):
    global passed
    passed += 1
    print(f"  [PASS] {msg}")


def fail(msg):
    global failed, errors
    failed += 1
    errors.append(msg)
    print(f"  [FAIL] {msg}")


async def start_server():
    print("  Starting Jarvis server...")
    env = {**os.environ, "JARVIS_TEST_MODE": "1", "PYTHONUNBUFFERED": "1"}
    # This gated server must stay deterministic regardless of what's in the
    # developer machine's .env for real-phone testing (Milestone 7 Phase 18)
    # — real VAPID keys make the push-subscribe scenario attempt a genuine
    # subscription, which Chrome's Push API deterministically refuses in
    # Playwright's incognito-like context, unrelated to what's being tested.
    # Set (not remove) to "" — app.main's load_dotenv() defaults to
    # override=False, so an *already-present* env var (even empty) wins
    # over whatever the .env file on disk says; a merely-absent var would
    # still get filled in from .env.
    for var in ("JARVIS_VAPID_PUBLIC_KEY", "JARVIS_VAPID_PRIVATE_KEY", "JARVIS_VAPID_SUBJECT"):
        env[var] = ""
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "uvicorn", "app.main:app",
        "--host", "127.0.0.1", "--port", "8000", "--log-level", "warning",
        cwd=PROJECT_ROOT, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    for _ in range(20):
        try:
            import urllib.request
            urllib.request.urlopen(SERVER_URL, timeout=2)
            print("  Server is running")
            return proc
        except Exception:
            await asyncio.sleep(1)
    print("  Server did not start")
    proc.kill()
    return None


async def load_page(page, with_speech_mock=True, force_unsupported=False):
    if force_unsupported:
        await page.add_init_script(UNSUPPORTED_SPEECH_INIT_SCRIPT)
    elif with_speech_mock:
        await page.add_init_script(SPEECH_MOCK_INIT_SCRIPT)
    await page.goto(SERVER_URL, wait_until="domcontentloaded")
    await page.locator("#message-input").wait_for(timeout=5 * MS)
    try:
        await page.locator(".status-connected").wait_for(timeout=5 * MS)
    except Exception:
        pass
    await asyncio.sleep(0.5)


async def wait_until(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = await predicate()
        if result:
            return result
        await asyncio.sleep(0.3)
    return await predicate()


# ── Scenario A: voice capability states ────────────────────────────

async def scenario_a(page, context):
    print("\n" + "=" * 60)
    print("SCENARIO A: Voice capability states")
    print("=" * 60)

    await load_page(page, with_speech_mock=True)
    mic = page.locator("#mic-btn")
    if not await mic.is_disabled():
        ok("Mic button enabled when SpeechRecognition is present (mocked)")
    else:
        fail("Mic button should be enabled when SpeechRecognition is present")

    await mic.click()
    listening = await wait_until(lambda: _has_class(page, "#mic-btn", "mic-listening"))
    if listening:
        ok("Mic button enters 'listening' state on start")
    else:
        fail("Mic button never entered 'listening' state")

    # Cancel while listening — must return to idle, must not submit anything.
    before_count = await page.locator(".event-user_message").count()
    await mic.click()
    idle = await wait_until(lambda: _has_class(page, "#mic-btn", "mic-idle"))
    after_count = await page.locator(".event-user_message").count()
    if idle:
        ok("Cancel while listening returns mic button to idle")
    else:
        fail("Mic button stuck after cancel")
    if after_count == before_count:
        ok("Cancelling voice input submits nothing")
    else:
        fail("Cancelling voice input unexpectedly submitted a message")

    # Unsupported browser: real headless Chromium actually ships a native
    # (if non-functional without a mic) SpeechRecognition, so this is
    # forced off explicitly to exercise the unsupported-browser fallback.
    page2 = await context.new_page()
    await load_page(page2, force_unsupported=True)
    mic2 = page2.locator("#mic-btn")
    if await mic2.is_disabled():
        ok("Mic button is disabled when SpeechRecognition is unavailable")
    else:
        fail("Mic button should be disabled without SpeechRecognition support")
    await page2.close()


def _has_class_sync():
    pass


async def _has_class(page, selector, cls):
    try:
        classes = await page.locator(selector).get_attribute("class")
        return classes is not None and cls in classes
    except Exception:
        return False


# ── Scenario B: transcript submission ──────────────────────────────

async def scenario_b(page, context):
    print("\n" + "=" * 60)
    print("SCENARIO B: Transcript submission")
    print("=" * 60)

    await load_page(page, with_speech_mock=True)
    before = await page.locator(".event-user_message").count()

    await page.locator("#mic-btn").click()
    await wait_until(lambda: _has_class(page, "#mic-btn", "mic-listening"))

    await page.evaluate(
        """() => {
            const r = window.__lastRecognitionInstance;
            r.onresult({ results: [[{ transcript: 'what needs my attention' }]] });
        }"""
    )

    async def got_message():
        return await page.locator(".event-user_message").count() > before

    if await wait_until(got_message):
        ok("Transcript entered the user-message pipeline")
    else:
        fail("Transcript never appeared as a user message")

    count_after = await page.locator(".event-user_message").count()
    await asyncio.sleep(1.0)
    count_settled = await page.locator(".event-user_message").count()
    if count_settled == count_after:
        ok("Exactly one message sent for one transcript (no duplicate submission)")
    else:
        fail(f"Duplicate submission detected: {count_after} then {count_settled}")

    idle = await wait_until(lambda: _has_class(page, "#mic-btn", "mic-idle"))
    if idle:
        ok("Mic button returns to idle after submission (not stuck)")
    else:
        fail("Mic button stuck after submission")

    async def got_reply():
        return await page.locator(".event-supervisor_message").count() > 0

    if await wait_until(got_reply):
        ok("Supervisor response rendered for the voice-submitted message")
    else:
        fail("No supervisor response rendered")


# ── Scenario C: speech controls ─────────────────────────────────────

async def scenario_c(page, context):
    print("\n" + "=" * 60)
    print("SCENARIO C: Speech controls")
    print("=" * 60)

    await load_page(page, with_speech_mock=True)

    btn = page.locator("#speech-toggle-btn")
    await btn.click()
    if await btn.get_attribute("aria-pressed") == "true":
        ok("Speech toggle enables spoken responses")
    else:
        fail("Speech toggle did not report enabled")

    await page.locator("#message-input").fill("what tasks are running")
    await page.locator("#message-input").press("Enter")

    async def spoke():
        calls = await page.evaluate("() => window.__ttsCalls.length")
        return calls > 0

    if await wait_until(spoke):
        ok("A concise supervisor reply was spoken while speech is enabled")
    else:
        fail("Nothing was spoken for a supervisor reply with speech enabled")

    # No overlapping utterances: call speak() twice back-to-back and check
    # the immediate count in the *same* evaluate round-trip — a separate
    # round-trip risks the fake's fast auto-resolve already draining the
    # queue before the check runs, which would look identical to a real
    # overlap bug but isn't one.
    immediate_calls = await page.evaluate(
        "() => { window.__ttsCalls = []; speak('first message'); speak('second message'); return window.__ttsCalls.length; }"
    )
    if immediate_calls == 1:
        ok("Second utterance is queued, not started concurrently (no overlap)")
    else:
        fail(f"Expected exactly 1 immediate speak() call, got {immediate_calls}")
    async def queue_drained():
        n = await page.evaluate("() => window.__ttsCalls.length")
        return n >= 2

    await wait_until(queue_drained, timeout=5)
    settled_calls = await page.evaluate("() => window.__ttsCalls.length")
    if settled_calls == 2:
        ok("Queued utterance plays after the first ends")
    else:
        fail(f"Expected 2 total speak() calls after queue drains, got {settled_calls}")

    # Speak something that stays "in progress" until explicitly stopped —
    # override the fake's auto-resolving speak() for just this check so
    # there's no race between browser-automation overhead and a fast fake
    # utterance finishing on its own.
    await page.evaluate(
        """() => {
            window.__ttsCalls = [];
            window.speechSynthesis.speaking = false;
            window.speechSynthesis.speak = function (utter) {
                window.__ttsCalls.push({ type: "speak", text: utter.text });
                this.speaking = true; // never auto-resolves — only stopSpeaking()'s cancel() ends it
            };
            speak('a message that should be interrupted before it finishes speaking on its own');
        }"""
    )
    await page.locator("#speech-stop-btn").wait_for(state="visible", timeout=3 * MS)
    await page.locator("#speech-stop-btn").click()
    cancelled = await page.evaluate("() => window.__ttsCalls.some(c => c.type === 'cancel')")
    if cancelled:
        ok("Stop-speaking button cancels in-progress speech")
    else:
        fail("Stop-speaking button did not cancel speech")

    await btn.click()
    if await btn.get_attribute("aria-pressed") == "false":
        ok("Speech toggle disables spoken responses")
    else:
        fail("Speech toggle did not report disabled")


# ── Scenario D: notification settings ───────────────────────────────

async def scenario_d(page, context):
    print("\n" + "=" * 60)
    print("SCENARIO D: Notification settings")
    print("=" * 60)

    await load_page(page, with_speech_mock=True)

    toggle = page.locator("#notify-completion-toggle-btn")
    await wait_until(lambda: _attr_is(page, "#notify-completion-toggle-btn", "aria-pressed", "true"))
    initial = await toggle.get_attribute("aria-pressed")
    await toggle.click()
    await wait_until(lambda: _attr_changed(page, "#notify-completion-toggle-btn", "aria-pressed", initial), timeout=5)
    flipped = await toggle.get_attribute("aria-pressed")
    if initial != flipped:
        ok(f"Completion-notification toggle flips ({initial} -> {flipped})")
    else:
        fail("Completion-notification toggle did not change state")

    import urllib.request, json
    with urllib.request.urlopen(SERVER_URL + "/api/settings") as resp:
        server_state = json.loads(resp.read())["notify_on_completion"]
    if str(server_state).lower() == flipped:
        ok("Server-side setting matches the toggled UI state")
    else:
        fail(f"Server setting {server_state} does not match UI {flipped}")

    await toggle.click()  # restore default for later scenarios

    opt_in = page.locator("#notify-opt-in-btn")
    await opt_in.click()

    async def status_shown():
        text = await page.locator("#voice-status").inner_text()
        return bool(text.strip())

    # This chain involves two real async round-trips (service-worker
    # registration + a fetch to /api/vapid-public-key) — poll instead of a
    # fixed sleep so it isn't flaky under system load.
    await wait_until(status_shown, timeout=8)
    status_text = await page.locator("#voice-status").inner_text()
    lowered = status_text.lower()
    if "not supported" in lowered or "configured" in lowered or "permission" in lowered:
        ok(f"Push opt-in handled gracefully without configured VAPID keys: {status_text!r}")
    else:
        fail(f"Unexpected push opt-in status text: {status_text!r}")


async def _attr_is(page, selector, attr, value):
    try:
        v = await page.locator(selector).get_attribute(attr)
        return v == value
    except Exception:
        return False


async def _count_at_least(page, selector, minimum):
    try:
        return await page.locator(selector).count() >= minimum
    except Exception:
        return False


async def _attr_changed(page, selector, attr, old_value):
    try:
        v = await page.locator(selector).get_attribute(attr)
        return v != old_value
    except Exception:
        return False


# ── Scenario E: deep links ──────────────────────────────────────────

async def scenario_e(page, context):
    print("\n" + "=" * 60)
    print("SCENARIO E: Deep links")
    print("=" * 60)

    await load_page(page, with_speech_mock=True)
    await page.locator("#message-input").fill("/mock-agent")
    await page.locator("#message-input").press("Enter")

    async def question_appeared():
        return await page.locator(".question-item").count() > 0

    if not await wait_until(question_appeared, timeout=10):
        fail("Setup: mock-agent question never appeared, skipping deep-link checks")
        return

    qid = await page.locator(".question-item").first.get_attribute("data-question-id")
    if not qid:
        fail("Setup: question item missing data-question-id")
        return

    page2 = await context.new_page()
    await load_page(page2, with_speech_mock=True)
    await page2.goto(f"{SERVER_URL}/?question={qid}", wait_until="domcontentloaded")
    await asyncio.sleep(0.5)

    async def question_appeared_2():
        return await page2.locator(".question-item").count() > 0

    await wait_until(question_appeared_2, timeout=10)

    async def highlighted():
        el = page2.locator(f'.question-item[data-question-id="{qid}"]')
        cls = await el.get_attribute("class") if await el.count() > 0 else None
        return cls and "deep-link-highlight" in cls

    if await wait_until(highlighted, timeout=6):
        ok("Deep link scrolls to and highlights the correct pending question")
    else:
        fail("Deep link did not highlight the target question")

    # Answer it, then deep-link to the now-resolved question — must not
    # show stale answer controls, and must surface an "already" message.
    await page.locator(f'.question-answer-input[data-question-id="{qid}"]').fill("B")
    await page.locator(f'.question-send-btn[data-question-id="{qid}"]').click()

    async def resolved():
        import urllib.request, json
        try:
            with urllib.request.urlopen(f"{SERVER_URL}/api/question/{qid}") as resp:
                return json.loads(resp.read())["status"] != "pending"
        except Exception:
            return False

    await wait_until(resolved, timeout=10)

    page3 = await context.new_page()
    await load_page(page3, with_speech_mock=True)
    await page3.goto(f"{SERVER_URL}/?question={qid}", wait_until="domcontentloaded")

    async def stale_message_shown():
        try:
            text = await page3.locator("#voice-status").inner_text()
            return "already" in text.lower()
        except Exception:
            return False

    if await wait_until(stale_message_shown, timeout=10):
        ok("Deep link to an already-resolved question reports it as resolved, no stale controls")
    else:
        fail("Deep link to a resolved question did not report it correctly")

    await page2.close()
    await page3.close()


# ── Scenario F: reconnect dedup ──────────────────────────────────────

async def scenario_f(page, context):
    print("\n" + "=" * 60)
    print("SCENARIO F: Reconnect dedup (notifications, speech, timeline)")
    print("=" * 60)

    await load_page(page, with_speech_mock=True)
    conv_id_1 = await page.evaluate("() => window.localStorage.getItem('jarvis_conversation_id')")

    await page.locator("#message-input").fill("/mock-agent")
    await page.locator("#message-input").press("Enter")

    async def notification_appeared():
        return await page.locator(".event-notification").count() > 0

    got_notification = await wait_until(notification_appeared, timeout=10)
    if got_notification:
        ok("A pending question produces a visible notification in the timeline")
    else:
        fail("No notification rendered for a pending question")

    # notification_appeared() can be satisfied by a *leftover* pending
    # notification replayed from an earlier scenario's still-unread DB row
    # (this scenario's own page.goto reconnects fresh, but the server-side
    # jarvis.db is shared across the whole run) — wait specifically for
    # *this* mock-agent's own question_asked event to populate
    # pendingQuestions before checking what gets spoken for it.
    async def own_question_arrived():
        n = await page.evaluate("() => Object.keys(pendingQuestions).length")
        return n > 0

    await wait_until(own_question_arrived, timeout=10)

    # Real-phone finding (Phase 18): the spoken text for a question
    # notification must be the actual question content, not just the
    # generic "Jarvis needs your answer" placeholder.
    spoken = await page.evaluate(
        """() => {
            var qid = Object.keys(pendingQuestions)[0];
            if (!qid) return null;
            var q = pendingQuestions[qid];
            return spokenTextForNotification({ notification_type: "QUESTION_REQUIRED", task_id: q.task_id, title: "x", body: "y" });
        }"""
    )
    if spoken and "Jarvis needs your answer" not in spoken and len(spoken) > 5:
        ok(f"Spoken text for a question notification uses the real question content: {spoken!r}")
    else:
        fail(f"Spoken text for a question notification fell back to the generic placeholder: {spoken!r}")

    notif_count_before = await page.locator(".event-notification").count()
    tts_before = await page.evaluate("() => window.__ttsCalls.length")

    await page.add_init_script(SPEECH_MOCK_INIT_SCRIPT)  # must be added before reload to apply to it
    await page.reload()
    await page.locator("#message-input").wait_for(timeout=5 * MS)
    await wait_until(lambda: _count_at_least(page, ".event-notification", notif_count_before), timeout=8)

    conv_id_2 = await page.evaluate("() => window.localStorage.getItem('jarvis_conversation_id')")
    if conv_id_1 == conv_id_2:
        ok("Conversation identity preserved across reconnect")
    else:
        fail(f"Conversation identity changed across reconnect: {conv_id_1} -> {conv_id_2}")

    notif_count_after = await page.locator(".event-notification").count()
    if notif_count_after == notif_count_before:
        ok("No duplicate notification rendered after reconnect")
    else:
        fail(f"Notification count changed on reconnect: {notif_count_before} -> {notif_count_after}")

    # Only count actual "speak" calls — setSpeechEnabled() harmlessly calls
    # stopSpeaking() (-> cancel()) once during page init regardless of
    # on/off state, which is not the thing Phase 5/14 cares about.
    speak_calls_after = await page.evaluate("() => window.__ttsCalls.filter(c => c.type === 'speak').length")
    if speak_calls_after == 0:
        ok("Reconnect does not trigger repeated speech for a previously-seen notification")
    else:
        fail(f"Reconnect triggered {speak_calls_after} unexpected speak() call(s)")

    # cleanup: cancel the outstanding mock-agent task so it doesn't leak
    # into later scenarios' pending-question counts
    cancel_btns = page.locator(".cancel-btn")
    count = await cancel_btns.count()
    for i in range(count):
        try:
            await cancel_btns.first.click()
            await asyncio.sleep(0.3)
        except Exception:
            pass


# ── Scenario G: input area stays visible on a short real-phone viewport ──
# Real-phone finding (Milestone 7 Phase 18, 2026-07-09): #needs-attention
# and #active-tasks both had flex-shrink: 0, so on a real device with a
# real pending question showing, they refused to shrink even when there
# wasn't enough screen height left for #input-area — the mic button and
# text box were pushed off the bottom of the screen entirely, with no way
# to type or answer a question at all, until the app was fully closed and
# reopened. This scenario uses its own short-viewport context (independent
# of scenarios A-F's shared page) to guard against that regressing.

async def scenario_g(context):
    print("\n" + "=" * 60)
    print("SCENARIO G: #input-area stays visible under a short viewport with a real pending question")
    print("=" * 60)

    page = await context.new_page()
    await load_page(page, with_speech_mock=False)
    # Deliberately short — simulates real Android browser/OS chrome
    # overhead (address bar, gesture nav bar) reducing the actually-visible
    # viewport well below a typical device's full screen height.
    await page.set_viewport_size({"width": 384, "height": 550})

    await page.locator("#message-input").fill("/mock-agent")
    await page.locator("#message-input").press("Enter")

    try:
        await page.locator(".question-item").first.wait_for(state="visible", timeout=15000)
    except Exception:
        fail("Scenario G setup: mock-agent question never appeared")
        await page.close()
        return

    vh = await page.evaluate("() => window.innerHeight")
    for sel, label in [("#input-area", "Input area"), ("#message-input", "Message text box"), ("#mic-btn", "Mic button")]:
        el = page.locator(sel)
        visible = await el.is_visible()
        box = await el.bounding_box()
        fits = bool(box) and box["y"] >= 0 and (box["y"] + box["height"]) <= vh
        if visible and fits:
            ok(f"{label} remains fully visible within the viewport under a short/constrained screen height")
        else:
            fail(f"{label} is not fully visible within the viewport (visible={visible}, box={box}, viewport_height={vh})")

    # cleanup
    cancel_btns = page.locator(".cancel-btn")
    count = await cancel_btns.count()
    for i in range(count):
        try:
            await cancel_btns.first.click()
            await asyncio.sleep(0.3)
        except Exception:
            pass
    await page.close()


# ── Scenario H: per-question mic answers that specific question directly ──
# Real-phone finding (Milestone 7 Phase 18): a natural spoken answer from
# the *main* chat mic ("approach a") didn't connect to the pending question
# — it routes through the full supervisor/LLM pipeline. This mic, next to
# the question's own custom-answer box, answers *that* question directly
# via /answer, bypassing that ambiguity entirely.

async def scenario_h(context):
    print("\n" + "=" * 60)
    print("SCENARIO H: Per-question mic answers that question directly")
    print("=" * 60)

    page = await context.new_page()
    await load_page(page, with_speech_mock=True)

    await page.locator("#message-input").fill("/mock-agent")
    await page.locator("#message-input").press("Enter")

    try:
        await page.locator(".question-item").first.wait_for(state="visible", timeout=15000)
    except Exception:
        fail("Scenario H setup: mock-agent question never appeared")
        await page.close()
        return

    qid = await page.locator(".question-item").first.get_attribute("data-question-id")
    mic_btn = page.locator(f'.question-mic-btn[data-question-id="{qid}"]')

    if await mic_btn.count() == 0:
        fail("Per-question mic button not rendered for a pending question")
        await page.close()
        return
    ok("Per-question mic button is present next to the custom-answer box")

    await mic_btn.click()

    async def mic_listening():
        cls = await mic_btn.get_attribute("class")
        return cls and "mic-listening" in cls

    if await wait_until(mic_listening, timeout=5):
        ok("Per-question mic button enters listening state on click")
    else:
        fail("Per-question mic button never entered listening state")

    await page.evaluate(
        """() => {
            const r = window.__lastRecognitionInstance;
            r.onresult({ results: [[{ transcript: 'approach a' }]] });
        }"""
    )

    async def question_resolved():
        import urllib.request, json as _json
        try:
            with urllib.request.urlopen(f"{SERVER_URL}/api/question/{qid}") as resp:
                return _json.loads(resp.read())["status"] != "pending"
        except Exception:
            return False

    if await wait_until(question_resolved, timeout=8):
        ok("Per-question voice answer resolved the correct question directly")
    else:
        fail("Per-question voice answer did not resolve the question")

    await page.close()


async def main():
    proc = await start_server()
    if not proc:
        print("FATAL: could not start server")
        sys.exit(1)

    console_errors = []
    network_failures = []

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            context = await browser.new_context(permissions=["notifications"])
            page = await context.new_page()
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
            page.on("requestfailed", lambda req: network_failures.append(req.url))

            for name, fn in [
                ("A", scenario_a), ("B", scenario_b), ("C", scenario_c),
                ("D", scenario_d), ("E", scenario_e), ("F", scenario_f),
            ]:
                try:
                    await fn(page, context)
                except Exception as e:
                    fail(f"Scenario {name} raised an exception: {e}")

            try:
                await scenario_g(context)
            except Exception as e:
                fail(f"Scenario G raised an exception: {e}")

            try:
                await scenario_h(context)
            except Exception as e:
                fail(f"Scenario H raised an exception: {e}")

            await browser.close()
    finally:
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=5)
        except ProcessLookupError:
            pass
        except asyncio.TimeoutError:
            proc.kill()

    print("\n" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 60)
    if console_errors:
        print(f"Console errors observed ({len(console_errors)}):")
        for e in console_errors[:10]:
            print(f"  - {e}")
    else:
        print("No browser console errors observed.")
    if network_failures:
        print(f"Failed network requests ({len(network_failures)}):")
        for u in network_failures[:10]:
            print(f"  - {u}")
    else:
        print("No failed network requests observed.")
    if errors:
        print("\nFailures:")
        for e in errors:
            print(f"  - {e}")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
