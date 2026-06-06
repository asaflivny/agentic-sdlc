#!/usr/bin/env python3
"""asdlc CLI — replay push events and install git hooks.

Subcommands:
    replay      Replay a saved push-event JSON against a running asdlc server.
    install-hook  Install the pre-push git hook in a local repo.

Usage:
    asdlc replay push_event.json [--url URL] [--secret SECRET]
    asdlc install-hook /path/to/repo [--url URL]
"""
import argparse
import hashlib
import hmac
import json
import os
import stat
import sys

try:
    import httpx
except ImportError:
    print("httpx is required: pip install httpx", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Pre-push hook template
# ---------------------------------------------------------------------------

_HOOK_TEMPLATE = """\
#!/usr/bin/env bash
# asdlc pre-push hook — installed by `asdlc install-hook`
set -euo pipefail

ASDLC_URL="${{ASDLC_URL:-{url}}}"
ASDLC_SECRET="${{ASDLC_SECRET:-{secret}}}"

remote="$1"
url_arg="$2"

# Build commits list from stdin (format: <local_ref> <local_sha> <remote_ref> <remote_sha>)
commits_json="[]"
while IFS= read -r line; do
    local_ref=$(echo "$line" | awk '{{print $1}}')
    local_sha=$(echo "$line" | awk '{{print $2}}')
    remote_sha=$(echo "$line" | awk '{{print $4}}')
    branch=$(echo "$local_ref" | sed 's|refs/heads/||')
    repo_name=$(basename "$(git rev-parse --show-toplevel)")
    clone_url="$(git rev-parse --show-toplevel)"

    payload=$(cat <<JSON
{{
  "ref": "$local_ref",
  "before": "$remote_sha",
  "after": "$local_sha",
  "repository": {{"name": "$repo_name", "clone_url": "$clone_url"}},
  "pusher": {{"name": "$(git config user.name)", "email": "$(git config user.email)"}},
  "commits": []
}}
JSON
)

    headers=('-H' 'Content-Type: application/json')
    if [ -n "$ASDLC_SECRET" ]; then
        sig=$(printf '%s' "$payload" | openssl dgst -sha256 -hmac "$ASDLC_SECRET" | awk '{{print $2}}')
        headers+=('-H' "X-Hub-Signature-256: sha256=$sig")
    fi

    curl -sf -X POST "$ASDLC_URL" "${{headers[@]}}" -d "$payload" > /dev/null || true
done

exit 0
"""


# ---------------------------------------------------------------------------
# replay subcommand
# ---------------------------------------------------------------------------

def _cmd_replay(args: argparse.Namespace) -> None:
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


# ---------------------------------------------------------------------------
# install-hook subcommand
# ---------------------------------------------------------------------------

def _cmd_install_hook(args: argparse.Namespace) -> None:
    import pathlib

    repo_path = pathlib.Path(args.repo_path).resolve()
    git_dir = repo_path / ".git"
    if not git_dir.is_dir():
        print(f"error: {repo_path} does not appear to be a git repository (.git not found)", file=sys.stderr)
        sys.exit(1)

    hook_path = git_dir / "hooks" / "pre-push"
    hook_dir = hook_path.parent
    hook_dir.mkdir(exist_ok=True)

    url = args.url or os.environ.get("ASDLC_URL", "http://localhost:8088/git/push")

    # Validate server reachability before writing
    try:
        r = httpx.get(url.rsplit("/git/push", 1)[0] + "/healthz", timeout=5.0)
        if r.status_code != 200:
            print(f"warning: server at {url} returned HTTP {r.status_code} — hook will still be installed", file=sys.stderr)
    except Exception as e:
        print(f"warning: could not reach {url} ({e}) — hook will still be installed", file=sys.stderr)

    hook_content = _HOOK_TEMPLATE.format(url=url, secret="")
    hook_path.write_text(hook_content)
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    print(f"✓ installed pre-push hook at {hook_path}")
    print()
    print("Next steps:")
    print(f"  1. Set ASDLC_URL={url} in your shell (or it will default to the hook value)")
    print("  2. Set ASDLC_SECRET=<your-webhook-secret> to enable HMAC signing")
    print("  3. Push to any branch — the hook will send the event to asdlc automatically")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="asdlc",
        description="asdlc CLI — replay push events and manage git hooks",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    # replay
    replay_p = sub.add_parser("replay", help="Replay a saved push-event JSON file against the server")
    replay_p.add_argument("event_file", help="Path to a push-event JSON file")
    replay_p.add_argument("--url", default="http://localhost:8088/git/push", help="Webhook endpoint (default: %(default)s)")
    replay_p.add_argument("--secret", default="", help="HMAC webhook secret (or set WEBHOOK_SECRET env var)")
    replay_p.add_argument("--results-url", default="", help="Base URL for GET /results/{run_id} (auto-derived from --url if omitted)")

    # install-hook
    hook_p = sub.add_parser("install-hook", help="Install the asdlc pre-push hook in a local git repo")
    hook_p.add_argument("repo_path", help="Path to the local git repository")
    hook_p.add_argument("--url", default="", help="asdlc webhook URL (default: http://localhost:8088/git/push)")

    args = parser.parse_args()
    if args.command == "replay":
        _cmd_replay(args)
    elif args.command == "install-hook":
        _cmd_install_hook(args)


# Keep the old asdlc-replay entry point working
def replay_main() -> None:
    """Legacy entry point for asdlc-replay (backwards compat)."""
    import sys
    sys.argv = ["asdlc", "replay"] + sys.argv[1:]
    main()


if __name__ == "__main__":
    main()
