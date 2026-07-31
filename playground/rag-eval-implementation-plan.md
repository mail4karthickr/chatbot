# Implementation Plan: Per-Document Golden Set Synthesizer + Eval Service

## Context for the implementer

This service adds an evaluation capability to an existing multimodal RAG product. Users upload arbitrary PDFs through an ingestion UI; the ingestion service parses, chunks, embeds, and stores them. When a user clicks "Evaluate", the system must:

1. Generate a synthetic golden set (Q/A pairs + expected image IDs) from the **raw PDF** — never from the stored chunks, because questions generated from chunks cannot detect parsing/chunking loss.
2. Replay each golden question through the real RAG pipeline and score it.
3. Show the questions, per-question results, and an aggregate score in the UI.

Key architectural decisions already made (do not revisit):

- Eval is **one async job with two stages** (`synthesize` → `evaluate`). The frontend calls one endpoint and polls one job.
- Golden sets are **immutable**, keyed by `(doc_id, doc_hash)`. Doc unchanged → reuse golden set, skip stage 1.
- Golden set generation samples pages against a **fixed budget** so 150-page docs cost the same as 30-page docs.
- Text pages → cheap text LLM. Figure/table pages → vision LLM on rendered page PNGs.
- Image IDs follow a **shared deterministic contract** (`{doc_id}_p{page}_img{index}`) used identically by the ingestion pipeline and the synthesizer. This is critical — image hit-rate scoring depends on it.
- Storage: MongoDB for golden sets and eval runs; S3 for PDFs and extracted images (Mongo stores S3 keys only).
- **Deployment topology: existing monorepo, new `apps/eval-service` as its own Docker container.** It never imports other services' code directly; it calls agent-service's `/generate` over HTTP for stage 2, consumes `eval.jobs` from RabbitMQ, and shares MongoDB/S3.
- **The image ID contract lives in a monorepo shared package** (`libs/pdf_contract`), installed into both services' images at build time via `pip install ./libs/pdf_contract`. It is a library, not a service — no container, no network calls. Both services depend on it; a CI contract test guards it.
- Golden sets record `contract_version` alongside `prompt_version`, so if the ID scheme ever changes, stored golden sets can be matched to compatible ingested documents.

## Stack (matched to the existing chatbot monorepo)

- Python 3.11+, FastAPI, Pydantic v2 — same as existing services
- PyMuPDF (`pymupdf`) for PDF inventory, text extraction, page rendering, image extraction
- `motor` (async MongoDB), S3 client matching whatever `s3-sync-service`/`ingestion-service` already use
- LLM calls via the same provider config as `agent-service` (currently `gpt-5.5` for generation): use the existing cheap-tier model for text-page Q/A + realism checks, and the vision-capable model for figure/table pages and judges. Wrap behind an interface in `services/llm.py` — reuse the provider client pattern already in the codebase rather than introducing a new SDK.
- Embeddings for dedup: reuse `text-embedding-3-small` (already used for retrieval — same client, same config source)
- Background jobs: **RabbitMQ is already running in this stack — use it.** API publishes an `eval.jobs` message; the eval worker consumes it. Job *status* still lives in Mongo (the queue carries the trigger, Mongo carries the state the UI polls). No Celery needed — a plain `aio-pika` consumer is enough.
- Testing: pytest, with 2–3 real sample PDFs committed under `tests/fixtures/` (one text-heavy, one figure-heavy, one large 100+ pages) — the existing `parsing_test_files/` folder may already contain usable candidates

## Repository layout (existing chatbot monorepo — NEW items marked)

