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

## Part 3: Cloud Deployment

> Lớp K5 deploy **agent Part 6** (`06-lab-complete/`) sau khi đạt `PASS 12/12`, không deploy app mẫu trong `03-cloud-deployment/` (xem CHECKPOINTS, CP6). Mục 3.1 dưới đây sẽ được điền sau Part 6.

### Exercise 3.1: Deploy (nền tảng chính)

- Nền tảng: _(điền sau Part 6)_
- URL public: _(điền sau Part 6, chi tiết trong `DEPLOYMENT.md`)_
- Kết quả 4 lệnh tự kiểm ở CP6 (`/health` 200, `/ready` 200, `/ask` không key 401, có key 200): _(điền sau Part 6)_

### Exercise 3.2: So sánh `railway.toml` với `render.yaml`

Files: `03-cloud-deployment/railway/railway.toml` và `03-cloud-deployment/render/render.yaml`.

| Khía cạnh | `railway.toml` (Railway) | `render.yaml` (Render Blueprint) | Vì sao khác |
|---|---|---|---|
| **Định dạng và phạm vi** | TOML, cấu hình **một service** (build + deploy của service chứa file) | YAML, mô tả **cả hạ tầng**: danh sách `services` gồm web service **và** Redis | Railway coi project gồm nhiều service tạo trên dashboard/CLI, mỗi service có config riêng. Render Blueprint là Infrastructure as Code: một file dựng toàn bộ stack |
| **Cách build** | `builder = "NIXPACKS"`: Railway tự đoán ngôn ngữ và cách cài (có thể đổi sang `DOCKERFILE`) | `runtime: python` + `buildCommand: pip install -r requirements.txt` ghi rõ | Railway ưu tiên "zero-config", tự detect. Render yêu cầu khai báo runtime và lệnh build tường minh. Phiên bản Python cũng phải tự ghim qua env `PYTHON_VERSION=3.11.0` |
| **Start command** | `uvicorn app:app --host 0.0.0.0 --port $PORT` | Giống hệt | Cả hai nền tảng đều **inject `PORT` động**, nên app phải bind `0.0.0.0:$PORT` (bài học Part 1) |
| **Health check** | `healthcheckPath = "/health"`, `healthcheckTimeout = 30` | `healthCheckPath: /health` | Cùng mục đích: chỉ chuyển traffic sang bản deploy mới khi `/health` trả 200. Tên khoá khác nhau (`healthcheckPath` và `healthCheckPath`) |
| **Restart khi crash** | Khai báo rõ: `restartPolicyType = "ON_FAILURE"`, `restartPolicyMaxRetries = 3` | Không có trong file, Render tự restart | Railway cho phép chỉnh chính sách restart. Render quản lý việc này, người dùng không cấu hình |
| **Biến môi trường và secret** | **Không có trong file**, đặt bằng `railway variables set ...` hoặc dashboard | Khai báo trong `envVars`: giá trị thường ghi thẳng (`ENVIRONMENT=production`), secret dùng `sync: false` (nhập tay trên dashboard) hoặc `generateValue: true` (Render tự sinh `AGENT_API_KEY` ngẫu nhiên) | Railway tách hẳn secret khỏi file nên không có nguy cơ commit nhầm. Render liệt kê **tên** biến trong file cho đầy đủ, nhưng giá trị secret vẫn không nằm trong git |
| **Database / Redis** | Không khai báo, phải thêm Redis riêng trong project rồi tự trỏ `REDIS_URL` | Khai báo luôn `type: redis` (`agent-cache`, `plan: free`, `maxmemoryPolicy: allkeys-lru`) | Blueprint dựng Redis cùng lúc với app, nhưng file mẫu chưa nối `REDIS_URL` của web service tới Redis. Khi deploy Part 6 phải thêm `fromService` để lấy connection string |
| **Region, gói, tự deploy** | Không có trong file (chọn trên dashboard) | `region: singapore`, `plan: free`, `autoDeploy: true` (push GitHub → tự deploy) | Render đưa nhiều lựa chọn hạ tầng vào code. Railway để trên dashboard. Railway deploy bằng `railway up` từ máy, còn Render kéo code từ GitHub |

