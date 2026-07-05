# Chatbot — a learning project for end-to-end, prod-style RAG

This repo is a hands-on walkthrough of what it takes to run a document-grounded
chatbot as a real system — not a notebook. The goal is not to ship a product;
it is to see, in concrete code, every layer that a production chatbot needs:
ingestion, storage, retrieval, an agent runtime, two frontends, and the glue
that keeps them consistent as documents change over time.

If you've built a RAG demo in one file and want to understand what breaks when
you turn it into a real system, this repo is meant to be read top-to-bottom.

---

## What you'll learn by reading the code

- **How to split a chatbot into real services** — retrieval, generation, and
  state each live in their own process, so each can be tested, scaled, or
  replaced independently.
- **How to keep a vector store in sync with a source-of-truth blob store** using
  a small relational ledger and content hashes (etags), so re-ingesting is
  idempotent and deletes are honored.
- **How multimodal ingestion actually works** — parsing PDFs into ordered
  text/image elements, captioning figures with a vision model, embedding both
  modalities, and linking them bidirectionally so a text question can surface
  the right figure.
- **How hybrid retrieval + reranking is wired end-to-end** — dense (Jina) +
  sparse (BM25) named vectors in Qdrant, then a cross-encoder rerank pass.
- **How an agent uses retrieval as a tool**, not as a hardcoded pipeline step —
  a LangGraph react agent decides when to call `search_documents`, and the
  answer surfaces citations + figures back to the UI.
- **How to structure two React apps around Redux Toolkit** — a chat frontend
  and a file-management frontend, each with slices, thunks, and typed hooks.

---

## Architecture at a glance

```
                   ┌────────────────────┐          ┌──────────────────────┐
   ask a question  │     agent-ui       │  chat    │    agent-service     │
   ──────────────► │   React + Redux    │ ───────► │  FastAPI + LangGraph │
                   │   (port 5173)      │          │     (port 8001)      │
                   └────────────────────┘          └──────────┬───────────┘
                                                              │ tool call:
                                                              │ search_documents
                                                              ▼
   upload / list /  ┌────────────────────┐   HTTP    ┌──────────────────────┐
   trigger ingest   │   ingestion-ui     │ ────────► │   ingestion-service  │
   ──────────────►  │   React + Redux    │           │   FastAPI RAG core   │
                    │    (port 5174)     │           │      (port 8000)     │
                    └────────────────────┘           └─┬────────┬────────┬──┘
                                                       │        │        │
                                                 diff/ │   put/ │        │ upsert
                                                 mark  │   get  │        │ query
                                                       ▼        ▼        ▼
                                            ┌──────────────┐ ┌──────┐ ┌────────┐
                                            │ s3-sync-svc  │ │  S3  │ │ Qdrant │
                                            │  (port 8002) │ │(blob)│ │(vector)│
                                            │   FastAPI    │ └──────┘ └────────┘
                                            └──────┬───────┘
                                                   ▼
                                             ┌───────────┐
                                             │ Postgres  │
                                             │  ledger   │
                                             └───────────┘
```

Two flows run through this graph.

### 1. Ingestion flow (offline, triggered from the ingestion UI)

1. User uploads PDFs to S3 through the ingestion UI, or files already exist in
   the bucket under `docs/`.
2. User clicks **Ingest**. `ingestion-service /ingest`:
   - Lists every object under `docs/` in S3.
   - Sends `{s3_key, etag, size, last_modified}` for each object to
     `s3-sync-service /diff`, which compares against the Postgres ledger and
     returns `{new, modified, deleted, unchanged}`.
   - For **new** and **modified** keys: downloads the file, parses it into
     ordered text/image elements (docling + PyMuPDF), captions each figure with
     `gpt-4o-mini`, builds chunks that link text↔image, embeds them (dense via
     Jina, sparse BM25 via FastEmbed), and upserts into Qdrant. On success,
     calls `mark-ingested` so the ledger stores the ingested etag.
   - For **deleted** keys: drops the Qdrant points for that `doc_id`, sweeps
     leftover figure images, and calls `mark-deleted`.
   - **unchanged** is skipped — this is why re-clicking Ingest is cheap and
     safe.

The point of the ledger is that the vector store should be a pure derivative of
S3. If S3 is the source of truth, the ledger records "what did we last derive?"
so we never do redundant work and never leave stale points behind.

### 2. Query flow (online, driven by the chat UI)

1. User types a question in the agent UI. It POSTs `{message, history}` to
   `agent-service /chat`.
2. `agent-service` runs a LangGraph react agent with one tool,
   `search_documents`. The agent decides whether to call the tool; when it
   does, the tool hits `ingestion-service /retrieve`.
3. `/retrieve` runs **hybrid search** over Qdrant (dense + sparse named
   vectors), then reranks the top ~50 candidates with a cross-encoder
   (`BAAI/bge-reranker-v2-m3`), and returns `{chunks, images}`. Images come
   from two sources — direct image-chunk hits and images linked to a
   surviving text chunk — and are reranked together.
4. The agent synthesizes an answer citing `chunk_id`s. The agent-service pulls
   the images and derived citations out of the tool trace and hands them back
   to the UI, which renders bubbles + citation list + figure gallery.