```
chatbot/
  apps/
    agent-service/              # EXISTING — the RAG pipeline (/generate). Small change needed:
                                #   eval mode flag on /generate returning retrieved passages
                                #   (with page_num + doc_id) and displayed image IDs (Phase 6)
    agent-ui/                   # EXISTING
    ingestion-service/          # EXISTING — two changes:
                                #   1) install pdf_contract, use make_image_id for stored figures
                                #   2) ensure every chunk carries page_num metadata
    ingestion-ui/               # EXISTING — will gain the Evaluate button + results views (Phase 8 contract)
    rabbitmq/                   # EXISTING — add eval.jobs queue definition
    s3-sync-service/            # EXISTING — source of doc-updated events / doc_hash
    eval-service/               # NEW — this plan's main deliverable
      Dockerfile                # build context = repo root; COPY libs/pdf_contract + pip install
      pyproject.toml            # dep: pdf-contract @ file:///./libs/pdf_contract
      app/
        main.py                 # FastAPI app, routers
        config.py               # env settings (Pydantic BaseSettings)
        models/
          golden.py             # GoldenRecord, GoldenSet pydantic models
          jobs.py               # EvalJob, JobStage, JobStatus
          results.py            # QuestionResult, EvalRun, AggregateScores
        services/
          s3_client.py          # fetch PDF, put/get extracted images
          llm.py                # provider wrapper (same client pattern as agent-service)
          inventory.py          # Step A: page inventory + classification
          sampler.py            # Step B: page sampling against budget
          page_prep.py          # Step C: text extraction, page render, image extraction (via pdf_contract)
          generator.py          # Step D: LLM Q/A generation (text + vision variants)
          validator.py          # Step E: filtering, dedup, forbidden-word checks
          golden_store.py       # Step F: Mongo persistence for golden sets
          eval_runner.py        # Stage 2: replay pipeline + score
          judge.py              # LLM-judge calls (correctness, faithfulness)
          rag_client.py         # thin HTTP client calling agent-service /generate (eval mode)
        workers/
          job_worker.py         # aio-pika consumer on eval.jobs; runs stage 1 then 2, updates Mongo status
        api/
          routes_eval.py        # POST /evaluate, GET /jobs/{id}, GET /docs/{doc_id}/golden, GET /docs/{doc_id}/runs
        prompts/
          text_page_qa.txt
          vision_page_qa.txt
          judge_correctness.txt
          judge_faithfulness.txt
          question_realism_check.txt
      tests/
        fixtures/               # sample PDFs (seed from parsing_test_files/ if suitable)
        test_inventory.py
        test_sampler.py
        test_page_prep.py
        test_validator.py
        test_generator_contract.py   # mocked LLM, tests JSON contract handling
        test_eval_runner.py
  libs/                         # NEW top-level folder
    pdf_contract/               # NEW shared library — NOT a service, NOT a container
      pyproject.toml
      pdf_contract/
        __init__.py             # exports CONTRACT_VERSION = "1.0.0"
        image_ids.py            # make_image_id(doc_id, page_num, index) -> str
        image_filter.py         # is_content_image(w, h) — shared tiny-image threshold
        page_meta.py            # page-number metadata conventions for chunks
      tests/
        test_image_ids.py
  tests_contract/               # NEW
    test_image_id_contract.py   # runs BOTH ingestion-service and eval-service image extraction
                                # over fixture PDFs, asserts identical ID sets;
                                # CI-triggered on libs/pdf_contract changes
  scripts/
    start-infra.sh              # EXISTING — verify mongo/rabbitmq/s3 covered
    start-all.sh                # EXISTING — add eval-service (API + worker) startup
```

Build note: the eval-service and ingestion-service images must be built with the **repo root as build context** (e.g. `docker build -f apps/eval-service/Dockerfile .`) so `COPY libs/pdf_contract` works. Encode this in start scripts / compose / CI so nobody builds from the service subdirectory by accident.

## Configuration (env vars, with defaults)

