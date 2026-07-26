# TranscribeApp — Cloud-Powered Transcription & Diarization

A production-oriented system: a lightweight desktop client uploads audio/video,
a FastAPI backend + RQ worker do all transcription/diarization in the cloud,
and the desktop app can close entirely — processing continues, and reopening
the app later shows the completed transcript.

**The desktop app never processes audio.** It only uploads, polls, and displays.

---

## 1. Architecture

```
┌─────────────┐      HTTPS       ┌──────────────┐
│  Desktop App │ ───────────────▶ │  FastAPI API  │
│  (PySide6)   │ ◀─────────────── │  (backend/)   │
└─────────────┘   poll / fetch    └──────┬───────┘
                                          │
                         ┌────────────────┼────────────────┐
                         ▼                ▼                ▼
                  ┌────────────┐   ┌────────────┐   ┌─────────────┐
                  │  Storage   │   │  Postgres  │   │   Redis     │
                  │ (Local/B2) │   │  Database  │   │   (Queue)   │
                  └────────────┘   └────────────┘   └──────┬──────┘
                                                             │
                                                             ▼
                                                     ┌───────────────┐
                                                     │  RQ Worker    │
                                                     │  (worker/)    │
                                                     └───────┬───────┘
                                                             │
                                                             ▼
                                                  ┌────────────────────┐
                                                  │  Speech Provider    │
                                                  │  Deepgram/Whisper/  │
                                                  │  OpenRouter/Assembly│
                                                  │  AI/Gladia          │
                                                  └────────────────────┘
```

Flow: Desktop uploads → Backend stores file + creates Job (status=`uploading`) →
Job pushed to Redis queue (status=`queued`) → API responds immediately with
`job_id` → Worker (separate process/container) picks up job (status=`processing`)
→ downloads file from storage → extracts audio if video → calls the configured
AI provider → normalizes response → saves segments/speakers/transcript to
Postgres (status=`completed`, or `failed`/`retrying` on error) → Desktop polls
`GET /jobs/{id}` (and later `GET /jobs/{id}/result`) independently, at any time,
even hours later and after a full restart.

---

## 2. Folder Structure

```
transcribeapp/
├── backend/                  # FastAPI app (the "API" service on Render)
│   ├── app/
│   │   ├── main.py           # FastAPI app instance, routers, startup
│   │   ├── config.py         # pydantic-settings, reads .env
│   │   ├── database.py       # SQLAlchemy engine/session/Base
│   │   ├── models/           # Job, MediaFile, Segment, Speaker
│   │   ├── schemas/          # Pydantic request/response models
│   │   ├── api/               # FastAPI routers (jobs.py, health.py, deps.py)
│   │   ├── services/          # JobService — orchestration/business logic
│   │   ├── repositories/      # JobRepository — all DB queries live here
│   │   ├── providers/         # SpeechProvider interface + implementations
│   │   ├── storage/           # StorageBackend interface (local / S3-B2)
│   │   └── utils/              # media helpers, export formats, logging
│   ├── requirements.txt
│   └── Dockerfile
├── worker/                    # RQ background worker (separate Render service)
│   ├── tasks.py                # process_transcription_job()
│   ├── run_worker.py           # `rq worker` entrypoint
│   ├── requirements.txt
│   └── Dockerfile
├── desktop/                    # PySide6 client — thin, no AI processing
│   ├── main.py
│   ├── client.py                # BackendClient (REST wrapper)
│   └── requirements.txt
├── shared/                      # Dependency-free constants shared by both sides
├── tests/                       # pytest unit + integration + API tests
├── docker-compose.yml            # local dev: redis + backend + worker
├── .env.example
└── README.md
```

---

## 3. Why these technology choices

