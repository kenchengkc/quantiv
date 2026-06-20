#!/usr/bin/env python3
"""Send an HMAC-signed GET to the backend, the same way the Vercel proxy does.

Usage:
    python smoke_signed.py [BASE_URL] [PATH]
    python smoke_signed.py http://localhost:8000 /api/ml/info

Signing contract (apps/backend/middleware/hmac_auth.py):
    X-Quantiv-Timestamp: <ms>
    X-Quantiv-Signature: hex(hmac_sha256(secret, f"{method}\\n{path}\\n{ts}\\n{sha256hex(body)}"))
Reads BACKEND_SHARED_SECRET from the environment, or from ./.env if unset.
"""
import hashlib
import hmac
import os
import sys
import time
import urllib.request

base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
path_q = sys.argv[2] if len(sys.argv) > 2 else "/api/ml/info"
path = path_q.split("?", 1)[0]  # middleware signs the path only, not the query

secret = os.getenv("BACKEND_SHARED_SECRET")
if not secret and os.path.exists(os.path.join(os.path.dirname(__file__), ".env")):
    for line in open(os.path.join(os.path.dirname(__file__), ".env")):
        if line.startswith("BACKEND_SHARED_SECRET="):
            secret = line.strip().split("=", 1)[1]
            break
if not secret:
    sys.exit("BACKEND_SHARED_SECRET not found (env or ./.env)")

ts = str(int(time.time() * 1000))
body = b""
canonical = f"GET\n{path}\n{ts}\n{hashlib.sha256(body).hexdigest()}"
sig = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()

req = urllib.request.Request(
    base.rstrip("/") + path_q,
    headers={"X-Quantiv-Timestamp": ts, "X-Quantiv-Signature": sig},
)
try:
    with urllib.request.urlopen(req, timeout=15) as r:
        print(f"HTTP {r.status} {path_q}")
        print(r.read(400).decode("utf-8", "replace"))
except urllib.error.HTTPError as e:
    print(f"HTTP {e.code} {path_q}")
    print(e.read(400).decode("utf-8", "replace"))
    sys.exit(1)