```
MONGO_URI, MONGO_DB=rag_eval
S3_BUCKET_DOCS, S3_BUCKET_ASSETS (extracted images)
TEXT_MODEL=<cheap tier of your existing provider>   # text-page generation + realism check
VISION_MODEL=gpt-5.5                                # figure/table pages + judges (must accept images)
EMBED_MODEL=text-embedding-3-small                  # dedup similarity (same as retrieval)
AGENT_SERVICE_URL=http://agent-service:PORT         # /generate target for stage 2
RABBITMQ_URL=amqp://...                             # existing broker; queue: eval.jobs
MAX_SAMPLED_PAGES=20
MAX_FIGURE_PAGES=10
QUESTIONS_PER_PAGE=2
TARGET_QUESTIONS=20            # stop generating once validated count reaches this
RENDER_DPI=150
DEDUP_SIM_THRESHOLD=0.90
TOP_K_RETRIEVAL=5              # for retrieval hit scoring, match production setting
```

---

## Phase 1 — Monorepo skeleton, shared package, models (no LLM, no external services)

**Tasks**

0. Create `libs/pdf_contract` first: `make_image_id(doc_id, page_num, index) -> str` implementing `{doc_id}_p{page_num}_img{index}` (1-based index), `is_content_image` size filter, `CONTRACT_VERSION` constant, and unit tests. Wire `apps/eval-service` and `apps/ingestion-service` `pyproject.toml` + Dockerfiles to install it (repo-root build context). Declare the `eval.jobs` queue in the existing RabbitMQ setup, and extend `scripts/start-all.sh` to launch eval-service (API + worker). Verify both images build.
1. Create the eval-service layout above with empty modules and a running FastAPI app (`GET /health`).
2. Implement Pydantic models:
   - `GoldenRecord`: `qid, question, ground_truth_answer, source_pages: list[int], expected_image_ids: list[str], category: Literal["text","table","image_required","multi_hop"]`
   - `GoldenSet`: `doc_id, doc_hash, generator: {model, prompt_version}, contract_version, created_at, records: list[GoldenRecord]`
   - `EvalJob`: `job_id, doc_id, doc_hash, status: Literal["queued","synthesizing","evaluating","done","failed"], stage_detail, error, created_at, updated_at`
   - `QuestionResult`: `qid, retrieval_hit: bool, retrieved_pages, image_hit: bool, displayed_image_ids, correctness: float, faithfulness: float, generated_answer`
   - `EvalRun`: `run_id, doc_id, golden_set_id, pipeline_config: dict, results: list[QuestionResult], aggregate: dict, created_at`
3. `config.py` with BaseSettings reading the env vars above.
4. Mongo collections + indexes: `golden_sets` (unique index on `(doc_id, doc_hash)`), `eval_runs` (index `doc_id, created_at`), `jobs` (index `status, created_at`).

**Acceptance**: app boots; `pytest` runs; models round-trip to/from JSON; Mongo indexes created on startup.

---

## Phase 2 — Page inventory + sampling (pure Python, testable against real PDFs)

**Tasks**

1. `inventory.py`: `build_inventory(pdf_bytes) -> list[PageInfo]` using PyMuPDF. Per page collect: `page_num, text_chars, image_count, has_table_hint, heading_text`. Table hint heuristics: many short lines with aligned numeric tokens, or PyMuPDF `find_tables()` if available. Classify each page:
   - `boilerplate`: cover (page 1 with <600 chars), TOC/references/index (regex on heading text: `contents|references|bibliography|index|appendix`), blank (<50 chars, 0 images)
   - `figure`: `image_count >= 1` (and not boilerplate)
   - `table`: table hint true (and not figure — figure wins if both)
   - `text`: everything else
2. `sampler.py`: `sample_pages(inventory, cfg) -> list[PageInfo]`:
   - Take all `figure` + `table` pages; if more than `MAX_FIGURE_PAGES`, sample evenly across the doc (e.g., sort by page_num, take every k-th).
   - Fill remaining budget up to `MAX_SAMPLED_PAGES` with `text` pages, stratified: split doc into thirds, sample proportionally from each third.
   - Never include boilerplate. Deterministic given a seed (seed on `doc_hash`) so re-runs are reproducible.
3. Unit tests against the three fixture PDFs asserting: boilerplate pages excluded, figure pages prioritized, budget respected, determinism (same hash → same sample).

