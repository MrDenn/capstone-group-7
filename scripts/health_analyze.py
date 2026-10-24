#!/usr/bin/env python3
"""Compute the Section 3 metrics from the data of health_collect.py and health_prior.py.

Steps:
  1. bots      three measures: GitHub account type "Bot", login pattern, templated comments
  2. aliases   two measures: same display name, same login after normalisation
  3. activity  per human account: PRs and issues opened, comments, reviews, /lgtm and /approve
  4. groups    key, frequent, occasional and one-time contributors; newcomers; dormant
  5. process   first human response, /ok-to-test wait, PR outcomes, retention, reviewer load,
               issue triage, and the stale rule of suggestion S1 tested on the bot-closed PRs
  6. sample    stratified random sample of newcomer pull requests for the qualitative analysis

Input:  data/raw/health_items.jsonl, data/raw/health_prior.json
Output: data/health_summary.json (all numbers), data/health_sample.md (sample for reading)
Usage:  python3 health_analyze.py
"""
import collections, datetime as dt, json, math, random, re, statistics, unicodedata

START, END = dt.datetime(2025, 8, 27, tzinfo=dt.timezone.utc), dt.datetime(2026, 8, 27, tzinfo=dt.timezone.utc)
RETENTION_DAYS = 90
SEED = 7  # our team number

# Decisions we took after checking the accounts flagged below by hand (Section 3.1).
CONFIRMED_BOTS = {"tuobi2"}  # flagged by behaviour only, but bots after inspection
CONFIRMED_HUMANS = set()      # flagged by a measure, but humans after inspection
CONFIRMED_ALIASES = {         # second login -> main login of the same person
    "sonika-arora": "sonikaarora",  # closes #136580 of sonikaarora "Will open a clean PR"
    "lumaks-redox": "mlushpenko",   # retracts #138763 of mlushpenko, same report as #138762
    "jdzikowskigke": "jdzikowski",  # same name and company, "gke" work account
}

BOT_LOGIN = re.compile(r"\[bot\]$|(^|[-_])(ro)?bot$", re.I)
COMMAND = re.compile(r"^/[a-z-]+", re.M)


def ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def days(a, b):
    return (b - a).total_seconds() / 86400


def norm_body(body):
    """First line of a comment with numbers, links and mentions removed, to find templates."""
    line = (body or "").strip().split("\n")[0].lower()
    return re.sub(r"https?://\S+|@[\w-]+|#?\d+", "", line).strip()[:80]


def pct(values, q):
    v = sorted(values)
    return v[min(len(v) - 1, int(q * len(v)))] if v else None


def summary(values):
    return {"n": len(values), "median": round(statistics.median(values), 1) if values else None,
            "p75": round(pct(values, 0.75), 1) if values else None}


items = [json.loads(l) for l in open("data/raw/health_items.jsonl")]
prior = json.load(open("data/raw/health_prior.json"))
prs = [i for i in items if i["__typename"] == "PullRequest"]
issues = [i for i in items if i["__typename"] == "Issue"]


def events(item):
    """(login, typename, kind, time, body) for everything that happened on one item."""
    if item["author"]:
        yield item["author"]["login"], item["author"]["__typename"], "open_" + item["__typename"], ts(item["createdAt"]), ""
    for c in item["comments"]["nodes"]:
        if c["author"]:
            yield c["author"]["login"], c["author"]["__typename"], "comment", ts(c["createdAt"]), c["body"]
    for r in item.get("reviews", {"nodes": []})["nodes"]:
        if r["author"] and r["submittedAt"]:
            yield r["author"]["login"], r["author"]["__typename"], "review", ts(r["submittedAt"]), ""


# 1. Bots -----------------------------------------------------------------------------
typename, names, comments_by = {}, collections.defaultdict(set), collections.defaultdict(list)
for it in items:
    for login, tn, kind, t, body in events(it):
        typename[login] = tn
        if kind == "comment":
            comments_by[login].append(body)
    for who in [it["author"]] + [c["author"] for c in it["comments"]["nodes"]]:
        if who and who.get("name"):
            names[who["login"]].add(who["name"])

flags = collections.defaultdict(set)
for login in typename:
    if typename[login] == "Bot":
        flags[login].add("B1 type")
    if BOT_LOGIN.search(login):
        flags[login].add("B2 name")
    bodies = comments_by[login]
    if len(bodies) >= 20:
        counts = collections.Counter(norm_body(b) for b in bodies)
        templated = sum(n for text, n in counts.items() if n >= 5 and not text.startswith("/"))
        if templated / len(bodies) >= 0.8:
            flags[login].add("B3 templates")
