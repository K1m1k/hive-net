# Testing guide

This guide explains **how** to test HiveNet, **what** should happen and **why** each test matters.
Go step by step: automated tests first, then a single PC, then a home network. Tests with people connecting
over the internet are not possible yet (see the end of this guide).

## Project layout

| Folder | Contents |
| --- | --- |
| `orchestrator/` | The central server: splits jobs, distributes cells, reassembles, verifies, central memory, honey, metrics, web panel |
| `bee-node/` | The program to install on every PC that lends computing power, with its local panel |
| `module-src/` | Sources of the WebAssembly computation modules (already compiled into `orchestrator/modules/`) |
| `scripts/` | Installation, server start-up, full demo |
| `examples/` | 2,000 sample reviews (with duplicates, capitals, stray spaces and a few pieces of personal data to remove) |
| `docs/` | `ROADMAP.md` (what is missing), `PARTICIPANTS.md` (for people lending their PC) |

## 0. Before anything else

From the project root, once:

```sh
sh scripts/install-server.sh
node -v
```

**Why:** this creates the isolated Python environment `.venv` with package versions free of known vulnerabilities,
without touching the system or other projects. Node must be at least **22.23.2**, **24.18.1** or **26.5.1**
(July 2026 security fixes); the automated tests check this.

## 1. Automated tests

```sh
cd bee-node && npm test && cd ..
cd orchestrator && ../.venv/bin/python -m unittest discover -s tests -v && cd ..
```

**Expected result:** `# fail 0` for the bee (about 30 tests, a few seconds) and `OK` for the network
(10 tests, about 2 minutes).

**Why:** automated tests check in a few minutes what would take hours by hand, and above all the cases that are
**hard to reproduce by hand**: a node dying halfway, a cheating node, duplicate deliveries, hostile modules,
abuse attempts. Run them **after every change** and before every test session with other people.

| Test group | What it guarantees |
| --- | --- |
| Modules and cryptography | Computations are correct, modules cannot ask the system for anything, data travels encrypted |
| Sandbox against hostile modules | Participants' PCs are protected even from a faulty module: no processes left running, no unbounded memory |
| HTTPS only | The bee's token never travels in plain text over the internet |
| Node version | Bees run on a Node.js release without known vulnerabilities |
| Bee metrics | The figures in the bee's panel are computed correctly |
| Node killed halfway | No data lost, result identical to a single PC |
| Duplicate and expired deliveries | No honey paid twice |
| Cheating node | Cheaters are caught and penalised, honest bees are not |
| Spot check | An error reported by the requester is confirmed and corrected |
| Abuse | Nobody can touch other people's bees, jobs or texts; huge requests and repeated logins are refused |
| Network metrics | Every cell has its metric, time remaining works, reports are complete and only the job's owner can see them |
| Dependencies | Neither the installed packages nor `requirements.txt` allow vulnerable versions |

If a test fails, **stop there**: read the message (it says what is wrong and often what to do).
Test data goes into the project's `.test-runs` folder and is deleted automatically; to inspect it after a failure,
prefix the command with `HIVENET_KEEP_TEST_RUNS=1`.

## 2. Full demo on a single computer

```sh
.venv/bin/python scripts/full_demo.py --sample
```

This starts the orchestrator, 2 test bees and a sample job. Then open:

- **Web panel:** `http://localhost:8000`, user `demo`, password `demo-password`
- **Bee panels:** `http://127.0.0.1:7771` and `http://127.0.0.1:7772`

Use `--bees 3` (up to 5) to start more bees. Ctrl+C stops everything. To start from scratch, delete the `demo-data` folder.

**Why:** the automated tests check the logic; here you check what a person actually sees: panels, buttons,
messages, reports.

