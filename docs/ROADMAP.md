# Status, measurements and next steps

## Where we are (October 2026)

| Use | Status |
| --- | --- |
| Testing on a single PC | Ready |
| Testing on a home network with trusted people | Ready (see `TESTING.md`, `docs/PARTICIPANTS.md`) |
| Testing with people over the internet | The baseline security listed below is still missing |
| Proving that distribution pays off | Not yet: it needs heavy operations and the benchmark |

**Project principles:**
- **Security from the very first test:** participants lend their PCs and their data.
- **Zero waste:** if something can be done using less CPU, network, memory or energy, it is done that way, even when
  it is harder to write.
- **Every choice backed by measurements:** every improvement is measured before and after with the performance report.

## Real measurements

Test on a single PC (2 cores), 3 bees, 20,000 texts, cleaning + statistics + embedding operations.

| Metric | Before | With the reused sandbox |
| --- | ---: | ---: |
| Sandbox start-up per cell | 62 ms (60% of active time) | **2.1 ms** (7%) |
| Module preparation per cell | 1.6 ms | 0.3 ms (already compiled) |
| Texts per second per bee (active time) | ~3,400 | **~12,400** |
| Job duration | 16 s | 12 s |
| Useful work (computation / active time) | 0.7% | 1.8% |
| Bottleneck | Sandbox start-up | **Result delivery (58%)** |

**What they show:**
- The V1 operations are so light (about 1 ms of computation per cell) that any network cost outweighs the
  computation: **for these operations a single PC is faster than the network**. This is expected: the network pays
  off with heavy jobs.
- Embedding results going out are about **7 times** the incoming data (32-bit vectors in base64).
- Verification costs between 9% and 15% of the computation.

## Security to complete before testing over the internet

| # | What | Why |
| --- | --- | --- |
| 1 | **Invitation-only sign-up** during testing (a code given by the organiser) | Today anyone who finds the address can sign up and create as many bees as they like; invitations eliminate multiple accounts and automated sign-ups |
| 2 | **HTTPS on the server** with Caddy and Let's Encrypt (both free) | Without it, passwords and tokens travel in plain text. The bee already refuses `http://` servers |
| 3 | **Module signing key off the server** (signing done on a separate PC; the server only holds the public key) | Today, anyone who took over the server could sign their own modules (they would remain confined to the sandbox, but it is one defence fewer) |
| 4 | **Texts encrypted in the central memory and the search index as well** (search over keyed word hashes) | Today `items` and `items_fts` are stored in plain text in the database: anyone copying the file could read the texts |
| 5 | **Verification assigned across different users and networks** | Today verification goes to another user, but two accounts belonging to the same person on the same network could confirm each other |
| 6 | **Hardened server**: firewall, unprivileged service user, encrypted backups, logs without user data, dependencies pinned by hash | Limits the damage if something goes wrong |
| 7 | **Stronger password hashing** (PBKDF2 from 200,000 to 600,000 iterations, or Argon2) | The values recommended today for stored passwords |
| 8 | **Participant rules** accepted at sign-up | Based on `docs/PARTICIPANTS.md`: non-sensitive data, what is collected, how it is deleted |
| 9 | Interface for **appealing penalties** | The evidence log already exists in the `penalties` table |
| 10 | Rate limiting shared across several servers | Today it lives in memory, which suits a single server |

**A limit inherent to the design:** the PC processing a cell has to see the data in plain text while processing it.
Anyone who modifies their own client could read it. Sensitive data will require a hardware-protected enclave with
attestation (V2).

## Efficiency: next steps, ordered by measured gain

| # | What | Expected gain |
| --- | --- | --- |
| 1 | **Embeddings sent as 16-bit** instead of 32-bit (the server already stores them as 16-bit) | Half the outgoing data for embeddings |
| 2 | **Binary protocol without base64**, compressed | A third less on every packet and result |
| 3 | **Key delivered with the packet**, encrypted for that bee only | One network round trip fewer per cell (about 12% of active time today) |
| 4 | **Packets sized to each bee** (30–60 s of computation based on its measured speed), and only as many as it can finish in time | Fixed per-cell costs become negligible; no queueing on the bee |
| 5 | **No polling**: bees and panel are notified by the server | Less traffic and less server CPU when the network is idle |
| 6 | **Spot-check verification inside a packet** instead of redoing the whole packet | Verification cost down from 9–15% to a few points |
| 7 | **Batched server writes** instead of one transaction per result | Less database load with many bees |
| 8 | **Zero-waste rule:** below a measured threshold, the server does the job itself | No distribution when it does not pay off |
| 9 | Preferring the most stable bees when assigning work | Fewer cells lost and redone |

