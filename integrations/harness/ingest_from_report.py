#!/usr/bin/env python3
"""Bridge: post recon results into the harness /api/ingest endpoint.

Reads a text file with one endpoint/finding per line (e.g. the all_endpoints.txt
produced by the js-analysis subagent) and posts each line as a finding.

Env:
  HARNESS_URL    default http://127.0.0.1:13337
  INGEST_TOKEN   required (read it from harness Settings in the UI)
"""
import argparse
import json
import os
import sys
import urllib.request
import urllib.error

p = argparse.ArgumentParser(description="Ingest recon lines into the Hackbots harness")
p.add_argument("file", nargs="?", default="all_endpoints.txt", help="text file, one finding per line")
p.add_argument("--target", default="local-lab", help="target name recorded in the finding")
p.add_argument("--target-id", type=int, default=0, help="optional target ID so the harness can cascade deletions")
p.add_argument("--severity", default="low", choices=["info", "low", "medium", "high", "critical"])
p.add_argument("--kind", default="endpoint")
p.add_argument("--limit", type=int, default=0, help="0 = all lines")
a = p.parse_args()

url = os.environ.get("HARNESS_URL", "http://127.0.0.1:13337").rstrip("/") + "/api/ingest"
token = os.environ.get("INGEST_TOKEN", "")
if not token:
    sys.exit("ERROR: set INGEST_TOKEN (copy it from harness Settings)")

try:
    lines = [l.strip() for l in open(a.file, encoding="utf-8") if l.strip() and not l.startswith("#")]
except OSError as e:
    sys.exit(f"ERROR: cannot read {a.file}: {e}")

if a.limit:
    lines = lines[: a.limit]
if not lines:
    sys.exit("No findings to ingest")

ok = 0
for line in lines:
    body = json.dumps({
        "title": line[:160],
        "severity": a.severity,
        "kind": a.kind,
        "detail": line,
        "source": a.file,
        "target": a.target,
        "target_id": a.target_id or None,
    }).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            result = json.load(res)
            print(f"[{result.get('id')}] {line[:70]}  webhook={result.get('webhookSent')}")
            ok += 1
    except urllib.error.HTTPError as e:
        print(f"FAIL {line[:70]}: HTTP {e.code} {e.read()[:120]}", file=sys.stderr)
    except Exception as e:
        print(f"FAIL {line[:70]}: {e}", file=sys.stderr)

print(f"Ingested {ok}/{len(lines)} findings")
sys.exit(0 if ok else 1)