bots = {l for l, f in flags.items() if (f & {"B1 type", "B2 name"}) or l in CONFIRMED_BOTS} - CONFIRMED_HUMANS
bot_report = {l: {"flags": sorted(f), "comments": len(comments_by[l]), "type": typename[l],
                  "bot": l in bots} for l, f in sorted(flags.items())}

def only_commands(body):
    """True for a comment that consists of Prow commands only, e.g. "/ok-to-test"."""
    return bool(body.strip()) and all(COMMAND.match(x.strip()) or not x.strip() for x in body.strip().split("\n"))


# Comments that are mostly Prow commands make a human look automated (borderline cases).
command_share = {}
for login, bodies in comments_by.items():
    if login not in bots and len(bodies) >= 50:
        command_share[login] = round(sum(map(only_commands, bodies)) / len(bodies), 2)

# 2. Aliases --------------------------------------------------------------------------
def norm_name(n):
    n = unicodedata.normalize("NFKD", n).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z]", "", n)


def norm_login(l):
    l = re.sub(r"[^a-z]", "", l.lower())
    return re.sub(r"(work|corp|google|redhat|msft|ms|ibm|dev|gh|github|official|alt)$", "", l)


by_name, by_login = collections.defaultdict(set), collections.defaultdict(set)
for login in typename:
    if login in bots:
        continue
    for n in names.get(login, ()):
        if len(norm_name(n)) >= 6:
            by_name[norm_name(n)].add(login)
    if len(norm_login(login)) >= 5:
        by_login[norm_login(login)].add(login)
alias_candidates = {"A1 same name": sorted(sorted(s) for s in by_name.values() if len(s) > 1),
                    "A2 similar login": sorted(sorted(s) for s in by_login.values() if len(s) > 1)}


def person(login):
    return CONFIRMED_ALIASES.get(login, login)


# 3. Activity per human account -------------------------------------------------------
act = collections.defaultdict(lambda: collections.Counter())
active_days = collections.defaultdict(set)
for it in items:
    for login, tn, kind, t, body in events(it):
        if login in bots or not (START <= t < END):
            continue
        p = person(login)
        act[p][kind] += 1
        for line in (body or "").split("\n"):
            if re.match(r"^/(lgtm|approve)\b(?!.*cancel)", line.strip()):
                act[p]["cmd_" + line.strip().split()[0][1:]] += 1
        active_days[p].add(t.date())
    if it["__typename"] == "PullRequest" and it["mergedAt"] and it["author"] and it["author"]["login"] not in bots:
        act[person(it["author"]["login"])]["merged_pr"] += 1
total = {p: c["open_PullRequest"] + c["open_Issue"] + c["comment"] + c["review"] for p, c in act.items()}
all_events = sum(total.values())

# 4. Contributor groups ---------------------------------------------------------------
ranked = sorted(total, key=total.get, reverse=True)
cum, key = 0, set()
for p in ranked:  # smallest set of accounts that produces half of all activity
    if cum >= all_events / 2:
        break
    key.add(p)
    cum += total[p]
months = {p: {(d.year, d.month) for d in ds} for p, ds in active_days.items()}
group = {}
for p in total:
    if p in key:
        group[p] = "key"
    elif len(months[p]) >= 6:
        group[p] = "frequent"
    elif len(active_days[p]) >= 2:
        group[p] = "occasional"
    else:
        group[p] = "one-time"
prior_by_person = collections.defaultdict(list)  # a merged person is new only if all their logins are
for l, v in prior.items():
    if l not in bots:
        prior_by_person[person(l)].append(v["before"])
newcomers = {p for p, before in prior_by_person.items() if all(b == 0 for b in before)}
gaps = [days(dt.datetime.combine(a, dt.time()), dt.datetime.combine(b, dt.time()))
        for p in total if group[p] in ("key", "frequent")
        for a, b in zip(sorted(active_days[p]), sorted(active_days[p])[1:])]
dormant_after = pct(gaps, 0.99)  # longer silences than this are rare for regular contributors
dormant = {p for p in total if group[p] in ("key", "frequent")
           and days(dt.datetime.combine(max(active_days[p]), dt.time(), dt.timezone.utc), END) > dormant_after}
share = lambda n: round(100 * sum(total[p] for p in ranked[:n]) / all_events, 1)

