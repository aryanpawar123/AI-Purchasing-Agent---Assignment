"""
Application configuration loaded from environment variables.

Uses pydantic-settings so every knob is documented in one place
and validated at startup. Defaults match .env.example.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # --- LLM ---
    llm_provider: str = "mock"          # "anthropic" | "openai" | "mock"
    llm_model: str = "claude-sonnet-4-20250514"
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # --- Database ---
    db_path: str = "purchasing_agent.db"

    # --- Policy parameters ---
    z_score: float = 1.65               # service-level z for safety stock
    approval_threshold: float = 50_000  # PO value above this needs human approval (INR)
    max_coverage_days: int = 30         # don't over-order beyond this many days
    review_period_days: int = 1         # replenishment review period
    perishable_coverage_factor: float = 0.8  # max coverage = shelf_life * this factor

    # --- Server ---
    backend_port: int = 8000
    frontend_port: int = 5173

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


# Singleton – import this everywhere
settings = Settings()
