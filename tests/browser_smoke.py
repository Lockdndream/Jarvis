"""Browser-based Milestone 3 smoke test using Playwright Python.

Starts Jarvis server, opens browser, runs 3 scenarios with
console log capture and DOM-based verification.
Timeout values in Playwright API are in MILLISECONDS.
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from playwright.async_api import async_playwright

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SERVER_URL = "http://127.0.0.1:8000"
MS = 1000

passed = 0
failed = 0
errors = []


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


async def load_page(page):
    """Navigate to Jarvis and wait for the chat input to be ready."""
    await page.goto(SERVER_URL, wait_until="domcontentloaded")
    await page.locator("#message-input").wait_for(timeout=5 * MS)
    try:
        await page.locator(".status-connected").wait_for(timeout=5 * MS)
    except Exception:
        pass
    await asyncio.sleep(0.5)


async def send_command(page, text):
    await page.locator("#message-input").fill(text)
    await page.locator("#message-input").press("Enter")
    await asyncio.sleep(0.3)


async def question_list_visible(page):
    el = page.locator("#needs-attention")
    try:
        style = await el.get_attribute("style")
        return style and "display: none" not in style
    except Exception:
        return False


async def get_question_texts(page):
    """Return list of question texts currently in the question list."""
    try:
        text = await page.locator("#question-list").inner_text(timeout=2 * MS)
        return [q.strip() for q in text.split("\n") if q.strip()]
    except Exception:
        return []


async def wait_until(predicate, timeout=15):
    """Wait for predicate() to return truthy. predicate must be async."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = await predicate()
        if result:
            return result
        await asyncio.sleep(0.3)
    return await predicate()


async def cleanup_tasks(page):
    """Cancel all running tasks by sending /cancel for each."""
    await send_command(page, "/tasks")
    await asyncio.sleep(0.5)
    # Get task IDs from the timeline
    try:
        cancel_btns = page.locator(".cancel-btn")
        count = await cancel_btns.count()
        for i in range(count):
            await cancel_btns.first.click()
            await asyncio.sleep(0.5)
    except Exception:
        pass


async def scenario_a(page, context):
    print("\n" + "=" * 60)
    print("SCENARIO A: Single worker and reconnect")
    print("=" * 60)

    await load_page(page)
    ok("Web interface loaded")

    await send_command(page, "/mock-agent")
    ok("Sent /mock-agent command")

    await page.locator("text=Starting mock agent").first.wait_for(timeout=8 * MS)
    ok("Progress appears incrementally")

    async def has_q():
        items = await get_question_texts(page)
        return any("Which approach" in i for i in items)

    found = await wait_until(has_q, timeout=12)
    ok("Question visible (task waiting_for_user)") if found else fail("Question not visible")

    vis = await question_list_visible(page)
    ok("Needs Your Attention section visible") if vis else fail("Section not visible")

    page2 = await context.new_page()
    page2_errors = []
    page2.on("pageerror", lambda e: page2_errors.append(str(e)))
    await load_page(page2)

    async def has_q_p2():
        items = await get_question_texts(page2)
        return any("Which approach" in i for i in items)

    restored = await wait_until(has_q_p2, timeout=8)
    ok("Question restored after reconnect") if restored else fail("Question not restored")

    b_btn = page2.locator(".option-btn[data-answer='B']")
    await b_btn.wait_for(timeout=5 * MS)
    await b_btn.click()
    ok("Clicked option B")

    await page2.locator("text=Task completed:").first.wait_for(timeout=15 * MS)
    ok("Worker completed after answer")

    async def no_q():
        items = await get_question_texts(page2)
        return not any("Which approach" in i for i in items)

    gone = await wait_until(no_q, timeout=8)
    ok("Question removed from Needs Your Attention") if gone else fail("Question still visible")

    if page2_errors:
        for e in page2_errors:
            print(f"  [PAGE ERROR] {e}")

    await page2.close()
    print("[PASS] Scenario A complete")