**Kết luận:** Railway dễ bắt đầu hơn: tự detect, deploy bằng CLI từ máy, file config ngắn, chỉ lo phần build và deploy. Render "khai báo" hơn: một file mô tả cả app lẫn Redis, region và gói, gắn với GitHub để tự deploy, nên dễ tái tạo và review trong PR. Khi deploy agent Part 6, cả hai đều cần đổi sang build bằng **Dockerfile** (`builder = "DOCKERFILE"`, hoặc `runtime: docker` trên Render), đúng như tiêu chí Deploy config trong `06-lab-complete/README.md`.

### Exercise 3.3: Pipeline CI/CD trên Cloud Run

Files: `03-cloud-deployment/production-cloud-run/cloudbuild.yaml` và `service.yaml`.

1. **Trigger:** mỗi lần push lên `main`, Cloud Build chạy `cloudbuild.yaml`. Các step chạy tuần tự nhờ `waitFor`.
2. **Test:** trong container `python:3.11-slim`, cài `requirements.txt` + `pytest` rồi chạy `pytest tests/`. Test fail thì pipeline dừng, không build.
3. **Build:** `docker build` image, gắn 2 tag `gcr.io/$PROJECT_ID/ai-agent:$COMMIT_SHA` và `:latest`, dùng `--cache-from ...:latest` để tận dụng layer cache. Sau đó **push** cả hai tag lên Container Registry.
4. **Deploy:** `gcloud run deploy ai-agent` với **đúng image theo commit SHA** (rollback được về commit cũ), region `asia-southeast1`, `min-instances=1` (tránh cold start), `max-instances=10`, 512Mi/1 CPU, timeout 60s. Secret `OPENAI_API_KEY` lấy từ **Secret Manager** (`--set-secrets`), không nằm trong code.
5. **`service.yaml`** mô tả cùng service đó theo dạng khai báo (Knative), áp bằng `gcloud run services replace`: autoscale 1–10 instance, mỗi instance tối đa 80 request đồng thời (`containerConcurrency`), secret `OPENAI_API_KEY` và `AGENT_API_KEY` từ Secret Manager, `livenessProbe` gọi `/health` và `startupProbe` gọi `/ready` (cùng hai endpoint đã học ở Part 1).

**Nhận xét thêm:**

- Thư mục `production-cloud-run/` không có `tests/`, `requirements.txt` hay `Dockerfile`. Chạy nguyên trạng thì step `test` fail ngay (pytest không thấy test nào sẽ trả exit code 5), và step `build` cũng không có Dockerfile để build. Pipeline này là mẫu, phải đặt cạnh code thật mới dùng được.
- `service.yaml` dùng image `:latest`, còn `cloudbuild.yaml` deploy theo `:$COMMIT_SHA`. Nên dùng SHA ở cả hai nơi để biết chính xác bản nào đang chạy.

### Checkpoint 3

- [ ] Deploy thành công lên ít nhất 1 platform _(sau Part 6)_
- [ ] Có public URL hoạt động _(sau Part 6)_
- [x] Hiểu cách set environment variables trên cloud
- [ ] Biết cách xem logs _(sau khi deploy)_

---

## Part 4: API Security

Ba lớp bảo vệ, mỗi lớp chặn một loại rủi ro khác nhau:

| Lớp | Chặn cái gì | Mã lỗi |
|---|---|---|
| Authentication (API key / JWT) | Người **không được phép** gọi API | 401 (thiếu/sai), 403 (không đủ quyền) |
| Rate limiting | Gọi **quá nhanh** (spam, bot, vòng lặp lỗi phía client) | 429 + `Retry-After` |
| Cost guard | Tiêu **quá nhiều tiền** (kể cả khi gọi chậm và hợp lệ) | 402 |

Cần **cả hai** rate limit và cost guard. Rate limit giới hạn *tốc độ*: 10 req/phút vẫn là 14 400 req/ngày, và với prompt dài hay model đắt thì vẫn có thể hết hàng trăm đô. Cost guard giới hạn *tổng tiền* theo ngày/tháng nhưng không ngăn được một đợt bắn dồn làm quá tải server. Một bên bảo vệ hạ tầng, một bên bảo vệ ví tiền.

### Exercise 4.1: API key authentication (`04-api-gateway/develop/app.py`)

