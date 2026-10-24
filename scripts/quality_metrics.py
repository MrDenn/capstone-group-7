#!/usr/bin/env python3
"""Measure the Section 2 metrics on one Kubernetes checkout.

Usage: python3 quality_metrics.py <kubernetes checkout> <simian.jar> [--coverage]

Runs the same tools with the same settings on any checkout, so that the numbers before
and after the refactoring can be compared:
  M1 cyclomatic complexity  gocyclo  (per function)
  M2 cognitive complexity   gocognit (per function; it leaves out functions with 0, we add them back)
  M3 size                   typemetrics.go (lines per function, methods and fields per type), scc (code lines)
  M4 coupling               go list (packages imported by / importing pkg/kubelet), typemetrics.go (packages used by a struct)
  M5 duplication            Simian, -language=java (Go has the same // and /* */ comments), thresholds 6 and 10
  M6 statement coverage     go test -coverprofile (only with --coverage, takes a few minutes)
  M7 cohesion               TCC of Bieman and Kang, from the "uses" and "struct" lines of typemetrics.go
Scope: non-test, non-generated .go files (no *_test.go, zz_generated*, *.pb.go) under pkg/kubelet, and
three other components as baselines: the ReplicaSet controller, the scheduler and kube-proxy. M4, M6
and the TCC of the central type are measured on the package that declares the component's central
type (CORE). Output: JSON on stdout.
"""
import collections, json, os, re, statistics, subprocess, sys

REPO, SIMIAN = sys.argv[1], sys.argv[2]
GOBIN = os.path.expanduser("~/go/bin")
IGNORE = r"_test\.go$|zz_generated|\.pb\.go$"
TYPEMETRICS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "typemetrics.go")
SCOPES = ["pkg/kubelet", "pkg/controller/replicaset", "pkg/scheduler", "pkg/proxy"]
# For each scope, the package of its central type and that type. kube-proxy has one Proxier per
# backend; iptables is the default mode on Linux (cmd/kube-proxy/app/server_linux.go).
CORE = {"pkg/kubelet": ("pkg/kubelet", "Kubelet"),
        "pkg/controller/replicaset": ("pkg/controller/replicaset", "ReplicaSetController"),
        "pkg/scheduler": ("pkg/scheduler", "Scheduler"),
        "pkg/proxy": ("pkg/proxy/iptables", "Proxier")}
# The function we refactor, and the helpers the refactoring creates (absent before).
TARGET = ["convertToAPIContainerStatuses", "defaultContainerStatuses", "volumeMountStatuses",
          "inferMissingContainerTerminations", "markRemovedByRestartAll", "isDefaultWaitingStatus",
          "applyRuntimeContainerStatuses", "addFeatureGatedStatusFields", "convertContainerStatus",
          "setImageVolumeStatuses", "imageVolumeSpec", "setContainerState",
          "convertContainerStatusResources", "convertContainerStatusUser",
          "applyRestartWaitingStatuses", "initContainerDone"]


def run(*cmd):
    return subprocess.run(cmd, cwd=REPO, capture_output=True, text=True).stdout


def per_function(tool, scope):
    """{position: (complexity, name)} for every function gocyclo/gocognit reports."""
    out = {}
    for line in run(f"{GOBIN}/{tool}", "-ignore", IGNORE, scope).splitlines():
        value, _pkg, name, pos = line.split(" ")
        out[pos.rsplit(":", 1)[0]] = (int(value), name)  # drop the column
    return out


def distribution(values, threshold):
    v = sorted(values)
    return {"n": len(v), "median": statistics.median(v), "p90": v[int(0.9 * len(v)) - 1],
            "max": v[-1], f">={threshold}": sum(x >= threshold for x in v)}