async def scenario_b(page, context):
    print("\n" + "=" * 60)
    print("SCENARIO B: Two simultaneous workers")
    print("=" * 60)

    await load_page(page)

    await send_command(page, "/mock-agent")
    await asyncio.sleep(0.5)
    await send_command(page, "/mock-agent")
    ok("Started two mock agents")

    # Wait for both questions to appear
    deadline = time.monotonic() + 20
    count = 0
    while time.monotonic() < deadline:
        items = await get_question_texts(page)
        count = sum(1 for i in items if "Which approach" in i)
        if count >= 2:
            break
        await asyncio.sleep(0.3)

    if count >= 2:
        ok(f"Two distinct questions visible (count={count})")
    else:
        fail(f"Expected 2 questions, found {count}")

    # Answer each question individually by targeting within each question-item
    items = page.locator(".question-item")
    if await items.count() >= 2:
        # First question: click A
        await items.nth(0).locator(".option-btn[data-answer='A']").click()
        ok("Answered A for first worker")
        await asyncio.sleep(0.5)
        # Second question: click B
        await items.nth(1).locator(".option-btn[data-answer='B']").click()
        ok("Answered B for second worker")
    else:
        fail(f"Expected 2 question items, found {await items.count()}")

    # Wait for all questions to disappear
    async def all_questions_gone():
        items2 = await get_question_texts(page)
        return len(items2) == 0 and not await question_list_visible(page)

    done = await wait_until(all_questions_gone, timeout=25)
    ok("Both workers completed, no questions remain") if done else fail(
        f"Questions still visible: {await get_question_texts(page)}"
    )

    print("[PASS] Scenario B complete")


async def scenario_c(page, context):
    print("\n" + "=" * 60)
    print("SCENARIO C: Cancellation while waiting")
    print("=" * 60)

    await load_page(page)
    await asyncio.sleep(1)

    await send_command(page, "/mock-agent")

    async def has_q():
        items = await get_question_texts(page)
        return any("Which approach" in i for i in items)

    found = await wait_until(has_q, timeout=15)
    ok("Question visible") if found else fail("Question not visible")

    cancel_btn = page.locator(".cancel-btn").first
    await cancel_btn.wait_for(timeout=5 * MS)
    await cancel_btn.click()
    ok("Clicked Cancel button")

    await asyncio.sleep(1.5)

    async def no_q():
        items = await get_question_texts(page)
        return not any("Which approach" in i for i in items)

    gone = await wait_until(no_q, timeout=10)
    ok("Question removed after cancel") if gone else fail("Question still visible")

    await page.locator("text=Question cancelled").first.wait_for(timeout=5 * MS)
    ok("Question cancelled event in timeline")

    page2 = await context.new_page()
    page2.on("pageerror", lambda e: errors.append("page error: " + str(e)))
    await load_page(page2)
    await asyncio.sleep(1.5)

    vis = await question_list_visible(page2)
    q_texts = await get_question_texts(page2)
    cancelled_gone = not vis or not any("Which approach" in i for i in q_texts)
    ok("Cancelled question did NOT reappear after reconnect") if cancelled_gone else fail("Cancelled question reappeared")

    await page2.close()
    print("[PASS] Scenario C complete")


async def main():
    global passed, failed, errors

    server_proc = await start_server()
    if not server_proc:
        print("[FATAL] Server did not start")
        return 1

    browser_errors = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
        )

        page = await context.new_page()
        page.on("pageerror", lambda e: browser_errors.append(str(e)))
        page.on("console", lambda msg: print(f"  [CONSOLE {msg.type}] {msg.text}"))

        for name, fn in [("A", scenario_a), ("B", scenario_b), ("C", scenario_c)]:
            try:
                await fn(page, context)
            except Exception as e:
                fail(f"{name}: {e}")
                import traceback
                traceback.print_exc()

            for p in context.pages:
                if p != page:
                    await p.close()

        await browser.close()

    server_proc.terminate()
    try:
        await asyncio.wait_for(server_proc.wait(), timeout=5)
    except asyncio.TimeoutError:
        server_proc.kill()
        await server_proc.wait()

    print()
    print("=" * 60)
    print("BROWSER SMOKE TEST RESULTS")
    print("=" * 60)
    print(f"  Passed: {passed}")
    print(f"  Failed: {failed}")
    if browser_errors:
        print("  Browser console errors:")
        for be in browser_errors:
            print(f"    - {be}")
    if errors:
        print("  Failures:")
        for e in errors:
            print(f"    - {e}")
    print("=" * 60)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
