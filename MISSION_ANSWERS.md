# Day 14 Lab - Mission Answers

## Part 1: Localhost vs Production

Môi trường chạy: Linux (`os.name == "posix"`), Python 3.12 trong virtualenv `.venv`, `fastapi==0.115.0`, `uvicorn[standard]==0.30.0`.

### Exercise 1.1: Anti-patterns found

File: `01-localhost-vs-production/develop/app.py`

| # | Anti-pattern | Dòng | Hậu quả |
|---|---|---|---|
| 1 | **Hardcode secret**: `OPENAI_API_KEY = "sk-..."` và `DATABASE_URL` chứa user/password `admin:password123` | 17–18 | Push lên GitHub là lộ key. Đổi key phải sửa code và deploy lại. Key nằm vĩnh viễn trong lịch sử git |
| 2 | **Log ra secret**: `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")` | 34 | Ai đọc được log (log aggregator, đồng nghiệp, nhà cung cấp cloud) đều thấy key. Khi chạy thử, log in ra đúng `[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this` |
| 3 | **Dùng `print()` thay cho logging**: không có level, timestamp hay cấu trúc. Log cả nội dung câu hỏi và câu trả lời của user | 33–38 | Không lọc hay tìm theo level/field được, không parse được trong Loki/Datadog. Log nội dung user là rủi ro về quyền riêng tư |
| 4 | **Không có health check** (`/health`, `/ready`) | 42–43 | Platform (Railway/Render/K8s) không biết app còn sống hay đã treo để restart. Load balancer không biết khi nào nên dồn traffic vào. Khi chạy thử, `GET /health` trả **404** |
| 5 | **Port cố định** `port=8000` | 52 | Railway/Render cấp port qua biến `PORT`. Port cứng thì platform không route traffic vào được |
| 6 | **Bind `host="localhost"`** | 51 | Chỉ nhận kết nối từ chính máy đó. Trong container, traffic từ bên ngoài không tới được app |
| 7 | **`reload=True` và `DEBUG = True` cứng trong code** | 21, 53 | Reloader theo dõi file, tốn CPU và chạy thêm một process cha. Bật debug trên production có thể lộ thông tin lỗi. Không tắt được bằng cấu hình |
| 8 | **Không có config management**: `MAX_TOKENS`, `DEBUG` là hằng số trong code | 20–22 | Muốn đổi giữa dev, staging và prod phải sửa code. Vi phạm 12-Factor "Config in environment" |
| 9 | **Không xử lý shutdown**: không có lifespan và không xử lý SIGTERM | — | Khi platform tắt hoặc redeploy container, request đang chạy có thể bị cắt ngang và không có bước dọn dẹp (đóng connection DB…) |
| 10 | **Input không được validate**: `question` nhận qua query string, không giới hạn độ dài, không chặn chuỗi rỗng | 31 | Câu hỏi dài hoặc rỗng vẫn được gửi thẳng tới LLM, gây tốn tiền. Câu hỏi nằm trong URL nên còn bị ghi lại ở access log và proxy |

### Exercise 1.2: Chạy bản develop

```bash
cd 01-localhost-vs-production/develop
pip install -r requirements.txt
python app.py
```

Bản develop nhận `question` qua **query string** (lưu ý số 3 trong README của lớp K5):

```text
$ curl -i -X POST "http://localhost:8000/ask?question=Hello"
HTTP/1.1 200 OK
server: uvicorn
content-type: application/json

{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
```

Gửi JSON như trong `CODE_LAB.md` thì bị 422, vì FastAPI tìm `question` trong query:

```text
$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 422 Unprocessable Entity

{"detail":[{"type":"missing","loc":["query","question"],"msg":"Field required","input":null}]}
```

Không có health check:

```text
$ curl -i http://localhost:8000/health
HTTP/1.1 404 Not Found

{"detail":"Not Found"}
```

Log phía server khi xử lý request, secret bị in ra:

```text
INFO:     Uvicorn running on http://localhost:8000 (Press CTRL+C to quit)
INFO:     Started reloader process [23363] using WatchFiles
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this
[DEBUG] Response: Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận.
INFO:     127.0.0.1:51214 - "POST /ask?question=Hello HTTP/1.1" 200 OK
```

**Quan sát:** app chạy được, nhưng chưa sẵn sàng cho production. Lộ secret, không có `/health`, chỉ nghe ở `localhost:8000`, và đang bật reloader (`Started reloader process`).

### Exercise 1.3: Comparison table

Chạy bản production:

```bash
cd ../production
cp .env.example .env
pip install -r requirements.txt
python app.py
```

```text
$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":0.4,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T08:18:07.228074+00:00"}

$ curl http://localhost:8000/ready
{"ready":true}

$ curl -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question": "Hello"}'
{"question":"Hello","answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic.","model":"gpt-4o-mini"}

$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{}'
HTTP/1.1 422 Unprocessable Entity
{"detail":"question field is required"}
```

Gửi SIGTERM (`kill -TERM <pid>`): process thoát với **exit code 0**, log in đủ chuỗi shutdown:

```text
{"time":"2026-11-25 15:18:16,987","level":"INFO","msg":"{"event": "agent_request", "question_length": 5, "client_ip": "127.0.0.1"}"}
{"time":"2026-11-25 15:18:17,096","level":"INFO","msg":"{"event": "agent_response", "response_length": 66}"}
{"time":"2026-11-25 15:18:17,298","level":"INFO","msg":"Agent shutting down gracefully — finishing in-flight requests..."}
{"time":"2026-11-25 15:18:17,399","level":"INFO","msg":"Shutdown complete"}
```