Retrieval and generation are deliberately split. `ingestion-service` never
touches the LLM at answer time; `agent-service` never touches Qdrant. This
means you can iterate on the retriever or the agent independently, and you can
point the agent at any retriever that respects the `/retrieve` contract.

---

## The five apps

Each app is a self-contained process with its own `.env`, its own `README`
scope, and its own port. This matches how you'd run them in a real deployment
(each becomes a container / a k8s deployment / a Cloud Run service).

### `apps/agent-ui` — chat frontend (port 5173)

React 19 + Redux Toolkit + Vite.

- Redux slice `chat` holds `messages[]` and `pending`.
- The `sendMessage` thunk pushes the user message on `pending`, calls
  `agent-service /chat`, and pushes the bot answer (or error) on
  `fulfilled` / `rejected`.
- Components are split by responsibility: `Header`, `MessageList`, `Composer`,
  and a bubble family (`UserBubble`, `BotBubble`, `ErrorBubble`,
  `MessageBubble`, `Citations`, `ImageGallery`).
- `App.tsx` is a 14-line composition — no state, no API calls.

### `apps/ingestion-ui` — S3 browser + ingest trigger (port 5174)

React 19 + Redux Toolkit + Vite.

- Three slices: `s3` (bucket / files / folders + list status), `ingest`
  (ingest run status + result), `ui` (modal state + busy + error + flash
  toast).
- Async thunks wrap every mutation (`uploadFilesThunk`,
  `createFolderThunk`, `deleteFolderThunk`, `deleteFileThunk`,
  `resetAllThunk`, `runIngest`) and chain a `fetchS3Files` refresh + a
  flash toast on success.
- Components: `Header`, `Stats`, `Banners`, `TreeCard` (recursive
  `FolderRow` / `FileRow`), `ModalHost` + 5 form components, `Toast`.

### `apps/agent-service` — agent runtime (port 8001)

FastAPI + LangGraph.

- One endpoint: `POST /chat` → `agent.run_agent(message, history)`.
- The agent is a `create_react_agent` with a single `@tool` —
  `search_documents(query)` — which HTTP-calls
  `ingestion-service /retrieve` and returns chunks + images as JSON.
- The trace is walked afterwards to extract `tool_calls`, dedupe images and
  citations, and surface them alongside the answer.

### `apps/ingestion-service` — the RAG core (port 8000)

FastAPI. Owns S3 access, document parsing, embedding, Qdrant, and retrieval.

Key modules:
- `storage.py` — S3 client (list / upload / download / delete / presigned
  URLs). Works against real AWS S3 or MinIO — the `.env` decides.
- `parser.py` — parses PDFs into an ordered list of text/image elements
  using docling + PyMuPDF, keeping page numbers and reading order.
- `ingest.py` — the orchestrator: parse → caption images with `gpt-4o-mini`
  → build `Chunk`s that link text↔image bidirectionally → embed → upsert.
- `embed.py` — Jina dense embeddings for text and image, FastEmbed BM25 for
  sparse.
- `vectordb.py` — Qdrant collection setup and hybrid search over `dense` +
  `sparse` named vectors.
- `rag.py` — retrieval-only: hybrid search → cross-encoder rerank → assemble
  images. Deliberately LLM-free.
- `sync_client.py` — thin HTTP client to `s3-sync-service`.

Endpoints:
- `GET  /s3/files` — list bucket + folder markers
- `POST /s3/upload`, `POST /s3/folder`, `DELETE /s3/file`, `DELETE /s3/folder`
- `POST /ingest` — the reconcile loop described above
- `POST /retrieve` — hybrid search + rerank (called by the agent)
- `POST /reset` — testing utility: drops Qdrant + wipes the ledger (S3
  untouched)

### `apps/s3-sync-service` — the ingestion ledger (port 8002)

FastAPI + SQLAlchemy + Postgres.

- Owns one table, `files`, keyed by `s3_key`. Each row tracks the observed
  S3 state (`s3_etag`, `s3_size`, `s3_last_modified`) plus the last
  successful ingest (`ingested_etag`, `ingested_at`, `status`).
- `POST /diff` takes the current S3 listing, refreshes observed state, and
  returns `{new, modified, deleted, unchanged}` — the classification the
  ingestion service acts on.
- `POST /files/mark-ingested`, `/mark-failed`, `/mark-deleted` close the
  loop after the ingestion service processes a batch.
- `POST /files/reset` is a test helper.

This service is intentionally boring — it exists so that "what does Qdrant
need to look like?" is a database query, not a guess.

---

## Storage & external dependencies

- **S3** (AWS S3 in this setup; MinIO works locally) — source of truth for
  raw documents and extracted figure images.
- **Postgres** — the ingestion ledger for `s3-sync-service`.
  A `docker-compose.yml` in that app brings one up on host port 5433.
- **Qdrant** (Qdrant Cloud in this setup; local Docker works too) — named
  vectors: `dense` (Jina) + `sparse` (BM25). Payload carries `chunk_id`,
  `doc_id`, `doc_version`, `page`, `kind`, `text`, `image_key`,
  `linked_image_keys`.
