"""Debug WebSocket message types from Jarvis server."""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv()

import websockets


async def main():
    ws = await websockets.connect("ws://127.0.0.1:8000/ws")

    # Drain init
    for _ in range(20):
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=2)
            data = json.loads(msg)
            print(f"INIT: type={data.get('type','?')}")
        except asyncio.TimeoutError:
            break

    # Send a message
    await ws.send(json.dumps({"type": "user_message", "content": "What tasks are running?"}))

    # Collect responses with detailed type info
    for i in range(30):
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=10)
            tname = type(msg).__name__
            mrepr = repr(msg)[:150]
            print(f"MSG[{i}]: type={tname}, repr={mrepr}")
            if isinstance(msg, bytes):
                try:
                    decoded = msg.decode()
                    data = json.loads(decoded)
                    dtype = type(data).__name__
                    dkeys = list(data.keys()) if isinstance(data, dict) else "N/A"
                    print(f"  -> bytes decoded OK, type={dtype}, keys={dkeys}")
                except Exception as e:
                    print(f"  -> bytes decode error: {e}")
            elif isinstance(msg, str):
                try:
                    data = json.loads(msg)
                    dtype = type(data).__name__
                    print(f"  -> str parsed, type={dtype}", end="")
                    if isinstance(data, dict):
                        evt = data.get("type","?")[:40]
                        print(f", ev_type={evt}")
                    else:
                        dstr = str(data)[:60]
                        print(f", value={dstr}")
                except json.JSONDecodeError as e:
                    print(f"  -> str NOT JSON: {e}")
            else:
                mtype = type(msg)
                print(f"  -> unexpected type: {mtype}")
        except asyncio.TimeoutError:
            print("TIMEOUT")
            break

    await ws.close()


if __name__ == "__main__":
    asyncio.run(main())
