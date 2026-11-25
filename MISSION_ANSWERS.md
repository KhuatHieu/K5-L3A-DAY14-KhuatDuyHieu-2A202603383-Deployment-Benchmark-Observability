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

## Part 2: Docker

Môi trường: Docker Engine 29.10.0, linux/amd64. Mọi image đều build **từ gốc repo** (lưu ý số 4 trong README của lớp K5).

### Exercise 2.1: Dockerfile questions

File: `02-docker/develop/Dockerfile`

1. **Base image:** `python:3.11`, bản Python đầy đủ trên Debian, có sẵn gcc, header và nhiều tool. Vì vậy image rất nặng (đo được 1.66 GB ở Exercise 2.2).
2. **Working directory:** `/app` (`WORKDIR /app`). Các lệnh `COPY`, `RUN` và `CMD` phía sau đều chạy tương đối từ thư mục này, và Docker tự tạo thư mục nếu chưa có.
3. **Tại sao `COPY requirements.txt` trước:** để tận dụng **Docker layer cache**. Mỗi lệnh trong Dockerfile tạo một layer. Khi một layer thay đổi, mọi layer phía sau phải build lại. `requirements.txt` hiếm khi đổi, còn `app.py` đổi liên tục. Đặt `COPY requirements.txt` và `RUN pip install` trước `COPY app.py` thì khi chỉ sửa code, Docker dùng lại layer `pip install` đã cache và chỉ chạy lại bước copy code. Build mất vài giây thay vì cài lại toàn bộ thư viện. Nếu `COPY . .` đứng trước `pip install`, chỉ cần sửa một dòng code là cài lại hết.
4. **CMD vs ENTRYPOINT:**
   - `CMD ["python", "app.py"]` là lệnh **mặc định** và bị ghi đè hoàn toàn khi truyền lệnh khác vào `docker run`. Ví dụ `docker run my-agent:develop sh` chạy `sh` thay cho app.
   - `ENTRYPOINT` là chương trình **cố định** của container. Tham số truyền vào `docker run` (hoặc `CMD`) được **nối thêm** vào sau nó làm argument, và chỉ đổi được bằng `--entrypoint`.
   - Hai lệnh thường đi chung: `ENTRYPOINT ["uvicorn"]` + `CMD ["main:app", "--port", "8000"]`, trong đó `ENTRYPOINT` cố định chương trình còn `CMD` cho tham số mặc định có thể đổi.
   - Cả hai nên viết ở dạng exec (mảng JSON) để process là **PID 1** và nhận trực tiếp SIGTERM. Dạng shell (`CMD python app.py`) chạy qua `/bin/sh -c`, nên signal có thể không tới app.

### Exercise 2.2: Build và run bản develop

```bash
docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .
docker run -d --name p2-dev -p 8000:8000 my-agent:develop
```

```text
$ docker ps
p2-dev  my-agent:develop  Up Less than a second  0.0.0.0:8000->8000/tcp, [::]:8000->8000/tcp

$ curl -i -X POST "http://localhost:8000/ask?question=What%20is%20Docker%3F"
HTTP/1.1 200 OK
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}

$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":0.5,"container":true}
```

Bản develop của Part 2 vẫn nhận `question` qua query string (lưu ý số 3). Debug container:

```text
$ docker exec p2-dev sh -c 'whoami; pwd'
root
/app

$ docker logs p2-dev
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     172.17.0.1:60996 - "POST /ask?question=What%20is%20Docker%3F HTTP/1.1" 200 OK
```

**Quan sát:** container chạy bằng **root** và không có `HEALTHCHECK`. Image size (`docker images my-agent:develop`): **1.66 GB** (`docker image inspect` → `Size = 1 662 983 689` bytes).

### Exercise 2.3: Multi-stage build

File: `02-docker/production/Dockerfile`

- **Stage 1 (`builder`)** dùng `python:3.11-slim`, cài thêm `gcc` và `libpq-dev` (tool để compile các package có phần C), rồi `pip install --user` để mọi thư viện nằm gọn trong `/root/.local`. Stage này **không được deploy**, nó chỉ là nơi build.
- **Stage 2 (`runtime`)** bắt đầu lại từ một `python:3.11-slim` sạch:
  - tạo user `appuser` (non-root)
  - `COPY --from=builder /root/.local` để chỉ lấy thư viện đã cài, cộng với `main.py` và `utils/`
  - `chown` cho `appuser`, rồi `USER appuser`
  - có `HEALTHCHECK` gọi `/health`
  - `CMD uvicorn ... --workers 2`
