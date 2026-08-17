#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe one project's live service in the background and write a cache file. The status line
only ever reads that file.

    python3 health-check.py <the health config, as JSON> <cache file>

The config lives under a project in `project-icons.json`:

    "health": {
      "url": "https://example.com/health",
      "label": "prod",
      "expect": {"status": "ok", "mongo": true, "client_ip_seen": true}
    }

**The value of this comes from where it runs**: your laptop, outside the VM, outside GCP,
outside your CDN. A watchdog running inside cannot catch "the whole machine is off, the
network is down", because it is not running either at that point — an external probe is exactly
that hole.

Three states, not two:

    ok       --  everything matched
    sick     --  something else answers, but this service does not (non-2xx, a field mismatch)
    offline  --  the control site does not answer either, so **it is your network, not production**
    (failing to write the file = unknown, judged from the cache's age by the status line)

Why sick and offline are separate: they look identical to the person reading, but one needs
attention now and the other needs the wifi to come back. **The control request is only made on
failure**, so the normal path never pays for it.
"""
import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request

TIMEOUT = 8
CONTROL_URL = "https://1.1.1.1/"          # only ever asked "is it me or them"


def describe_failure(exc):
    """Turn an exception into one sentence a person can act on.

    **Never print a Python exception class name on screen.** The first version printed
    `urlerror`, which is internal vocabulary — the reader cannot tell "no DNS record" from
    "connection refused" from "timed out", and those three call for completely different things
    (fix the domain / the service is not up / the service is stuck).
    """
    inner = getattr(exc, "reason", exc)
    name = type(inner).__name__
    text = str(inner).lower()
    if isinstance(inner, socket.gaierror) or "name or service" in text \
            or "nodename nor servname" in text:
        return "no DNS record"
    if isinstance(inner, socket.timeout) or "timed out" in text or name == "timeout":
        return "timed out"
    if "connection refused" in text or "econnrefused" in text:
        return "connection refused"
    if "certificate" in text or "ssl" in text:
        return "certificate problem"
    if "network is unreachable" in text or "no route" in text:
        return "no route"
    return "unreachable"


def fetch(url, timeout=TIMEOUT):
    """Return (http status, body, error string). Nothing here raises."""
    req = urllib.request.Request(url, headers={"User-Agent": "claude-statusline-health"})
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=ssl.create_default_context()) as r:
            return r.status, r.read(8192).decode("utf-8", "replace"), None
    except urllib.error.HTTPError as e:
        return e.code, "", "HTTP %d" % e.code
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        return None, "", describe_failure(e)


def reachable(url=CONTROL_URL):
    code, _, _ = fetch(url, timeout=5)
    return code is not None


def check(cfg):
    url = cfg.get("url")
    if not url:
        return {"state": "unknown", "detail": "no url set"}

    t0 = time.time()
    code, body, err = fetch(url)
    ms = int((time.time() - t0) * 1000)

    if code is None:
        # No answer. **Ask whether it is your own network first**, or a dropped wifi gets
        # reported as production being down.
        if not reachable():
            return {"state": "offline", "detail": "this machine is offline", "ms": ms}
        return {"state": "sick", "detail": err or "unreachable", "ms": ms}

    if not (200 <= code < 300):
        return {"state": "sick", "detail": "HTTP %d" % code, "ms": ms, "http": code}

    expect = cfg.get("expect") or {}
    if expect:
        try:
            data = json.loads(body)
        except Exception:
            return {"state": "sick", "detail": "the response is not JSON", "ms": ms, "http": code}
        bad = [k for k, want in expect.items() if data.get(k) != want]
        if bad:
            # Say **which key** is wrong. "unhealthy" on its own is a sentence nobody can do
            # anything with.
            first = bad[0]
            return {"state": "sick", "ms": ms, "http": code,
                    "detail": "%s=%s" % (first, json.dumps(data.get(first),
                                                           ensure_ascii=False)),
                    "bad": bad}

    return {"state": "ok", "ms": ms, "http": code}


def main():
    cfg = json.loads(sys.argv[1])
    out = sys.argv[2]
    payload = check(cfg)
    payload["checked_at"] = int(time.time())
    payload["label"] = cfg.get("label") or "prod"
    tmp = out + ".tmp%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, out)


if __name__ == "__main__":
    main()
