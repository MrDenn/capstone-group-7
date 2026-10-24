#!/usr/bin/env python3
"""Look up, for every account that opened a pull request or issue in the window, whether it
was involved in kubernetes/kubernetes before the window (Section 3).

"involves:<login>" matches pull requests and issues that the account opened, commented on,
was assigned to or was mentioned in. An account without any such item created before the
window is a newcomer. One GraphQL query asks for 25 accounts at once, sorted by creation
date, so the first result is the oldest item the account was involved in.

Input:  data/raw/health_items.jsonl (from health_collect.py)
Output: data/raw/health_prior.json, {login: {"before": count, "first": date or null}}
Usage: python3 health_prior.py
"""
import json, os, subprocess, sys, time

IN, OUT = "data/raw/health_items.jsonl", "data/raw/health_prior.json"
START = "2025-08-27"
BATCH = 25


def query(logins):
    parts = []
    for i, login in enumerate(logins):
        q = f"repo:kubernetes/kubernetes involves:{login} created:<{START} sort:created-asc"
        parts.append(f'a{i}: search(query:{json.dumps(q)}, type:ISSUE, first:1)'
                     '{issueCount nodes{... on PullRequest{createdAt} ... on Issue{createdAt}}}')
    body = "query{rateLimit{remaining} " + " ".join(parts) + "}"
    for attempt in range(6):
        r = subprocess.run(["gh", "api", "graphql", "-f", "query=" + body], capture_output=True, text=True)
        if r.returncode == 0:
            return json.loads(r.stdout)["data"]
        print("retry", attempt, r.stderr.strip()[:200], file=sys.stderr)
        time.sleep(30 * (attempt + 1))
    sys.exit("giving up")


def main():
    logins = sorted({json.loads(l)["author"]["login"] for l in open(IN) if json.loads(l)["author"]})
    result = json.load(open(OUT)) if os.path.exists(OUT) else {}
    todo = [l for l in logins if l not in result]
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        data = query(batch)
        for j, login in enumerate(batch):
            s = data[f"a{j}"]
            result[login] = {"before": s["issueCount"],
                             "first": s["nodes"][0]["createdAt"] if s["nodes"] else None}
        json.dump(result, open(OUT, "w"))
        print(i + len(batch), "of", len(todo), "rate left", data["rateLimit"]["remaining"], flush=True)
        time.sleep(2)  # stay clear of the secondary rate limit for searches


if __name__ == "__main__":
    main()