## To prove with the benchmark (before expanding)

The same job on **1 PC** and on **2, 5 and 10 bees**, including bees dropping out and a cheating bee, with an
automatically generated document: speed-up, per-bee efficiency, share of fixed costs, verification cost, energy, and
the **break-even point** (the job size above which distribution pays off).
It has to be run with a heavy operation (neural embeddings): with today's operations the outcome is already known
(it does not pay off).

## Deviations from the specification (and why)

| Specification | This version | Reason | Next step |
| --- | --- | --- | --- |
| Node client in Rust | Client in Node.js | Rust libraries could not be downloaded in the development environment; Node has WebAssembly and cryptography built in, with zero dependencies | Port to Rust for a harder-to-tamper client and a single executable |
| Neural embeddings (multilingual-e5-small) | Feature-hashing embeddings (256 dimensions) | No model downloads were possible during development | New `embed_onnx`-style module with a quantised model; same interface, new operation version |
| PostgreSQL + pgvector | SQLite (WAL) + in-memory vector search with numpy + FTS5 | Zero installation, suitable for V1 | Migrate to PostgreSQL + pgvector (`halfvec`) beyond roughly 1 million texts or with several servers |
| MessagePack | JSON (gzip-compressed by the server) | No external libraries | Binary format (see Efficiency, item 2) |
| Web panel in Next.js | Plain HTML + JavaScript without libraries, served by the server | No build step and no Node on the server | Next.js if the panel grows a lot |

## Not available yet

- Entity and relation extraction (knowledge map) and an automatic topic catalogue.
- Detection of personal names during personal-data scrubbing (needs an AI model).
- CPU temperature in the node's status lights (not readable consistently across systems).
- Automatic updates and signing of the node client.
- V2: images, sensitivity levels with hardware enclaves and attestation, payments.

## Calibration with real data

- Work-unit value (`bytes_per_unit` in `orchestrator/hive_server/modules.py`): to be recalibrated with the benchmark.
- Per-core power draw in the node panel (default 10 W) and electricity price (€0.30/kWh): adjustable by the user.
- Verification probabilities and daily caps, based on the network's real behaviour.

## Done

**October 2026**
- Performance metrics for every cell (bee and server), stored in the same transaction as the result; adaptive time
  remaining; "Performance" panel; `.md` report and raw `.csv` data; bee report.
- Sandbox in a separate process, stopped by the operating system when time runs out (previously a module asking for
  endless memory left a thread running that kept consuming CPU); modules without a memory limit or with imported
  functions are rejected; modules rebuilt with a 256 MB limit (identical results).
- Sandbox reused across cells with the module already compiled (start-up from 62 ms to 2 ms per cell).
- The bee only accepts HTTPS servers (HTTP only towards the same PC or with `HIVENET_ALLOW_INSECURE=1`).
- Server dependencies pinned to minimums without known vulnerabilities (starlette ≥ 1.1.0, cryptography ≥ 49.0.0);
  minimum Node 22.23.2 / 24.18.1 / 26.5.1.
- New tests: sandbox against hostile modules, HTTPS only, Node version, bee metrics, network metrics and reports,
  access to reports, forged metrics discarded, dependencies. Test data stays inside the project (`.test-runs`) and is
  deleted afterwards.
- The whole project translated into English (code, interfaces, messages, tests, documentation).

**After the first review**
- Unlinking a bee: ownership is checked first, before anything is touched.
- Disputes: the original provided must have the same hash (SHA-256) as the one submitted with the job.
- Search: only shows texts from the user's own jobs.
- Request size limit enforced before and during the read; rate limits on sign-in, the bee protocol and the panel.

## Not to forget: the full vision (future versions)

- [ ] **Training on demand**: every user can request training of their own AI, images included (V2 images, V3 distributed fine-tuning).
- [ ] **The server as a "supreme AI"**: the central memory feeds models trained by the network, not just an archive of results (V2 chatbot with memory, V3–V4 in-house models).
- [ ] **Paying users**: those who do not lend computing power pay, and get priority and data according to what they pay.
- [ ] **Assignment to the most stable nodes**: work goes preferably to the most reliable bees, not simply the first free one.
- [ ] **Use of the GPU** on nodes that have one.
- [ ] **Sensitive data** handled only by nodes with a hardware enclave and attestation.
- [ ] **Results delivered in the format** the user asks for (today JSONL only).
