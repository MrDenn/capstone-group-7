"""Count contributors of kubernetes/kubernetes through the GitHub GraphQL API.

Three counts, used to check the contributor numbers in Section 7:
  prs      authors of merged pull requests, restricted to the pull requests
           whose merge commit is an ancestor of the pinned commit
  commits  GitHub accounts linked to the author or co-author e-mail of the
           commits on the default branch (what the repository page counts)
  window   for the pull requests merged in the analysis window (data/merges.csv
           from git_commits.py): how often the owner of the head repository in
           the merge commit differs from the pull request author

Needs an authenticated `gh` CLI (GraphQL is not available without a token).
Usage: python github_accounts.py prs|commits|window [bare-clone-path]
"""
import collections
import csv
import json
import re
import subprocess
import sys

PIN = "f54c212e3a2f75d674b717a9b29052b20b60aefc"

PRS = """query($c:String){repository(owner:"kubernetes",name:"kubernetes"){
pullRequests(states:MERGED,first:100,after:$c,orderBy:{field:CREATED_AT,direction:ASC}){
pageInfo{hasNextPage endCursor} nodes{number author{login}}}}}"""

COMMITS = """query($c:String){repository(owner:"kubernetes",name:"kubernetes"){
defaultBranchRef{target{... on Commit{history(first:100,after:$c){
pageInfo{hasNextPage endCursor}
nodes{parents{totalCount} authors(first:10){nodes{email user{login}}}}}}}}}}"""


def pages(query, path):
    """Yield every node of a paginated GraphQL connection."""
    cursor = None
    while True:
        args = ["gh", "api", "graphql", "-f", "query=" + query]
        if cursor:
            args += ["-f", "c=" + cursor]
        for _ in range(5):  # retry transient 502s
            r = subprocess.run(args, capture_output=True, text=True)
            if r.returncode == 0:
                break
        else:
            sys.exit(r.stderr)
        conn = json.loads(r.stdout)["data"]["repository"]
        for key in path:
            conn = conn[key]
        yield from conn["nodes"]
        if not conn["pageInfo"]["hasNextPage"]:
            return
        cursor = conn["pageInfo"]["endCursor"]


def count_prs(clone):
    # Pull requests merged into the history of the pinned commit, with the
    # owner of the head repository taken from the merge commit message.
    log = subprocess.run(["git", "-C", clone, "log", "--merges", "--format=%s", PIN],
                         capture_output=True, text=True, check=True).stdout
    owner = {}
    for m in re.finditer(r"^Merge pull request #(\d+) from ([^/ ]+)/", log, re.M):
        owner[int(m[1])] = m[2].lower()

    authors, ghosts, seen = set(), set(), 0
    for pr in pages(PRS, ["pullRequests"]):
        if pr["number"] not in owner:
            continue
        seen += 1
        if pr["author"]:
            authors.add(pr["author"]["login"].lower())
        else:  # deleted account, shown as "ghost"
            ghosts.add(owner[pr["number"]])
    owners = set(owner.values())
    print(f"merge commits of pull requests:       {len(owner)}")
    print(f"found through the API:                {seen}")
    print(f"head-repository owners (git method):  {len(owners)}")
    print(f"pull request authors (API):           {len(authors)}")
    print(f"  deleted accounts, by owner name:    {len(ghosts - authors)}")
    print(f"owners that authored no pull request: {len(owners - authors)}")
    print(f"authors missing from the owners:      {len(authors - owners)}")


def count_commits():
    linked, nonmerge, unlinked, total = set(), set(), set(), 0
    for c in pages(COMMITS, ["defaultBranchRef", "target", "history"]):
        total += 1
        merge = c["parents"]["totalCount"] > 1
        for a in c["authors"]["nodes"]:
            if a["user"]:
                linked.add(a["user"]["login"].lower())
                if not merge:
                    nonmerge.add(a["user"]["login"].lower())
            elif not merge:
                unlinked.add((a["email"] or "").lower())
    print(f"commits on the default branch:        {total}")
    print(f"linked accounts (authors+co-authors): {len(linked)}")
    print(f"  without merge commits:              {len(nonmerge)}")
    print(f"unlinked author e-mail addresses:     {len(unlinked)}")


def is_bot(login):
    l = login.lower()
    return l.endswith("[bot]") or l.endswith("-robot") or l.endswith("-bot") or l in ("kubernetes-prow", "k8s-ci-robot")


def count_window():
    merges = list(csv.DictReader(open("data/merges.csv")))
    # the same top-10 as in analyze_activities.py: humans ranked by commits
    commits = [c for c in csv.DictReader(open("data/commits.csv"))
               if not c["author_name"].lower().endswith("robot")]
    top10 = {l.lower() for l, _ in collections.Counter(c["login"] for c in commits if c["login"]).most_common(10)}

    # one GraphQL request asks for the authors of 50 pull requests at once
    author = {}
    numbers = [int(m["pr"]) for m in merges]
    for i in range(0, len(numbers), 50):
        fields = " ".join(f"p{n}: pullRequest(number:{n}){{author{{login}}}}" for n in numbers[i:i + 50])
        query = 'query{repository(owner:"kubernetes",name:"kubernetes"){' + fields + "}}"
        # pull requests that GitHub has removed come back as null with an
        # error message, so a reply with data is used even if gh reports errors
        for _ in range(5):  # retry transient 502s
            r = subprocess.run(["gh", "api", "graphql", "-f", "query=" + query], capture_output=True, text=True)
            if r.stdout.strip() and json.loads(r.stdout).get("data"):
                break
        else:
            sys.exit(r.stderr)
        for key, pr in json.loads(r.stdout)["data"]["repository"].items():
            if pr and pr["author"]:
                author[int(key[1:])] = pr["author"]["login"].lower()

    found = [m for m in merges if int(m["pr"]) in author]
    differ = [m for m in found if m["login"].lower() != author[int(m["pr"])]]
    owners = {m["login"].lower() for m in merges if not is_bot(m["login"])}
    authors = {a for a in author.values() if not is_bot(a)}
    print(f"pull requests merged in the window:   {len(merges)}")
    print(f"  with an author from the API:        {len(found)}")
    print(f"  owner differs from author:          {len(differ)}")
    print(f"  of these, by a top-10 contributor:  {sum(author[int(m['pr'])] in top10 for m in differ)}")
    print(f"distinct accounts, owners (git):      {len(owners)}")
    print(f"distinct accounts, authors (API):     {len(authors)}")


if __name__ == "__main__":
    if sys.argv[1] == "prs":
        count_prs(sys.argv[2] if len(sys.argv) > 2 else "kubernetes.git")
    elif sys.argv[1] == "window":
        count_window()
    else:
        count_commits()
