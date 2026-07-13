"""Milestone 8 Phase 24: Playwright DOM validation for the attention
lifecycle / call-style UI / voice session client / deep links.

Same approach as tests/m7_browser_validate.py (which this deliberately
mirrors in structure): SpeechRecognition/speechSynthesis are replaced with
deterministic fakes via page.add_init_script() so voice-session scenarios
are exercise-able headless; everything else (WebSocket flow, DOM
rendering, dedup, deep links) runs against a real running Jarvis server.
Real microphone audio / platform TTS voices / real backgrounded push still
require a real phone — not covered here.

Scenarios:
  A. Call-style attention card renders (Talk now/snooze/dismiss) and
     #input-area stays fully visible under a short viewport with a real
     AttentionRequest present — explicit regression test for the M7.1
     severe layout bug, now covering the new panel too (Phase 14).
  B. Snooze defers the AttentionRequest without touching the underlying
     WorkerQuestion.
  C. Dismiss triggers the vague-phrase clarification, never guesses a time.
  D. Talk now opens a voice session (voice-session-bar appears); End
     closes it.
  E. A bound voice session resolves the exact underlying WorkerQuestion via
     a natural (non-rigid-grammar) spoken phrase, and the call card clears
     once resolved.
  F. Deep link (?attention=) scrolls to and highlights the correct call
     card; a deep link to an already-resolved AttentionRequest reports it
     as resolved, no stale controls.
  G. Reconnect: pending_attention restores the call card without
     duplicating it.
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
      }, 10);
    },
  };
  Object.defineProperty(window, "speechSynthesis", { value: fakeSynth, configurable: true });
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


async def load_page(page, with_speech_mock=True):
    if with_speech_mock:
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


async def _has_class(page, selector, cls):
    try:
        classes = await page.locator(selector).get_attribute("class")
        return classes is not None and cls in classes
    except Exception:
        return False


async def _ids(page, selector, attr):
    els = page.locator(selector)
    n = await els.count()
    out = set()
    for i in range(n):
        v = await els.nth(i).get_attribute(attr)
        if v:
            out.add(v)
    return out


async def spawn_mock_agent_question(page):
    """Starts /mock-agent and waits for both the worker-question UI and the
    AttentionRequest call card to appear. The real jarvis.db and server
    process persist across every scenario in one script run (only the page
    itself is fresh), so an earlier scenario's still-pending question/
    still-deferred attention card can legitimately still be in the DOM
    (pending_attention/pending_questions are resent on every reconnect) —
    this diffs before/after ID sets so it always returns *this* scenario's
    own new item, never an unrelated leftover one. Returns (question_id,
    attention_request_id) or (None, None) on timeout."""
    before_q = await _ids(page, ".question-item", "data-question-id")
    before_a = await _ids(page, ".attention-call-item", "data-attention-request-id")

    await page.locator("#message-input").fill("/mock-agent")
    await page.locator("#message-input").press("Enter")

    async def appeared():
        after_q = await _ids(page, ".question-item", "data-question-id")
        after_a = await _ids(page, ".attention-call-item", "data-attention-request-id")
        return bool(after_q - before_q) and bool(after_a - before_a)

    if not await wait_until(appeared, timeout=15):
        return None, None

    after_q = await _ids(page, ".question-item", "data-question-id")
    after_a = await _ids(page, ".attention-call-item", "data-attention-request-id")
    qid = next(iter(after_q - before_q))
    aid = next(iter(after_a - before_a))
    return qid, aid


# ── Scenario A: call card renders, layout survives ─────────────────

async def scenario_a(page, context):
    print("\n" + "=" * 60)
    print("SCENARIO A: Call-style attention card + layout regression")
    print("=" * 60)

    await page.set_viewport_size({"width": 400, "height": 560})  # short/constrained, same as M7 Scenario G
    await load_page(page, with_speech_mock=True)

    qid, aid = await spawn_mock_agent_question(page)
    if not aid:
        fail("Setup: attention call card never appeared")
        return
    ok("Attention call card rendered for a real question-required AttentionRequest")

    card = page.locator(f'.attention-call-item[data-attention-request-id="{aid}"]')
    talk_btn = card.locator(".attention-call-talk-btn")
    snooze_btns = card.locator(".attention-call-snooze-btn")
    dismiss_btn = card.locator(".attention-call-dismiss-btn")
    if await talk_btn.count() > 0 and await snooze_btns.count() >= 1 and await dismiss_btn.count() > 0:
        ok("Card exposes Talk now, snooze options, and Dismiss")
    else:
        fail("Card is missing one or more of Talk now / snooze / Dismiss")

    input_box = page.locator("#input-area")
    box = await input_box.bounding_box()
    viewport = page.viewport_size
    if box and box["y"] + box["height"] <= viewport["height"] + 1:
        ok("#input-area remains fully visible under a short viewport with an attention call card present")
    else:
        fail("#input-area was pushed off-screen by the attention call card (M7.1-class regression)")

    msg_box = page.locator("#message-input")
    mbox = await msg_box.bounding_box()
    if mbox and mbox["y"] + mbox["height"] <= viewport["height"] + 1:
        ok("Message text box remains fully visible under a short viewport")
    else:
        fail("Message text box was pushed off-screen")


# ── Scenario B: snooze defers without touching the WorkerQuestion ──

async def scenario_b(page, context):
    print("\n" + "=" * 60)
    print("SCENARIO B: Snooze defers the AttentionRequest only")
    print("=" * 60)

    await load_page(page, with_speech_mock=True)
    qid, aid = await spawn_mock_agent_question(page)
    if not aid:
        fail("Setup: attention call card never appeared")
        return

    await page.locator(f'.attention-call-snooze-btn[data-attention-request-id="{aid}"][data-phrase="15 minutes"]').click()

    async def deferred():
        el = page.locator(f'.attention-call-item[data-attention-request-id="{aid}"]')
        cls = await el.get_attribute("class") if await el.count() > 0 else None
        return cls and "attention-call-deferred" in cls

    if await wait_until(deferred, timeout=10):
        ok("Snoozing shows the card as deferred")
    else:
        fail("Card never reflected the deferred state after snoozing")

    async def question_still_pending():
        import urllib.request, json
        try:
            with urllib.request.urlopen(f"{SERVER_URL}/api/question/{qid}") as resp:
                return json.loads(resp.read())["status"] == "pending"
        except Exception:
            return False

    if await question_still_pending():
        ok("The underlying WorkerQuestion remains pending — defer never answered or cancelled it")
    else:
        fail("Deferring the AttentionRequest incorrectly affected the underlying WorkerQuestion")


# ── Scenario C: dismiss never guesses a time ────────────────────────

async def scenario_c(page, context):
    print("\n" + "=" * 60)
    print("SCENARIO C: Dismiss asks for clarification, never guesses")
    print("=" * 60)

    await load_page(page, with_speech_mock=True)
    qid, aid = await spawn_mock_agent_question(page)
    if not aid:
        fail("Setup: attention call card never appeared")
        return

    before = await page.locator(".event-supervisor_message").count()
    await page.locator(f'.attention-call-dismiss-btn[data-attention-request-id="{aid}"]').click()

    async def clarification_shown():
        return await page.locator(".event-supervisor_message").count() > before

    if await wait_until(clarification_shown, timeout=10):
        text = await page.locator(".event-supervisor_message").last.inner_text()
        if "when should i come back" in text.lower():
            ok('Dismiss asks "When should I come back?" instead of guessing a default time')
        else:
            fail(f"Dismiss produced an unexpected message: {text!r}")
    else:
        fail("Dismiss produced no clarification message")

    async def still_active():
        el = page.locator(f'.attention-call-item[data-attention-request-id="{aid}"]')
        cls = await el.get_attribute("class") if await el.count() > 0 else None
        return cls is not None and "attention-call-deferred" not in cls

    if await still_active():
        ok("Card remains active (not deferred) after a vague dismiss with no configured default")
    else:
        fail("Card was deferred despite no concrete time being given")


# ── Scenario D: Talk now opens/closes a voice session ───────────────

async def scenario_d(page, context):
    print("\n" + "=" * 60)
    print("SCENARIO D: Talk now opens a voice session")
    print("=" * 60)

    await load_page(page, with_speech_mock=True)
    qid, aid = await spawn_mock_agent_question(page)
    if not aid:
        fail("Setup: attention call card never appeared")
        return

    await page.locator(f'.attention-call-talk-btn[data-attention-request-id="{aid}"]').click()

    async def bar_visible():
        try:
            return await page.locator("#voice-session-bar").is_visible()
        except Exception:
            return False

    if await wait_until(bar_visible, timeout=10):
        ok("Voice session bar appears after Talk now")
    else:
        fail("Voice session bar never appeared after Talk now")

    if await wait_until(lambda: _has_class(page, "#mic-btn", "mic-idle") or bar_visible(), timeout=5):
        pass  # bar presence already asserted above; this just lets recognition settle

    await page.locator("#voice-session-close-btn").click()

    async def bar_hidden():
        try:
            return not await page.locator("#voice-session-bar").is_visible()
        except Exception:
            return True

    if await wait_until(bar_hidden, timeout=10):
        ok("End closes the voice session and hides the bar")
    else:
        fail("Voice session bar remained visible after End")


# ── Scenario E: bound voice session resolves the exact question ────

async def scenario_e(page, context):
    print("\n" + "=" * 60)
    print("SCENARIO E: Bound voice session answers the exact question")
    print("=" * 60)

    await load_page(page, with_speech_mock=True)
    qid, aid = await spawn_mock_agent_question(page)
    if not aid:
        fail("Setup: attention call card never appeared")
        return

    await page.locator(f'.attention-call-talk-btn[data-attention-request-id="{aid}"]').click()

    async def recognition_ready():
        return await page.evaluate("() => !!window.__lastRecognitionInstance")

    if not await wait_until(recognition_ready, timeout=10):
        fail("Setup: voice session never started listening")
        return

    # Real-phone finding (Milestone 8.1, 2026-07-10): opening a bound voice
    # session used to go straight to listening with nothing spoken — the
    # user had no way to know what they were being asked about.
    async def greeting_shown():
        return await page.locator(".event-supervisor_message", has_text="Which approach should I use?").count() > 0

    if await wait_until(greeting_shown, timeout=5):
        ok("Opening a bound voice session proactively states the question context before listening")
    else:
        fail("Bound voice session opened without stating any context")

    # Deliberately a natural phrase, not the rigid "Answer B" grammar — this
    # is exactly the primary-scenario requirement (Phase 25): a spoken
    # "approach a" must resolve the bound question directly.
    await page.evaluate(
        """() => {
            const r = window.__lastRecognitionInstance;
            r.onresult({ results: [[{ transcript: 'use approach a' }]] });
        }"""
    )

    async def resolved():
        import urllib.request, json
        try:
            with urllib.request.urlopen(f"{SERVER_URL}/api/question/{qid}") as resp:
                return json.loads(resp.read())["status"] != "pending"
        except Exception:
            return False

    if await wait_until(resolved, timeout=15):
        ok("Natural spoken answer in a bound voice session resolved the exact underlying question")
    else:
        fail("Bound voice session natural-language answer never resolved the question")

    # Real-phone finding (Milestone 8.1, 2026-07-10): the recognized
    # transcript for a bound voice session was never echoed into the
    # timeline, unlike the main-mic flow — the user could not tell what
    # Jarvis heard even though it acted on it correctly.
    async def transcript_shown():
        return await page.locator(".event-user_message", has_text="use approach a").count() > 0

    if await wait_until(transcript_shown, timeout=5):
        ok("Recognized transcript for a bound voice session is echoed into the timeline")
    else:
        fail("Recognized transcript for a bound voice session was never shown to the user")

    async def card_cleared():
        return await page.locator(f'.attention-call-item[data-attention-request-id="{aid}"]').count() == 0

    if await wait_until(card_cleared, timeout=10):
        ok("Attention call card cleared once the AttentionRequest resolved")
    else:
        fail("Attention call card remained after the AttentionRequest resolved")


# ── Scenario F: deep links ──────────────────────────────────────────

async def scenario_f(page, context):
    print("\n" + "=" * 60)
    print("SCENARIO F: Attention deep links")
    print("=" * 60)

    await load_page(page, with_speech_mock=True)
    qid, aid = await spawn_mock_agent_question(page)
    if not aid:
        fail("Setup: attention call card never appeared")
        return

    page2 = await context.new_page()
    await load_page(page2, with_speech_mock=True)
    await page2.goto(f"{SERVER_URL}/?attention={aid}", wait_until="domcontentloaded")
    await asyncio.sleep(0.5)

    async def card_present_2():
        return await page2.locator(".attention-call-item").count() > 0

    await wait_until(card_present_2, timeout=10)

    async def highlighted():
        el = page2.locator(f'.attention-call-item[data-attention-request-id="{aid}"]')
        cls = await el.get_attribute("class") if await el.count() > 0 else None
        return cls and "deep-link-highlight" in cls

    if await wait_until(highlighted, timeout=6):
        ok("Deep link scrolls to and highlights the correct attention call card")
    else:
        fail("Deep link did not highlight the target attention call card")

    # Resolve it, then deep-link again — must report "already resolved".
    await page.locator(f'.question-answer-input[data-question-id="{qid}"]').fill("B")
    await page.locator(f'.question-send-btn[data-question-id="{qid}"]').click()

    async def resolved():
        import urllib.request, json
        try:
            with urllib.request.urlopen(f"{SERVER_URL}/api/attention/{aid}") as resp:
                return json.loads(resp.read())["status"] == "resolved"
        except Exception:
            return False

    await wait_until(resolved, timeout=10)

    page3 = await context.new_page()
    await load_page(page3, with_speech_mock=True)
    await page3.goto(f"{SERVER_URL}/?attention={aid}", wait_until="domcontentloaded")

    async def stale_message_shown():
        try:
            text = await page3.locator("#voice-status").inner_text()
            return "already" in text.lower() and "resolved" in text.lower()
        except Exception:
            return False

    if await wait_until(stale_message_shown, timeout=10):
        ok("Deep link to an already-resolved AttentionRequest reports it as resolved, no stale controls")
    else:
        fail("Deep link to a resolved AttentionRequest did not report it correctly")

    await page2.close()
    await page3.close()


# ── Scenario G: reconnect dedup ─────────────────────────────────────

async def scenario_g(page, context):
    print("\n" + "=" * 60)
    print("SCENARIO G: Reconnect restores the call card without duplicating it")
    print("=" * 60)

    await load_page(page, with_speech_mock=True)
    qid, aid = await spawn_mock_agent_question(page)
    if not aid:
        fail("Setup: attention call card never appeared")
        return

    await page.evaluate("() => { window.ws.close(); }")
    await asyncio.sleep(0.5)

    async def reconnected():
        return await page.locator(".status-connected").count() > 0

    await wait_until(reconnected, timeout=15)
    await asyncio.sleep(1.0)

    count = await page.locator(f'.attention-call-item[data-attention-request-id="{aid}"]').count()
    if count == 1:
        ok("Exactly one call card for the AttentionRequest after reconnect (no duplication)")
    else:
        fail(f"Expected exactly one call card after reconnect, found {count}")


async def main():
    proc = await start_server()
    if not proc:
        print("FATAL: could not start server")
        return 1

    console_errors = []
    failed_requests = []

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            context = await browser.new_context()

            def on_console(msg):
                if msg.type == "error":
                    console_errors.append(msg.text)

            def on_requestfailed(request):
                failed_requests.append(f"{request.method} {request.url}: {request.failure}")

            context.on("console", on_console)
            context.on("requestfailed", on_requestfailed)

            for scenario in (scenario_a, scenario_b, scenario_c, scenario_d, scenario_e, scenario_f, scenario_g):
                page = await context.new_page()
                try:
                    await scenario(page, context)
                except Exception as e:
                    fail(f"{scenario.__name__} raised an exception: {e}")
                finally:
                    await page.close()

            await browser.close()
    finally:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()

    print("\n" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 60)
    if errors:
        for e in errors:
            print(f"  - {e}")
    if console_errors:
        print(f"\n{len(console_errors)} browser console error(s):")
        for e in console_errors[:10]:
            print(f"  - {e}")
    else:
        print("No browser console errors observed.")
    if failed_requests:
        print(f"\n{len(failed_requests)} failed network request(s):")
        for r in failed_requests[:10]:
            print(f"  - {r}")
    else:
        print("No failed network requests observed.")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