- **Tại sao image nhỏ hơn:**
  1. Base là `slim` thay vì bản full: bỏ compiler, man page và các thư viện hệ thống không cần.
  2. Mọi thứ chỉ phục vụ việc build (gcc, libpq-dev, apt cache, pip cache) nằm lại ở stage builder và bị bỏ đi. Image cuối chỉ chứa layer của stage `runtime`. Xem `docker history`: layer thư viện được copy sang chỉ nặng **40.2 MB**.
  3. `--no-cache-dir` và `rm -rf /var/lib/apt/lists/*` không để rác trong layer.

```bash
docker build -f 02-docker/production/Dockerfile -t my-agent:advanced .
```

```text
$ docker images my-agent
IMAGE               ID             DISK USAGE   CONTENT SIZE
my-agent:advanced   7560dd3f4e7a        237MB         57.4MB
my-agent:develop    c9ab3dc5790c       1.66GB          426MB
```

| Image | Size (`DISK USAGE`) | Bytes (`docker image inspect`) | Content size (nén) |
|---|---:|---:|---:|
| Develop (`python:3.11`, single-stage) | **1.66 GB** | 1 662 983 689 | 426 MB |
| Production (`python:3.11-slim`, multi-stage) | **237 MB** | 237 385 955 | 57.4 MB |
| **Chênh lệch** | **nhỏ hơn 85.7 %** (≈ 7.0 lần) | | nhỏ hơn 86.5 % |

Docker 29 dùng containerd image store, nên `docker images` hiện hai cột: `DISK USAGE` (dung lượng sau khi giải nén trên đĩa) và `CONTENT SIZE` (dung lượng nén khi push/pull). Phần trăm chênh lệch tính theo `DISK USAGE`: `(1 - 237 385 955 / 1 662 983 689) × 100 = 85.7 %`.

Chạy thử bản production:

```text
$ docker run -d --name p2-prod -p 8000:8000 my-agent:advanced
$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":0.2,"version":"2.0.0","timestamp":"2026-11-25T08:21:49.358028"}

$ curl -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question": "What is Docker?"}'
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}

$ docker exec p2-prod whoami
appuser
$ docker exec p2-prod which gcc
(không có gcc)

$ docker ps   # sau ~30 s, HEALTHCHECK chạy lần đầu
p2-prod  Up 31 seconds (healthy)
```

Log có 2 dòng `Starting agent...` vì `--workers 2` chạy 2 process. Container chạy bằng `appuser`, không có compiler trong image cuối, và Docker tự đánh dấu `healthy`.

### Exercise 2.4: Docker Compose stack

File: `02-docker/production/docker-compose.yml`. Chạy theo lưu ý số 5 và 7 trong README (cần `.env.local`, có đặt `-p`):

```bash
cd 02-docker/production
touch .env.local
docker compose -p k5-day14-docker up -d --build
```

**Architecture diagram:**

```text
                 Host (máy mình)
                       │  :80 (và :443)
                       ▼
 ┌──────────────────── network "internal" (bridge) ────────────────────┐
 │                                                                     │
 │   ┌──────────────────────────┐                                      │
 │   │ nginx  (nginx:alpine)    │  reverse proxy + load balancer       │
 │   │ - rate limit 10 r/s/IP   │  + security headers                  │
 │   │   (burst 20) → 429       │                                      │
 │   └────────────┬─────────────┘                                      │
 │                │ proxy_pass http://agent:8000  (upstream agent_backend)
 │                ▼                                                    │
 │   ┌──────────────────────────┐                                      │
 │   │ agent (build Dockerfile, │  FastAPI, uvicorn 2 workers          │
 │   │  target: runtime)        │  không publish port ra host          │
 │   └──────┬─────────────┬─────┘                                      │
 │          │             │  env REDIS_URL / QDRANT_URL                │
 │          ▼             ▼                                            │
 │   ┌─────────────┐  ┌──────────────────┐                             │
 │   │ redis:7-    │  │ qdrant:v1.9.0    │                             │
 │   │ alpine      │  │ vector DB (RAG)  │                             │
 │   │ cache/rate  │  │ :6333            │                             │
 │   │ limit :6379 │  │                  │                             │
 │   └──────┬──────┘  └────────┬─────────┘                             │
 └──────────┼──────────────────┼───────────────────────────────────────┘
            ▼                  ▼
     volume redis_data   volume qdrant_data
```

**Services được start (4):** `redis`, `qdrant`, `agent`, `nginx`. Thứ tự khởi động do `depends_on` quyết định: `agent` chờ `redis` và `qdrant` **healthy** (`condition: service_healthy`), còn `nginx` chờ `agent` start.

```text
$ docker compose -p k5-day14-docker ps
SERVICE   IMAGE                   STATUS                    PORTS
agent     k5-day14-docker-agent   Up 39 seconds (healthy)   8000/tcp
nginx     nginx:alpine            Up 39 seconds             0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
qdrant    qdrant/qdrant:v1.9.0    Up 50 seconds (healthy)   6333-6334/tcp
redis     redis:7-alpine          Up 50 seconds (healthy)   6379/tcp
```

