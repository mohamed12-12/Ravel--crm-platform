from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from services.data_authority import load_data_authority


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_AGENT_CONVERSATION_PROMPT_FILE = (
    PROJECT_ROOT / "services" / "ai_agent" / "ai_agent_app" / "prompts" / "agent_conversation.md"
)
DEFAULT_GEMINI_AGENT_SYSTEM_PROMPT_FILE = (
    PROJECT_ROOT / "services" / "ai_agent" / "ai_agent_app" / "prompts" / "gemini_agent_system.md"
)


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    lowered = value.strip().lower()
    return lowered in {"1", "true", "yes", "y", "on"}


def _optional_path(raw: str | None) -> Path | None:
    if not raw:
        return None
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate


def _read_text_file(raw: str | None, default_path: Path) -> str:
    candidate = _optional_path(raw) if raw else default_path
    if candidate is None or not candidate.exists():
        return ""
    return candidate.read_text(encoding="utf-8").strip()


def _extract_sheet_id(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    marker = "/spreadsheets/d/"
    if marker in text:
        tail = text.split(marker, 1)[1]
        return tail.split("/", 1)[0].strip()
    return text


@dataclass(frozen=True)
class Settings:
    app_env: str
    app_host: str
    app_port: int
    app_secret_key: str
    log_level: str

    ai_agent_mode: str
    ai_max_tool_rounds: int
    ai_provider: str
    gemini_api_key: str
    gemini_model: str
    ai_agent_system_prompt: str
    openai_api_key: str
    openai_model: str

    data_authority: str
    data_schema_version: str
    crm_access_mode: str
    crm_api_base_url: str
    crm_api_token: str
    sheet_mirror_enabled: bool
    demo_data_mode: bool

    sheet_backend: str
    excel_source_workbook: Path
    excel_runtime_workbook: Path
    google_sheet_id: str
    google_service_account_json: str
    google_application_credentials: str

    meta_verify_token: str
    meta_page_access_token: str
    meta_app_secret: str
    meta_graph_api_version: str
    public_webhook_url: str

    human_handoff_phone: str
    human_handoff_email: str
    admin_alert_webhook_url: str
    demo_reset_on_start: bool
    default_country_code: str
    demo_write_mode: str

    agent_persona_name: str
    agent_conversation_prompt: str
    website_url: str
    post_trip_handoff_enabled: bool
    post_trip_handoff_keywords: str
    post_trip_handoff_responsible_employee: str

    @property
    def ai_enabled(self) -> bool:
        return self.ai_provider == "gemini" and bool(self.gemini_api_key)

    @property
    def gemini_agent_enabled(self) -> bool:
        return self.ai_agent_mode == "gemini" and bool(self.gemini_api_key)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.app_env == "production" and not self.app_secret_key:
            errors.append("APP_SECRET_KEY is required in production.")
        if self.ai_agent_mode not in {"deterministic", "gemini", "tool_calling"}:
            errors.append("AI_AGENT_MODE must be either 'deterministic', 'gemini', or 'tool_calling'.")
        if self.ai_max_tool_rounds <= 0:
            errors.append("AI_MAX_TOOL_ROUNDS must be a positive integer.")
        if self.app_env == "production" and self.ai_agent_mode != "tool_calling":
            errors.append("AI_AGENT_MODE must be 'tool_calling' in production so agent data access uses the CRM API contract.")
        if self.sheet_backend not in {"excel", "google", "google_sheets"}:
            errors.append("SHEET_BACKEND must be either 'excel', 'google', or 'google_sheets'.")
        if self.sheet_backend == "excel" and not self.excel_source_workbook.exists():
            errors.append(f"Source workbook not found: {self.excel_source_workbook}")
        if self.app_port <= 0:
            errors.append("APP_PORT must be a positive integer.")
        if self.sheet_backend in {"google", "google_sheets"}:
            if not self.google_sheet_id:
                errors.append(f"GOOGLE_SHEET_ID is required when SHEET_BACKEND={self.sheet_backend}.")
            creds_path = _optional_path(self.google_application_credentials)
            if creds_path is None or not creds_path.exists():
                errors.append(f"GOOGLE_APPLICATION_CREDENTIALS file was not found for SHEET_BACKEND={self.sheet_backend}.")
        authority = load_data_authority(environment=self.app_env)
        errors.extend(
            authority.validate(
                environment=self.app_env,
                database_url=os.getenv("DATABASE_URL", ""),
                system_db_path=os.getenv("RAHMA_SYSTEM_DB_PATH", ""),
                excel_source_workbook=str(self.excel_source_workbook),
                excel_runtime_workbook=str(self.excel_runtime_workbook),
            )
        )
        return errors


def load_settings() -> Settings:
    _load_env_file(PROJECT_ROOT / ".env")
    app_env = os.getenv("APP_ENV", "development").strip().lower()

    source = os.getenv("EXCEL_SOURCE_WORKBOOK", "archive/source-artifacts/RT - Travelers Database.phase5.ready.xlsx")
    runtime = os.getenv("EXCEL_RUNTIME_WORKBOOK", "archive/source-artifacts/RT - Travelers Database.phase5.demo.xlsx")

    source_path = Path(source)
    if not source_path.is_absolute():
        source_path = PROJECT_ROOT / source_path
    runtime_path = Path(runtime)
    if not runtime_path.is_absolute():
        runtime_path = PROJECT_ROOT / runtime_path

    authority = load_data_authority(environment=app_env)

    return Settings(
        app_env=os.getenv("APP_ENV", "development"),
        app_host=os.getenv("APP_HOST", "127.0.0.1"),
        app_port=int(os.getenv("APP_PORT", "5001")),
        app_secret_key=(
            os.getenv("APP_SECRET_KEY", "").strip()
            or ("rahma-traveler-demo" if app_env != "production" else "")
        ),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        ai_agent_mode=os.getenv("AI_AGENT_MODE", "deterministic").strip().lower() or "deterministic",
        ai_max_tool_rounds=int(os.getenv("AI_MAX_TOOL_ROUNDS", "4")),
        ai_provider=os.getenv("AI_PROVIDER", "gemini").strip().lower(),
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash").strip(),
        ai_agent_system_prompt=(
            os.getenv("AI_AGENT_SYSTEM_PROMPT", "").strip()
            or _read_text_file(
                os.getenv("AI_AGENT_SYSTEM_PROMPT_FILE", "").strip(),
                DEFAULT_GEMINI_AGENT_SYSTEM_PROMPT_FILE,
            )
        ),
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_model=os.getenv("OPENAI_MODEL", "").strip(),
        data_authority=authority.authority,
        data_schema_version=authority.schema_version,
        crm_access_mode=authority.agent_access_mode,
        crm_api_base_url=authority.crm_api_base_url,
        crm_api_token=authority.crm_api_token,
        sheet_mirror_enabled=authority.sheet_mirror_enabled,
        demo_data_mode=authority.demo_data_mode,
        sheet_backend=os.getenv("SHEET_BACKEND", "excel").strip().lower(),
        excel_source_workbook=source_path,
        excel_runtime_workbook=runtime_path,
        google_sheet_id=_extract_sheet_id(os.getenv("GOOGLE_SHEET_ID", "")),
        google_service_account_json=os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip(),
        google_application_credentials=os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip(),
        meta_verify_token=os.getenv("META_VERIFY_TOKEN", "").strip(),
        meta_page_access_token=os.getenv("META_PAGE_ACCESS_TOKEN", "").strip(),
        meta_app_secret=os.getenv("META_APP_SECRET", "").strip(),
        meta_graph_api_version=os.getenv("META_GRAPH_API_VERSION", "").strip(),
        public_webhook_url=os.getenv("PUBLIC_WEBHOOK_URL", "").strip(),
        human_handoff_phone=os.getenv("HUMAN_HANDOFF_PHONE", "").strip(),
        human_handoff_email=os.getenv("HUMAN_HANDOFF_EMAIL", "").strip(),
        admin_alert_webhook_url=os.getenv("ADMIN_ALERT_WEBHOOK_URL", "").strip(),
        demo_reset_on_start=_bool(os.getenv("DEMO_RESET_ON_START"), default=False),
        default_country_code=os.getenv("DEFAULT_COUNTRY_CODE", "20").strip(),
        demo_write_mode=os.getenv("DEMO_WRITE_MODE", "demo").strip().lower(),
        agent_persona_name=os.getenv("AGENT_PERSONA_NAME", "").strip(),
        agent_conversation_prompt=(
            os.getenv("AGENT_CONVERSATION_PROMPT", "").strip()
            or _read_text_file(
                os.getenv("AGENT_CONVERSATION_PROMPT_FILE", "").strip(),
                DEFAULT_AGENT_CONVERSATION_PROMPT_FILE,
            )
        ),
        website_url=os.getenv("WEBSITE_URL", "").strip(),
        post_trip_handoff_enabled=_bool(os.getenv("POST_TRIP_HANDOFF_ENABLED"), default=False),
        post_trip_handoff_keywords=os.getenv("POST_TRIP_HANDOFF_KEYWORDS", "help,support,agent,human,assistance,مساعدة,دعم,انسان,بشر").strip(),
        post_trip_handoff_responsible_employee=os.getenv("POST_TRIP_HANDOFF_RESPONSIBLE_EMPLOYEE", "Operations Team").strip(),
    )