# 5. Process metrics on pull requests -------------------------------------------------
def author_group(item):
    login = item["author"]["login"]
    if person(login) in newcomers:
        return "newcomer"
    return "member" if item["authorAssociation"] in ("MEMBER", "OWNER", "COLLABORATOR") else "other non-member"


def closed_by_triage_bot(item):
    return item["state"] == "CLOSED" and not item.get("mergedAt") and any(
        c["author"] and c["author"]["login"] == "k8s-triage-robot" and re.search(r"^/close", c["body"], re.M)
        for c in item["comments"]["nodes"])


def first_human_response(item):
    author = item["author"]["login"]
    times = [t for login, tn, kind, t, body in events(item)
             if kind != "open_" + item["__typename"] and login != author and login not in bots]
    return days(ts(item["createdAt"]), min(times)) if times else None


def author_waiting(item):
    """The rule we propose in S1: the author is waiting if no human other than the author wrote a
    comment or review that is more than Prow commands, or if the author wrote the last such one."""
    author = person(item["author"]["login"])
    said = sorted((t, person(login)) for login, tn, kind, t, body in events(item)
                  if login not in bots and kind in ("comment", "review") and not (kind == "comment" and only_commands(body)))
    return all(p == author for _, p in said) or said[-1][1] == author


def closed_by(item):
    """Who closed an item: the triage bot, Prow (on a /close command or a merged fix), the author or another human."""
    if item["state"] != "CLOSED" or item.get("mergedAt"):
        return ""
    if closed_by_triage_bot(item):
        return "triage bot"
    nodes = item.get("timelineItems", {}).get("nodes") or []
    actor = nodes[-1]["actor"]["login"] if nodes and nodes[-1].get("actor") else None
    if actor is None:
        return "unknown"
    if actor in bots:
        return "prow"
    return "author" if person(actor) == person(item["author"]["login"]) else "other human"


def wilson(k, n, z=1.96):
    """95% Wilson interval of a share k/n, in percent."""
    p, d = k / n, 1 + z * z / n
    centre, half = (p + z * z / (2 * n)) / d, z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(100 * (centre - half)), round(100 * (centre + half))]


human_prs = [p for p in prs if p["author"] and p["author"]["login"] not in bots]
process = {}
for g in ("newcomer", "other non-member", "member"):
    sel = [p for p in human_prs if author_group(p) == g]
    resp = [first_human_response(p) for p in sel]
    merged = [p for p in sel if p["mergedAt"]]
    ok_wait, ok_never = [], 0
    for p in sel:
        asked = [ts(c["createdAt"]) for c in p["comments"]["nodes"]
                 if c["author"] and c["author"]["login"] == "k8s-ci-robot" and "I'm waiting for a" in c["body"]]
        if asked:
            given = [ts(c["createdAt"]) for c in p["comments"]["nodes"] if c["author"]
                     and c["author"]["login"] not in bots and re.search(r"^/ok-to-test", c["body"], re.M)]
            if given:
                ok_wait.append(days(asked[0], min(given)))
            else:
                ok_never += 1
    process[g] = {
        "prs": len(sel),
        "first_human_response_days": summary([r for r in resp if r is not None]),
        "no_human_response": sum(r is None for r in resp),
        "merged": len(merged),
        "closed_by_triage_bot": sum(closed_by_triage_bot(p) for p in sel),
        "still_open": sum(p["state"] == "OPEN" for p in sel),
        "days_to_merge": summary([days(ts(p["createdAt"]), ts(p["mergedAt"])) for p in merged]),
        "ok_to_test_requested": len(ok_wait) + ok_never,
        "ok_to_test_wait_days": summary(ok_wait),
        "ok_to_test_never": ok_never,
    }

# Retention: newcomers whose first item came early enough to observe 90 days after it.
first_item, items_by = {}, collections.defaultdict(list)
for it in items:
    if it["author"] and it["author"]["login"] not in bots:
        items_by[person(it["author"]["login"])].append(it)
retention = collections.Counter()
for p in newcomers:
    own = sorted(items_by.get(p, []), key=lambda i: i["createdAt"])
    if not own or ts(own[0]["createdAt"]) > END - dt.timedelta(days=RETENTION_DAYS):
        continue
    first = own[0]
    returned = any(0 < days(ts(first["createdAt"]), ts(o["createdAt"])) <= RETENTION_DAYS and
                   days(ts(first["createdAt"]), ts(o["createdAt"])) >= 1 for o in own[1:])
    if first["__typename"] == "PullRequest":
        kind = "merged" if first["mergedAt"] else ("bot-closed" if closed_by_triage_bot(first) else "not merged")
    else:
        kind = "issue"
    retention[kind + (" returned" if returned else " gone")] += 1