**Chúng giao tiếp thế nào:**

- Cả 4 service nằm chung bridge network `internal`. Docker có DNS nội bộ, nên các service gọi nhau **bằng tên service** (`agent`, `redis`, `qdrant`) thay vì IP. Kiểm tra từ trong container agent:

  ```text
  $ docker compose -p k5-day14-docker exec -T agent python -c "...socket.create_connection..."
  redis -> 172.20.0.3 port 6379 OK
  qdrant -> 172.20.0.2 port 6333 OK
  ```

- **Chỉ `nginx` publish port ra host** (80, 443). `agent`, `redis` và `qdrant` không có `ports:` nên bên ngoài không gọi thẳng được (`curl localhost:8000` → `Failed to connect`). Mọi traffic đi qua Nginx, là nơi đặt rate limit và security header.
- Nginx dùng `proxy_pass http://agent_backend` với `upstream agent_backend { server agent:8000; }`. Khi scale agent, Nginx round-robin giữa các instance.
- Agent nhận địa chỉ Redis và Qdrant qua env (`REDIS_URL=redis://redis:6379/0`, `QDRANT_URL=http://qdrant:6333`). Secret nằm trong `env_file: .env.local`, file này đã có trong `.gitignore`.
- Dữ liệu Redis và Qdrant nằm trên named volume (`redis_data`, `qdrant_data`), nên container bị xoá thì dữ liệu vẫn còn.

**Test qua Nginx:**

```text
$ curl -i http://localhost/health
HTTP/1.1 200 OK
Server: nginx
Content-Type: application/json
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
X-XSS-Protection: 1; mode=block

{"status":"ok","uptime_seconds":7.8,"version":"2.0.0","timestamp":"2026-11-25T08:24:50.000753"}

$ curl -i -X POST http://localhost/ask -H "Content-Type: application/json" -d '{"question": "Explain microservices"}'
HTTP/1.1 200 OK
{"answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}
```

Header `Server: nginx` (không lộ version do `server_tokens off`) và 3 security header chứng tỏ request đã đi qua Nginx.

Thử rate limit của Nginx: bắn 40 request `POST /ask` song song:

```text
     21 200
     19 429
```

Kết quả khớp cấu hình `rate=10r/s burst=20 nodelay`: 1 request trong hạn mức cộng 20 request burst được đi qua, phần còn lại bị trả **429**. Log nginx: `"POST /ask HTTP/1.1" 429 45`.

Dọn stack: `docker compose -p k5-day14-docker down -v`.

**Sự cố gặp phải khi làm:**

- Cổng 80 trên máy đang bị nginx hệ thống chiếm. Phải dừng service đó trước khi `docker compose up`, nếu không thì container `nginx` lỗi `bind: address already in use`.
- Docker Hub trả `You have reached your unauthenticated pull rate limit` khi pull `redis`, `nginx` và `qdrant`. Cách xử lý: pull từ mirror công khai `mirror.gcr.io` (`docker pull mirror.gcr.io/library/redis:7-alpine` rồi `docker tag ... redis:7-alpine`, tương tự cho 2 image còn lại). Cách khác là `docker login` để có hạn mức cao hơn.

### Checkpoint 2

- [x] Hiểu cấu trúc Dockerfile
- [x] Biết lợi ích của multi-stage builds
- [x] Hiểu Docker Compose orchestration
- [x] Biết cách debug container (`docker logs`, `docker exec`)

---

## AI log

| Part | Hỏi AI gì | Nhận được gì | Tự sửa / tự kiểm tra thế nào |
|---|---|---|---|
| Part 1 | Nhờ Claude Code (Opus 5.5) đọc repo, chạy bản develop và production của Part 1, rồi soạn câu trả lời | Danh sách anti-pattern, bảng so sánh, các lệnh curl và phân tích vì sao log JSON không hiện | Mọi output trong bài lấy từ lệnh chạy thật trên máy (curl, `kill -TERM`, exit code). Nguyên nhân mất log được kiểm chứng bằng cách chạy lại với `OPENAI_API_KEY=dummy` |
| Part 2 | Nhờ Claude Code (Opus 5.5) build 2 image, chạy container và stack compose, trả lời Exercise 2.1–2.4, vẽ sơ đồ | Câu trả lời 2.1, giải thích multi-stage, sơ đồ stack, cách xử lý cổng 80 bị chiếm và Docker Hub rate limit (dùng `mirror.gcr.io`) | Kích thước image lấy từ `docker images` và `docker image inspect` trên máy, % tính từ số bytes. Kết quả 200/429 qua Nginx và kết nối nội bộ redis/qdrant đều từ lệnh chạy thật |
