#!/usr/bin/env python3
"""Aggregate the mined data into the numbers, table and plot used in Sections 6 and 7.
Inputs: data/commits.csv, data/merges.csv (git_commits.py), data/search_items.jsonl (search_collect.py)
Outputs: data/summary.json and Figures/activities_weekly.pdf"""
import csv, json, collections, datetime as dt
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

START, END = dt.date(2025, 8, 27), dt.date(2026, 8, 26)          # inclusive
def d(s): return dt.date.fromisoformat(s[:10])
def inwin(s): return START <= d(s) <= END
def is_bot(login, typ=""):
    l = login.lower()
    return typ == "Bot" or l.endswith("[bot]") or l.endswith("-robot") or l.endswith("-bot") or l in ("kubernetes-prow", "k8s-ci-robot")

commits = [r for r in csv.DictReader(open("data/commits.csv")) if inwin(r["commit_date"])]
merges = [r for r in csv.DictReader(open("data/merges.csv")) if inwin(r["date"])]
items = [json.loads(l) for l in open("data/search_items.jsonl")]
items = [i for i in items if inwin(i["created_at"])]
prs = [i for i in items if i["kind"] == "pr"]; issues = [i for i in items if i["kind"] == "issue"]
human_commits = [c for c in commits if not c["author_name"].lower().endswith("robot")]
S = {"window": [str(START), str(END)],
     "commits_all": len(commits), "commits_human": len(human_commits),
     "commit_bots": collections.Counter(c["author_name"] for c in commits if c not in human_commits).most_common(),
     "merged_prs": len(merges),
     "prs_opened": len(prs), "prs_opened_bots": sum(is_bot(p["user"], p["user_type"]) for p in prs),
     "pr_bot_logins": collections.Counter(p["user"] for p in prs if is_bot(p["user"], p["user_type"])).most_common(),
     "issues_opened": len(issues), "issues_opened_bots": sum(is_bot(p["user"], p["user_type"]) for p in issues),
     "issue_bot_logins": collections.Counter(p["user"] for p in issues if is_bot(p["user"], p["user_type"])).most_common(),
     "prs_merged_of_opened": sum(1 for p in prs if p["merged_at"]),
     "unique_commit_logins": len({c["login"] for c in human_commits if c["login"]}),
     "unique_pr_authors": len({p["user"] for p in prs if not is_bot(p["user"], p["user_type"])}),
     "unique_issue_authors": len({p["user"] for p in issues if not is_bot(p["user"], p["user_type"])}),
}
S["unique_any"] = len({c["login"].lower() for c in human_commits if c["login"]} |
                      {p["user"].lower() for p in items if not is_bot(p["user"], p["user_type"])})
# top-10 humans by commits
cc = collections.Counter(c["login"] for c in human_commits if c["login"])
prc = collections.Counter(p["user"].lower() for p in prs); isc = collections.Counter(p["user"].lower() for p in issues)
mc = collections.Counter(m["login"].lower() for m in merges)
sigs = collections.defaultdict(collections.Counter)
for p in prs:
    for l in p["labels"]:
        if l.startswith("sig/"): sigs[p["user"].lower()][l] += 1
top = []
for login, n in cc.most_common(10):
    L = login.lower()
    top.append(dict(login=login, commits=n, prs_opened=prc[L], issues_opened=isc[L], merged_prs=mc[L],
                    sigs=sigs[L].most_common(3)))
S["top10"] = top
S["top10_by_prs_opened"] = [(u, n) for u, n in prc.most_common(15)]
S["top10_by_issues"] = [(u, n) for u, n in isc.most_common(10)]
# weekly series (7-day bins from START; last partial day folded into the final bin)
nb = 52
def b(s): return min((d(s) - START).days // 7, nb - 1)
ser = {k: [0] * nb for k in ("commits", "prs", "issues", "merged")}
for c in human_commits: ser["commits"][b(c["commit_date"])] += 1
for p in prs: ser["prs"][b(p["created_at"])] += 1
for p in issues: ser["issues"][b(p["created_at"])] += 1
for m in merges: ser["merged"][b(m["date"])] += 1
weeks = [START + dt.timedelta(days=7 * i) for i in range(nb)]
S["weekly"] = {k: v for k, v in ser.items()}; S["week_starts"] = [str(w) for w in weeks]
for k in ser:
    i = max(range(nb), key=lambda j: ser[k][j]); lo = min(range(nb), key=lambda j: ser[k][j])
    S[f"peak_{k}"] = (str(weeks[i]), ser[k][i]); S[f"low_{k}"] = (str(weeks[lo]), ser[k][lo])
monthly = collections.defaultdict(lambda: collections.Counter())
for c in human_commits: monthly[c["commit_date"][:7]]["commits"] += 1
for p in prs: monthly[p["created_at"][:7]]["prs"] += 1
for p in issues: monthly[p["created_at"][:7]]["issues"] += 1
for m in merges: monthly[m["date"][:7]]["merged"] += 1
S["monthly"] = {k: dict(v) for k, v in sorted(monthly.items())}
json.dump(S, open("data/summary.json", "w"), indent=1, default=str)

fig, ax = plt.subplots(figsize=(10, 4.6))
ax.plot(weeks, ser["commits"], label="Commits (non-merge, master)", color="#1f4e79", lw=1.8)
ax.plot(weeks, ser["prs"], label="Pull requests opened", color="#c71918", lw=1.8)
ax.plot(weeks, ser["merged"], label="Pull requests merged into master", color="#c71918", lw=1.2, ls="--")
ax.plot(weeks, ser["issues"], label="Issues opened", color="#2e7d32", lw=1.8)
events = [("2025-11-07", "v1.35 code freeze", ":"), ("2025-12-17", "v1.35.0", "-"),
          ("2026-03-19", "v1.36 code freeze", ":"), ("2026-04-22", "v1.36.0", "-"),
          ("2026-07-23", "v1.37 code freeze", ":"), ("2026-08-26", "v1.37.0", "-")]
ymax = max(max(v) for v in ser.values())
for day, lab, ls in events:
    x = dt.date.fromisoformat(day); ax.axvline(x, color="gray", ls=ls, lw=1)
    ax.text(x, ymax * 1.02, lab, rotation=90, va="bottom", ha="right", fontsize=7, color="dimgray")
ax.set_ylabel("Count per week"); ax.set_ylim(0, ymax * 1.28)
ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.16), fontsize=8, ncol=4, frameon=False); ax.grid(axis="y", alpha=.3)
ax.set_xlim(START, END); fig.autofmt_xdate(); fig.tight_layout()
fig.savefig("Figures/activities_weekly.pdf")
print(json.dumps({k: v for k, v in S.items() if k not in ("weekly", "week_starts")}, indent=1, default=str)[:6000])