def measure(scope):
    cyclo = per_function("gocyclo", scope)
    cognit = per_function("gocognit", scope)
    # gocognit does not print functions with complexity 0; gocyclo prints every function.
    cognit = {pos: cognit.get(pos, (0, name)) for pos, (_, name) in cyclo.items()}

    tm = {"func": [], "struct": [], "uses": []}
    for line in run("go", "run", TYPEMETRICS, scope).splitlines():
        kind, pos, *rest = line.split("\t")
        tm[kind].append([pos] + rest)
    funcs, structs = tm["func"], tm["struct"]
    # Receiver types are keyed by directory, since e.g. "manager" exists in several packages.
    nom = collections.Counter((os.path.dirname(p), recv) for p, recv, _, _ in funcs if recv != "-")
    wmc = collections.Counter()
    for p, recv, _, _ in funcs:
        if recv != "-":
            wmc[os.path.dirname(p), recv] += cyclo[p][0]
    tcc = cohesion(structs, tm["uses"])
    core_dir, core_type = CORE[scope]

    res = {
        "M1_cyclomatic": distribution([c for c, _ in cyclo.values()], 10),
        "M2_cognitive": distribution([c for c, _ in cognit.values()], 15),
        "M3_function_lines": distribution([int(n) for *_, n in funcs], 60),
        "M3_methods_per_type": distribution(list(nom.values()), 20),
        "M3_fields_per_struct": distribution([int(s[2]) for s in structs], 20),
        "M3_WMC_per_type": distribution(list(wmc.values()), 50),
        "M7_TCC_per_type": {"n": len(tcc), "median": statistics.median(t for t, _ in tcc.values()),
                            "<1/3": sum(t < 1 / 3 for t, _ in tcc.values())},
        "top10_cyclomatic": sorted(([c, n, p] for p, (c, n) in cyclo.items()), reverse=True)[:10],
        "top10_cognitive": sorted(([c, n, p] for p, (c, n) in cognit.items()), reverse=True)[:10],
        # Lanza and Marinescu's God Class conditions on WMC and TCC (ATFD needs type information).
        "top10_WMC_with_TCC": [[w, round(tcc[t][0], 2) if t in tcc else None, nom[t], "/".join(t)]
                               for t, w in wmc.most_common(10)],
        "core": {"package": core_dir, "type": core_type, "NOM": nom[core_dir, core_type],
                 "WMC": wmc[core_dir, core_type], "TCC": round(tcc[core_dir, core_type][0], 3),
                 "TCC_pairs": tcc[core_dir, core_type][1],
                 "TCC_exported_methods": tcc_exported(structs, tm["uses"], core_dir, core_type)},
    }

    scc = json.loads(run(f"{GOBIN}/scc", "--format", "json", "--not-match", IGNORE, scope))
    go = next(lang for lang in scc if lang["Name"] == "Go")
    res["M3_scc_code_lines"], res["M3_scc_files"] = go["Code"], go["Count"]
    res["M5_duplication"] = {t: simian(scope, t) for t in (6, 10)}

    if scope == "pkg/kubelet":
        kubelet = [f for f in funcs if f[1] == "Kubelet"]
        kubelet_pos = {f[0] for f in kubelet}
        struct = next(s for s in structs if s[1] == "Kubelet" and s[0].startswith("pkg/kubelet/kubelet.go"))
        res["Kubelet"] = {
            "methods": len(kubelet),
            "files_with_methods": len({f[0].rsplit(":", 1)[0] for f in kubelet}),
            "fields": int(struct[2]),
            "packages_used_by_fields": len(struct[3].split(",")),
            "sum_cyclomatic_WMC": sum(c for p, (c, _) in cyclo.items() if p in kubelet_pos),
            "sum_cognitive": sum(c for p, (c, _) in cognit.items() if p in kubelet_pos),
            "methods_cyclomatic>=10": sum(1 for p, (c, _) in cyclo.items() if p in kubelet_pos and c >= 10),
        }
        lines = {f[2]: int(f[3]) for f in funcs if f[0].startswith("pkg/kubelet/kubelet")}
        target = {}
        for pos, (c, name) in cyclo.items():
            if name.split(".")[-1] in TARGET and pos.startswith("pkg/kubelet/kubelet"):
                short = name.split(".")[-1]
                target[short] = {"cyclomatic": c, "cognitive": cognit[pos][0], "lines": lines[short], "pos": pos}
        res["target"] = target
    res["M4_coupling"] = coupling("k8s.io/kubernetes/" + core_dir)
    return res


def method_fields(fields, uses):
    """{method: attributes it uses directly, or indirectly through the methods it calls on the
    receiver} for one type, as in Bieman and Kang's definition of TCC."""
    used = collections.defaultdict(set)
    calls = collections.defaultdict(set)
    for _, _, name, selected in uses:
        selected = set(filter(None, selected.split(",")))
        used[name] |= selected & fields
        calls[name] |= selected
    names = set(used)
    changed = True
    while changed:
        changed = False
        for name in names:
            for callee in calls[name] & names:
                if not used[callee] <= used[name]:
                    used[name] |= used[callee]
                    changed = True
    return used


def tcc_of(used, methods):
    """Share of the pairs of methods that use at least one common attribute (None without pairs)."""
    methods = sorted(methods)
    pairs = len(methods) * (len(methods) - 1) // 2
    connected = sum(1 for i, a in enumerate(methods) for b in methods[i + 1:] if used[a] & used[b])
    return (connected / pairs if pairs else None), pairs


