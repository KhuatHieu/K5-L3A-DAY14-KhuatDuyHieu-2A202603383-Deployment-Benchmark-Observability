# Deployment Information

## Public URL

https://agent-production-11dbcbd4.up.railway.app

## Platform

**Railway**. Project `k5-day14-agent`, environment `production`, gồm 2 service:

| Service | Nguồn | Vai trò |
|---|---|---|
| `agent` | `06-lab-complete/` (build bằng `Dockerfile`, cấu hình `railway.toml`) | AI agent, FastAPI + uvicorn 2 worker |
| `Redis` | Railway Redis (database template) | State dùng chung: rate limit, cost guard |

Agent nối tới Redis qua **private network** của Railway (`redis.railway.internal:6379`). Redis không mở ra Internet.

Cách deploy (chạy trong `06-lab-complete/`):

```bash
railway init --name k5-day14-agent
railway add --database redis
railway add --service agent
railway variable set --service agent --skip-deploys \
  AGENT_API_KEY=<random> 'REDIS_URL=${{Redis.REDIS_URL}}' \
  ENVIRONMENT=production RATE_LIMIT_PER_MINUTE=10 DAILY_BUDGET_USD=1.0 MONTHLY_BUDGET_USD=10.0 LOG_LEVEL=INFO
railway up --service agent --detach
railway domain --service agent
```

`railway.toml`: `builder = "DOCKERFILE"`, `healthcheckPath = "/health"`. Railway chỉ chuyển traffic sang bản deploy mới khi `/health` trả 200. Railway tự cấp `PORT=8080` và `CMD` của Dockerfile lắng nghe theo `${PORT:-8000}`:

```text
INFO:     Uvicorn running on http://0.0.0.0:8080 (Press CTRL+C to quit)
```

## Test Commands

Đặt biến trước khi chạy (key lấy trên Railway, **không** ghi vào repo):

```bash
BASE=https://agent-production-11dbcbd4.up.railway.app
AGENT_API_KEY=<key trong Railway → agent → Variables>
```

4 lệnh tự kiểm (CP6). Mỗi lệnh chỉ in mã HTTP nên không lộ key:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200
```

Kết quả (2026-11-25):

```text
200
200
401
200
```

### Health Check

```bash
curl https://agent-production-11dbcbd4.up.railway.app/health
# {"status":"ok","version":"1.0.0","environment":"production","uptime_seconds":32.6}

curl https://agent-production-11dbcbd4.up.railway.app/ready
# {"ready":true}          ← agent kết nối được Redis trên Railway
```

### API Test (with authentication)

```bash
curl -X POST https://agent-production-11dbcbd4.up.railway.app/ask \
  -H "X-API-Key: $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "test", "question": "What is deployment?"}'
# {"question":"What is deployment?","answer":"Deployment là quá trình đưa code từ máy bạn lên server để người khác dùng được.","usage":{"input_tokens":4,"output_tokens":19,"cost_usd":1.2e-05,"rate_limit_remaining":8}}
```

### Rate limit trên cloud (`RATE_LIMIT_PER_MINUTE=10`)

12 request liên tiếp (2 request trước đó đã dùng 2 slot trong cửa sổ 60 s):

```text
200 200 200 200 200 200 200 200 429 429 429 429
```

### Script chấm với URL public

```text
$ python check_production_ready.py --public-url https://agent-production-11dbcbd4.up.railway.app
✅ Public URL: GET https://agent-production-11dbcbd4.up.railway.app/health → 200
```

## Environment Variables Set

Đặt trên Railway (service `agent` → Variables). **Không** có trong code hay trong git:

| Biến | Giá trị | Ghi chú |
|---|---|---|
| `AGENT_API_KEY` | *(secret, 32 byte ngẫu nhiên)* | Sinh bằng `secrets.token_urlsafe(32)`, chỉ lưu trên Railway |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` | Reference variable: Railway tự điền connection string của service Redis (có mật khẩu) |
| `ENVIRONMENT` | `production` | Bật fail-fast khi thiếu key, tắt `/docs` |
| `RATE_LIMIT_PER_MINUTE` | `10` | |
| `DAILY_BUDGET_USD` | `1.0` | |
| `MONTHLY_BUDGET_USD` | `10.0` | |
| `LOG_LEVEL` | `INFO` | |
| `PORT` | *(Railway tự cấp: 8080)* | Không tự đặt |

## Logs

```bash
railway logs --service agent --lines 100
```

Log JSON của app được Railway tự parse thành các trường có cấu trúc, lọc được theo `event`, `status`, `path`…:

```text
2026-11-25T08:58:44.849Z [INFO]  ts="2026-11-25T08:58:44.848+00:00" logger="agent" event="startup" version="1.0.0" env="production" port=8080 rate_limit_per_minute=10 daily_budget_usd=1 monthly_budget_usd=10
```

## Screenshots

- `screenshots/dashboard.png`: Railway dashboard của project (2 service `agent` + `Redis`)
- `screenshots/running.png`: deployment `agent` ở trạng thái Active/Success
- `screenshots/test.png`: terminal chạy 4 lệnh tự kiểm (200 / 200 / 401 / 200)