### Background Queue: **RQ** (Redis Queue) — chosen over Celery / Dramatiq / Huey
| Option | Verdict |
|---|---|
| **RQ** ✅ | Minimal setup (just Redis), one worker process = one Render "Background Worker" service, easy to reason about, good enough throughput for this workload (I/O-bound API calls, not CPU-bound). |
| Celery | More powerful (routing, canvas, multiple brokers) but heavier ops burden (beat, broker/backend split, more config) — overkill for a single job type. |
| Dramatiq | Good, but smaller ecosystem/community than RQ; fewer Render deployment examples. |
| Huey | Lightweight like RQ but weaker monitoring tooling (`rq-dashboard` is a nice free plus for RQ). |

### Storage: **Local (dev) / Backblaze B2 (prod)** — S3-compatible abstraction
Backblaze B2 has a free tier (10GB storage, 1GB/day egress) and speaks the S3
API, so the same `S3Storage` class also works unmodified against AWS S3 or
Supabase Storage just by changing `S3_ENDPOINT_URL`. Render's persistent disks
are avoided as the primary production store because Render's free/starter
tiers don't guarantee disk persistence across deploys in the same way, and
because the API and worker run as **separate services** that need to share
files — object storage solves that cleanly, a shared local disk does not.

### Database: **SQLite (dev) → PostgreSQL (prod)**
SQLAlchemy models are DB-agnostic; only `DATABASE_URL` changes. Render offers
a free managed Postgres instance, which is used in production.

### AI Provider: **Deepgram primary**, with a strict abstraction (`SpeechProvider`)
Deepgram returns transcription + diarization + word timestamps + confidence
in a single call, which minimizes worker complexity. `app/providers/factory.py`
is the **single switch point** — changing `SPEECH_PROVIDER` in `.env` swaps
providers app-wide; no other file changes. Whisper, OpenRouter, AssemblyAI,
and Gladia implementations are included as fallbacks (see code comments for
each one's actual capabilities/limitations — e.g. OpenAI's hosted Whisper API
has no native diarization).

---

## 4. Job States

`uploading → queued → processing → completed`
On error: `processing → retrying → processing` (up to 3 automatic retries with
exponential backoff) `→ failed` (after retries exhausted) or manually
`→ cancelled` at any point before completion. Every transition stamps
`queued_at` / `started_at` / `completed_at` on the `Job` row.

---

## 5. API Reference

All endpoints except `GET /health` require header `X-API-Key: <DESKTOP_API_KEY>`.

| Method | Path | Description |
|---|---|---|
| POST | `/upload` | multipart upload (`file`, optional `provider`) → creates + queues job |
| POST | `/jobs/start/{job_id}` | re-enqueue an existing job (manual retry) |
| GET | `/jobs` | list jobs (`?limit=&offset=`) |
| GET | `/jobs/{id}` | job status/metadata |
| GET | `/jobs/{id}/result` | transcript + segments (once completed) |
| GET | `/jobs/{id}/export?format=txt\|json\|srt\|vtt` | export in the given format |
| POST | `/jobs/{id}/cancel` | cancel a pending/in-progress job |
| DELETE | `/jobs/{id}` | delete job + its stored file |
| GET | `/health` | queue length, failed job count, avg processing time |

### Example: upload
```bash
curl -X POST https://your-api.onrender.com/upload \
  -H "X-API-Key: $DESKTOP_API_KEY" \
  -F "file=@meeting.mp4"
```
```json
{ "job_id": "b6f1...", "status": "queued" }
```

### Example: result
```bash
curl https://your-api.onrender.com/jobs/b6f1.../result -H "X-API-Key: $DESKTOP_API_KEY"
```
```json
{
  "id": "b6f1...",
  "status": "completed",
  "readable_transcript": "Speaker 1\n\nHello everyone.\n\nSpeaker 2\n\nHi.",
  "segments": [
    {"speaker": "Speaker 1", "start": 0.32, "end": 3.94, "text": "Hello everyone.", "confidence": 0.97},
    {"speaker": "Speaker 2", "start": 4.11, "end": 6.54, "text": "Hi.", "confidence": 0.95}
  ]
}
```

---

## 6. Running locally

