#!/usr/bin/env python3
"""External uptime probe for patentref.io (PatentRef checklist 5.4, the 5.8 uptime clock).

Runs in GitHub Actions from the public repository patentref/status (`ops/uptime/uptime.yml` in the
patentref repo is the source of that workflow; `ops/monitoring.md` explains the whole arrangement).
Standard library only. One run:

  1. GET $TARGET/api/v1/meta/health/ with a 10 s timeout; on anything but a 200 with {"ok": true},
     wait RETRY_SECONDS and try once more. A run is a pass when either attempt passes.
  2. With PROBE_KEY set: one keyed query (patent 10000000, one field) with X-Api-Key, recorded as
     the keyed figure; it never changes the pass/fail of the run (the health route is the contract).
  3. Append the probe to uptime/history.json ({"probes": [...]}, kept to KEEP_DAYS) and write
     uptime/latest.json. The workflow commits both.
  4. Print the line and write ok=true|false, status and ms to $GITHUB_OUTPUT for the alert steps.

Never prints the key or any secret. Exit 0 on a pass, 1 on a failed probe (the workflow keeps going).
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

TARGET = os.environ.get("TARGET", "https://patentref.io").rstrip("/")
HEALTH = TARGET + "/api/v1/meta/health/"
KEYED = TARGET + "/api/v1/patent/?q=%7B%22patent_id%22%3A%2210000000%22%7D&f=%5B%22patent_id%22%5D"
HISTORY = Path(os.environ.get("HISTORY", "uptime/history.json"))
LATEST = HISTORY.with_name("latest.json")
TIMEOUT = float(os.environ.get("PROBE_TIMEOUT", "10"))
RETRY_SECONDS = float(os.environ.get("RETRY_SECONDS", "15"))
KEEP_DAYS = int(os.environ.get("KEEP_DAYS", "45"))
RUNNER = os.environ.get("RUNNER_LABEL", "github-actions")
UA = "patentref-uptime-probe/1 (+https://github.com/patentref/status)"


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get(url, headers=None):
    """(status, ms, body_bytes, error_class). status 0 on a connection-level failure."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read(65536)
            return r.status, round((time.perf_counter() - t0) * 1000, 1), body, ""
    except urllib.error.HTTPError as e:
        return e.code, round((time.perf_counter() - t0) * 1000, 1), e.read(65536) if e.fp else b"", "HTTPError"
    except Exception as e:  # URLError, timeout, ssl
        return 0, round((time.perf_counter() - t0) * 1000, 1), b"", e.__class__.__name__


def health_once():
    status, ms, body, err = get(HEALTH)
    ok = False
    data_version = ""
    code_version = ""
    if status == 200:
        try:
            j = json.loads(body.decode("utf-8"))
            ok = bool(j.get("ok"))
            data_version = str(j.get("data_version", ""))
            code_version = str(j.get("version", ""))
        except ValueError:
            err = "bad JSON"
    return {"ok": ok, "status": status, "ms": ms, "error": err, "data_version": data_version, "code_version": code_version}


def main():
    t = now_iso()
    first = health_once()
    attempts = 1
    result = first
    if not first["ok"]:
        time.sleep(RETRY_SECONDS)
        second = health_once()
        attempts = 2
        result = second if second["ok"] else first
        if not second["ok"]:
            result = {**second, "first_status": first["status"], "first_error": first["error"]}
    keyed = None
    key = os.environ.get("PROBE_KEY", "").strip()
    if key:
        ks, kms, kbody, kerr = get(KEYED, {"X-Api-Key": key})
        keyed = {"status": ks, "ms": kms}
        if kerr and ks != 200:
            keyed["error"] = kerr
    probe = {"t": t, "ok": result["ok"], "status": result["status"], "ms": result["ms"], "attempts": attempts,
             "keyed": keyed, "data_version": result["data_version"], "code_version": result["code_version"], "runner": RUNNER}
    if result.get("error"):
        probe["error"] = result["error"]
    if "first_status" in result:
        probe["first_status"] = result["first_status"]
        if result.get("first_error"):
            probe["first_error"] = result["first_error"]

    hist = {"probes": []}
    if HISTORY.exists():
        try:
            hist = json.loads(HISTORY.read_text(encoding="utf-8"))
        except ValueError:
            hist = {"probes": []}
    hist.setdefault("target", TARGET)
    hist.setdefault("cadence_minutes", 5)
    hist.setdefault("rule", "the span between two consecutive failed probes counts as down; a lone failed probe counts as nothing")
    cutoff = time.time() - KEEP_DAYS * 86400
    probes = [p for p in hist.get("probes", []) if isinstance(p, dict)]
    probes = [p for p in probes if _ts(p.get("t")) >= cutoff]
    probes.append(probe)
    hist["probes"] = probes
    hist["updated"] = t
    hist["count"] = len(probes)
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    HISTORY.write_text(json.dumps(hist, separators=(",", ":")) + "\n", encoding="utf-8")
    LATEST.write_text(json.dumps(probe, indent=1) + "\n", encoding="utf-8")

    line = (f"{t} {'PASS' if probe['ok'] else 'FAIL'} health {probe['status']} in {probe['ms']} ms"
            f" (attempts {attempts})" + (f", keyed {keyed['status']} in {keyed['ms']} ms" if keyed else ", no key")
            + (f", data {probe['data_version']}" if probe["data_version"] else "") + (f", error {probe['error']}" if probe.get("error") else ""))
    print(line)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"ok={'true' if probe['ok'] else 'false'}\nstatus={probe['status']}\nms={probe['ms']}\nline={line}\n")
    return 0 if probe["ok"] else 1


def _ts(s):
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    sys.exit(main())
