"""Central configuration. Every secret comes from environment variables - nothing is hard-coded."""
from __future__ import annotations

import os
import secrets
import threading
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/

_thread = threading.local()


def force_offline(active: bool) -> None:
    """Make the *current thread* run the agents on their deterministic fallbacks (no LLM calls).
    The sample-traffic loader uses it; every other thread keeps using the configured LLM."""
    _thread.offline = active


def _load_dotenv() -> None:
    """Tiny .env loader so we don't need python-dotenv. Real env vars always win."""
    for candidate in (BASE_DIR / ".env", BASE_DIR.parent / ".env"):
        if not candidate.exists():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _bool(name: str, default: bool = False) -> bool:
    return _env(name, str(default)).lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    # --- core ---
    app_name: str = "Cadral Ops Agent"
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'app.db'}"))
    # No secret defaults in code: if unset, a random per-process secret is generated (sessions reset on restart)
    jwt_secret: str = field(default_factory=lambda: _env("JWT_SECRET") or secrets.token_urlsafe(48))
    jwt_expiry_hours: int = field(default_factory=lambda: int(_env("JWT_EXPIRY_HOURS", "12")))
    cors_origins: str = field(default_factory=lambda: _env("CORS_ORIGINS", "*"))
    # Render sets RENDER_EXTERNAL_URL for every web service, so links in alerts and reports work there without setup
    public_base_url: str = field(default_factory=lambda: _env("PUBLIC_BASE_URL", _env("RENDER_EXTERNAL_URL", "http://localhost:8000")))

    # --- LLM (OpenAI-compatible; Groq free tier by default) ---
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "groq"))  # groq | gemini | openai | mock
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY", _env("GROQ_API_KEY", "")))
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL", ""))
    # Groq retired llama-3.3-70b-versatile / llama-3.1-8b-instant for free and developer keys on 2026-08-16;
    # these are the replacements its deprecation notice names (both support tool use and JSON mode).
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "openai/gpt-oss-120b"))
    llm_fast_model: str = field(default_factory=lambda: _env("LLM_FAST_MODEL", "openai/gpt-oss-20b"))
    llm_timeout: float = field(default_factory=lambda: float(_env("LLM_TIMEOUT", "45")))
    # gpt-oss models reason before answering; "low" keeps that short, which matters on the free tier's 8K tokens/min
    llm_reasoning_effort: str = field(default_factory=lambda: _env("LLM_REASONING_EFFORT", "low"))

    # Optional fine-tuned classifier endpoint (e.g. HF Inference / Ollama running the LoRA-merged model)
    classifier_url: str = field(default_factory=lambda: _env("CLASSIFIER_URL", ""))

    # --- RAG ---
    embedding_model: str = field(default_factory=lambda: _env("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"))
    reranker_model: str = field(default_factory=lambda: _env("RERANKER_MODEL", "Xenova/ms-marco-MiniLM-L-6-v2"))
    rag_backend: str = field(default_factory=lambda: _env("RAG_BACKEND", "auto"))  # auto | lite (no ONNX models)
    rag_min_score: float = field(default_factory=lambda: float(_env("RAG_MIN_SCORE", "0.12")))
    rag_heuristic_min_score: float = field(default_factory=lambda: float(_env("RAG_HEURISTIC_MIN_SCORE", "0.45")))
    docs_dir: Path = BASE_DIR / "data" / "docs"
    index_dir: Path = BASE_DIR / "data" / "index"

    # --- Human-in-the-loop ---
    # "auto" = send replies immediately (still reviewable); "review" = hold every reply for approval
    reply_mode: str = field(default_factory=lambda: _env("REPLY_MODE", "auto"))
    refund_auto_limit_gbp: float = field(default_factory=lambda: float(_env("REFUND_AUTO_LIMIT_GBP", "150")))

    # --- Channels / notifications ---
    slack_webhook_url: str = field(default_factory=lambda: _env("SLACK_WEBHOOK_URL"))
    telegram_bot_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    telegram_webhook_secret: str = field(default_factory=lambda: _env("TELEGRAM_WEBHOOK_SECRET", ""))
    telegram_polling: bool = field(default_factory=lambda: _bool("TELEGRAM_POLLING", False))

    smtp_host: str = field(default_factory=lambda: _env("SMTP_HOST"))
    smtp_port: int = field(default_factory=lambda: int(_env("SMTP_PORT", "587")))
    smtp_user: str = field(default_factory=lambda: _env("SMTP_USER"))
    smtp_password: str = field(default_factory=lambda: _env("SMTP_PASSWORD"))
    smtp_from: str = field(default_factory=lambda: _env("SMTP_FROM", _env("SMTP_USER")))
    imap_host: str = field(default_factory=lambda: _env("IMAP_HOST"))
    imap_user: str = field(default_factory=lambda: _env("IMAP_USER", _env("SMTP_USER")))
    imap_password: str = field(default_factory=lambda: _env("IMAP_PASSWORD", _env("SMTP_PASSWORD")))
    imap_poll_seconds: int = field(default_factory=lambda: int(_env("IMAP_POLL_SECONDS", "60")))
    inbound_email_secret: str = field(default_factory=lambda: _env("INBOUND_EMAIL_SECRET", ""))
    report_recipients: str = field(default_factory=lambda: _env("REPORT_RECIPIENTS"))
    report_cron_hour: int = field(default_factory=lambda: int(_env("REPORT_CRON_HOUR", "8")))

    # --- Seed users (only used on first boot) ---
    admin_email: str = field(default_factory=lambda: _env("ADMIN_EMAIL", "admin@demo.co.uk"))
    admin_password: str = field(default_factory=lambda: _env("ADMIN_PASSWORD"))  # unset -> random, printed once
    agent_email: str = field(default_factory=lambda: _env("AGENT_EMAIL", "agent@demo.co.uk"))
    agent_password: str = field(default_factory=lambda: _env("AGENT_PASSWORD"))

    enable_scheduler: bool = field(default_factory=lambda: _bool("ENABLE_SCHEDULER", True))
    demo_seed_on_boot: str = field(default_factory=lambda: _env("DEMO_SEED_ON_BOOT", "auto"))  # true | false | auto
    # The dashboard's "Run evaluation" button starts a second Python process; turn it off on very small hosts.
    allow_eval_run: bool = field(default_factory=lambda: _bool("ALLOW_EVAL_RUN", True))

    @property
    def resolved_llm_base_url(self) -> str:
        if self.llm_base_url:
            return self.llm_base_url.rstrip("/")
        return {
            "groq": "https://api.groq.com/openai/v1",
            "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
            "openai": "https://api.openai.com/v1",
            "openrouter": "https://openrouter.ai/api/v1",
        }.get(self.llm_provider, "")

    @property
    def llm_enabled(self) -> bool:
        return not getattr(_thread, "offline", False) and self.llm_provider != "mock" and bool(self.llm_api_key)

    @property
    def should_seed_demo(self) -> bool:
        """Load sample traffic on first boot? "auto" says yes only on throwaway demo hosts (Hugging Face Spaces,
        Render) that use the default SQLite file - never when a real database is configured."""
        value = self.demo_seed_on_boot.strip().lower()
        if value in {"1", "true", "yes", "on"}:
            return True
        demo_host = bool(os.environ.get("SPACE_ID") or os.environ.get("RENDER"))
        return value == "auto" and demo_host and not os.environ.get("DATABASE_URL")


settings = Settings()