```bash
cp .env.example .env               # fill in DEEPGRAM_API_KEY at minimum
docker-compose up --build
```
This starts Redis, the API (http://localhost:8000/docs for interactive Swagger UI),
and the RQ worker. Then, in another shell:

```bash
cd desktop
pip install -r requirements.txt
export BACKEND_BASE_URL=http://localhost:8000
export DESKTOP_API_KEY=dev-desktop-key-change-me
python main.py
```

### Without Docker
```bash
# Terminal 1 - Redis
redis-server

# Terminal 2 - Backend
cd backend && pip install -r requirements.txt
uvicorn app.main:app --reload

# Terminal 3 - Worker
cd worker && pip install -r requirements.txt -r ../backend/requirements.txt
python run_worker.py

# Terminal 4 - Desktop
cd desktop && pip install -r requirements.txt
python main.py
```

---

## 7. Deploying to Render (free tier)

1. **Redis** — create a free Render Redis instance (or use Upstash's free tier
   if Render Redis isn't available on your plan) → copy its internal URL into
   `REDIS_URL`.
2. **PostgreSQL** — create a free Render Postgres instance → copy `DATABASE_URL`.
3. **Object storage** — create a free Backblaze B2 bucket → set
   `STORAGE_BACKEND=b2`, `S3_ENDPOINT_URL`, `S3_BUCKET_NAME`,
   `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`.
4. **API service** — new Render **Web Service**, root `backend/`, Dockerfile
   build (`backend/Dockerfile`), add all `.env` vars in the Render dashboard.
5. **Worker service** — new Render **Background Worker**, Dockerfile build
   (`worker/Dockerfile`), same env vars (must share `REDIS_URL`/`DATABASE_URL`
   with the API service).
6. Set `BACKEND_BASE_URL` in the desktop app's environment to the deployed
   API's public URL, and distribute `DESKTOP_API_KEY` securely to users.

Render's free web services spin down on inactivity; the first request after
idle may be slow. The worker service, once started, keeps polling Redis
independently of API traffic.

---

## 8. Security notes
- Provider API keys (Deepgram/OpenAI/etc.) live **only** in backend/worker
  environment variables — never shipped to or read by the desktop app.
- Desktop authenticates to the backend with a shared `X-API-Key` header
  (`DESKTOP_API_KEY`). For true multi-user production use, replace this with
  per-user JWT auth (a `User` model / `/auth` routes are natural next steps —
  the `deps.py` dependency is the single place to swap in real auth).

---

## 9. Error handling implemented
- Upload validation rejects unsupported extensions before a job is even created.
- Provider calls get an inline retry (network/transient errors) inside a
  single task run, plus a job-level retry (up to 3x, exponential backoff) that
  re-enqueues the whole task if the provider call keeps failing (e.g. rate
  limits, expired keys) — each attempt visible via `retry_count` and
  `status=retrying`.
- Corrupted/unsupported media surfaces as a `failed` job with `error_message`
  set from the underlying ffmpeg/provider exception, rather than crashing the
  worker process.

---

## 10. Production improvements / scaling / roadmap
- **Scaling**: RQ workers scale horizontally — just run more worker instances
  pointed at the same Redis queue; add `rq-dashboard` for live queue monitoring.
- **Multi-user auth**: add a `User` model + JWT, scope jobs by `user_id`.
- **Resumable uploads**: switch `/upload` to a pre-signed-URL flow (client
  uploads directly to B2/S3, then calls `/jobs/start`) to avoid routing large
  files through the API process and to support resumable/chunked uploads.
- **Diarization for Whisper**: integrate `pyannote.audio` on the worker for a
  true diarization pass when using the Whisper fallback.
- **DOCX export**: add `python-docx` to `utils/media.py`'s export helpers.
- **Webhooks**: optionally POST to a callback URL on job completion instead
  of relying purely on desktop polling.
- **Observability**: ship worker/API logs to a hosted log sink; add
  Prometheus metrics alongside the existing `/health` endpoint.