| Check | What to do | What should happen | Why |
| --- | --- | --- | --- |
| Sign-up | Create a new user from the web panel | The 100 welcome honey appear | The honey ledger starts correctly |
| Personal data | In "Submit a job", upload `examples/sample-reviews.txt` | Before sending, the panel shows how much personal data was removed | Scrubbing happens **in the browser**, before data leaves the PC |
| Progress and time remaining | Watch the job card while it runs | A growing percentage, a **time remaining** that settles after a few seconds, bees working, cells/min, texts/s, KB/s | Whoever submits a job needs to know how long to wait, like a download |
| Performance | Click **Performance** on the card | Tiles, phase bar, bottleneck with advice, queue waits | Shows where time is lost: the basis for every improvement |
| Report | Click **Download report** and **Raw data (CSV)** | A readable `.md` file and a `.csv` with one row per cell | These are the documents for evaluating the project with real figures |
| Results | Download the results | Cleaned texts, duplicates flagged, personal data replaced | The job does what it promises |
| Spot check | Click **Spot check** | "20 of 20 checked are correct" | The requester can verify on their own that the network made no mistakes |
| Reuse | Submit the same file again | It costs much less and shows "reused existing results" | Zero waste: a computation already done is never redone |
| Search | In "Search", try "noisy washing machine" | Relevant texts, from your own jobs only | The central memory is useful and private |
| Bee panel | Open `http://127.0.0.1:7771` | "How your bee is working" card and downloadable bee report; pause within 2 seconds, intensity, schedule, electricity cost | Whoever lends a PC always sees what the bee does and what it costs, and can stop it |
| Bee switched off | During a large job, stop one bee (end its process) | The job still finishes with the others | The network copes with PCs going away |
| Group of friends | Create a group and join it from another user with the code | Leaderboard visible only within the group | The game works without exposing anyone |
| Badges and level | Sign in as `owner1` | Honey, level and badges updated | Credits reach whoever did the work |

### How to read the performance report

| Item | Meaning | Good value |
| --- | --- | --- |
| **Useful work** | Share of the cells' active time spent on actual computation; the rest is the cost of distribution (network, encryption, start-up) | As high as possible; below 20% distribution does not pay off |
| **Bottleneck** | The overhead phase that weighs most, with advice on what to do | "Computation" means the overhead is already small |
| **Queue waits** | How long cells wait for a free bee or sit queued on a bee | Not a cost: they show whether more bees would help |
| **Estimated speed-up** | Pure computation time a single PC would have needed, divided by the actual duration | Above 1 the network is faster. It is an estimate: the real proof is the benchmark (see ROADMAP) |
| **Bee utilisation** | How much of the bees' available time went into computation | As high as possible |
| **Verification cost** | Computation spent re-checking the bees' results | Low, but never zero: it is the safeguard against cheating |
| **KB per text** | Data exchanged for every text processed | As low as possible |

**Today**, with the lightweight V1 operations, useful work stays below 5%: the report says so clearly and this is
expected. Its purpose is precisely to measure every improvement before and after.

## 3. Tests on a home network, with trusted people

**Before inviting anyone:** give them `docs/PARTICIPANTS.md` and run the automated tests (step 1).

1. On the PC acting as server: `sh scripts/start-server.sh` and note the PC's address on the network (for example `192.168.1.10`).
2. On every PC to connect, copy the `bee-node` folder (it needs an up-to-date Node.js, see step 0), then:
   ```sh
   HIVENET_ALLOW_INSECURE=1 node hivenet-bee.js setup --server http://192.168.1.10:8000 --user user_name
   HIVENET_ALLOW_INSECURE=1 node hivenet-bee.js start
   ```
3. On every PC, open `http://127.0.0.1:7777` and choose hours and intensity.

**Why `HIVENET_ALLOW_INSECURE=1`:** the bee refuses `http://` servers (its token would travel in plain text). On a
home network without HTTPS this has to be allowed **explicitly**, and the bee prints a warning. Never use it over
the internet.

**What to collect from every test:** the job report (`.md`), the raw data (`.csv`) and every bee's report (from its
panel). With different PCs you can see how network speed, computation and bottlenecks change.

### Codespaces

A Codespace with port 8000 made public gives you an **https** address and works as a test server. GitHub's free plan
for personal accounts includes a limited number of core-hours per month (beyond that you pay), and a Codespace shuts
down when idle: an always-on network needs a stable server.

## 4. Tests with people over the internet: not yet

Still missing, in this order (details in `docs/ROADMAP.md`):
1. **HTTPS on the server** (Caddy, free): without it, passwords and tokens would travel in plain text.
2. **Invitation-only sign-up:** today anyone who finds the address can sign up and create as many bees as they like.
3. **Module signing key off the server:** today, anyone who took over the server could sign their own modules
   (they would still be confined to the sandbox).
4. **Rules for participants**, signed or accepted: which data is used, what is collected, how it is deleted.