# Reviewer load: who types /lgtm and /approve.
approvers = collections.Counter({p: c["cmd_approve"] for p, c in act.items() if c["cmd_approve"]})
lgtms = collections.Counter({p: c["cmd_lgtm"] for p, c in act.items() if c["cmd_lgtm"]})
top_share = lambda cnt, n: round(100 * sum(v for _, v in cnt.most_common(n)) / max(1, sum(cnt.values())), 1)
# The same for /approve, but counted once per item, so that a repeated /approve does not count twice.
approved_items = collections.Counter()
for it in items:
    approved_items.update({person(c["author"]["login"]) for c in it["comments"]["nodes"]
                           if c["author"] and c["author"]["login"] not in bots and START <= ts(c["createdAt"]) < END
                           and any(re.match(r"^/approve\b(?!.*cancel)", l.strip()) for l in c["body"].split("\n"))})

# S1: which bot-closed newcomer PRs a stale rule would have kept open.
bot_closed_new = [p for p in human_prs if author_group(p) == "newcomer" and closed_by_triage_bot(p)]
stale_rules = {"newcomer_prs_closed_by_bot": len(bot_closed_new),
               "no_github_review": sum(p["reviews"]["totalCount"] == 0 for p in bot_closed_new),
               "no_human_response": sum(first_human_response(p) is None for p in bot_closed_new),
               "author_waiting": sum(map(author_waiting, bot_closed_new))}

# Dormant frequent contributors: who they are.
members = {person(i["author"]["login"]) for i in items
           if i["author"] and i["authorAssociation"] in ("MEMBER", "OWNER", "COLLABORATOR")}
after_window = {person(login) for it in items for login, tn, kind, t, body in events(it) if login not in bots and t >= END}
dormant_frequent = [p for p in dormant if group[p] == "frequent"]

# Issues: triage.
human_issues = [i for i in issues if i["author"] and i["author"]["login"] not in bots]
untriaged = [i for i in human_issues if any(l["name"] == "needs-triage" for l in i["labels"]["nodes"])]
sigs = lambda i: {l["name"] for l in i["labels"]["nodes"] if l["name"].startswith("sig/")}
issue_stats = {
    "issues": len(human_issues),
    "triage_accepted": sum(any(l["name"] == "triage/accepted" for l in i["labels"]["nodes"]) for i in human_issues),
    "still_needs_triage": sum(any(l["name"] == "needs-triage" for l in i["labels"]["nodes"]) for i in human_issues),
    "closed_by_triage_bot": sum(closed_by_triage_bot(i) for i in human_issues),
    "first_human_response_days": summary([r for r in map(first_human_response, human_issues) if r is not None]),
    "no_human_response": sum(first_human_response(i) is None for i in human_issues),
    "good_first_issue": sum(any(l["name"] == "good first issue" for l in i["labels"]["nodes"]) for i in human_issues),
    "needs_triage_open": sum(i["state"] == "OPEN" for i in untriaged),
    "needs_triage_closed_by": dict(collections.Counter(closed_by(i) for i in untriaged if i["state"] == "CLOSED")),
    "needs_triage_open_no_human_response": sum(i["state"] == "OPEN" and first_human_response(i) is None for i in untriaged),
    "needs_triage_open_by_newcomers": sum(i["state"] == "OPEN" and person(i["author"]["login"]) in newcomers for i in untriaged),
    "needs_triage_open_median_age_days": round(statistics.median(
        days(ts(i["createdAt"]), dt.datetime(2026, 10, 16, tzinfo=dt.timezone.utc)) for i in untriaged if i["state"] == "OPEN")),
    "needs_triage_by_sig": {s: [sum(s in sigs(i) for i in untriaged), sum(s in sigs(i) for i in human_issues)]
                            for s in sorted(set().union(*map(sigs, human_issues)))},
}

# 6. Stratified sample of newcomer pull requests ---------------------------------------
rng = random.Random(SEED)
strata = {"newcomer PR closed by the triage bot": [p for p in human_prs if author_group(p) == "newcomer" and closed_by_triage_bot(p)],
          "newcomer PR merged": [p for p in human_prs if author_group(p) == "newcomer" and p["mergedAt"]]}
