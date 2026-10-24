#!/usr/bin/env python3
"""Per-contributor file/area activity in the analysis window.
Needs a blob-less clone with trees (git clone --bare --filter=blob:none --shallow-since=2025-08-20).
Outputs data/files_by_commit.csv (sha, path) and prints the top areas per top-10 contributor."""
import csv, collections, subprocess, sys
REPO = sys.argv[1]
commits = {r["sha"]: r for r in csv.DictReader(open("data/commits.csv"))}
out = subprocess.run(["git", "-C", REPO, "log", "--no-merges", "--since=2025-08-27T00:00:00Z",
                      "--until=2026-08-27T00:00:00Z", "--name-only", "--format=@%H"],
                     capture_output=True, text=True, check=True).stdout
pairs, cur = [], None
for line in out.splitlines():
    if line.startswith("@"): cur = line[1:]
    elif line.strip(): pairs.append((cur, line.strip()))
with open("data/files_by_commit.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["sha", "path"]); w.writerows(pairs)
def area(p):
    parts = p.split("/")
    if parts[0] == "staging": return "/".join(parts[:4])
    if parts[0] in ("pkg", "cmd", "test", "plugin", "hack"): return "/".join(parts[:3]) if len(parts) > 2 else "/".join(parts[:2])
    return parts[0]
top = sys.argv[2].split(",")
by = collections.defaultdict(collections.Counter)
for sha, p in pairs:
    lg = commits.get(sha, {}).get("login")
    if lg in top: by[lg][area(p)] += 1
for lg in top:
    print(lg, by[lg].most_common(6))
print("commits with file info:", len({s for s, _ in pairs}), "of", len(commits))