- **API key được check ở đâu?** Trong dependency `verify_api_key` (dòng 39–54). Hàm này đọc header `X-API-Key` qua `APIKeyHeader(name="X-API-Key", auto_error=False)` và được gắn vào endpoint bằng `_key: str = Depends(verify_api_key)` (dòng 70). Endpoint nào cần bảo vệ chỉ cần thêm `Depends`, còn `/` và `/health` để public (platform cần gọi `/health` mà không có key). Key đúng lấy từ env `AGENT_API_KEY` (dòng 35).
- **Sai key thì sao?** Thiếu header → **401** `Missing API key`. Có header nhưng sai → **403** `Invalid API key.` Request bị chặn trước khi chạm tới LLM.
- **Làm sao rotate key?** Key nằm trong env nên rotate không cần sửa code: đặt giá trị mới cho `AGENT_API_KEY` trên platform (`railway variables set ...`) rồi restart/redeploy. Để không làm gián đoạn client, có thể tạm cho app chấp nhận **2 key** (cũ và mới, ví dụ `AGENT_API_KEYS=old,new`), báo client chuyển sang key mới, rồi xoá key cũ. Lỡ lộ key thì rotate ngay, không chờ.

Chạy với `AGENT_API_KEY=secret-key-123 python app.py`:

```text
$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 401 Unauthorized
content-type: application/json

{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

$ curl -i -X POST http://localhost:8000/ask -H "X-API-Key: wrong-key" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 403 Forbidden
content-type: application/json

{"detail":"Invalid API key."}

$ curl -i -X POST http://localhost:8000/ask -H "X-API-Key: secret-key-123" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 200 OK
content-type: application/json

{"question":"Hello","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}

$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK

{"status":"ok"}
```

**Nhận xét:**

- Bản develop **in key ra stdout** lúc khởi động (`API Key: secret-key-123`, dòng 91) và còn có default `"demo-key-change-in-production"` khi quên đặt env. Quên đặt `AGENT_API_KEY` thì app vẫn chạy với key ai cũng biết. Nên fail fast như `config.validate()` ở Part 1.
- So sánh `api_key != API_KEY` bằng `!=` thông thường. Nên dùng `hmac.compare_digest` để tránh timing attack.
- Sai key trả 403. **Part 6 yêu cầu sai key cũng trả 401**, phải lưu ý khi làm `06-lab-complete/app/auth.py`.

### Exercise 4.2: JWT authentication (`04-api-gateway/production/auth.py`)

**JWT flow:**

1. Client `POST /auth/token` với username/password. `authenticate_user` so với `DEMO_USERS` (sai → 401 `Invalid credentials`).
2. Server `create_token`: tạo payload `{sub, role, iat, exp = now + 60 phút}`, ký **HS256** bằng `JWT_SECRET` (env), trả về `access_token`.
3. Client gửi `Authorization: Bearer <token>` ở mỗi request.
4. `verify_token` (dependency) `jwt.decode` để kiểm tra chữ ký và hạn, rồi lấy `username` và `role` **từ chính token**. Server không cần tra DB hay session (stateless), nên chạy nhiều instance vẫn verify được miễn là dùng chung secret.
   - Không có token → **401** (kèm `WWW-Authenticate: Bearer`)
   - Hết hạn → **401** `Token expired`
   - Bị sửa hoặc giả → **403** `Invalid token`

Chạy `python app.py` trong `production/`, dùng tài khoản `student` / `demo123` (lưu ý số 6):

```text
$ curl -i -X POST http://localhost:8000/auth/token -H "Content-Type: application/json" -d '{"username":"student","password":"wrong"}'
HTTP/1.1 401 Unauthorized
x-content-type-options: nosniff
x-frame-options: DENY

{"detail":"Invalid credentials"}

$ curl -X POST http://localhost:8000/auth/token -H "Content-Type: application/json" -d '{"username":"student","password":"demo123"}'
{"access_token":"eyJ***MASKED***","token_type":"bearer","expires_in_minutes":60,"hint":"Include in header: Authorization: Bearer eyJ***MASKED***"}
```

Payload của token (phần giữa, chỉ là base64, **ai cũng đọc được mà không cần secret**):

```text
{'sub': 'student', 'role': 'user', 'iat': 1795595387, 'exp': 1795598987}
```

Vì vậy không được đưa thông tin nhạy cảm vào JWT. Chữ ký chỉ đảm bảo payload **không bị sửa**, không giấu được nội dung. `exp - iat = 3600` giây, đúng 60 phút.

