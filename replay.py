#!/usr/bin/env python3
"""Replay a saved push-event JSON against a running asdlc server.

Usage:
    asdlc-replay push_event.json
    asdlc-replay push_event.json --url http://localhost:8080/git/push --secret mysecret
"""
import argparse
import hashlib
import hmac
import json
import os
import sys

try:
    import httpx
except ImportError:
    print("httpx is required: pip install httpx", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay a push-event JSON file against asdlc")
    parser.add_argument("event_file", help="Path to a push-event JSON file")
    parser.add_argument(
        "--url", default="http://localhost:8080/git/push", help="Webhook endpoint (default: %(default)s)"
    )
    parser.add_argument(
        "--secret", default="", help="HMAC webhook secret (or set WEBHOOK_SECRET env var)"
    )
    parser.add_argument(
        "--results-url", default="", help="Base URL for GET /results/{run_id} (auto-derived from --url if omitted)"
    )
    args = parser.parse_args()

    with open(args.event_file, "rb") as f:
        body = f.read()

    headers: dict[str, str] = {"Content-Type": "application/json"}
    secret = args.secret or os.environ.get("WEBHOOK_SECRET", "")
    if secret:
        sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        headers["X-Hub-Signature-256"] = sig

    r = httpx.post(args.url, content=body, headers=headers, timeout=10.0)
    print(f"HTTP {r.status_code}")
    try:
        data = r.json()
        print(json.dumps(data, indent=2))
    except Exception:
        print(r.text)
        return

    if r.status_code != 202 or "run_id" not in data:
        return

    run_id = data["run_id"]
    base = args.results_url or args.url.rsplit("/git/push", 1)[0]
    results_url = f"{base}/results/{run_id}"
    print(f"\nPoll for results: GET {results_url}")
    print("Waiting for workflow to complete (Ctrl-C to stop)...")

    import time
    for attempt in range(120):
        time.sleep(3)
        try:
            resp = httpx.get(results_url, timeout=5.0)
            if resp.status_code == 200:
                result = resp.json()
                total = sum(len(r.get("findings", [])) for r in result.get("agent_results", []))
                print(f"\nDone — {total} finding(s):")
                print(json.dumps(result, indent=2))
                return
        except Exception:
            pass
        print(f"  still running... ({(attempt + 1) * 3}s)", end="\r")

    print("\nTimed out waiting for results.")


if __name__ == "__main__":
    main()
