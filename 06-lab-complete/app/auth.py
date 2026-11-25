"""
API Key authentication (tiêu chí 6).

  • Client gửi header  X-API-Key: <key>
  • Key phải trùng biến môi trường AGENT_API_KEY
  • Thiếu hoặc sai key → HTTP 401 (không phải 403 hay 422)
"""
import hmac

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from app.config import settings

# auto_error=False: tự trả 401 thay vì 403 mặc định của FastAPI khi thiếu header
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

_UNAUTHORIZED = {"WWW-Authenticate": "ApiKey"}


def verify_api_key(api_key: str | None = Security(_api_key_header)) -> str:
    """Dependency cho FastAPI — dùng trong /ask: Depends(verify_api_key)."""
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key (header X-API-Key)",
                            headers=_UNAUTHORIZED)
    expected = settings.agent_api_key
    # compare_digest: thời gian so sánh không phụ thuộc vị trí ký tự sai (chống timing attack).
    # Server chưa cấu hình key → từ chối tất cả, không bao giờ "mở cửa".
    if not expected or not hmac.compare_digest(api_key.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Invalid API key", headers=_UNAUTHORIZED)
    return api_key
