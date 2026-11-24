# Lyftr AI — Full-Stack LLM Chatbot

A React + FastAPI chatbot with multi-turn SQLite history, LiteLLM integration, multi-key failover, response caching, request metadata, Docker Compose and a failure-simulation hook.

## 1. Live URLs

- Frontend: add your deployed frontend URL after deployment.
- Backend: add your deployed backend URL after deployment.
- Health: `https://YOUR-BACKEND/health`

## 2. Run locally: with Docker and without

### Docker (recommended)

1. Copy `.env.example` to `.env`.
2. Add **two valid API keys** from the same provider/account family supported by your chosen `LLM_MODEL`.
3. Set `LLM_MODEL` (default `openai/gpt-4o-mini`) and optionally `LLM_API_BASE`.
4. Run:

```bash
make up
```

Open `http://localhost:3000`. Backend runs at `http://localhost:8000`.

Stop with `make down`; the named Docker volume `app_data` preserves SQLite history and LiteLLM's disk cache.

### Without Docker

Backend:

```bash
cd backend
python -m venv .venv
# Windows: .venv\\Scripts\\activate
source .venv/bin/activate
pip install -r requirements.txt
export LLM_API_KEYS="key_one,key_two"
export LLM_MODEL="openai/gpt-4o-mini"
export DATABASE_URL="sqlite:///./app.db"
export ALLOWED_ORIGINS="http://localhost:3000"
uvicorn app.main:app --reload --port 8000
```

Frontend in another terminal:

```bash
cd frontend
npm install
# Set VITE_API_BASE_URL=http://localhost:8000 in frontend/.env if needed
npm run dev
```

On Windows PowerShell, use `$env:LLM_API_KEYS="key_one,key_two"` and `$env:ALLOWED_ORIGINS="http://localhost:3000"`.

## 3. Design decisions: key fallback, caching, SQLite session storage

- **Key fallback:** `LLM_API_KEYS` is parsed in order and assigned stable IDs `key_1`, `key_2`, etc. Each request tries keys in order. Provider 429, quota and 5xx failures move to the next key. Provider details and secrets are never returned to the browser.
- **Failure simulation:** `X-Simulate-Failure` is honored only when `ENABLE_FAILURE_SIMULATION=true`. Example: `key_1=429` skips the first key for that request; `*=429` makes every configured key fail and returns the required 503.
- **Caching:** LiteLLM's disk cache is enabled. A small SQLite response cache also provides deterministic exact-message-list cache behavior and reliable `LLM_CACHE` metadata. Its key hashes the model plus the full system/history/new-message payload. Cached responses reuse the original token counts and have `key_id: null`.
- **SQLite:** Chat messages are persisted in `/data/app.db` in Docker. The schema and index are created automatically on startup. The system prompt is not stored in visible history.
- **Tokens:** `input_tokens` and `output_tokens` are taken from the provider usage object for the current completion, not accumulated across the conversation.

## 4. Verification: how to see failover, cache hits and per-message token usage

### Health

```bash
curl http://localhost:8000/health
```

Expected shape:

```json
{"status":"ok","keys_configured":2,"cache_enabled":true}
```

### Live request

```bash
curl -X POST http://localhost:8000/chat \\
  -H "Content-Type: application/json" \\
  -d '{"session_id":"demo-a","message":"Explain DNS in one sentence."}'
```

Send the identical first message using a **different session ID**. The second request should report `LLM_CACHE`, preserve identical reply/token usage, and have `key_id: null`.

### Failover

```bash
curl -X POST http://localhost:8000/chat \\
  -H "Content-Type: application/json" \\
  -H "X-Simulate-Failure: key_1=429" \\
  -d '{"session_id":"failover-demo","message":"What is an API?"}'
```

With two configured keys, a successful response should identify `key_2` and `fallback_attempts: 1`.

### All providers unavailable

```bash
curl -X POST http://localhost:8000/chat \\
  -H "Content-Type: application/json" \\
  -H "X-Simulate-Failure: *=429" \\
  -d '{"session_id":"failure-demo","message":"Test unavailable providers"}'
```

Expected: HTTP 503 and `{"detail":"all providers unavailable"}`. The failed request is not added to history.

### Session history

```bash
curl http://localhost:8000/sessions/demo-a/messages
```

The endpoint returns oldest-first user and assistant messages. Assistant messages include their metadata.

### Tests

```bash
cd backend
pytest -q
```

## 5. Setup Used

- Editor: add the editor you used.
- AI tools: disclose the AI tools you used while building and testing this project.

## 6. Bonus features (if any)

- Dark, responsive chat interface.
- Optional per-session system prompt.
- New chat and session history retrieval.
- Visible model, cache/live source, latency, per-request tokens, key ID and fallback count.