def type_uses(structs, uses):
    """{(dir, type): (field names, its "uses" lines)} for every struct type with methods."""
    fields = collections.defaultdict(set)
    for pos, name, _, _, names in structs:
        fields[os.path.dirname(pos), name] |= set(filter(None, names.split(",")))
    by_type = collections.defaultdict(list)
    for u in uses:
        by_type[os.path.dirname(u[0]), u[1]].append(u)
    return {t: (fields[t], u) for t, u in by_type.items() if t in fields}


def cohesion(structs, uses):
    """{(dir, type): (TCC, pairs)} for every struct type with at least two methods. A method
    declared in several files (build variants) counts once. All methods count, not only the
    exported ones, because in Go the package, not the type, is the unit of encapsulation."""
    out = {}
    for t, (fields, u) in type_uses(structs, uses).items():
        used = method_fields(fields, u)
        if len(used) >= 2:
            out[t] = tcc_of(used, used)
    return out


def tcc_exported(structs, uses, directory, name):
    """TCC of one type over its exported methods only, as Bieman and Kang define it, and the
    number of exported methods."""
    fields, u = type_uses(structs, uses)[directory, name]
    used = method_fields(fields, u)
    exported = [m for m in used if m[0].isupper()]
    return tcc_of(used, exported)[0], len(exported)


def coupling(pkg):
    """Efferent coupling Ce (direct non-test imports) and afferent coupling Ca (packages of
    the main module that import pkg), and instability I = Ce / (Ca + Ce). The rank of pkg by
    Ce among all packages of the main module shows whether a high Ce is unusual."""
    imports = {}
    for line in run("go", "list", "-e", "-f", "{{.ImportPath}}{{range .Imports}} {{.}}{{end}}", "./...").splitlines():
        name, *deps = line.split(" ")
        imports[name] = deps
    ce = imports[pkg]
    ca = [p for p, deps in imports.items() if pkg in deps]
    ce_internal = [d for d in ce if d.startswith("k8s.io/")]
    all_ce = sorted((len(deps) for deps in imports.values()), reverse=True)
    return {"Ce": len(ce), "Ce_k8s.io": len(ce_internal), "Ca": len(ca),
            "I": round(len(ce) / (len(ca) + len(ce)), 2), "importers": sorted(ca),
            "rank_by_Ce": 1 + sum(c > len(ce) for c in all_ce), "packages": len(all_ce),
            "median_Ce_all_packages": statistics.median(all_ce),
            "higher_Ce": sorted(p for p, deps in imports.items() if len(deps) > len(ce))}


def simian(scope, threshold):
    """Duplicate lines found by Simian, as a share of its significant lines."""
    # Files are passed one by one: Simian's "-includes=dir/**/*.go" skips the files directly in dir.
    files = sorted(os.path.relpath(os.path.join(d, f), REPO) for d, _, fs in os.walk(os.path.join(REPO, scope))
                   for f in fs if f.endswith(".go") and not re.search(IGNORE, f))
    out = run("java", "-jar", SIMIAN, f"-threshold={threshold}", "-language=java", *files)
    dup = re.search(r"Found (\d+) duplicate lines in (\d+) blocks in (\d+) files", out)
    total = re.search(r"Processed a total of (\d+) significant \((\d+) raw\) lines in (\d+) files", out)
    dup_lines = int(dup.group(1)) if dup else 0
    return {"duplicate_lines": dup_lines, "blocks": int(dup.group(2)) if dup else 0,
            "significant_lines": int(total.group(1)), "files": int(total.group(3)),
            "percent": round(100 * dup_lines / int(total.group(1)), 1)}


def coverage():
    out = subprocess.run(["go", "test", "./pkg/kubelet/", "-count=1", "-coverprofile=/tmp/kubelet.cover"],
                         cwd=REPO, capture_output=True, text=True).stdout
    total = re.search(r"coverage: ([\d.]+)% of statements", out).group(1)
    funcs = run("go", "tool", "cover", "-func=/tmp/kubelet.cover")
    target = {}
    for line in funcs.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[1] in TARGET:
            target[parts[1]] = parts[2]
    return {"package_statements_covered": total + "%", "target": target}


def core_coverage(package):
    """Statement coverage of the unit tests of one baseline package."""
    out = run("go", "test", "./" + package + "/", "-count=1", "-cover")
    return re.search(r"coverage: ([\d.]+)% of statements", out).group(1) + "%"


if __name__ == "__main__":
    result = {"commit": run("git", "rev-parse", "HEAD").strip(),
              "go": run("go", "version").strip(),
              "scopes": {s: measure(s) for s in SCOPES}}
    if "--coverage" in sys.argv:
        result["M6_coverage"] = coverage()
        for s in SCOPES[1:]:
            result["scopes"][s]["M6_coverage_core"] = core_coverage(CORE[s][0])
    json.dump(result, sys.stdout, indent=1)
