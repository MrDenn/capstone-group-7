#!/usr/bin/env python3
"""Mine commit activity of kubernetes/kubernetes (master) for a one-year window.

Uses a treeless clone (git clone --bare --filter=tree:0) so only commit metadata is needed.
Outputs:
  data/commits.csv      one row per non-merge commit: sha, author date, author name/email, GitHub login (if mapped)
  data/merges.csv       one row per PR merge commit: sha, merge date, PR number, PR author login
Mapping author -> GitHub login: for every "Merge pull request #N from <login>/<branch>" commit M,
the commits in M^1..M^2 are that PR's commits, so their author e-mail is attributed to <login>.
"""
import csv, re, subprocess, sys, collections

REPO = sys.argv[1] if len(sys.argv) > 1 else "k8s-hist.git"
import os
SINCE = os.environ.get("SINCE", "2025-08-27T00:00:00Z")  # v1.34.0 release day (27 Aug 2025), inclusive
UNTIL = os.environ.get("UNTIL", "2026-08-27T00:00:00Z")  # v1.37.0 release day (26 Aug 2026) inclusive
os.makedirs("data", exist_ok=True)
MERGE_RE = re.compile(r"^Merge pull request #(\d+) from ([^/\s]+)/")

def git(*args):
    return subprocess.run(["git", "-C", REPO, *args], capture_output=True, text=True, check=True).stdout

merges = []
for line in git("log", f"--since={SINCE}", f"--until={UNTIL}", "--merges", "--format=%H|%P|%cI|%s").splitlines():
    sha, parents, cdate, subj = line.split("|", 3)
    m = MERGE_RE.match(subj)
    if not m:
        continue
    p = parents.split()
    merges.append(dict(sha=sha, p1=p[0], p2=p[1] if len(p) > 1 else "", date=cdate, pr=int(m.group(1)), login=m.group(2)))

email2login = collections.defaultdict(collections.Counter)
commit2login = {}
for mg in merges:
    if not mg["p2"]:
        continue
    for l in git("log", "--format=%H|%ae", f"{mg['p1']}..{mg['p2']}").splitlines():
        h, ae = l.split("|", 1)
        email2login[ae.lower()][mg["login"]] += 1
        commit2login[h] = mg["login"]

rows = []
for line in git("log", f"--since={SINCE}", f"--until={UNTIL}", "--no-merges", "--format=%H|%aI|%cI|%an|%ae").splitlines():
    h, adate, cdate, an, ae = line.split("|", 4)
    login = commit2login.get(h) or (email2login[ae.lower()].most_common(1)[0][0] if email2login.get(ae.lower()) else "")
    rows.append(dict(sha=h, author_date=adate, commit_date=cdate, author_name=an, author_email=ae, login=login))

with open("data/commits.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
with open("data/merges.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["sha", "date", "pr", "login"]); w.writeheader()
    w.writerows({k: m[k] for k in ["sha", "date", "pr", "login"]} for m in merges)
print(f"{len(rows)} non-merge commits, {len(merges)} PR merges, {sum(1 for r in rows if r['login'])} commits mapped to a login")
