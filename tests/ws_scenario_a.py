"""Quick WebSocket test for scenario A (deterministic fast path)."""
import asyncio, json, websockets

async def main():
    ws = await websockets.connect("ws://127.0.0.1:8000/ws")
    # Drain init
    for _ in range(20):
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=2)
        except asyncio.TimeoutError:
            break

    msg = json.dumps({"type": "user_message", "content": "What needs my attention?"})
    await ws.send(msg)
    print(f">>> {msg}")

    for i in range(30):
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=15)
            data = json.loads(msg)
            t = data.get("type", "?")
            c = str(data.get("content", ""))[:100]
            print(f"[{i}] type={t}")
            if c:
                print(f"    content={c}")
            if t == "supervisor_message":
                print("=== GOT SUPERVISOR MESSAGE ===")
                break
        except asyncio.TimeoutError:
            print("TIMEOUT")
            break

    await ws.close()

asyncio.run(main())