**Acceptance**: `python -m eval_service.tools.inspect fixtures/report.pdf` (add a tiny CLI) prints the inventory table and the sampled page list; tests pass.

---

## Phase 3 — Page preparation + image ID contract

**Tasks**

1. `page_prep.py`:
   - `extract_page_text(doc, page_num) -> str` — page text plus last paragraph of previous page (continuity).
   - `render_page_png(doc, page_num, dpi) -> bytes`.
   - `extract_page_images(doc, doc_id, page_num) -> list[ExtractedImage]` where each `image_id` comes from `pdf_contract.make_image_id(doc_id, page_num, index)`, index = order of appearance (PyMuPDF xref order on that page, 1-based). **Filter out tiny images** (<50×50 px — usually logos/decorations) — do this filtering BEFORE indexing, and put the size threshold in `pdf_contract` too, since ingestion must filter identically or indices drift.
   - Upload extracted images to `S3_BUCKET_ASSETS/{doc_id}/{image_id}.png`.
2. **Critical cross-cutting task**: update the **existing ingestion service** to import `pdf_contract` and use `make_image_id` when it stores figures (its Dockerfile already installs the package from Phase 1). Implement `tests_contract/test_image_id_contract.py`: run both services' image-extraction paths over the fixture PDFs and assert identical ID sets. Add a CI rule that this test runs on any change under `libs/pdf_contract/`. If ingestion cannot be changed yet, write an adapter mapping its current IDs → contract IDs, and log a TODO.
3. Verify chunk metadata: confirm the ingestion pipeline stores `page_num` on every chunk (needed for retrieval-hit scoring in Phase 6). If missing, add it — this is a blocker for Phase 6.

**Acceptance**: contract test passes; a fixture figure page round-trips (render PNG, extract images, IDs stable across runs); images visible in S3 (or localstack/minio in dev).

---

## Phase 4 — Q/A generation (LLM calls)

**Tasks**

1. `prompts/text_page_qa.txt` — instruct: generate `QUESTIONS_PER_PAGE` questions a real user of this document would ask; answerable from the provided text alone; user has NOT read the document, so no references to "the page/section/paragraph/document/figure/table"; answers 1–3 sentences fully supported by the text; output strict JSON array `[{question, ground_truth_answer, category}]` with `category` ∈ `text|multi_hop`. No prose, no markdown fences.
2. `prompts/vision_page_qa.txt` — input: page PNG + the list of valid image IDs on that page. Instruct: questions whose answers require the visual content (trend in a chart, structure in a diagram, values in a table); include `expected_image_ids` ⊆ the provided list, or `[]`; same phrasing rules; `category` ∈ `image_required|table`; strict JSON.
3. `generator.py`:
   - `generate_for_page(page_info, prepared_input) -> list[RawRecord]` — routes to text vs vision model.
   - Robust JSON handling: strip fences, retry once on parse failure with an appended "return ONLY valid JSON" reminder, then give up on that page (log, continue).
   - Concurrency: process pages with `asyncio.gather` bounded by a semaphore (e.g., 4 concurrent LLM calls). Per-call timeout (60s) and one retry on transient errors.
   - Stop early once validated-record count (Phase 5 runs inline) reaches `TARGET_QUESTIONS`.
4. Version the prompts: `prompt_version` string constant bumped on any prompt edit; stored in the golden set.

**Acceptance**: `test_generator_contract.py` with mocked LLM responses covers: valid JSON, fenced JSON, invalid JSON retry, hallucinated image ID passthrough (validator catches later). A manual smoke script runs generation on one fixture PDF end-to-end and prints records.

---

## Phase 5 — Validation, dedup, and golden set persistence

**Tasks**

