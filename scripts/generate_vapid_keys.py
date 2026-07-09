"""One-time setup: generate a VAPID keypair for Web Push (Milestone 7 Phase 8).

Usage:
    python scripts/generate_vapid_keys.py

Prints JARVIS_VAPID_PUBLIC_KEY / JARVIS_VAPID_PRIVATE_KEY lines to add to
your .env file. The private key is a secret — never commit it, never log
it, never send it to the browser. Only the public key is safe to expose
(and is, deliberately, via GET /api/vapid-public-key).
"""
from py_vapid import Vapid02
import base64


def main():
    vapid = Vapid02()
    vapid.generate_keys()

    private_raw = vapid.private_key.private_numbers().private_value.to_bytes(32, "big")
    private_b64 = base64.urlsafe_b64encode(private_raw).rstrip(b"=").decode()

    public_raw = vapid.public_key.public_bytes(
        encoding=__import__("cryptography.hazmat.primitives.serialization", fromlist=["Encoding"]).Encoding.X962,
        format=__import__("cryptography.hazmat.primitives.serialization", fromlist=["PublicFormat"]).PublicFormat.UncompressedPoint,
    )
    public_b64 = base64.urlsafe_b64encode(public_raw).rstrip(b"=").decode()

    print("# Add these to .env (never commit the private key):")
    print(f"JARVIS_VAPID_PUBLIC_KEY={public_b64}")
    print(f"JARVIS_VAPID_PRIVATE_KEY={private_b64}")
    print("JARVIS_VAPID_SUBJECT=mailto:you@example.com")


if __name__ == "__main__":
    main()
