
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from typing import Optional

import aiosqlite
import litellm
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from litellm import Cache


# Configuration

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")

if os.getenv("VERCEL"):
    DB_PATH = "/tmp/lyftr_app.db"
else:
    DB_PATH = DATABASE_URL.replace("sqlite:////", "/", 1).replace(
        "sqlite:///", "", 1
    )

os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)


LLM_API_KEYS = [
    key.strip()
    for key in os.getenv("LLM_API_KEYS", "").split(",")
    if key.strip()
]

LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-4o-mini")
LLM_API_BASE = os.getenv("LLM_API_BASE") or None

ENABLE_FAILURE_SIMULATION = (
    os.getenv("ENABLE_FAILURE_SIMULATION", "false").lower() == "true"
)

DEFAULT_SYSTEM_PROMPT = os.getenv(
    "DEFAULT_SYSTEM_PROMPT",
    "You are a helpful, accurate assistant. Use the conversation history when answering."
)


# LiteLLM built-in cache
litellm.cache = Cache(
    type="disk",
    disk_cache_dir=os.getenv("LITELLM_CACHE_DIR", "/tmp/litellm_cache")
)
litellm.set_verbose = False


# FastAPI application
app = FastAPI(
    title="Lyftr AI Chatbot",
    version="1.0.0"
)

origins = [
    origin.strip()
    for origin in os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:3000"
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"]
)


# Request model
class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)
    system_prompt: Optional[str] = None

    @field_validator("message")
    @classmethod
    def message_not_blank(cls, value):
        if not value.strip():
            raise ValueError("message must not be empty")
        return value.strip()

    @field_validator("session_id")
    @classmethod
    def session_not_blank(cls, value):
        if not value.strip():
            raise ValueError("session_id must not be empty")
        return value.strip()


# Database initialization
@app.on_event("startup")
async def startup():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT,
                created_at TEXT NOT NULL
            )
        """)

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_session
            ON messages(session_id, id)
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS response_cache (
                cache_key TEXT PRIMARY KEY,
                reply TEXT NOT NULL,
                model TEXT NOT NULL,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL
            )
        """)

        await db.commit()

    if len(LLM_API_KEYS) < 2:
        print("WARNING: Fewer than two LLM API keys configured.")


