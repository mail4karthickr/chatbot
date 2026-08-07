# Design Doc: Ingestion-to-Retrieval Pipeline Evaluation (DeepEval)

> **AMENDMENT (2026-08-05, user decision — supersedes the fingerprint design
> below):** the pipeline-fingerprint comparison (D5, §6.2 fingerprint inputs,
> `--reuse`/`--force-reuse`/`--rebuild` modes, and the T0 staleness check) was
> judged not worth its complexity and has been **removed**. `seed.py` now
> **always** performs the full atomic rebuild (quiesce → reset → clear prefix →
> upload → verify → ingest → poll → verify → manifest), preceded by an
> interactive confirmation stating what will be erased and the time/cost;
> `--yes` skips the prompt for scripted use. Consequences accepted with the
> decision: every seed costs ~3–5 min + OpenAI cents, and staleness between
> seeds is the developer's responsibility — after changing service code,
> dependencies, or fixtures, re-run `seed.py` before trusting scores (the suite
> no longer detects a stale corpus at test time; original review finding P0-1
> is now mitigated by process, not machinery). The 9-step rebuild protocol,
> seed lock, service-venv probe (queue/collection identity), and all isolation
> guarantees are unchanged. A repo-root orchestrator **`run-evals.sh`** is the
> primary entry point: starts the test stack if not already running (waits for
> health), asks whether to ingest (yes → `seed.py --yes`; no → keep existing
> corpus), then runs pytest (`-m "not judged"` by default; extra args pass
> through). It supersedes §9's step-by-step commands for daily use.

> **Status:** v5.1 — **APPROVED FOR IMPLEMENTATION** (fifth review, 2026-08-01).
> All findings from five review rounds are closed (see Appendix); the fifth review's
> two corrections (queue resolution via a service-venv helper subprocess, never a
> service import — §6.2; D5/status aligned with the full 7-input fingerprint) are
> incorporated.
> §6.2 is the authoritative rebuild protocol; §6.5 defines the binding T2 pass criteria.
> Reference design for evaluating `apps/ingestion-service`. Scoped so any developer
> can understand, run, and extend the eval system without prior context.
> Naming note (review P1-1): this suite evaluates the **ingestion→retrieval
> pipeline end-to-end**; it does not claim to isolate ingestion quality. The
> invariants tier (§6.4 T1) exists precisely to localize failures.

---

## 1. Context & Motivation

The ingestion pipeline converts PDFs into a searchable hybrid index:

```
S3 (docs/) ─► POST /ingest ─► diff vs ledger ─► RabbitMQ ─► worker ─► ingest_document()
                              (s3-sync-service,                       ├─ Docling parse
                               Postgres)                              ├─ image captioning (gpt-5-mini)
                                                                      ├─ chunking
                                                                      ├─ embeddings (dense + BM25 sparse)
                                                                      └─ Qdrant upsert + doc_summary
```

There is no systematic way to answer: *"is each type of PDF (prose, tables, charts)
ingested well enough to be retrieved correctly?"* Regressions in parsing, chunking,
or captioning are discovered only by manually querying the UI.

This design adds an **evaluation suite** run as a dev tool (pytest CLI, per the
2026-07-31 decision — not a service). It exercises the **real user path** end to
end (S3 upload → `/ingest` → broker → worker → Qdrant → `/retrieve`) against a
fully isolated local test stack.

## 2. Goals / Non-goals

| Goals (v1) | Non-goals (v1, explicitly deferred) |
|---|---|
| Detect ingestion/retrieval regressions per document type (text-heavy, table-heavy, chart/image-heavy) | Generation evals (`synthesize_answer` + Faithfulness/AnswerRelevancy) |
| **Prod-shaped**: identical code path as production, plumbing included | Benchmark corpus (3–5 docs/type; §10) — v1 ships the **smoke corpus** (3 docs) |
| **Hard isolation**: every backend is a separate local test instance; sanitized env + preflight checks; zero service-code changes | Scanned/OCR, multi-column, cross-page-table fixture types (§10) |
| **Regression-correct**: pipeline changes force re-ingestion (fingerprint, §6.2) — the suite never scores yesterday's index against today's code | CI integration; network-egress lockdown (§10) |
| **Reproducible**: pinned images/packages/models; committed fixtures; manifest + baseline with full run metadata | nDCG@k (needs multi-passage relevance annotations) |
| Layered metrics: smoke → invariants → deterministic retrieval → LLM-judged | Hard pass/fail gates on judged metrics (report-first until variance is measured, §6.6) |

## 3. Key Architecture Decisions

