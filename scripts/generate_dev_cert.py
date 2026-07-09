"""Generate a self-signed local HTTPS certificate for Jarvis (Milestone 7
Phase 3).

Voice input (getUserMedia), service workers, and Web Push all require a
"secure context" — HTTPS, or the special-cased http://localhost. A phone
on your LAN talking to your laptop's IP address is neither, so plain
`http://<laptop-ip>:8000` cannot support any of them (see SESSION.md
Milestone 7 Phase 1 capability matrix).

Preferred: install mkcert (https://github.com/FiloSottile/mkcert) and run
`mkcert <laptop-ip> localhost 127.0.0.1` — the resulting cert is trusted
automatically by any device that also trusts your mkcert root CA, with no
browser warning.

Fallback (this script): a self-signed certificate. Your phone's browser
will show a "not secure" / "proceed anyway" warning on first visit — that
warning is expected for a self-signed cert; once accepted, the connection
is still a genuine HTTPS secure context and the microphone/service-worker/
push APIs work normally.

Usage:
    python scripts/generate_dev_cert.py [extra-hostname-or-ip ...]

Writes cert.pem and key.pem to the repository root (both are git-ignored;
never commit them). Then run:
    uvicorn app.main:app --host 0.0.0.0 --port 8443 --ssl-keyfile key.pem --ssl-certfile cert.pem
"""
import ipaddress
import socket
import sys
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def _detect_lan_ip() -> str | None:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


def main():
    extra = sys.argv[1:]
    hostnames = {"localhost"}
    ips = {"127.0.0.1"}

    lan_ip = _detect_lan_ip()
    if lan_ip:
        ips.add(lan_ip)
        print(f"Detected LAN IP: {lan_ip}")

    for h in extra:
        try:
            ipaddress.ip_address(h)
            ips.add(h)
        except ValueError:
            hostnames.add(h)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "jarvis.local")])
    san = [x509.DNSName(h) for h in sorted(hostnames)] + [
        x509.IPAddress(ipaddress.ip_address(ip)) for ip in sorted(ips)
    ]

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    with open("key.pem", "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))
    with open("cert.pem", "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    print("Wrote cert.pem and key.pem (git-ignored).")
    print("Covers: " + ", ".join(sorted(hostnames) + sorted(ips)))
    print()
    print("Run:")
    print("  uvicorn app.main:app --host 0.0.0.0 --port 8443 --ssl-keyfile key.pem --ssl-certfile cert.pem")
    print()
    print("On your phone, browse to https://<laptop-ip>:8443 and accept the")
    print("self-signed certificate warning once. Prefer mkcert (see this")
    print("script's module docstring) to avoid that warning entirely.")


if __name__ == "__main__":
    main()
