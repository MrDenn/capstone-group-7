#!/usr/bin/env python3
"""Fetch a PR's public conversation page from github.com and print (author, text) pairs
for comments and review comments, in page order. Used to find coordination/awareness quotes."""
import re, html, sys, os, urllib.request
def fetch(n):
    p = f"html/{n}.html"
    if not os.path.exists(p):
        req = urllib.request.Request(f"https://github.com/kubernetes/kubernetes/pull/{n}", headers={"User-Agent": "Mozilla/5.0"})
        open(p, "wb").write(urllib.request.urlopen(req, timeout=60).read())
    return open(p, encoding="utf-8", errors="ignore").read()
def thread(n):
    s = fetch(n)
    title = re.search(r"<title>(.*?)</title>", s, re.S).group(1)
    marks = [(m.start(), m.group(1)) for m in re.finditer(r'class="author[^"]*"[^>]*>([^<]+)</a>', s)]
    out = []
    for i, (pos, who) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(s)
        seg = s[pos:end]
        m = re.search(r'class="[^"]*comment-body[^"]*"[^>]*>(.*?)</(?:td|div)>\s*(?:</div>|<div|</td>)', seg, re.S)
        if not m: continue
        t = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1)))).strip()
        if t: out.append((who, t))
    return html.unescape(title), out
if __name__ == "__main__":
    for n in sys.argv[1:]:
        title, out = thread(n)
        print(f"#### {n}: {title[:110]}")
        for who, t in out:
            if who in ("k8s-ci-robot", "github-project-automation", "k8s-triage-robot"): continue
            print(f"  [{who}] {t[:260]}")