| # | Decision | Choice | Rationale |
|---|---|---|---|
| D1 | Test style | **Black-box E2E** through real HTTP endpoints; read-only Qdrant inspection allowed for invariants | Exercises the exact production path incl. broker + ledger. Invariant checks read the test index directly (qdrant-client) but never import service code. |
| D2 | Isolation | **Full parallel test infra, one compose file** (`docker-compose.test.yml`, repo root): MinIO, Postgres, RabbitMQ, Qdrant on non-conflicting ports, **pinned image versions** | Server-level isolation; one lifecycle. Pinning (review P1-6): exact tags/digests captured at implementation and recorded in every report. |
| D3 | Config mechanism | **Sanitized allowlisted environment** at service startup: the launcher explicitly sets EVERY backend endpoint + credential var (test values); only non-backend config (OPENAI key, model names) falls through to `.env` | Review P0-2. A partial override risks a typo'd var silently falling back to a cloud endpoint. Overriding the complete backend set (`QDRANT_URL`, `QDRANT_API_KEY`, `S3_*`, `RABBITMQ_URL`, `SYNC_URL`, `DATABASE_URL`) removes every fallback path to R2/Qdrant Cloud/dev infra. Preflight validation on top (§5.3). |
| D4 | Real deps, not mocks | MinIO/Postgres/RabbitMQ/Qdrant are the real software in containers. **OpenAI stays real** — captions/embeddings ARE the quality being measured | Backend idiom: run real dependencies; mock nothing you can run. |
| D5 | Rebuild semantics | `seed.py` keeps a **corpus manifest** with fixture hashes + a **pipeline fingerprint** covering code, dependencies AND configuration — the seven inputs of §6.2: (1) all `*.py` under the service (excl. `.venv`, `evals/`, `data/`), (2) non-Python config assets (`*.yaml/yml/json/toml`, prompt/template files), (3) the service `requirements.txt`, (4) the service venv's effective installed versions (`pip freeze`), (5) a normalized effective-settings snapshot (resolved model IDs, sorted-key JSON), (6) the service runtime (Python version + platform), (7) fixture hashes. Any change → automatic quiesce + purge + full re-ingest. Modes: `--reuse` / `--force-reuse` / `--rebuild` (§6.2) | Review P0-1 + second review #1 + third review #2. The service's content-hash skip means code changes don't trigger re-ingestion. Hashing *everything* closes the dependency- and config-change false-pass; occasional unnecessary rebuilds are the accepted cost of never missing a relevant input. |
| D6 | Completion detection | **Quiesce-reset-then-poll**: a rebuild first drains the broker (purge queue + wait for zero in-flight via the RabbitMQ management API on :15673), *then* `POST /reset` empties the index, ledger, and artifacts — only then is the corpus submitted. Polling `GET /documents` for all fixture doc_summaries then provably reflects the *current* run. Post-seed invariant checks verify chunks exist per doc. **`/reset` is an existing endpoint** (`app.py:252`): recreates the Qdrant collection, resets the ledger (sync-service), sweeps `_artifacts/`, clears the parse cache — it does NOT touch RabbitMQ, which is exactly why the quiesce step exists (§6.2) | Review P0-3 + second review #2. A `/jobs` status endpoint would be cleaner but requires service changes — rejected for v1 (zero-code-change principle). The empty-index argument is sound only if no stale work can repopulate the index; quiescing the broker before reset guarantees that. |
| D7 | Corpus staging | v1 = **smoke corpus** (3 docs, ~15–20 goldens) for fast dev validation. **Benchmark corpus** (3–5 docs/type, run pre-merge) is phase 2 | Review P1-2. Three docs can't support benchmark claims; v1 is honest about being a smoke suite. |
| D8 | Test framework | pytest + DeepEval plugin; eval venv = `deepeval`, `pytest`, `httpx`, `qdrant-client` — **exact versions pinned** | Black-box → tiny venv, no service-dep conflicts. qdrant-client is read-only, for invariants. |
| D9 | Judge | OpenAI via existing key; judged metrics are **report-first** (no hard thresholds) until variance is calibrated over ≥3 repeated runs; DeepEval telemetry disabled (`DEEPEVAL_TELEMETRY_OPT_OUT=1`) | Review P1-7. |

## 4. System Overview

### 4.1 Two stacks, side by side

```
        DEV / "PROD" STACK  (start-all.sh)      TEST STACK  (start-test-stack.sh)
        ──────────────────────────────────      ─────────────────────────────────────
S3      Cloudflare R2 (cloud)                   MinIO container            :9000 (console :9001)
Qdrant  Qdrant Cloud                            qdrant-eval container      :6333
Ledger  postgres container         :5433        postgres-test container    :5434
Broker  rabbitmq container         :5672        rabbitmq-test container    :5673 (mgmt :15673)
Sync    s3-sync-service            :8003        s3-sync-service (test env) :8013
API     ingestion-service          :8000        ingestion-service (test env) :8010
Worker  N workers                               1 worker (test env)
OpenAI  real API                                real API (deliberately — D4)
```

Both stacks run simultaneously: every port and every data store differs. The
Python services are the same code started twice with different environments.

### 4.2 Eval-run sequence

