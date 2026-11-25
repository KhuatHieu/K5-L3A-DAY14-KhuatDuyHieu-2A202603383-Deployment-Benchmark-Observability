"""
Kết nối Redis dùng chung cho rate limiter và cost guard.

  • Đọc REDIS_URL từ config
  • App vẫn KHỞI ĐỘNG ĐƯỢC khi Redis chưa sẵn sàng: redis.from_url() chỉ tạo
    connection pool, chưa mở kết nối nào cho tới lệnh đầu tiên
  • Timeout ngắn để /ready trả 503 nhanh thay vì treo khi Redis chết
"""
import redis

from app.config import settings

r: redis.Redis = redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=settings.redis_timeout_seconds,
    socket_timeout=settings.redis_timeout_seconds,
    health_check_interval=30,
)


def redis_healthy() -> bool:
    """Dùng cho GET /ready: True nếu PING được Redis."""
    try:
        return bool(r.ping())
    except redis.RedisError:
        return False


def close() -> None:
    """Đóng các kết nối trong pool khi shutdown."""
    try:
        r.close()
    except redis.RedisError:
        pass
