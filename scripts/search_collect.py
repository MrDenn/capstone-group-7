#!/usr/bin/env python3
"""Collect every PR and issue opened in kubernetes/kubernetes in the analysis window via the
GitHub Search API (unauthenticated: 10 requests/minute, max 1000 results per query).
The window is split into half-month slices so no slice exceeds the 1000-result cap.
Output: data/search_items.jsonl (one JSON object per PR/issue, fields trimmed).
"""
import json, os, sys, time, datetime, urllib.request, urllib.parse

REPO = "kubernetes/kubernetes"
START, END = datetime.date(2025, 8, 27), datetime.date(2026, 8, 26)   # inclusive: v1.34.0 release day .. v1.37.0 release day
import os as _os
KINDS = _os.environ.get("KINDS", "pr,issue").split(",")
SKIP_BEFORE = {k: _os.environ.get("SKIP_"+k.upper()) for k in ("pr","issue")}  # resume helpers
OUT = "data/search_items.jsonl"
os.makedirs("data", exist_ok=True)
PAUSE = 6.5  # seconds between calls -> stays under 10 req/min

def slices(start, end):
    d = start
    while d <= end:
        e = min(d + datetime.timedelta(days=14), end)
        yield d, e
        d = e + datetime.timedelta(days=1)

def search(q, page):
    url = "https://api.github.com/search/issues?" + urllib.parse.urlencode(
        {"q": q, "per_page": 100, "page": page, "sort": "created", "order": "asc"})
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "tue-capstone"})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            wait = 65 if e.code in (403, 429) else 10
            print(f"HTTP {e.code} on {q} p{page}; sleeping {wait}s", flush=True); time.sleep(wait)
    raise RuntimeError("giving up on " + q)

def trim(it, kind):
    return dict(kind=kind, number=it["number"], title=it["title"], user=it["user"]["login"],
                user_type=it["user"]["type"], created_at=it["created_at"], closed_at=it["closed_at"],
                merged_at=(it.get("pull_request") or {}).get("merged_at"), state=it["state"],
                comments=it["comments"], labels=[l["name"] for l in it["labels"]],
                author_association=it.get("author_association"))

done = set()
if os.path.exists(OUT):
    for l in open(OUT):
        o = json.loads(l); done.add((o["kind"], o["number"]))
with open(OUT, "a") as out:
    for kind in KINDS:
        for a, b in slices(START, END):
            rng = SKIP_BEFORE.get(kind)
            if rng:  # skip slices fully inside an already-collected range "YYYY-MM-DD,YYYY-MM-DD"
                lo, hi = rng.split(",")
                if lo <= str(a) and str(b) <= hi:
                    continue
            q = f"repo:{REPO} is:{kind} created:{a}..{b}"
            page, got = 1, 0
            while True:
                res = search(q, page); time.sleep(PAUSE)
                items = res["items"]
                for it in items:
                    if (kind, it["number"]) not in done:
                        out.write(json.dumps(trim(it, kind)) + "\n"); done.add((kind, it["number"]))
                got += len(items)
                total = res["total_count"]
                if total > 1000: print("WARNING: slice over 1000", q, total, flush=True)
                if got >= min(total, 1000) or not items: break
                page += 1
            print(f"{kind} {a}..{b}: {total}", flush=True)
            out.flush()
print("done", len(done))
