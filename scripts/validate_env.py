#!/usr/bin/env python3
"""
Quantiv env validator (non-destructive)
- Parses .env.local in repo root
- Verifies required variables are set
- Checks filesystem paths
- Checks TCP reachability to Postgres and Redis
- Optionally checks Polygon host reachability
"""
from __future__ import annotations
import os
import re
import sys
import socket
import ssl
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = REPO_ROOT / ".env.local"

REQUIRED_VARS = [
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "REDIS_URL",
    "PARQUET_ROOT",
    "POLYGON_API_KEY",
    "NEXT_PUBLIC_API_URL",
]


def parse_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Allow leading 'export '
        if line.lower().startswith("export "):
            line = line[7:].lstrip()
        # Split on first '='
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        # Remove surrounding quotes if present
        if (val.startswith("\"") and val.endswith("\"")) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        env[key] = val
    return env


def mask(s: str, keep: int = 4) -> str:
    if s is None:
        return ""
    return ("*" * max(0, len(s) - keep)) + s[-keep:]


def tcp_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def main() -> int:
    print("[Quantiv] Env validation\n")
    env = parse_env_file(ENV_FILE)
    missing = []
    for v in REQUIRED_VARS:
        if not env.get(v):
            missing.append(v)
            print(f"{v:<22} : MISSING")
        else:
            if v in {"POSTGRES_PASSWORD", "POLYGON_API_KEY"}:
                print(f"{v:<22} : set ({len(env[v])} chars, ..{env[v][-4:]})")
            elif v == "REDIS_URL":
                try:
                    p = urlparse(env[v])
                    host, port = p.hostname or "", p.port or 6379
                    print(f"{v:<22} : set ({p.scheme}://{host}:{port})")
                except Exception:
                    print(f"{v:<22} : INVALID URL")
            else:
                print(f"{v:<22} : {env[v]}")

    print()
    # Parquet path
    pr = env.get("PARQUET_ROOT")
    if pr and Path(pr).is_dir():
        print(f"[Parquet] Root exists: {pr}")
    else:
        print(f"[Parquet] Root not found: {pr}")

    # Postgres reachability
    ph, pp = env.get("POSTGRES_HOST"), env.get("POSTGRES_PORT")
    if ph and pp and pp.isdigit():
        ok = tcp_reachable(ph, int(pp))
        print(f"[Postgres] TCP {'reachable' if ok else 'NOT reachable'} at {ph}:{pp}")
    else:
        print("[Postgres] Skipped (host/port missing)")

    # Redis reachability
    ru = env.get("REDIS_URL", "")
    try:
        p = urlparse(ru)
        rh, rp = p.hostname, p.port or 6379
        if rh:
            ok = tcp_reachable(rh, int(rp))
            print(f"[Redis] TCP {'reachable' if ok else 'NOT reachable'} at {rh}:{rp}")
        else:
            print("[Redis] Invalid REDIS_URL")
    except Exception as e:
        print(f"[Redis] Error parsing REDIS_URL: {e}")

    # Polygon host TCP
    ok = tcp_reachable("api.polygon.io", 443)
    print(f"[Polygon] TCP {'reachable' if ok else 'NOT reachable'} at api.polygon.io:443")

    print("\n[Summary]")
    if missing:
        print("⚠️  Missing required vars:", ", ".join(missing))
        return 2
    print("✅ Env variables set. Reachability checks above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
