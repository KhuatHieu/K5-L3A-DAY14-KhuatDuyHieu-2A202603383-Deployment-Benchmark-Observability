"""
Config — 12-Factor: mọi cấu hình đọc từ biến môi trường.

Không có secret nào nằm trong code: AGENT_API_KEY bắt buộc đặt qua env
(trên Railway/Render là biến môi trường của service, local là file .env).
"""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

# Chỉ để tiện khi chạy local: nạp .env nếu có, KHÔNG ghi đè biến đã đặt sẵn
# (trên cloud / trong Docker, biến môi trường do platform cấp luôn được ưu tiên).
load_dotenv(override=False)


def _env_float(name: str, default: str) -> float:
    return float(os.getenv(name, default))


@dataclass(frozen=True)
class Settings:
    port: int = field(default_factory=lambda: int(os.getenv("PORT", "8000")))
    environment: str = field(default_factory=lambda: os.getenv("ENVIRONMENT", "development"))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())

    # Security — không có giá trị mặc định: thiếu key thì mọi request /ask bị 401
    agent_api_key: str = field(default_factory=lambda: os.getenv("AGENT_API_KEY", ""))

    # State dùng chung (rate limit, cost) nằm trong Redis
    redis_url: str = field(default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    redis_timeout_seconds: float = field(default_factory=lambda: _env_float("REDIS_TIMEOUT_SECONDS", "1.0"))

    # Rate limit: số request tối đa / 60 giây cho mỗi API key
    rate_limit_per_minute: int = field(default_factory=lambda: int(os.getenv("RATE_LIMIT_PER_MINUTE", "10")))

    # Cost guard: 0 = không giới hạn theo kỳ đó
    daily_budget_usd: float = field(default_factory=lambda: _env_float("DAILY_BUDGET_USD", "1.0"))
    monthly_budget_usd: float = field(default_factory=lambda: _env_float("MONTHLY_BUDGET_USD", "10.0"))

    def validate(self) -> "Settings":
        """Fail fast: production mà thiếu API key thì không cho khởi động."""
        if self.environment == "production" and not self.agent_api_key:
            raise RuntimeError("AGENT_API_KEY must be set when ENVIRONMENT=production")
        if self.rate_limit_per_minute < 1:
            raise RuntimeError("RATE_LIMIT_PER_MINUTE must be >= 1")
        return self


settings = Settings().validate()