sample = {name: rng.sample(sorted(sel, key=lambda p: p["number"]), min(15, len(sel))) for name, sel in strata.items()}
with open("data/health_sample.md", "w") as f:
    f.write(f"# Stratified sample of newcomer pull requests (seed {SEED})\n\n")
    for name, sel in sample.items():
        f.write(f"## {name} ({len(sel)} of {len(strata[name])})\n\n")
        for p in sorted(sel, key=lambda p: p["number"]):
            f.write(f"### #{p['number']} {p['title']}\nhttps://github.com/kubernetes/kubernetes/pull/{p['number']}"
                    f" by {p['author']['login']}, opened {p['createdAt'][:10]}, comments {p['comments']['totalCount']}\n\n")
            for c in p["comments"]["nodes"]:
                who = c["author"]["login"] if c["author"] else "ghost"
                body = re.sub(r"\s+", " ", c["body"])[:300]
                f.write(f"- {c['createdAt'][:10]} **{who}**: {body}\n")
            f.write("\n")

out = {
    "items": {"prs": len(prs), "issues": len(issues), "truncated_comments": sum(i["comments"]["totalCount"] > 40 for i in items)},
    "bots": {"flagged": bot_report, "bots": sorted(bots),
             "events_by_bots": sum(1 for it in items for e in events(it) if e[0] in bots),
             "events_total": sum(1 for it in items for e in events(it))},
    "command_share_of_humans": dict(sorted(command_share.items(), key=lambda x: -x[1])[:15]),
    "aliases": alias_candidates,
    "activity": {"accounts": len(total), "events": all_events,
                 "by_kind": dict(sum(act.values(), collections.Counter())),
                 "top1pct_share": share(len(ranked) // 100), "top10pct_share": share(len(ranked) // 10),
                 "key_size": len(key), "key_min_events": min(total[p] for p in key)},
    "groups": dict(collections.Counter(group.values())),
    "accounts_by_active_months": dict(collections.Counter(len(m) for m in months.values())),
    "groups_events": {g: sum(total[p] for p in total if group[p] == g) for g in set(group.values())},
    "newcomers": {"authors_new": len(newcomers & set(total)),
                  "authors": sum(1 for l in prior if l not in bots),
                  "by_group": dict(collections.Counter(group[p] for p in newcomers if p in group))},
    "dormant": {"threshold_days": round(dormant_after, 1), "count": len(dormant),
                "by_group": dict(collections.Counter(group[p] for p in dormant)),
                "gap_p90": pct(gaps, 0.9), "gap_p99": pct(gaps, 0.99),
                "frequent_newcomers": sum(p in newcomers for p in dormant_frequent),
                "frequent_members": sum(p in members for p in dormant_frequent),
                "frequent_events": sum(total[p] for p in dormant_frequent),
                "frequent_last_active_may_june": sum(max(active_days[p]).month in (5, 6) for p in dormant_frequent),
                "frequent_active_after_window": sum(p in after_window for p in dormant_frequent)},
    "process_prs": process,
    "retention_90d": dict(retention),
    "retention_90d_ci95": {k: wilson(retention[k + " returned"], retention[k + " returned"] + retention[k + " gone"])
                           for k in ("merged", "not merged", "bot-closed", "issue")},
    "stale_rules_s1": stale_rules,
    "reviewers": {"approve_cmds": sum(approvers.values()), "approve_accounts": len(approvers),
                  "approve_top10_share": top_share(approvers, 10), "approve_top2_share": top_share(approvers, 2),
                  "approve_top10": approvers.most_common(10),
                  "approve_items": sum(approved_items.values()), "approve_items_top10_share": top_share(approved_items, 10),
                  "approve_items_top2_share": top_share(approved_items, 2),
                  "lgtm_cmds": sum(lgtms.values()), "lgtm_accounts": len(lgtms),
                  "lgtm_top10_share": top_share(lgtms, 10), "lgtm_top10": lgtms.most_common(10)},
    "issues": issue_stats,
    "key_contributors": [(p, total[p], dict(act[p])) for p in ranked[:len(key)]],
}
json.dump(out, open("data/health_summary.json", "w"), indent=1, default=str, sort_keys=True)
print(json.dumps({k: v for k, v in out.items() if k not in ("key_contributors", "bots")}, indent=1, default=str))
print("bots:", json.dumps(out["bots"]["bots"]), "flagged:", json.dumps(bot_report))
