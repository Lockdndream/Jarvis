"""Deterministic mock interactive worker for Jarvis.

Protocol:
  JARVIS_QUESTION:{"question_id":"<uuid>","question":"...","options":["A","B"],"context":"..."}
"""
import json
import os
import sys
import time
import uuid


def main():
    test_mode = os.environ.get("JARVIS_TEST_MODE") == "1"
    delay = 0.1 if test_mode else 1.0

    question_id = str(uuid.uuid4())

    print("Starting mock agent...", flush=True)
    time.sleep(delay)

    print("Inspecting project...", flush=True)
    time.sleep(delay)

    print("Found two possible approaches.", flush=True)
    time.sleep(delay)

    question_data = json.dumps({
        "question_id": question_id,
        "question": "Which approach should I use?",
        "options": ["A", "B"],
        "context": "A is faster. B is safer."
    })
    print(f"JARVIS_QUESTION:{question_data}", flush=True)

    answer = sys.stdin.readline().strip()

    print(f"Received answer: {answer}", flush=True)
    time.sleep(delay)

    print(f"Proceeding with approach {answer}...", flush=True)
    time.sleep(delay)

    print("Working...", flush=True)
    time.sleep(delay)

    print("Task complete.", flush=True)


if __name__ == "__main__":
    main()