```
Developer        start-test-stack.sh       seed.py                    Test services + infra
    │                    │                    │                              │
    ├─ ./start-test-stack.sh                  │                              │
    │                    ├─ compose up (pinned images) + health waits        │
    │                    ├─ build sanitized env (full backend override set)  │
    │                    ├─ PREFLIGHT: every endpoint localhost, ports 5434/5673/6333/9000,
    │                    │             bucket has -eval suffix — refuse to start otherwise
    │                    ├─ start sync :8013, API :8010, worker ────────────►│
    │                    │                    │                              │
    ├─ python seed.py                         │                              │
    │                    │                    ├─ acquire exclusive .seed.lock (2nd seed exits)
    │                    │                    ├─ hash fixtures + compute pipeline fingerprint
    │                    │                    ├─ compare with corpus manifest
    │                    │                    │    unchanged → REUSE (done in seconds)
    │                    │                    │    changed / --rebuild ↓  (9-step protocol, §6.2)
    │                    │                    ├─ 1 QUIESCE broker (mgmt API :15673): purge queue,
    │                    │                    │    wait ready==0 AND unacked==0 (in-flight drained)
    │                    │                    ├─ 2 POST /reset  (test index+ledger now EMPTY —
    │                    │                    │    and provably stays empty: no stale work exists)
    │                    │                    ├─ 3 CLEAR docs/eval-corpus/ prefix in MinIO
    │                    │                    │    (DELETE /s3/folder) + verify prefix empty
    │                    │                    ├─ 4 POST /s3/upload  fixtures → docs/eval-corpus/
    │                    │                    ├─ 5 VERIFY bucket holds exactly the expected keys
    │                    │                    ├─ 6 POST /ingest  (202; enqueued == fixture count)
    │                    │                    ├─ 7 poll GET /documents until all fixture doc_ids
    │                    │                    │    present (sound: index started empty) — 15 min cap
    │                    │                    ├─ 8 verify invariants (≥1 text chunk/doc, summary, ledger)
    │                    │                    └─ 9 write corpus manifest, release lock
    ├─ pytest -m smoke|deterministic|judged   │                              │
    │                      per golden: POST /retrieve ──────────────────────►│
    │                      T1 invariants · T2 doc-hit/MRR + evidence recall  │
    │                      T3 DeepEval metrics ──► OpenAI judge              │
    │                      write results/<run>.json (full metadata §6.7)     │
    └─ ./stop-test-stack.sh   (volumes persist; `down -v` = factory reset)
```

### 4.3 External calls & cost

| Call | When | Cost profile |
|---|---|---|
| Docling parse | Every **rebuild** (not every run) | ~10–60 s/doc (measured) |
| Captions + embeddings (OpenAI) | Every rebuild | Cents per corpus |
| Query embeddings (OpenAI) | Every eval run | Negligible |
| DeepEval judge (OpenAI) | `judged` runs only, ~2 calls/case | Cents per run |
| R2 / Qdrant Cloud / dev Postgres / dev RabbitMQ | **Never** (D3 + preflight) | — |

## 5. Test Infrastructure

### 5.1 `docker-compose.test.yml` (repo root, pinned)

| Service | Image (pin captured at implementation, recorded in reports) | Host port | Volume |
|---|---|---|---|
| `minio` | `minio/minio:<exact RELEASE tag>` | 9000, 9001 | `minio-test-data` |
| `postgres-test` | `postgres:16-alpine@<digest>` | **5434**→5432 | `postgres-test-data` |
| `rabbitmq-test` | `rabbitmq:3.13-management@<digest>` | **5673**→5672, 15673→15672 | `rabbitmq-test-data` |
| `qdrant-eval` | `qdrant/qdrant:v1.18.3` (verified working) | 6333 | `qdrant-eval-data` |

Same creds as dev for symmetry (`minioadmin/minioadmin`, `sync/sync`, `app/app`);
health checks mirror the dev compose files. The app's `ensure_bucket()` /
`init_db()` / `create_collection()` create everything at startup — no init containers.

### 5.2 Sanitized service environment (D3)

The launcher constructs the service environment from an **explicit allowlist** —
every backend-touching variable is set to its test value; nothing backend-related
can fall through to `.env`:

| Var | Test value | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://sync:sync@localhost:5434/sync` | s3-sync-service (psycopg3 scheme, matching dev `.env`) |
| `QDRANT_URL` | `http://localhost:6333` | |
| `QDRANT_API_KEY` | `` (explicitly empty) | prevents `.env` fallback |
| `S3_ENDPOINT` | `http://localhost:9000` | |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | `minioadmin` / `minioadmin` | prevents R2 creds reaching test procs |
| `S3_BUCKET` | `docs-eval` | `-eval` suffix asserted by preflight |
| `S3_REGION` | `us-east-1` | MinIO default |
| `RABBITMQ_URL` | `amqp://app:app@localhost:5673/` | |
| `SYNC_URL` | `http://localhost:8013` | |
| `EVAL_MODE` | `true` | marker for humans/logs; unused by services |