1. `validator.py`, applied per record:
   - Required fields present and non-empty; question ends with `?` or is imperative-interrogative.
   - Forbidden substrings in question (case-insensitive): `figure`, `table`, `image`, `page`, `document`, `diagram`, `chart`, `section`, `above`, `below`, `this pdf` → drop. (Note: "table" may appear legitimately, e.g. "database table" — allow an override list; start strict, loosen with data.)
   - `expected_image_ids` must be a subset of the actual image IDs on the source page → else drop (models hallucinate IDs).
   - Length sanity: question 15–300 chars, answer 20–600 chars.
2. Dedup: embed all surviving questions (any cheap embedding model, or reuse the product's embedder); cosine similarity > `DEDUP_SIM_THRESHOLD` → keep the first, drop the rest.
3. Optional realism check (config flag, default on): batch the surviving questions into ONE cheap-model call using `prompts/question_realism_check.txt` — "for each question, answer yes/no: could a user who has never seen this document plausibly ask it?" Drop the `no`s.
4. `golden_store.py`: assign `qid`s (`q1..qN`), attach `source_pages` and category, stamp `contract_version` from `pdf_contract.CONTRACT_VERSION`, write the `GoldenSet` document. Upsert semantics: if `(doc_id, doc_hash)` exists, return existing (idempotent). Log drop statistics (`generated=28, dropped_forbidden=3, dropped_dedup=2, dropped_ids=1, final=22`).

**Acceptance**: unit tests for every filter rule; end-to-end smoke on a fixture PDF produces a stored golden set with ≥12 records including ≥3 `image_required` (for the figure-heavy fixture).

---

## Phase 6 — Eval runner (stage 2)

**Tasks**

1. `rag_client.py`: async HTTP client for **agent-service** `POST /generate` exposing `answer(question, doc_ids=[doc_id]) -> {answer_text, retrieved_chunks: [{chunk_id, page_num, doc_id, text}], displayed_image_ids: [str]}`. Agent-service already tracks retrieved passages, reranked images, and sources internally (its logs show `sources=N`, `reranked=8 images=3`) — add an `eval_mode: true` flag (or `include_debug`) to `/generate` that surfaces these in the response instead of only logging them. Also confirm `/generate` accepts a `doc_ids` filter and scope every eval question to the doc under test — logs show `doc_ids=None` today; without scoping, retrieval searches the whole corpus and per-doc scores are contaminated by other documents.
2. Deterministic metrics in `eval_runner.py`:
   - `retrieval_hit`: any retrieved chunk (top `TOP_K_RETRIEVAL`) has `page_num ∈ source_pages` (± 1 page tolerance, since chunks straddle pages).
   - `image_hit`: `expected_image_ids ⊆ displayed_image_ids` when non-empty; also track false positives — images displayed when `expected_image_ids == []`.
3. LLM-judge metrics in `judge.py` (use the vision-capable/strong model):
   - `correctness` (0–1): generated answer vs `ground_truth_answer` given the question. Prompt returns strict JSON `{score, reason}`.
   - `faithfulness` (0–1): generated answer vs retrieved context only.
4. Aggregation: overall score = weighted mean (suggest `0.35*correctness + 0.25*faithfulness + 0.25*retrieval_hit_rate + 0.15*image_hit_rate`; put weights in config). Also report per-category breakdowns — `image_required` scores are the multimodal health signal.
5. Persist `EvalRun` including `pipeline_config` snapshot (chunker version, embed model, top_k, generation model, reranker) — fetch from agent-service/ingestion-service config endpoints or env. Without this field, score changes over time are uninterpretable.

**Acceptance**: `test_eval_runner.py` with a mocked `rag_client` verifies hit logic (including page tolerance and image false positives) and aggregation math; a live smoke run against the real pipeline completes for one fixture doc.

---

## Phase 7 — Job orchestration + API

**Tasks**

1. `job_worker.py`: `aio-pika` consumer on the `eval.jobs` queue with `prefetch_count=1`. On message `{job_id}`: load the job from Mongo, atomically flip `queued → synthesizing` (skip stage 1 with a log line if a golden set exists for `(doc_id, doc_hash)`), then `→ evaluating`, run stage 2, `→ done`. Ack only after a terminal status is written. On exception → status `failed` with `error`, then ack anyway (never nack-requeue — a poisoned PDF would loop forever); retry happens via the UI's re-evaluate button.
2. Routes:
   - `POST /evaluate {doc_id}` → resolves current `doc_hash` from S3 metadata / s3-sync-service, creates the job doc in Mongo (`queued`), publishes `{job_id}` to `eval.jobs`, returns `{job_id}`. Reject with 409 if an active job exists for the doc.
   - `GET /jobs/{job_id}` → status + stage detail (e.g., `synthesizing: page 12/20`).
   - `GET /docs/{doc_id}/golden` → latest golden set (records list for the UI, shown as soon as stage 1 completes).
   - `GET /docs/{doc_id}/runs?limit=10` → eval runs, newest first (enables a score-over-time view later).
3. Progress reporting: worker updates `stage_detail` after each page (stage 1) and each question (stage 2) so the UI can show a real progress bar.

**Acceptance**: full end-to-end via API on a fixture doc: POST evaluate → poll → done; golden endpoint returns questions mid-job once synthesis finishes; re-POST with unchanged doc skips synthesis (verify via logs/timing).

---

## Phase 8 — Frontend contract + hardening (last)

**Tasks**

1. Document the UI contract in `README.md`: the three GETs above, the job state machine, and the result shape for the score card (aggregate + per-category) and question table (question, category badge, pass/fail per metric, expandable generated-answer vs ground-truth comparison).
2. Cost guardrails: per-job token accounting (log tokens per LLM call, sum into the job doc); config cap `MAX_JOB_COST_TOKENS` that fails the job gracefully if exceeded.
3. Failure-mode tests: encrypted PDF, scanned/no-text PDF (inventory finds 0 text pages → treat all content pages as figure pages, cap at budget), 1-page PDF, PDF with 0 images (golden set is text-only — valid, image_hit_rate reported as n/a not 0).
4. Structured logging throughout (job_id on every line) — this is the debugging surface when a user reports a weird score.

**Acceptance**: all edge-case fixtures produce either a valid golden set or a clean `failed` status with an actionable error; no unhandled exceptions in worker logs.

---

## Execution notes for Claude Code

- Implement phases strictly in order; each phase's acceptance criteria must pass before moving on. Phases 2–3 involve no LLM calls — get them fully tested first.
- Mock all LLM calls in tests; keep one `scripts/smoke.py` for manual live runs against real models.
- The two highest-risk items are flagged inline: the **image ID contract** (Phase 3, task 2) and **chunk page metadata** (Phase 3, task 3). Verify both against the existing ingestion codebase before writing generator code — if either is broken, Phase 6 metrics are meaningless.
- Prompt files are data, not code: never inline prompts in Python; always bump `prompt_version` when editing them.
- Prefer boring choices: no new infra — Mongo, S3, and RabbitMQ are already in the stack; polling `GET /jobs/{id}` before websockets.
- The eval service must never import agent-service or ingestion-service modules — only `pdf_contract`. Cross-service interaction is HTTP (+ RabbitMQ + shared Mongo/S3) exclusively; treat a direct import as a review-blocking error.
- Three existing services need small, surgical changes — make them as separate, reviewable commits: ingestion-service (use `pdf_contract` for figure IDs; chunk `page_num` metadata), agent-service (`eval_mode` response fields on `/generate`; honor `doc_ids` scoping), ingestion-ui (Evaluate button + results views, Phase 8 contract).
- `pdf_contract` is a library, never a service: no HTTP endpoints, no container of its own, no runtime state. Both services import it; any change to it bumps `CONTRACT_VERSION` and must pass `tests_contract/` in CI.
- Always build Docker images from the repo root (`docker build -f apps/eval-service/Dockerfile .`); building from a service subdirectory breaks the `COPY libs/pdf_contract` step. Bake the correct invocation into the start scripts and CI.
