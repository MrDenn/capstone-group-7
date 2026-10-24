#!/usr/bin/env python3
"""Collect every pull request and issue opened in kubernetes/kubernetes in the analysis
window, with their comments, reviews, labels and who closed them (Section 3).

Uses the GitHub GraphQL search through the authenticated `gh` CLI. The window is split
into half-month slices, as in search_collect.py, so that no slice exceeds the 1,000
results that one search can return. Per item we keep at most the first 40 comments,
20 reviews and 20 labels (the total counts are stored, so truncation is visible).

Output: data/raw/health_items.jsonl, one JSON object per pull request or issue.
Usage: python3 health_collect.py
"""
import datetime, json, os, subprocess, sys, time

REPO = "kubernetes/kubernetes"
START, END = datetime.date(2025, 8, 27), datetime.date(2026, 8, 26)  # inclusive, as in Section 1.6
OUT = "data/raw/health_items.jsonl"

ACTOR = "author{login __typename ... on User{name}}"
COMMON = f"""number title createdAt closedAt state authorAssociation {ACTOR}
labels(first:20){{totalCount nodes{{name}}}}
comments(first:40){{totalCount nodes{{{ACTOR} createdAt body}}}}
timelineItems(last:1,itemTypes:[CLOSED_EVENT]){{nodes{{... on ClosedEvent{{createdAt actor{{login __typename}}}}}}}}"""
QUERY = f"""query($q:String!,$c:String){{rateLimit{{remaining resetAt}}
search(query:$q,type:ISSUE,first:50,after:$c){{issueCount pageInfo{{hasNextPage endCursor}}
nodes{{__typename
... on PullRequest{{{COMMON} mergedAt baseRefName mergedBy{{login}}
  reviews(first:20){{totalCount nodes{{{ACTOR} state submittedAt}}}}}}
... on Issue{{{COMMON} stateReason}}}}}}}}"""


def graphql(q, cursor):
    args = ["gh", "api", "graphql", "-f", "query=" + QUERY, "-f", "q=" + q]
    if cursor:
        args += ["-f", "c=" + cursor]
    for attempt in range(6):  # retry transient 502s and secondary rate limits
        r = subprocess.run(args, capture_output=True, text=True)
        if r.returncode == 0:
            return json.loads(r.stdout)["data"]
        print("retry", attempt, r.stderr.strip()[:200], file=sys.stderr)
        time.sleep(30 * (attempt + 1))
    sys.exit("giving up on " + q)


def slices():
    d = START
    while d <= END:
        e = min(d + datetime.timedelta(days=14), END)
        yield d, e
        d = e + datetime.timedelta(days=1)


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    done = set()
    if os.path.exists(OUT):  # resume after an interruption
        done = {(json.loads(l)["__typename"], json.loads(l)["number"]) for l in open(OUT)}
    with open(OUT, "a") as out:
        for a, b in slices():
            q = f"repo:{REPO} created:{a}..{b}"
            cursor, n = None, 0
            while True:
                data = graphql(q, cursor)
                s = data["search"]
                if s["issueCount"] > 1000:
                    sys.exit(f"slice {q} has {s['issueCount']} results, more than search returns")
                for node in s["nodes"]:
                    n += 1
                    if (node["__typename"], node["number"]) not in done:
                        out.write(json.dumps(node) + "\n")
                if data["rateLimit"]["remaining"] < 100:  # wait for the hourly reset
                    reset = datetime.datetime.fromisoformat(data["rateLimit"]["resetAt"].replace("Z", "+00:00"))
                    time.sleep(max(0, (reset - datetime.datetime.now(datetime.timezone.utc)).total_seconds()) + 5)
                if not s["pageInfo"]["hasNextPage"]:
                    break
                cursor = s["pageInfo"]["endCursor"]
            print(a, b, "items", n, "of", s["issueCount"], "rate left", data["rateLimit"]["remaining"], flush=True)
    keys = [(json.loads(l)["__typename"], json.loads(l)["number"]) for l in open(OUT)]
    assert len(keys) == len(set(keys)), "an item was written twice"


if __name__ == "__main__":
    main()
