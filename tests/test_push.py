"""Milestone 7 Phase 18 (real-phone acceptance) regression tests.

A real backgrounded, battery-unrestricted, installed-PWA Android device only
displayed a push notification after being foregrounded, never while actually
backgrounded. Two real fixes were needed, in order:

1. pywebpush defaults to ttl=0, which tells the push service to drop the
   message rather than hold/retry it if the device can't be reached
   immediately. Fixed with an explicit non-zero TTL — this alone did not
   resolve the issue on the real device.
2. The remaining symptom (delivered but deferred until the user next
   activated the device) matches Android Doze mode holding normal-priority
   FCM messages for the next maintenance window. Fixed by setting the Web
   Push `Urgency: high` header (RFC 8030), which FCM maps to a high-priority,
   Doze-bypassing message.

These tests guard against either fix silently regressing.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import push


def test_send_one_uses_a_non_zero_ttl(monkeypatch):
    captured = {}

    def fake_webpush(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("pywebpush.webpush", fake_webpush)
    monkeypatch.setenv("JARVIS_VAPID_PRIVATE_KEY", "fake-private-key")

    push._send_one(
        {"endpoint": "https://push.example/ep1", "p256dh": "p-key", "auth": "a-key"},
        "{}",
    )

    assert captured.get("ttl") == push._PUSH_TTL_SECONDS
    assert push._PUSH_TTL_SECONDS > 0


def test_send_one_sets_high_urgency_header(monkeypatch):
    captured = {}

    def fake_webpush(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("pywebpush.webpush", fake_webpush)
    monkeypatch.setenv("JARVIS_VAPID_PRIVATE_KEY", "fake-private-key")

    push._send_one(
        {"endpoint": "https://push.example/ep1", "p256dh": "p-key", "auth": "a-key"},
        "{}",
    )

    assert captured.get("headers", {}).get("Urgency") == "high"


def test_ttl_constant_is_a_reasonable_positive_duration():
    # Not 0 (drop-if-unreachable) and not absurdly long (stale notifications
    # piling up indefinitely on the device).
    assert 0 < push._PUSH_TTL_SECONDS <= 24 * 60 * 60
