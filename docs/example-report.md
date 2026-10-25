# Example performance report

This is a real report produced by HiveNet for a test job (3 bees on a single 2-core PC, the sample reviews from `examples/` repeated 15 times), downloaded with **Download report** in the web panel. See [`TESTING.md`](../TESTING.md) for how to read it.

---

## Performance report — job 1 “Sample reviews”

Generated: 2026-10-25T21:45:39  
Status: done · operations: clean,stats,embed · texts: 30000 (unique: 657)  
Submitted: 2026-10-25T21:45:10 · duration: **3s** (2.71 s)

## Summary

- Bottleneck: **Result delivery (estimate)** (26.5% of the cells' active time)
- What to do: Result delivery is slow: slow upstream network or large results (e.g. embeddings). Consider compression. Overall, actual computation is only 1.0% of the time: packets are too small or the operation too light for distribution to pay off.
- Useful work (computation) as a share of the cells' active time: **1.0%**
- Bees that worked: 3 · cells: 114 + 23 for verification · rejected: 0
- Reused from memory: 0.0 units not recomputed (distributed: 7.463)

## Does distribution pay off? (estimate for this job)

| Metric | Value |
|---|---:|
| Pure computation a single PC would have done | 0.049 s |
| Actual duration on the network | 2.71 s |
| Estimated speed-up (>1 = the network is faster) | 0.018× |
| Effective bee utilisation (computation / available time) | 0.7% |
| Verification cost relative to computation | 9.8% |

> Estimate: the "single PC" figure is the sum of the computation times measured on the bees, without network or encryption. The definitive proof is the benchmark of 1 PC against 2/5/10 nodes on the same job.

## Where each cell's time goes (active time)

| Phase | Mean (ms) | 95th percentile (ms) | Total (s) | Share |
|---|---:|---:|---:|---:|
| Packet download | 9.72 | 10.5 | 1.331 | 24.1% |
| Key request | 6.29 | 13.0 | 0.862 | 15.6% |
| Sandbox start-up | 5.96 | 9.2 | 0.817 | 14.8% |
| Decryption | 0.24 | 0.85 | 0.032 | 0.6% |
| Module preparation | 0.61 | 2.46 | 0.084 | 1.5% |
| Computation (WebAssembly module) | 0.39 | 1.49 | 0.054 | 1.0% |
| Result sealing | 1.78 | 6.84 | 0.243 | 4.4% |
| Result delivery (estimate) | 10.69 | 22.0 | 1.464 | 26.5% |
| Result check (server) | 4.72 | 10.13 | 0.646 | 11.7% |

## Queue waits

| Wait | Mean (ms) | 95th percentile (ms) | Total (s) |
|---|---:|---:|---:|
| Waiting for a free bee (server) | 1146.99 | 2057.87 | 157.138 |
| Queued on the node | 51.12 | 164.0 | 7.004 |

Waits are normal when a job has more cells than there are bees available: they show how much more bees would help, not what a cell costs.

## Network and resources

- Data sent to bees: 2907.4 KB · received from bees: 3654.0 KB · 0.21 KB per text · average 2418.1 KB/s
- CPU used by bees: 0.422 s · peak memory of a bee: 168.1 MB · module memory: 0.4 MB

## By operation

| Operation | Cells | Texts | Computation (s) | Mean per cell (ms) | KB exchanged |
|---|---:|---:|---:|---:|---:|
| clean | 108 | 30000 | 0.038 | 0.35 | 4601.1 |
| stats | 3 | 657 | 0.004 | 1.36 | 50.8 |
| embed | 3 | 657 | 0.007 | 2.25 | 926.8 |

## Bees (anonymised)

| Bee | Cells | Rejected | Texts | Computation (s) | Busy | Texts/s |
|---|---:|---:|---:|---:|---:|---:|
| Bee 1 | 48 | 0 | 13283 | 0.019 | 65.6% | 7457.8 |
| Bee 2 | 48 | 0 | 13030 | 0.019 | 64.1% | 7497.0 |
| Bee 3 | 41 | 0 | 11411 | 0.015 | 50.4% | 8341.1 |

## Server

- Linux x86_64, Python 3.13.17, 2 cores · up for 30s
- CPU used: 2.35 s (average 0.077 cores) · memory: 92.0 MB (peak 95.6 MB) · load: 0.27

| Request | Count | Mean (ms) | 95th pct. (ms) | Max (ms) | Errors | KB sent | Total time (s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| node_result | 137 | 7.61 | 17.47 | 273.38 | 0 | 4.5 | 1.04 |
| node_work | 43 | 16.54 | 14.32 | 269.55 | 0 | 2931.3 | 0.71 |
| register | 4 | 97.02 | 99.79 | 99.79 | 0 | 0.2 | 0.39 |
| node_key | 137 | 1.55 | 4.66 | 16.69 | 0 | 7.2 | 0.21 |
| jobs_submit | 1 | 101.92 | 101.92 | 101.92 | 0 | 0.4 | 0.1 |
| jobs_list | 2 | 5.46 | 10.05 | 10.05 | 0 | 0.9 | 0.01 |
| job_metrics | 1 | 11.5 | 11.5 | 11.5 | 0 | 4.2 | 0.01 |
| public_stats | 3 | 1.43 | 1.83 | 1.83 | 0 | 0.3 | 0.0 |
| nodes_register | 3 | 1.08 | 1.19 | 1.19 | 0 | 0.6 | 0.0 |
| node_hello | 3 | 0.87 | 1.48 | 1.48 | 0 | 0.6 | 0.0 |
| modules_manifest | 3 | 0.18 | 0.26 | 0.26 | 0 | 3.3 | 0.0 |
| job_get | 1 | 1.22 | 1.22 | 1.22 | 0 | 0.4 | 0.0 |

## How to read these figures

- **Waiting for a free bee**: time during which the cell is ready but no bee picks it up.
- **Useful work**: the share of active time spent on actual computation; the rest is the cost of distribution (network, encryption, start-up).
- **Texts/s per bee**: texts processed per second of active time (computation plus overhead), i.e. the bee's real speed.
- **Result delivery**: measured by the bee on its previous delivery, so it is an estimate.
- The raw data for every cell is in the CSV file you can download from the panel.