| Feature | Develop | Production | Tại sao quan trọng? |
|---|---|---|---|
| **Config** | Hardcode trong code (`OPENAI_API_KEY`, `DEBUG`, `MAX_TOKENS`) | Đọc từ biến môi trường qua `config.py` (`Settings` dataclass + `os.getenv`), có `.env.example` làm mẫu | Cùng một code (cùng một image) chạy được ở dev, staging và prod, chỉ khác env. Secret không nằm trong git. Đổi key không cần build lại |
| **Secrets** | Key thật nằm trong code và bị in ra log | Không có secret trong code. Log chỉ ghi `question_length`, không ghi key hay nội dung | Lộ key LLM là mất tiền thật. Log thường được gửi đi nhiều nơi và giữ rất lâu |
| **Validate config** | Không có | `validate()`: thiếu `AGENT_API_KEY` khi `ENVIRONMENT=production` thì `raise ValueError` | Fail fast: app chết ngay lúc khởi động nếu thiếu config, không chạy "trông như bình thường" rồi lỗi ở request đầu tiên |
| **Health check** | Không có (`/health` → 404) | `/health` (liveness: uptime, version) và `/ready` (readiness: 503 khi chưa sẵn sàng) | Platform dùng `/health` để biết khi nào restart container. Load balancer dùng `/ready` để không gửi traffic vào instance đang khởi động hoặc đang tắt |
| **Logging** | `print()` không cấu trúc, có log secret | Log dạng JSON (`time`, `level`, `msg`), mỗi sự kiện có field `event` | Log dạng JSON parse, lọc và đếm được trong log aggregator, ví dụ đếm `agent_request` theo thời gian. Không lộ dữ liệu nhạy cảm |
| **Shutdown** | Đột ngột, không có hook | `lifespan`: startup gán `is_ready=True`, shutdown gán `is_ready=False` rồi chờ request đang chạy. Có handler SIGTERM. Đã test: exit code 0 | Khi redeploy hoặc scale down, platform gửi SIGTERM. Tắt an toàn thì request đang xử lý không bị cắt, connection được đóng sạch |
| **Host / Port** | `localhost:8000` cứng | `HOST` (mặc định `0.0.0.0`) và `PORT` lấy từ env | Trong container phải bind `0.0.0.0` thì bên ngoài mới gọi vào được. Railway/Render cấp `PORT` động |
| **Reload / Debug** | `reload=True` luôn bật | `reload=settings.debug`, mặc định `DEBUG=false` | Reloader chỉ dành cho lúc dev. Trên production nó tốn tài nguyên và thêm một process cha, làm việc nhận signal phức tạp hơn |
| **Input** | Query string, không kiểm tra | Body JSON, câu hỏi rỗng thì trả 422 với thông báo rõ ràng | Báo lỗi rõ cho client và không tốn tiền gọi LLM cho request vô nghĩa |
| **CORS** | Không cấu hình | `ALLOWED_ORIGINS` lấy từ env | Chỉ frontend được phép mới gọi API từ trình duyệt |

### Những gì mình quan sát thêm khi chạy bản production

1. **Log JSON không hiện khi chưa đặt `OPENAI_API_KEY`.** `config.py` được import trước `logging.basicConfig(...)` trong `app.py`. `validate()` gọi `logging.warning(...)`, và vì root logger chưa có handler nên lời gọi này tự cấu hình root với format mặc định và level WARNING. Đến lúc `app.py` gọi `basicConfig` thì root đã có handler nên lời gọi đó không còn tác dụng, và mọi log INFO bị nuốt. Khi chạy với `OPENAI_API_KEY=dummy` thì log JSON hiện đủ (output ở trên). Cách sửa: gọi `basicConfig(..., force=True)`, hoặc cấu hình logging trước khi import `config`.
2. **Dòng log chưa phải JSON hợp lệ.** `msg` là một chuỗi JSON lồng trong chuỗi JSON khác nhưng không được escape (`"msg":"{"event": ...}"`). Một số parser sẽ báo lỗi. Ở Part 6 nên dùng formatter tạo dict rồi `json.dumps` toàn bộ dòng log.
3. **`.env` không được tự nạp.** `requirements.txt` có `python-dotenv` nhưng code không gọi `load_dotenv()`. Biến trong `.env` chỉ có tác dụng nếu tự `export` hoặc dùng `docker run --env-file`. Ngoài ra `APP_NAME=AI Agent` có dấu cách mà không có ngoặc kép, nên `source .env` trong shell bị lỗi.
4. **Handler SIGTERM tự viết chỉ để ghi log.** Sau khi chạy, uvicorn cài handler riêng của nó. Phần tắt an toàn thực chất do uvicorn và `lifespan` đảm nhiệm, còn dòng `Received SIGTERM` in ra sau `Shutdown complete`.

### Checkpoint 1

- [x] Hiểu tại sao hardcode secrets là nguy hiểm
- [x] Biết cách dùng environment variables
- [x] Hiểu vai trò của health check endpoint
- [x] Biết graceful shutdown là gì

---

## AI log

| Part | Hỏi AI gì | Nhận được gì | Tự sửa / tự kiểm tra thế nào |
|---|---|---|---|
| Part 1 | Nhờ Claude Code (Opus 5.5) đọc repo, chạy bản develop và production của Part 1, rồi soạn câu trả lời | Danh sách anti-pattern, bảng so sánh, các lệnh curl và phân tích vì sao log JSON không hiện | Mọi output trong bài lấy từ lệnh chạy thật trên máy (curl, `kill -TERM`, exit code). Nguyên nhân mất log được kiểm chứng bằng cách chạy lại với `OPENAI_API_KEY=dummy` |
