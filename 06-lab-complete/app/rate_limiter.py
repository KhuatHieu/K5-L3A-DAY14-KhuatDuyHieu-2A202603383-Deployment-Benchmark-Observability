"""
Rate limiting (tiêu chí 7).

  • Mỗi API key tối đa RATE_LIMIT_PER_MINUTE request trong 60 giây (sliding window)
  • Vượt giới hạn → HTTP 429 kèm header Retry-After
  • State lưu trong Redis (sorted set, score = timestamp) để mọi worker / instance
    dùng chung và vẫn còn sau khi restart agent

Toàn bộ "xoá cũ → đếm → thêm" chạy trong MỘT Lua script nên atomic: nhiều request
đồng thời (nhiều worker, nhiều instance) không thể cùng lọt qua khi chỉ còn 1 slot.
"""
import hashlib
import math
import time
import uuid

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

WINDOW_SECONDS = 60

# KEYS[1] = zset key; ARGV = now_ms, window_ms, limit, member
# Trả về {allowed (1/0), số request trong cửa sổ, ms cho tới khi có slot trống}
_SLIDING_WINDOW_LUA = """
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
local count = redis.call('ZCARD', KEYS[1])
if count >= limit then
  local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
  local retry_ms = window
  if oldest[2] then retry_ms = tonumber(oldest[2]) + window - now end
  return {0, count, retry_ms}
end
redis.call('ZADD', KEYS[1], now, ARGV[4])
redis.call('PEXPIRE', KEYS[1], window)
return {1, count + 1, 0}
"""
_sliding_window = r.register_script(_SLIDING_WINDOW_LUA)


def _bucket_key(api_key: str) -> str:
    # Không lưu API key thô trong Redis — chỉ lưu hash
    digest = hashlib.sha256(api_key.encode()).hexdigest()[:16]
    return f"ratelimit:{digest}"


def check_rate_limit(key: str) -> dict:
    """Raise 429 nếu `key` đã dùng hết hạn mức trong 60 giây gần nhất."""
    limit = settings.rate_limit_per_minute
    now_ms = int(time.time() * 1000)
    allowed, used, retry_ms = _sliding_window(
        keys=[_bucket_key(key)],
        args=[now_ms, WINDOW_SECONDS * 1000, limit, f"{now_ms}-{uuid.uuid4().hex}"],
    )
    if not allowed:
        retry_after = max(1, math.ceil(int(retry_ms) / 1000))
        raise HTTPException(
            status_code=429,
            detail={"error": "Rate limit exceeded", "limit": limit,
                    "window_seconds": WINDOW_SECONDS, "retry_after_seconds": retry_after},
            headers={"Retry-After": str(retry_after),
                     "X-RateLimit-Limit": str(limit),
                     "X-RateLimit-Remaining": "0"},
        )
    return {"limit": limit, "remaining": max(0, limit - int(used))}
