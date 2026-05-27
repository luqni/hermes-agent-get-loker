import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    LARAVEL_API_URL: str = os.getenv("LARAVEL_API_URL", "http://localhost:8000/api")
    HERMES_WEBHOOK_TOKEN: str = os.getenv("HERMES_WEBHOOK_TOKEN", "super-secret-hermes-token-123")
    SCRAPE_INTERVAL_MINUTES: int = int(os.getenv("SCRAPE_INTERVAL_MINUTES", "30"))
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    # --- TAMBAHKAN BARIS INI ---
    AI_PROVIDER: str = os.getenv("AI_PROVIDER", "gemini") 

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()
