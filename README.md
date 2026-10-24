# Capstone Software Development (2IRR80), team 7: Kubernetes

Scripts and patches for our final report on [kubernetes/kubernetes](https://github.com/kubernetes/kubernetes). Appendix A of the report says what each script does, and Table A.2 gives the tool versions and exact commands of every metric.

All code measurements are taken at commit [`f54c212`](https://github.com/kubernetes/kubernetes/tree/f54c212e3a2f75d674b717a9b29052b20b60aefc) (tag v1.37.0). The activity data covers the year between the v1.34.0 and v1.37.0 releases, 27 August 2025 to 26 August 2026.

## Scripts

Run them from the root of this repository: they write their output to `data/` (and the plot to `Figures/`), which they create where needed.

| Report | Script | Run with |
|---|---|---|
| §1.6–1.8 | `git_commits.py` | `python3 scripts/git_commits.py <clone>`, on a `git clone --bare --filter=tree:0` |
| | `search_collect.py` | `python3 scripts/search_collect.py` (unauthenticated GitHub search, slow) |
| | `file_activity.py` | `python3 scripts/file_activity.py <clone> <login,login,...>`, on a `--filter=blob:none` clone |
| | `analyze_activities.py` | `mkdir -p Figures && python3 scripts/analyze_activities.py` (needs matplotlib) |
| | `pr_thread.py` | `mkdir -p html && python3 scripts/pr_thread.py <PR number> ...` |
| | `github_accounts.py` | `python3 scripts/github_accounts.py prs\|commits\|window [clone]` (needs `gh auth login`) |
| §2 | `quality_metrics.py` | `python3 scripts/quality_metrics.py <kubernetes checkout> <simian.jar> --coverage > out.json` |
| | `typemetrics.go` | called by `quality_metrics.py`; alone: `go run scripts/typemetrics.go <dir>` |
| | `importusage.go` | `go run scripts/importusage.go <checkout>/pkg/kubelet` (P6) |
| §3 | `health_collect.py` | `python3 scripts/health_collect.py` (needs `gh auth login`, about an hour) |
| | `health_prior.py` | `python3 scripts/health_prior.py` |
| | `health_analyze.py` | `python3 scripts/health_analyze.py` |

`quality_metrics.py` expects gocyclo 0.6.0, gocognit 1.3.0 and scc 3.7.0 in `~/go/bin`, and Simian 2.5.10, which is not included because of its licence. It measures `pkg/kubelet` and the three baselines of Table 2.4 (ReplicaSet controller, scheduler, kube-proxy). `--coverage` runs the unit tests and takes a few minutes.

The data for §3 was collected on 16 October 2026. Running the collection again gives slightly different numbers, since pull requests change state and accounts get deleted.

## Patches

| Patch | Report | Change |
|---|---|---|
| `0001-kubelet-split-convertToAPIContainerStatuses-into-hel.patch` | P1 | Splits `convertToAPIContainerStatuses` (V(G) 109) into helpers in the new file `pkg/kubelet/kubelet_container_status.go` |
| `0002-kubelet-unit-tests-for-volumeMountStatuses-and-image.patch` | P2 | Unit tests for two of these helpers; applies on top of P1 |

To measure the states of §2.5:

```sh
git clone https://github.com/kubernetes/kubernetes && cd kubernetes
git checkout f54c212e3a2f75d674b717a9b29052b20b60aefc
git am <this repo>/patches/0001-*.patch   # after P1
git am <this repo>/patches/0002-*.patch   # after P1+P2
```

After each step, run `quality_metrics.py` on the checkout.