Deliberately **not** overridden (loads from each service's `.env`): `OPENAI_API_KEY`,
`EMBED_MODEL`, `CAPTION_MODEL`, `GENERATE_MODEL` — the real models are part of what's
measured; their names are recorded in every report. No secrets appear in committed files.

### 5.3 Preflight validation (launcher + re-checked by `seed.py`)

Before starting any service process, assert — and refuse to launch on failure:

- `QDRANT_URL`, `S3_ENDPOINT`, `SYNC_URL` resolve to `localhost`/`127.0.0.1`
- `DATABASE_URL` uses port **5434**; `RABBITMQ_URL` uses port **5673**
- `S3_BUCKET` ends with `-eval`
- All four containers are healthy; ports 8010/8013 are free
- ⚠️ Test services are launched via **direct uvicorn invocation** (`--port 8010`,
  no `--reload`) — never via `start-dev-server.sh`, which hardcodes dev ports
  *and kills whatever holds them* (it would take down the dev stack)

**Effective-settings validation (second review #3).** Checking the shell variables
the launcher exports is not enough — what matters is what each service *resolves*
through its own pydantic `Settings` class (aliases, `.env` fallback, empty-string
handling). So before starting each process, the launcher runs a tiny validator
**inside that service's own venv, with the sanitized env**:

```bash
env <sanitized-vars> apps/ingestion-service/.venv/bin/python \
    evals/validate_effective_settings.py --service ingestion
```

The validator imports the service's `config.get_settings()` and asserts the
*resolved* values: `qdrant_url`/`s3_endpoint`/`sync_url` are localhost,
`database_url` port 5434, `rabbitmq_url` port 5673, `s3_bucket` ends in `-eval`.
It exits non-zero (and the launcher refuses to start) on any mismatch. This also
empirically proves that empty-string env overrides (e.g. `QDRANT_API_KEY=""`)
behave as intended, instead of assuming pydantic semantics.

### 5.4 Lifecycle

`start-test-stack.sh` traps INT/TERM → kills service PIDs, leaves containers up
(fast re-runs). `stop-test-stack.sh` → `compose down` (volumes survive);
`down -v` is the full factory reset — §8 requires a from-scratch rebuild to work.

## 6. Eval Suite Design

### 6.1 Directory layout

```
plan/ingestion-eval-design.md         # this document
docker-compose.test.yml               # pinned test infra (repo root)
start-test-stack.sh / stop-test-stack.sh
apps/ingestion-service/evals/
├── .venv/                            # gitignored: deepeval, pytest, httpx, qdrant-client (pinned)
├── requirements.txt                  # exact versions
├── fixtures/                         # committed smoke corpus (6.3)
├── corpus-expectations.json          # committed per-doc invariant ranges (6.4 T1)
├── seed.py                           # modes, manifest, fingerprint (6.2); embeds the
│                                     #   service probe: a snippet run by the SERVICE
│                                     #   venv's interpreter that emits queue, collection,
│                                     #   model + runtime config as normalized JSON —
│                                     #   eval code never imports service modules
├── validate_effective_settings.py   # preflight: executed in each service's own venv,
│                                     #   asserts values resolved by the real Settings
│                                     #   class against the test env (5.3)
├── goldens.json                      # evidence-level test cases (6.5)
├── conftest.py                       # base-url, fail-fast, goldens loader, markers, telemetry opt-out
├── harness.py                        # metrics, results writer
├── test_smoke.py                     # T0 (6.4)
├── test_ingestion_invariants.py      # T1
├── test_retrieval.py                 # T2 + T3
├── results/                          # gitignored local runs
├── baselines/                        # COMMITTED: v1.json + schema.json (review P1-5)
├── .manifest.json                    # gitignored seeded-corpus state
└── README.md
```

### 6.2 `seed.py` — modes, manifest, fingerprint, atomic rebuild

**Pipeline fingerprint** (second review #1: must catch dependency changes, not
just code edits) = SHA-256 over, in a stable order:

```
1. Every *.py file under apps/ingestion-service/  (glob, NOT a hand-maintained
   list — excl. .venv/, evals/, data/; a stale list is how inputs get missed)
2. Every non-Python config asset under the service (*.yaml, *.yml, *.json,
   *.toml, prompt/template files; same exclusions) — none exist today, but the
   glob future-proofs against config being extracted out of code
3. apps/ingestion-service/requirements.txt
4. The service venv's effective installed versions (pip freeze output —
   catches transitive upgrades the requirements file doesn't pin)
5. A normalized effective-settings snapshot: pipeline-affecting values resolved
   through the service's Settings class (embed_model, caption_model,
   generate_model), serialized as sorted-key JSON
6. The service runtime: Python version + platform.system() + platform.machine()
   of the service venv (identical packages can behave differently across Python
   minors — fourth review #2)
7. SHA-256 of each fixture PDF
```

**Where pipeline config actually lives (third review #2 — established from code):**
in this codebase the caption/summary prompts are string constants in `ingest.py`,
chunking logic is code in `ingest.py`/`parser.py`, embedding dimension is the
`DIM`/`DENSE_DIM` constants, and the collection schema (vector names, IDF
modifier, payload indexes) is hardcoded in `vectordb.py`. All of that is Python
source → covered by item 1. The ONLY pipeline config sourced outside Python is
the model IDs from `.env` → covered by item 5. Item 2 exists so this claim stays
true automatically if config ever moves into files.

Over-triggering is accepted by design: an irrelevant `.py` edit costs one
unnecessary ~5-minute rebuild; a missed relevant input costs a false pass.

| Mode | Fingerprint matches | Fingerprint differs |
|---|---|---|
| *(default)* | reuse (seconds) | **rebuild** |
| `--reuse` | reuse | **fail, exit non-zero** — "pipeline changed; rerun default or --force-reuse" |
| `--force-reuse` | reuse | reuse anyway — prominent warning, **exit 0** (explicit opt-in to a stale corpus for retrieval-only iteration) |
| `--rebuild` | rebuild | rebuild |

(Second review #6: `--reuse` is now strict-and-failing; `--force-reuse` is the
explicit escape hatch — no mode both warns and blocks the workflow.)

**Atomic rebuild sequence** (second review #2 + third review #1 — `/reset`
touches neither RabbitMQ nor the source objects in S3, so both stale in-flight
work AND stale fixture objects must be cleared explicitly):

```
1. QUIESCE       — purge the ingestion queue (mgmt API :15673), then poll until
                   messages_ready == 0 AND messages_unacknowledged == 0.
                   Own timeout (10 min — an in-flight Docling job can be slow);
                   on timeout report ready/unacked counts, consumer count,
                   queue name + vhost, and the worker log tail.
2. RESET         — POST /reset on the test API (existing endpoint, app.py:252:
                   recreates Qdrant collection, resets ledger, sweeps _artifacts/,
                   clears parse cache)
3. CLEAR PREFIX  — DELETE /s3/folder?path=docs/eval-corpus (existing endpoint,
                   app.py:178) → verify via GET /s3/files that the prefix is
                   empty. Without this, a fixture renamed/removed from git would
                   linger in MinIO and be re-ingested as a stale distractor.
4. UPLOAD        — POST /s3/upload fixtures → docs/eval-corpus/<type>/
5. VERIFY UPLOAD — GET /s3/files: bucket contains exactly the expected fixture
                   keys under the prefix, no more, no fewer
6. INGEST        — POST /ingest (202; check `enqueued` count == fixture count)
7. POLL          — GET /documents until every fixture doc_id present (timeout
                   15 min; on timeout: dump worker log tail, purge queue, fail)
8. VERIFY        — per doc: ≥1 text chunk, exactly one doc_summary, ledger row exists
9. MANIFEST      — write .manifest.json (fingerprint + fixture hashes + timestamps)
```

Quiesce correctness rests on a **verified assumption**: the worker uses manual
acks and acknowledges only *after* the job fully completes or terminally fails
(`worker.py:119,129` — ack-on-success after `mark_ingested`, ack-on-failure to
drop poison messages). Therefore `unacked == 0` ⇒ no ingest is executing.

**Queue identity (fourth review #3, boundary fixed in fifth review #1):** the
quiesce/purge must target the exact queue the worker consumes — `seed.py`
resolves the queue name (along with the collection name, resolved model settings,
and service runtime) through a single probe subprocess executed in the
**ingestion-service virtual environment** (same pattern as
`validate_effective_settings.py`, §5.3): the service venv's interpreter reads
`broker.QUEUE`/`vectordb.COLLECTION` and prints normalized JSON, which `seed.py`
consumes. Eval code never imports service modules directly and never hardcodes
a copy of the queue name — preserving the black-box dependency boundary. The
default vhost of the test `RABBITMQ_URL` is used. Pre-ingestion assertion (between steps 5 and 6):
*queue exists AND consumer count == 1 (the one test worker) AND ready == 0 AND
unacked == 0* — catching both "purged the wrong queue" and "worker not running"
before any job is submitted. Queue name + vhost appear in preflight validation,
quiesce diagnostics, and run metadata.

**Concurrency guard** (third review): `seed.py` takes an exclusive lock file
(`evals/.seed.lock`, `flock`-style) for the whole sequence — a second concurrent
seed exits immediately with a clear message instead of interleaving
quiesce/reset/upload with the first.

Completion soundness: after step 1 no stale work exists, after step 2 the index
is empty, and after step 3+5 the bucket holds exactly the current fixtures — so
every `doc_summary` seen in step 7 (written **last** by `ingest_document` — the
service's own completion marker) belongs to the current run by construction.

### 6.3 Smoke corpus (v1) — parse-validated fixtures

| doc_type | File | Why | Parse check (Docling) |
|---|---|---|---|
| text-heavy | Bitcoin whitepaper (9 p.) | Prose-dominant, public, stable | 15 text + 8 image els, 11 s |
| table-heavy | Fed H.4.1 release (11 p.) | Dense financial tables | 17 text + 1 image, 47 s |
| chart-image | YOLO paper (10 p.) | Figures/charts with captions | 25 text + 5 images, 28 s |

doc_id = S3 key: `docs/eval-corpus/<type>/<file>.pdf`. This corpus supports
**smoke-level claims only** (D7); the benchmark corpus is phase 2 (§10).

### 6.4 The evaluation pyramid (review P1-1)

| Tier | File / marker | What it checks | Failure semantics |
|---|---|---|---|
| **T0 smoke** | `test_smoke.py`, `-m smoke` | Test API answers; corpus seeded; manifest fingerprint matches current code (else: "run seed.py") | Hard assert |
| **T1 ingestion invariants** | `test_ingestion_invariants.py`, `-m deterministic` | Per doc, against committed `corpus-expectations.json` ranges: text-chunk count, image-chunk count, captions non-empty, no empty/duplicate text chunks, page metadata present, exactly one `doc_summary`, dense+sparse vectors on every point | Hard assert — **localizes failures to ingestion** before any retrieval metric runs |
| **T2 retrieval (deterministic)** | `test_retrieval.py`, `-m deterministic` | Document Hit@5, Document MRR, **Evidence Recall@k** (6.5) | Hard assert |
| **T3 judged** | `test_retrieval.py`, `-m judged` | DeepEval ContextualRelevancy + ContextualRecall | **Report-first**: scores recorded, no hard thresholds until variance is calibrated (≥3 repeated runs; then thresholds set from observed spread) |

T1 reads the test Qdrant directly (read-only, qdrant-client) — the one sanctioned
deviation from pure black-box, because these checks are exactly what localizes
"ingestion broke" vs "retrieval broke" (the review's central P1-1 point).

### 6.5 Goldens — evidence-level (review P1-3/P1-4)

```json
{
  "id": "table-001",
  "query": "What is the total of the Federal Reserve's balance sheet?",
  "expected_doc_ids": ["docs/eval-corpus/table-heavy/fed-h41-balance-sheet.pdf"],
  "expected_output": "<hand-written reference answer>",
  "expected_evidence": [
    {"doc_id": "docs/eval-corpus/table-heavy/fed-h41-balance-sheet.pdf",
     "page": 2, "modality": "table",
     "required_anchors": ["<identifying label>", "<answer-bearing value>"],
     "alternative_anchor_groups": [["<variant label>", "<variant value>"]],
     "fact": "<the specific fact>"}
  ],
  "doc_type": "table-heavy",
  "difficulty": "single-lookup | multi-cell-lookup | visual-only"
}
```

Multi-document note (fourth review #4): in v1 every golden has **exactly one**
expected document; each evidence item nonetheless carries its own `doc_id`,
future-proofing the schema for cross-document questions without a migration.

Metric definitions (explicit, per review):
- **Document Hit@5** — any chunk of an expected doc in top 5.
- **Document MRR** — reciprocal rank of the *first* chunk belonging to an expected doc.
- **Evidence Recall@k** — fraction of `expected_evidence` items *matched*. An
  evidence item matches a retrieved chunk only when **all three** hold
  (second review #4 — page match alone is a filter, never sufficient;
  fourth review #1 — anchors are conjunctive where it matters):
  1. chunk `doc_id` == the evidence item's `doc_id`, **and**
  2. chunk `page` == evidence `page`, **and**
  3. **all** `required_anchors` appear in the normalized chunk text
     (lowercased, whitespace/punctuation collapsed), **or** all anchors of
     **one** `alternative_anchor_groups` entry appear.
  Rationale: a disjunctive anchor list would let `["total assets", "7,568,432"]`
  pass on a chunk containing only the label without the value. Authoring rule:
  every text/table evidence item's `required_anchors` must pair the identifying
  label with the answer-bearing value. `alternative_anchor_groups` exists mainly
  for `visual-only` items, where generated caption wording varies — each group
  is one complete accepted phrasing.

**T2 pass criteria — explicit** (second review #5):
- Per golden case (hard assert): **Document Hit@5 == 1 AND Evidence Recall@5 == 1.0**
  (every evidence item for that case retrieved in the top 5).
- **Document MRR** is an aggregate: reported per doc_type; the delta vs. the
  committed baseline is **report-only in v1** (third review) — never a gate.
  An MRR regression threshold is introduced only after run-to-run variation is
  known from repeated baseline runs.

Authoring rules: written after corpus inspection; ~5–7 cases/doc; queries never
quote the document verbatim; **≥1 `visual-only` golden** whose answer exists only
in chart pixels — not in body text and not in the printed figure caption
(`caption_hint` is stored as payload, never indexed; the *generated* pixels-only
caption is what's embedded — so this golden passes only if VLM captioning works;
review P1-4); **≥1 table golden requiring row×column association** whose value
does not appear in surrounding prose.

### 6.6 Judged-metric policy (review P1-7)

Judge = OpenAI via existing key; model ID pinned in conftest and recorded per run.
`DEEPEVAL_TELEMETRY_OPT_OUT=1` set in conftest. Bounded retries on API errors;
an API failure marks the case *errored*, never *failed* (model outage ≠ quality
regression). Thresholds become hard gates only after the calibration step
(implementation step 11) measures score spread across ≥3 identical runs.

### 6.7 Results & baselines (review P1-5)

- `results/<timestamp>.json` — gitignored; every run.
- `baselines/v1.json` — **committed** after calibration; the reference for comparisons.
- Every report records: git SHA, pipeline fingerprint, fixture SHA-256s, docker
  image tags/digests, **eval-venv AND service-venv package versions (full
  `pip freeze` lists)**, service runtime (Python version, OS, architecture,
  docling version), queue name + vhost, model IDs (embed/caption/judge),
  `top_n`, per-case retrieved chunk IDs + scores + rank, per-case metric scores,
  durations, report `schema_version`. This is what makes a score movement
  attributable (code? model? config? runtime? corpus?) and reproducible across
  developers and machines (fourth review #2).

## 7. Implementation Steps

| # | Step | Deliverable | Done when |
|---|---|---|---|
| 1 | Test infra | `docker-compose.test.yml` (pinned) | Four containers healthy via one `up`; tags/digests recorded |
| 2 | Launcher + preflight | `start-test-stack.sh` / `stop-test-stack.sh` + `validate_effective_settings.py` (runs in the service venv, §5.3) | Both stacks side by side; preflight rejects a deliberately-wrong endpoint; effective-settings validation passes in the service venv; API answers on :8010 |
| 3 | Fixtures | 3 PDFs committed under `evals/fixtures/` | Present (parse already validated) |
| 4 | Eval venv | pinned `requirements.txt`; venv rebuilt | `pytest -m smoke` collects; versions recorded |
| 5 | `seed.py` | modes + manifest + fingerprint + quiesce + invariant verification + embedded service probe (queue/collection/settings/runtime as JSON via the service venv, §6.2) | Rebuild populates empty index; **re-run reuses in seconds**; editing any service `.py` (even whitespace) triggers rebuild; `pip install -U <dep>` in service venv triggers rebuild; `--reuse` after either exits non-zero; `--force-reuse` warns + exits 0 |
| 6 | Corpus inspection | chunk counts, captions, samples from test Qdrant | **Checkpoint with user**; findings become `corpus-expectations.json` |
| 7 | `corpus-expectations.json` + T1 | invariant tests | T1 green against seeded corpus |
| 8 | Goldens | `goldens.json` (evidence-level, incl. visual-only + table-association cases) | Reviewed against actual corpus content |
| 9 | T2 + harness + T3 | `test_retrieval.py`, `harness.py`, results writer | Full suite runs; deliberately-wrong golden fails T2 (proves detection), then corrected |
| 10 | README | `evals/README.md` | A new developer can run everything from it alone |
| 11 | Calibration | ≥3 identical judged runs | Variance measured; T3 thresholds proposed from spread |
| 12 | Baseline | `baselines/v1.json` committed | Full metadata present; user sign-off |

## 8. Verification

- **Isolation**: preflight rejects wrong endpoints; a full eval run changes nothing
  in Qdrant Cloud (point count), R2, dev Postgres, dev RabbitMQ; dev stack keeps
  serving throughout.
- **Regression correctness (the P0-1 test)**: touch any service `.py` → `seed.py`
  rebuilds; upgrade a service dependency → rebuilds; `--reuse` after either exits
  non-zero; `--force-reuse` proceeds with a warning.
- **Completion soundness (the P0-3 test)**: kill the worker mid-rebuild → seed
  times out and fails loudly (never reports ready). Leave a poison message in the
  queue, run a rebuild → quiesce purges it; the reset index stays clean.
- **Effective-settings validation**: run the validator with one deliberately
  wrong var (e.g. prod-shaped `QDRANT_URL`) → launcher refuses to start.
- **Idempotency**: unchanged code + fixtures → seed reuses in seconds.
- **Stale-fixture cleanup (third review #1 test)**: delete a fixture from the
  repo → rebuild → its object is gone from MinIO, its chunks/summary absent from
  the index, and the corpus contains exactly the remaining fixtures.
- **Concurrency**: start a second `seed.py` while one runs → it exits immediately
  citing the lock.
- **Detection**: one deliberately-wrong golden fails before baseline is recorded.
- **Clean recovery**: `down -v` + fresh start rebuilds everything from committed files.

## 9. How to Run (target developer experience)

Marker semantics (second review #7): markers **select**, they don't accumulate —
`-m judged` alone would run *only* T3. The commands below spell out the exact
selection expressions; the README documents these as the only supported invocations.

```bash
./start-test-stack.sh                       # pinned infra + test services (leave running)
cd apps/ingestion-service/evals
.venv/bin/python seed.py                    # fingerprint-aware: reuse or rebuild
.venv/bin/python seed.py --rebuild          # force clean re-ingest (regression check)
.venv/bin/python seed.py --force-reuse      # knowingly stale corpus, retrieval-only iteration

.venv/bin/python -m pytest -m smoke                    # T0 only — seconds, is everything up?
.venv/bin/python -m pytest -m "not judged"             # T0+T1+T2 — free, no LLM judge
.venv/bin/python -m pytest                             # everything: T0+T1+T2+T3 (OpenAI cost)
./stop-test-stack.sh
```

## 10. Future Work (explicitly deferred)

- **Benchmark corpus** (phase 2): 3–5 docs/type incl. multi-column prose,
  cross-page/nested tables, visual-only charts, forms, long docs, scanned/OCR;
  run pre-merge rather than per-edit.
- Generation evals; nDCG@k (needs multi-passage annotations).
- `/jobs/{id}` status endpoint in the service (would replace reset-then-poll with
  true per-job tracking; a service change, hence deferred).
- CI integration; network-egress restriction for test processes.
- Run-over-run trend reporting; containerizing the Python services.

---

## Appendix: review disposition (2026-08-01)

| Finding | Disposition |
|---|---|
| P0-1 cached ingestion hides regressions | **Adopted** — fingerprint + manifest + rebuild-by-default-on-change (D5, §6.2). *(The "simplified source-file hash" described here was superseded in v4/v5: the final fingerprint covers sources, config assets, requirements, installed versions, settings snapshot, service runtime and fixtures — see §6.2.)* |
| P0-2 isolation not guaranteed | **Adopted** — complete backend-var allowlist + preflight (D3, §5.2–5.3). `env -i` not used verbatim: pydantic loads `.env` from disk regardless, so the real fix is overriding *every* backend var explicitly; OPENAI intentionally still flows from `.env`. Egress lockdown deferred (§10). |
| P0-3 stale doc_summary | **Adopted via reset-then-poll** (D6) — empty-index start makes the marker provably current; `/jobs` endpoint rejected for v1 (service change). Post-seed artifact checks added. |
| P1-1 name + pyramid + invariants | **Adopted** — renamed; T0–T3 pyramid; T1 invariant tier with committed expectations (§6.4). |
| P1-2 corpus too small | **Adopted as staging** — v1 explicitly a smoke corpus; benchmark corpus is phase 2 (D7, §10). |
| P1-3 evidence-level goldens | **Adopted** — schema extended; Evidence Recall@k defined; MRR defined explicitly (§6.5). nDCG deferred. |
| P1-4 visual golden may test parser, not captioner | **Adopted** — `visual-only` difficulty class required; note: repo already indexes only *generated* pixels-only captions (printed `caption_hint` is payload-only), which is what makes this golden meaningful (§6.5). |
| P1-5 baseline vs gitignored results | **Adopted** — `baselines/` committed, `results/` gitignored, full report metadata (§6.7). |
| P1-6 pin everything | **Adopted** — images, packages, models pinned and recorded (§5.1, D8). |
| P1-7 execution modes, judge variance | **Adopted** — smoke/deterministic/judged markers; report-first judged metrics; calibration step; telemetry opt-out; API-error ≠ failure (§6.4, §6.6, step 11). |

### Second review (2026-08-01) — conditional approval; all five required + three clarifications incorporated in v4

| Finding | Disposition |
|---|---|
| #1 fingerprint misses dependency changes | **Adopted** — fingerprint now hashes ALL service `*.py` (glob, no hand-maintained list) + `requirements.txt` + service-venv `pip freeze` + model IDs + fixtures (D5, §6.2). Over-triggering accepted. |
| #2 reset not atomic w.r.t. broker; `/reset` existence unconfirmed | **Adopted** — quiesce step (purge + wait ready==0 & unacked==0 via mgmt API :15673) before reset; 7-step atomic rebuild sequence (§6.2). **Confirmed from code**: `POST /reset` exists at `app.py:252` (Qdrant + ledger + `_artifacts/` + parse cache; not RabbitMQ — hence the quiesce). |
| #3 preflight checks env vars, not resolved settings | **Adopted** — `validate_effective_settings.py` runs in each service's own venv with the sanitized env, asserts values resolved by the service's real `Settings` class (§5.3). |
| #4 evidence page-match too weak | **Adopted** — match requires doc AND page AND ≥1 normalized anchor in chunk text; `anchors` list replaces single `reference_text` (variants for generated captions) (§6.5). |
| #5 deterministic thresholds unspecified | **Adopted** — per-case hard gate: Hit@5 == 1 AND Evidence Recall@5 == 1.0; MRR is aggregate/baseline-compared, not a per-case gate (§6.5). |
| #6 `--reuse` contradictory | **Adopted** — `--reuse` strict (fails on mismatch), `--force-reuse` explicit escape hatch (warn, exit 0) (§6.2). |
| #7 marker semantics | **Adopted** — documented exact selection expressions; `-m "not judged"` for the free tier stack, bare `pytest` for everything (§9). |

### Third review (2026-08-01) — approved for implementation; both required corrections + four clarifications incorporated in v5

| Finding | Disposition |
|---|---|
| #1 rebuild leaves stale fixture objects in MinIO | **Adopted** — rebuild sequence gains CLEAR PREFIX (via existing `DELETE /s3/folder`, `app.py:178`) + VERIFY UPLOAD (bucket holds exactly the expected keys) steps (§6.2); stale-fixture test added to §8. |
| #2 fingerprint misses non-Python pipeline config | **Adopted** — normalized effective-settings snapshot (sorted-key JSON of resolved model IDs) + glob over non-Python config assets added to the fingerprint. Additionally **established from code**: prompts, chunking, embedding dims, and collection schema are in-code constants in this service, so source hashing already covers them (§6.2). |
| Quiesce timeout + ack semantics | **Adopted** — 10-min quiesce timeout with full diagnostics; ack-after-completion verified at `worker.py:119,129` and stated as the assumption `unacked==0 ⇒ no ingest executing` (§6.2). |
| MRR baseline comparison undefined | **Adopted** — MRR delta is report-only in v1; regression threshold deferred until variation is measured (§6.5). |
| Concurrent seeds | **Adopted** — exclusive `evals/.seed.lock` around the whole sequence (§6.2). |
| Stale appendix wording | **Adopted** — first-review P0-1 row marked superseded by the final fingerprint design. |

### Fourth review (2026-08-01) — approval reaffirmed; four implementation-hardening clarifications incorporated

| Clarification | Disposition |
|---|---|
| #1 anchors too permissive for tables | **Adopted** — `required_anchors` (conjunctive, label+value) + `alternative_anchor_groups` (one complete phrasing per group); matching rule updated (§6.5). |
| #2 service runtime missing from fingerprint/reports | **Adopted** — Python version + platform added as fingerprint input 6; reports gain service-venv `pip freeze`, Python/OS/arch, docling version (§6.2, §6.7). |
| #3 queue identity | **Adopted** — queue name resolved from the service's `broker.QUEUE` via a probe subprocess in the service venv (see fifth review #1); pre-ingestion assertion (exists ∧ consumers==1 ∧ ready==0 ∧ unacked==0); recorded in preflight, diagnostics, metadata (§6.2). |
| #4 multi-doc goldens ambiguity | **Adopted** — v1: exactly one expected doc per golden; per-evidence `doc_id` added for future multi-doc support (§6.5). |

### Fifth review (2026-08-01) — conditionally approved; two corrections incorporated

| Correction | Disposition |
|---|---|
| #1 queue discovery must not import service code | **Adopted** — `seed.py` resolves the queue name through a helper executed in the ingestion-service virtual environment; it does not directly import service modules. One probe subprocess (service venv's interpreter) prints normalized JSON with the queue name, collection name, resolved model settings, and runtime; `seed.py` consumes the JSON. The eval venv stays isolated from service dependencies (§6.2). |
| #2 fingerprint input count inconsistent (six vs. seven) | **Adopted** — status header and D5 corrected to the 7-input fingerprint of §6.2 (service runtime is input 6, fixture hashes input 7). |