```text
$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Explain JWT"}'
HTTP/1.1 401 Unauthorized
www-authenticate: Bearer

{"detail":"Authentication required. Include: Authorization: Bearer <token>"}

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer <token đã sửa 1 ký tự chữ ký>" -H "Content-Type: application/json" -d '{"question":"Explain JWT"}'
HTTP/1.1 403 Forbidden

{"detail":"Invalid token."}

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question":""}'
HTTP/1.1 422 Unprocessable Entity

{"detail":[{"type":"string_too_short","loc":["body","question"],"msg":"String should have at least 1 character","input":"","ctx":{"min_length":1}}]}
```

Phân quyền theo role: `student` (role `user`) gọi `/admin/stats` → **403** `{"detail":"Admin only"}`. `teacher` (role `admin`) gọi được:

```text
$ curl http://localhost:8000/admin/stats -H "Authorization: Bearer $ADMIN_TOKEN"
{"total_users":"N/A (in-memory demo)","global_cost_usd":0.000546,"global_budget_usd":10.0}
```

### Exercise 4.3: Rate limiting (`04-api-gateway/production/rate_limiter.py`)

- **Algorithm:** **Sliding window log**. Mỗi user có một `deque` lưu timestamp các request. Mỗi lần check, các timestamp cũ hơn `now - 60s` bị loại khỏi đầu deque. Nếu số còn lại `>= max_requests` thì trả 429, ngược lại thêm `now` vào. Khác *fixed window* (đếm theo từng phút cố định, có thể cho 2× limit ở ranh giới hai phút), cửa sổ ở đây **trượt theo thời gian thực** nên luôn đúng "tối đa N request trong 60 giây bất kỳ". Đây không phải token bucket: không có "token" được nạp dần, và không cho burst.
- **Limit:** user thường (`rate_limiter_user`) **10 req/phút**. Admin (`rate_limiter_admin`) **100 req/phút**.
- **Bypass cho admin:** trong `app.py` dòng 141, `limiter = rate_limiter_admin if role == "admin" else rate_limiter_user`. Role lấy từ JWT (đã ký, client không tự sửa được) nên admin dùng limiter riêng với hạn mức cao hơn. Muốn bỏ hẳn limit cho admin thì bỏ qua `limiter.check()` khi `role == "admin"`, nhưng giữ một limit cao vẫn an toàn hơn (token admin bị lộ vẫn không spam vô hạn được).

Vòng lặp 20 request với token `student` (token đã che):

```text
$ for i in {1..20}; do curl -s -o /dev/null -w "$i %{http_code}\n" -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{\"question\": \"Test $i\"}"; done
1 200
2 200
3 200
4 200
5 200
6 200
7 200
8 200
9 200
10 200
11 429
12 429
13 429
14 429
15 429
16 429
17 429
18 429
19 429
20 429
```

Response đầy đủ khi đã vượt limit:

```text
$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question":"Test 21"}'
HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
x-ratelimit-reset: 1795595449
retry-after: 59
content-type: application/json

{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":59}}
```

Request 1–10 → 200, từ request 11 → 429, đúng 10 req/phút. Header `Retry-After: 59` báo client đợi 59 giây (request cũ nhất rời khỏi cửa sổ). Cùng lúc đó, `teacher` (admin) gọi 20 lần liên tiếp đều 200 (`20 × 200`), vì có hạn mức 100.

**Hạn chế của bản này:** state nằm trong RAM (`self._windows`), nên:

1. Restart là mất, user được reset limit.
2. Chạy 2+ instance hoặc `--workers 2` thì mỗi process đếm riêng, limit thật thành N × 10.

Part 6 phải lưu state trong **Redis**, ví dụ sorted set: `ZREMRANGEBYSCORE` (xoá cũ) → `ZCARD` (đếm) → `ZADD` (thêm) → `EXPIRE`.

### Exercise 4.4: Cost guard

**Bản có sẵn** (`production/cost_guard.py`, in-memory):

- `check_budget(user)` được gọi **trước** khi gọi LLM:
  - global budget ($10/ngày tổng) hết → **503**
  - budget user ($1/ngày) hết → **402**
  - ≥ 80% thì ghi log warning
- `record_usage` được gọi **sau** khi gọi LLM, cộng token và tính tiền theo giá gpt-4o-mini ($0.15/1M input, $0.60/1M output). Record tự reset khi sang ngày mới (so `record.day`).

Thực tế mock rất rẻ: 10 request của `student` tốn **$0.000184** (`/me/usage`), nên muốn thấy 402 qua HTTP phải gửi khoảng 54 000 request. Mình kiểm tra logic 402/503 bằng cách gọi thẳng module với budget nhỏ (không sửa file của đề):