# Helper functions
def cache_fingerprint(model: str, messages: list[dict]) -> str:
    raw = json.dumps(
        {"model": model, "messages": messages},
        sort_keys=True,
        ensure_ascii=False
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def simulated_status(header: Optional[str], key_id: str):
    if not ENABLE_FAILURE_SIMULATION or not header:
        return None

    for pair in header.split(","):
        if "=" not in pair:
            continue

        target, status = pair.strip().split("=", 1)

        if target.strip() in ("*", key_id):
            try:
                code = int(status.strip())
                if code in (429, 500, 502, 503):
                    return code
            except ValueError:
                return None

    return None


def usage_value(usage, field):
    try:
        return int(getattr(usage, field, 0) or 0)
    except Exception:
        return 0


# Health endpoint
@app.get("/health")
async def health():
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("SELECT 1")

        db_ok = True

    except Exception as exc:
        print(f"Health database error: {exc}")
        db_ok = False

    if not db_ok:
        raise HTTPException(
            status_code=503,
            detail="database unavailable"
        )

    return {
        "status": "ok",
        "keys_configured": len(LLM_API_KEYS),
        "cache_enabled": True
    }


# Chat endpoint
@app.post("/chat")
async def chat(
    body: ChatRequest,
    x_simulate_failure: Optional[str] = Header(default=None)
):
    started = time.perf_counter()

    if not LLM_API_KEYS:
        raise HTTPException(
            status_code=503,
            detail="all providers unavailable"
        )

    async with aiosqlite.connect(DB_PATH) as db:

        # Load previous conversation
        cur = await db.execute(
            """
            SELECT role, content
            FROM messages
            WHERE session_id=?
            ORDER BY id ASC
            """,
            (body.session_id,)
        )

        history_rows = await cur.fetchall()

        system_prompt = body.system_prompt or DEFAULT_SYSTEM_PROMPT

        messages = [
            {"role": "system", "content": system_prompt}
        ]

        messages.extend(
            {"role": role, "content": content}
            for role, content in history_rows
        )

        messages.append({
            "role": "user",
            "content": body.message
        })

        fingerprint = cache_fingerprint(LLM_MODEL, messages)

        # Check application response cache
        cur = await db.execute(
            """
            SELECT reply, model, input_tokens, output_tokens
            FROM response_cache
            WHERE cache_key=?
            """,
            (fingerprint,)
        )

        cached = await cur.fetchone()

        if cached:
            reply, model, input_tokens, output_tokens = cached

            latency_ms = max(
                0,
                int((time.perf_counter() - started) * 1000)
            )

            metadata = {
                "resolution_source": "LLM_CACHE",
                "model": model,
                "latency_ms": latency_ms,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "key_id": None,
                "fallback_attempts": 0
            }

        else:
            reply = None
            metadata = None

            # Try configured API keys in sequence
            for index, api_key in enumerate(LLM_API_KEYS, start=1):

                key_id = f"key_{index}"

                simulated = simulated_status(
                    x_simulate_failure,
                    key_id
                )

                if simulated:
                    print(
                        f"Simulated HTTP {simulated} failure for {key_id}"
                    )
                    continue

                try:
                    kwargs = {
                        "model": LLM_MODEL,
                        "messages": messages,
                        "api_key": api_key,
                        "timeout": 45,
                        "caching": True
                    }

                    if LLM_API_BASE:
                        kwargs["api_base"] = LLM_API_BASE

                    response = await litellm.acompletion(**kwargs)

                    response_text = response.choices[0].message.content

                    if isinstance(response_text, list):
                        response_text = " ".join(
                            str(item.get("text", ""))
                            for item in response_text
                            if isinstance(item, dict)
                        )

                    reply = str(response_text or "").strip()

                    if not reply:
                        raise RuntimeError("Empty model response")

                    usage = getattr(response, "usage", None)

                    input_tokens = usage_value(
                        usage,
                        "prompt_tokens"
                    )

                    output_tokens = usage_value(
                        usage,
                        "completion_tokens"
                    )

                    latency_ms = max(
                        0,
                        int((time.perf_counter() - started) * 1000)
                    )

                    metadata = {
                        "resolution_source": "LIVE_INFERENCE",
                        "model": LLM_MODEL,
                        "latency_ms": latency_ms,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "key_id": key_id,
                        "fallback_attempts": index - 1
                    }

                    await db.execute(
                        """
                        INSERT OR REPLACE INTO response_cache
                        (cache_key, reply, model, input_tokens, output_tokens)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            fingerprint,
                            reply,
                            LLM_MODEL,
                            input_tokens,
                            output_tokens
                        )
                    )

                    break

                except Exception as exc:
                    # Detailed provider error for debugging
                    print(
                        f"Provider attempt {key_id} failed: "
                        f"{type(exc).__name__}: {str(exc)}"
                    )

                    status = getattr(exc, "status_code", None)

                    if status is None:
                        status = getattr(
                            getattr(exc, "response", None),
                            "status_code",
                            None
                        )

                    error_message = str(exc).lower()

                    retryable = (
                        status in (429, 500, 502, 503)
                        or any(
                            term in error_message
                            for term in (
                                "quota",
                                "rate limit",
                                "429",
                                "500",
                                "502",
                                "503"
                            )
                        )
                    )

                    if not retryable:
                        break

                    continue

            if reply is None or metadata is None:
                await db.rollback()

                raise HTTPException(
                    status_code=503,
                    detail="all providers unavailable"
                )

        # Save conversation messages
        now = datetime.now(timezone.utc).isoformat()

        await db.execute(
            """
            INSERT INTO messages
            (session_id, role, content, metadata, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                body.session_id,
                "user",
                body.message,
                None,
                now
            )
        )

        await db.execute(
            """
            INSERT INTO messages
            (session_id, role, content, metadata, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                body.session_id,
                "assistant",
                reply,
                json.dumps(metadata),
                datetime.now(timezone.utc).isoformat()
            )
        )

        await db.commit()

    return {
        "session_id": body.session_id,
        "reply": reply,
        "metadata": metadata
    }


# Conversation history endpoint
@app.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str):

    async with aiosqlite.connect(DB_PATH) as db:

        exists = await (
            await db.execute(
                """
                SELECT 1
                FROM messages
                WHERE session_id=?
                LIMIT 1
                """,
                (session_id,)
            )
        ).fetchone()

        if not exists:
            raise HTTPException(
                status_code=404,
                detail="session not found"
            )

        rows = await (
            await db.execute(
                """
                SELECT role, content, metadata
                FROM messages
                WHERE session_id=?
                ORDER BY id ASC
                """,
                (session_id,)
            )
        ).fetchall()

    result = []

    for role, content, metadata in rows:
        item = {
            "role": role,
            "content": content
        }

        if role == "assistant" and metadata:
            item["metadata"] = json.loads(metadata)

        result.append(item)

    return {
        "session_id": session_id,
        "messages": result
    }