- **OpenAI** — `gpt-4o` for the react agent, `gpt-4o-mini` for figure
  captioning during ingestion.
- **Jina AI** — dense text + image embeddings.
- **Cross-encoder** — `BAAI/bge-reranker-v2-m3` runs locally via
  `sentence-transformers`.
- **Langfuse** — observability hooks are configured via env; wire up when
  you want traces.

---

## Repo layout

```
Chatbot/
├── README.md                         (this file)
├── .gitignore
└── apps/
    ├── agent-ui/                     React chat UI (Redux)
    │   └── src/
    │       ├── app/                  store + typed hooks
    │       ├── features/chat/        chatSlice + Message types
    │       └── components/           Header, MessageList, Composer, bubbles/
    │
    ├── ingestion-ui/                 React S3 UI (Redux)
    │   └── src/
    │       ├── app/                  store + typed hooks
    │       ├── features/             s3 / ingest / ui slices
    │       ├── components/           Header, Stats, Banners, tree/, modal/
    │       └── utils/                format + tree helpers
    │
    ├── agent-service/                LangGraph agent (FastAPI)
    │   ├── app.py                    /chat endpoint
    │   ├── agent.py                  react agent + search_documents tool
    │   └── config.py
    │
    ├── ingestion-service/            RAG core (FastAPI)
    │   ├── app.py                    S3 CRUD + /ingest + /retrieve + /reset
    │   ├── ingest.py                 parse → caption → embed → upsert
    │   ├── parser.py                 PDF → ordered text/image elements
    │   ├── embed.py                  Jina dense + FastEmbed sparse
    │   ├── vectordb.py               Qdrant hybrid search
    │   ├── rag.py                    hybrid + cross-encoder rerank
    │   ├── storage.py                S3 client
    │   ├── sync_client.py            calls into s3-sync-service
    │   ├── models.py                 Chunk model
    │   ├── Dockerfile
    │   └── docker-compose.yml
    │
    └── s3-sync-service/              Ingestion ledger (FastAPI + Postgres)
        ├── app.py                    /diff + /files/mark-* + /files/reset
        ├── models.py                 File SQLAlchemy model
        ├── db.py, schemas.py
        └── docker-compose.yml        Postgres (host port 5433)
```

---

## Getting it running locally

You need Python 3.12+, Node 22+, and Docker.

### 1. Config

Copy each `.env.example` to `.env` and fill it in:

```
cp apps/agent-service/.env.example       apps/agent-service/.env
cp apps/ingestion-service/.env.example   apps/ingestion-service/.env
cp apps/s3-sync-service/.env.example     apps/s3-sync-service/.env
```

You'll need: OpenAI API key, Jina API key, an S3 bucket (real AWS or a local
MinIO), a Qdrant endpoint (cloud or local Docker), and the Postgres URL
(defaults work with the bundled `docker-compose`).

### 2. Bring up Postgres for the sync service

```
cd apps/s3-sync-service && docker compose up -d
```

### 3. Install and run each service

Each Python service uses its own venv:

```
# in each of s3-sync-service, ingestion-service, agent-service:
python3.12 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

Then run them, each in its own terminal:

```
# s3-sync-service   (port 8002)
./.venv/bin/uvicorn app:app --port 8002 --reload

# ingestion-service (port 8000)
./.venv/bin/uvicorn app:app --port 8000 --reload

# agent-service     (port 8001)
./.venv/bin/uvicorn app:app --port 8001 --reload
```

### 4. Run the UIs

```
cd apps/ingestion-ui && npm install && npm run dev     # port 5174
cd apps/agent-ui     && npm install && npm run dev     # port 5173
```

### 5. Try it

- Open the ingestion UI at http://localhost:5174 — upload a PDF under any
  folder, then click **Ingest**.
- Open the chat UI at http://localhost:5173 — ask a question about the
  document you ingested.

### Port map

| Service            | Port |
|--------------------|------|
| agent-ui           | 5173 |
| ingestion-ui       | 5174 |
| ingestion-service  | 8000 |
| agent-service      | 8001 |
| s3-sync-service    | 8002 |
| Postgres (docker)  | 5433 |

---

## Reading order (if you want to learn from this repo)

1. `apps/ingestion-service/app.py` — start at `/ingest`. Follow every step of
   the reconcile loop and see how the three data stores stay consistent.
2. `apps/s3-sync-service/app.py` + `models.py` — understand what "the ledger"
   actually is and why etags are enough.
3. `apps/ingestion-service/ingest.py` — read how one document becomes chunks,
   including the text↔image linking pass. This is where multimodal RAG
   diverges from single-modal RAG.
4. `apps/ingestion-service/embed.py` + `vectordb.py` — hybrid search setup.
5. `apps/ingestion-service/rag.py` — retrieval + rerank, LLM-free by design.
6. `apps/agent-service/agent.py` — how the agent uses retrieval as a tool.
7. `apps/agent-ui/` and `apps/ingestion-ui/` — Redux Toolkit slice + thunk
   patterns for a chat UI and a CRUD UI.