```text
>>> g = CostGuard(daily_budget_usd=0.001); mỗi request 200 input + 300 output tokens
request 1: 200  cost_so_far=$0.00021
request 2: 200  cost_so_far=$0.00042
request 3: 200  cost_so_far=$0.00063
request 4: 200  cost_so_far=$0.00084      (log: User student at 84% budget)
request 5: 200  cost_so_far=$0.00105
request 6: HTTP 402 {'error': 'Daily budget exceeded', 'used_usd': 0.00105, 'budget_usd': 0.001, 'resets_at': 'midnight UTC'}
global budget hết, user khác: HTTP 503 Service temporarily unavailable due to budget limits. Try again tomorrow.
```

Request 5 vẫn được duyệt dù đã vượt nhẹ, vì check làm **trước** khi biết chi phí thật (chi phí ≥ budget mới chặn). Đây là lý do nên **ước tính trước** như bản Redis bên dưới.

**Bản tự cài đặt với Redis** ($10/tháng/user, reset đầu tháng):

```python
from datetime import datetime, timezone
import redis

MONTHLY_BUDGET_USD = 10.0
KEY_TTL_SECONDS = 32 * 24 * 3600  # > 1 tháng, key tháng cũ tự hết hạn

# Kiểm tra + cộng trong MỘT lệnh atomic phía Redis
_RESERVE_LUA = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local cost = tonumber(ARGV[1])
if current + cost > tonumber(ARGV[2]) then
  return 0
end
redis.call('INCRBYFLOAT', KEYS[1], ARGV[1])
redis.call('EXPIRE', KEYS[1], ARGV[3])
return 1
"""

def _month_key(user_id, now=None):
    now = now or datetime.now(timezone.utc)
    return f"budget:{user_id}:{now:%Y-%m}"   # tháng mới → key mới → tự reset

class RedisCostGuard:
    def __init__(self, r: redis.Redis, budget_usd=MONTHLY_BUDGET_USD):
        self.r, self.budget = r, budget_usd
        self._reserve = r.register_script(_RESERVE_LUA)

    def check_budget(self, user_id, estimated_cost, now=None) -> bool:
        """True nếu còn budget (đã giữ chỗ estimated_cost), False nếu vượt."""
        key = _month_key(user_id, now)
        return bool(self._reserve(keys=[key], args=[estimated_cost, self.budget, KEY_TTL_SECONDS]))

    def adjust(self, user_id, delta_usd, now=None):
        """Sau khi gọi LLM: cộng/trừ chênh lệch giữa chi phí thật và ước tính."""
        self.r.incrbyfloat(_month_key(user_id, now), delta_usd)
```

Cách làm:

1. **Key theo tháng** `budget:<user>:<YYYY-MM>`. Sang tháng mới thì key mới bắt đầu từ 0, nên **tự reset** mà không cần cron. `EXPIRE` 32 ngày để key tháng cũ tự bị xoá.
2. **Giữ chỗ trước, điều chỉnh sau:** trước khi gọi LLM, ước tính chi phí (theo độ dài prompt và `max_tokens`) và giữ chỗ. Vượt budget thì endpoint trả **402**. Sau khi có số token thật, `adjust` cộng hoặc trừ phần chênh lệch.
3. **Atomic bằng Lua script:** GET, so sánh và INCRBYFLOAT chạy trong **một** lệnh phía Redis, nên không có request nào chen vào giữa.
4. State nằm trong Redis, dùng chung cho mọi instance và vẫn còn sau khi restart (khác bản in-memory).

Test với Redis thật (`redis:7-alpine` container tạm):

```text
== 1. Tuần tự: mỗi request ước tính $3, budget $10/tháng
  request 1: OK   spent=$3.00
  request 2: OK   spent=$6.00
  request 3: OK   spent=$9.00
  request 4: BLOCK spent=$9.00        ← 9 + 3 > 10 → chặn (endpoint trả 402)
  request 5: BLOCK spent=$9.00
== 2. User khác không bị ảnh hưởng
  bob: True  spent=$3.00
== 3. Reset đầu tháng (key theo tháng)
  30/11: spent=$9.00, thêm $3 -> False
  01/12: spent=$0.00, thêm $3 -> True
  keys: ['budget:carol:2026-11', 'budget:carol:2026-12'], TTL=2764800s
== 4. Điều chỉnh theo chi phí thật sau khi gọi LLM
  dave spent=$0.12                    ← giữ chỗ $0.50, chi phí thật $0.12
== 5. 200 request ĐỒNG THỜI, mỗi cái $0.10 (budget $10 => tối đa 100 được duyệt)
  Lời giải mẫu (GET rồi INCRBYFLOAT): duyệt 126/200, spent=$12.60
  Lua script atomic:                  duyệt 100/200, spent=$10.00
```

**Vì sao không dùng nguyên lời giải mẫu trong CODE_LAB:** lời giải đó `GET` rồi mới `INCRBYFLOAT`, tức là 2 lệnh rời. Khi nhiều request (hoặc nhiều instance) chạy cùng lúc, chúng cùng đọc thấy "còn budget" trước khi có ai kịp cộng, nên tất cả cùng được duyệt (**race condition**). Ở test 5, khi mô phỏng độ trễ mạng 2 ms giữa app và Redis (như khi Redis nằm trên máy khác trên cloud), lời giải mẫu duyệt 126 request và **chi $12.60, vượt budget 26 %**. Bản Lua dừng đúng ở $10.00.

### Nhận xét thêm về bản production

- Middleware `security_headers` cố xoá header `server` nhưng response vẫn có `server: uvicorn`. Header này do uvicorn thêm **sau** middleware của app. Muốn ẩn phải chạy uvicorn với `--no-server-header` (`server_header=False`).
- Response `/ask` có `"budget_remaining_usd": usage.total_cost_usd`, tức là trả **số đã dùng** chứ không phải số còn lại (đặt tên sai field).
- `JWT_SECRET` có default `"super-secret-change-in-production-please"`. Quên đặt env thì ai biết default (có trong repo public) đều tự ký được token admin. Ở production phải bắt buộc đặt và fail fast.
- Rate limit chạy **trước** cost guard. Request bị 429 không tốn tiền LLM, đúng thứ tự.

### Checkpoint 4

- [x] Implement API key authentication
- [x] Hiểu JWT flow
- [x] Implement rate limiting
- [x] Implement cost guard với Redis

---

## AI log

| Part | Hỏi AI gì | Nhận được gì | Tự sửa / tự kiểm tra thế nào |
|---|---|---|---|
| Part 1 | Nhờ Claude Code (Opus 5.5) đọc repo, chạy bản develop và production của Part 1, rồi soạn câu trả lời | Danh sách anti-pattern, bảng so sánh, các lệnh curl và phân tích vì sao log JSON không hiện | Mọi output trong bài lấy từ lệnh chạy thật trên máy (curl, `kill -TERM`, exit code). Nguyên nhân mất log được kiểm chứng bằng cách chạy lại với `OPENAI_API_KEY=dummy` |
| Part 2 | Nhờ Claude Code (Opus 5.5) build 2 image, chạy container và stack compose, trả lời Exercise 2.1–2.4, vẽ sơ đồ | Câu trả lời 2.1, giải thích multi-stage, sơ đồ stack, cách xử lý cổng 80 bị chiếm và Docker Hub rate limit (dùng `mirror.gcr.io`) | Kích thước image lấy từ `docker images` và `docker image inspect` trên máy, % tính từ số bytes. Kết quả 200/429 qua Nginx và kết nối nội bộ redis/qdrant đều từ lệnh chạy thật |
| Part 3 | Nhờ Claude Code (Opus 5.5) so sánh `railway.toml` với `render.yaml` và mô tả pipeline Cloud Run | Bảng so sánh và mô tả 5 bước pipeline, kèm nhận xét pipeline mẫu thiếu `tests/` và `Dockerfile` | Đối chiếu từng dòng với file cấu hình trong repo (`ls production-cloud-run/` xác nhận không có `tests/` và `Dockerfile`). Phần deploy làm sau Part 6 |
| Part 4 | Nhờ Claude Code (Opus 5.5) chạy bản develop/production, viết script test 401/403/422/200/429, cài đặt cost guard bằng Redis cho Exercise 4.4 | Output các mã HTTP (token đã che), cài đặt `RedisCostGuard` dùng Lua script, test so sánh với lời giải mẫu | Mọi output từ server chạy thật trên máy. Cost guard Redis test với container `redis:7-alpine` thật. Test race condition có thêm độ trễ 2 ms giả lập cho lệnh `GET` (ghi rõ trong bài) |
