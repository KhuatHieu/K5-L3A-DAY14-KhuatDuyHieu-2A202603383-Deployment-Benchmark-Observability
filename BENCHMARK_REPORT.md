# Benchmark & Observability Report — Day 14

> Mọi con số lấy từ report JSON / CSV / screenshot thật trong repo, không ước lượng.
> JSON của `load_test.py` và `run_eval.py`: `reports/` (bản gốc ở `06-lab-complete/benchmark-observability/develop/reports/`).

**Họ tên:** Khuat Duy Hieu — 2A202603383
**Ngày chạy:** 2026-11-25
**Môi trường:** Laptop Intel Core Ultra 7 155H (22 luồng), 15 GB RAM, Linux 6.18 (CachyOS), Docker Engine 29.10.0, Python 3.12.15. Agent develop chạy trực tiếp bằng Python (1 process uvicorn), load generator chạy trên cùng máy.

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < **500** ms | Khi không có tail latency, LLM mock mất khoảng 150–220 ms (p95 = 214.3 ms ở `MOCK_SLOW_RATE=0`). 500 ms chừa hơn 2× headroom cho tải cao và jitter, mà vẫn đủ nhanh cho chat tương tác. Đặt theo **p95** chứ không theo avg, vì avg che mất nhóm user bị chậm (xem 2.2) |
| Error rate `/ask` | < **2** % | Mock có `MOCK_ERROR_RATE=0.01` (1 % lỗi upstream, giống 429/503 của OpenAI). Ngưỡng 2 % chấp nhận nhiễu tự nhiên của provider nhưng báo động khi tỉ lệ lỗi gấp đôi bình thường |
| Accuracy trên golden dataset | ≥ **85** % | Đây là ngưỡng gate trong CI (`--min-accuracy 0.85`), kèm điều kiện **không có câu nào regression** so với baseline. Bản v2 sau khi sửa KB đạt 100 %. 85 % cho phép thêm câu khó vào dataset mà không chặn mọi PR, nhưng chặn được các lỗi lớn như quay về v1 (4.3 %) |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

### 2.1 Concurrency vs latency (agent v2)

Lệnh: `python load_test.py --url http://localhost:8002 --requests 300 --concurrency <c> --out reports/load_v2_c<c>.json` (agent v2, cấu hình mặc định: `MOCK_SLOW_RATE=0.03`, `MOCK_ERROR_RATE=0.01`).

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 4.33 | 187.8 | 219.5 | 1672.3 | 1.33 % (4/300) | ✅ PASS |
| 10 | 38.3 | 187.1 | 219.0 | 1703.3 | 2.00 % (6/300) | ✅ PASS |
| 50 | 111.36 | 191.0 | 231.7 | 1691.7 | 1.00 % (3/300) | ✅ PASS |
| 100 | 140.67 | 201.0 | 282.5 | 1696.1 | 1.33 % (4/300) | ✅ PASS |

Nguồn: `reports/load_v2_c1.json`, `load_v2_c10.json`, `load_v2_c50.json`, `load_v2_c100.json`. Mọi lỗi đều là HTTP 503 do mock giả lập lỗi upstream.

**Nhận xét:** RPS tăng **gần tuyến tính từ 1 lên 10** (4.33 → 38.3, gấp 8.8 lần khi concurrency gấp 10), sau đó **tăng chậm lại**: concurrency gấp 5 (10 → 50) chỉ cho RPS gấp 2.9, và gấp 2 tiếp (50 → 100) chỉ cho gấp 1.26.

- Mỗi request chủ yếu là **chờ** (`asyncio.sleep` mô phỏng LLM ~190 ms), không tốn CPU. Event loop async xử lý nhiều request chờ song song, nên ở concurrency thấp: RPS ≈ concurrency / latency (10 / 0.19 s ≈ 52; đo được 38 vì có tail latency 1.7 s kéo dài một số "slot").
- Ở concurrency cao, phần **CPU** (parse JSON, Pydantic, middleware, Prometheus metrics, log JSON) của **một process Python duy nhất** bắt đầu nghẽn. p50 tăng từ 187 lên 201 ms, p95 từ 219 lên 282.5 ms: request phải xếp hàng chờ event loop. Thêm nữa, mỗi lần chạy chỉ có 300 request, nên ở c=100 phần khởi động và kết thúc (lúc chưa đủ / không còn đủ 100 request song song) chiếm tỉ lệ lớn trong 2.13 s.
- p99 ~1.7 s ở **mọi** mức concurrency: đây không phải do tải mà do 3 % request bị chậm giả lập (`MOCK_SLOW_RATE=0.03`, +1500 ms).
- Muốn tăng tiếp: chạy nhiều worker / instance (Phần 5). Nút thắt ở đây là CPU của 1 process, không phải LLM.

### 2.2 Tail latency

Lệnh: khởi động lại v2 với `MOCK_SLOW_RATE=<r>`, rồi `python load_test.py --url http://localhost:8002 --requests 300 --concurrency 20 --out reports/load_v2_slow_<r>.json`.

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 188.2 | 214.3 | 219.2 |
| 0.03 (mặc định) | 224.7 | 220.6 | 1692.1 |
| 0.1 | 338.5 | 1690.6 | 1712.5 |

Nguồn: `reports/load_v2_slow_0.json`, `load_v2_slow_0.03.json`, `load_v2_slow_0.1.json`. SLO p95 ≤ 500 ms: PASS / PASS / **FAIL** (ở 0.1).

**Nhận xét:** **p99 nhạy nhất**, rồi đến p95. Avg kém nhạy nhất.

- **0 → 0.03:** avg chỉ tăng **36.5 ms** (+19 %) và p95 gần như không đổi (+6.3 ms), nhưng **p99 nhảy từ 219 lên 1692 ms (gấp 7.7 lần)**. 3 % request chậm nằm hoàn toàn trên ngưỡng 95 % nên p95 không thấy, chỉ p99 bắt được.
- **0.03 → 0.1:** khi > 5 % request chậm, **p95 cũng nhảy lên 1690.6 ms** và SLO FAIL, trong khi avg mới là 338.5 ms (+150 ms so với 0). Nhìn avg ta tưởng "chậm hơn một chút", nhưng thực tế cứ 10 user thì 1 người chờ hơn 1.7 s.
- **Vì sao SLO không nên đặt theo average:** average trộn số đông nhanh với số ít rất chậm thành một số "trông ổn", nên che mất đúng nhóm user có trải nghiệm tệ nhất. Average cũng bị kéo bởi outlier theo kiểu khó diễn giải. Percentile trả lời trực tiếp câu hỏi "X % user chờ không quá bao lâu", và vi phạm được phát hiện ngay khi nhóm chậm vượt ngưỡng (p95 FAIL ở 10 %, p99 đã báo động từ 3 %). Ngoài ra, RPS cũng giảm từ 102.87 xuống 47.85 khi slow rate tăng (với concurrency cố định 20, các slot bị request chậm giữ lâu hơn), nên tail latency còn làm giảm cả throughput.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3 % (1/23) | 91.3 % (21/23) | **100 % (23/23)** |
| Avg tokens / câu | 57.4 | 108.8 | 116.7 |
| Tổng cost (USD) | 0.000409 | 0.000758 | 0.000817 |
| Cost / câu đúng (USD) | 0.00040875 | 0.00003607 | 0.00003553 |
| Latency p50 / p95 (ms) | 156.3 / 187.0 | 178.7 / 1526.6 | 186.2 / 216.8 |
| Quality gate | ❌ FAIL (exit 1) | ✅ PASS (exit 0) | ✅ PASS (exit 0, `--min-accuracy 1.0`, 0 regression) |

Nguồn: `reports/eval_v1.json`, `reports/eval_v2.json` (chạy với `--baseline reports/eval_v1.json`: 20 câu cải thiện, 0 regression), `reports/eval_v2_fixed.json` (chạy với `--min-accuracy 1.0 --baseline reports/eval_v2.json`: 2 câu cải thiện, 0 regression). Gate của v1/v2 dùng ngưỡng mặc định 80 %.

**Những câu v2 trả lời sai ban đầu và cách bạn sửa:**

| ID | Keyword bị thiếu | Bạn đã sửa gì |
|---|---|---|
| `security-02` ("JWT khác gì API key trong xác thực?") | `token`, `hết hạn`, `chữ ký` | KB không có tài liệu nào khớp `jwt`, nên v2 không retrieve được gì và trả câu generic. Thêm entry trigger `["jwt", "json web token"]`: so sánh API key (chuỗi cố định, không hết hạn) với JWT (token có **chữ ký** số, chứa user/role/thời điểm **hết hạn**, server chỉ cần xác minh chữ ký) |
| `scaling-03` ("Kubernetes HPA scale dựa trên những chỉ số nào?") | `cpu`, `memory`, `custom metrics` | Thêm entry trigger `["hpa", "autoscal", "kubernetes"]`: HPA tăng giảm số pod theo **CPU**, **memory** hoặc **custom metrics** (RPS, độ dài hàng đợi, latency từ Prometheus) |

Trước khi thêm, mình kiểm tra các trigger mới chỉ khớp đúng câu cần sửa trong golden dataset (`jwt` → chỉ `security-02`; `hpa`, `kubernetes` → chỉ `scaling-03`), để retrieval top-1 của các câu khác không bị "cướp" (nguyên nhân thường gặp gây regression). Kết quả: 0 regression. Sửa ở `develop/utils/mock_llm.py` và chép y hệt sang `production/utils/mock_llm.py`. Không sửa `golden_dataset.jsonl`.

**Quality gate trong CI:** `.github/workflows/eval-quality-gate.yml` chạy ở mỗi PR đụng tới `benchmark-observability/`: bật agent v2 (`MOCK_ERROR_RATE=0` để không flaky) rồi chạy `python eval/run_eval.py --min-accuracy 0.85`. `run_eval.py` kết thúc bằng `raise SystemExit(0 if gate_ok else 1)`, với `gate_ok = accuracy >= min_accuracy and not regressions`. Exit code 1 làm step fail, job fail; đặt job này làm *required status check* cho `main` (branch protection) thì PR không merge được. Đã kiểm chứng trên máy:

```text
$ python eval/run_eval.py --url http://localhost:8001 --min-accuracy 0.85   # v1
exit code = 1        ← CI fail, chặn merge
$ python eval/run_eval.py --url http://localhost:8002 --min-accuracy 0.85   # v2
exit code = 0        ← CI pass
```

**Nhận xét về trade-off chất lượng / chi phí / latency giữa v1 và v2:**

- **Chất lượng:** v2 có retrieval (RAG) nên accuracy tăng từ 4.3 % lên 91.3 %, rồi 100 % sau khi bổ sung KB. v1 chỉ đúng 1 câu nhờ trùng keyword `deploy`.
- **Chi phí:** v2 tốn **~1.9 lần token mỗi câu** (108.8 so với 57.4), vì prompt chứa thêm tài liệu được retrieve và câu trả lời dài hơn. Tổng cost cũng gần gấp đôi ($0.000758 so với $0.000409). Nhưng **cost / câu đúng rẻ hơn ~11 lần** ($0.000036 so với $0.000409), vì v1 trả tiền cho 22 câu trả lời vô dụng. Khi so hai phiên bản, "cost / câu đúng" là thước đo đúng hơn "cost / câu": một agent rẻ nhưng sai thì vô giá trị.
- **Latency:** v2 thêm bước retrieval (~20 ms) và prompt dài hơn (+0.2 ms/token), nên p50 tăng ~22–30 ms (156 → 179 → 186 ms), chấp nhận được với SLO 500 ms. p95 của `eval_v2.json` (1526.6 ms) cao là do 2/23 câu (`docker-01`, `bench-01`) rơi vào tail latency ngẫu nhiên (`MOCK_SLOW_RATE=0.03`), không phải do v2 chậm hơn về bản chất: lần chạy `eval_v2_fixed` p95 chỉ 216.8 ms. Với 23 mẫu, p95 eval rất nhiễu, nên latency phải đo bằng load test (Phần 2) chứ không bằng eval.

---


## 4. Observability stack (production)

Stack chạy bằng `docker compose -p k5-day14-observability ...` trong `06-lab-complete/benchmark-observability/production/`: agent ×2 (mặc định) + Nginx + Prometheus + Grafana + Jaeger + Locust. Tải nền cho mục 4: Locust headless **30 user, ramp 5 user/s** (`docker compose run -d --no-deps locust ... -u 30 -r 5`) chạy suốt các thí nghiệm. Unit test alert rules: `promtool test rules /p/alert_rules_test.yml` → `SUCCESS`.

> **Sửa lỗi của đề để stack chạy được:** `production/Dockerfile` cài thư viện vào `/root/.local` rồi copy sang `/home/agent/.local`, nhưng user `agent` được tạo với `HOME=/app` (`useradd -d /app`), nên Python tìm user site-packages ở `/app/.local` và container crash loop với `ModuleNotFoundError: No module named 'uvicorn'`. Đã thêm `ENV PYTHONUSERBASE=/home/agent/.local` (giống Dockerfile Part 5).

### 4.1 Screenshot

- [x] Dashboard Grafana khi đang có tải (thấy rõ Golden Signals): [`screenshots/grafana-dashboard.png`](screenshots/grafana-dashboard.png) (16:32, 2 instance, 30 user). Ảnh thêm lúc đang chạy thí nghiệm A + D: [`screenshots/grafana-chaos-A-D.png`](screenshots/grafana-chaos-A-D.png) (Error rate 20.17 % đỏ, Instances up = 1)
- [x] Một trace chậm trong Jaeger (thấy span nào chiếm thời gian): [`screenshots/jaeger-slow-trace.png`](screenshots/jaeger-slow-trace.png)
- [x] Một trace lỗi trong Jaeger (có event `exception`): [`screenshots/jaeger-error-trace.png`](screenshots/jaeger-error-trace.png). Span `chat mock-llm-small` có `error.type=upstream_unavailable`, event `exception` với `exception.type=utils.mock_llm.LLMError`, `exception.message=Upstream LLM error: 503 Service Unavailable (mock)` và stacktrace
- [x] Trang Alerts của Prometheus với ít nhất 1 alert đang **firing**: [`screenshots/prometheus-alert-firing.png`](screenshots/prometheus-alert-firing.png). **3 alert firing cùng lúc**: `AgentHighErrorRate` (thí nghiệm A, value 0.19), `AgentInstanceDown` (thí nghiệm D, instance `172.20.0.6:8000`) và `LLMCostBurnRateHigh`

Ảnh chụp bằng Chrome headless điều khiển qua DevTools Protocol trên chính máy chạy stack (bấm mở span / alert bằng click chuột thật).

**Đọc dashboard lúc có tải (Exercise 7.4).** Số từ Prometheus lúc 16:31:29, cùng PromQL với các panel; ảnh Grafana chụp lúc 16:32:

| Golden signal | Giá trị |
|---|---|
| Traffic `/ask` | 18.53 req/s (stat panel trong ảnh lúc 16:32: 14.0 req/s, vì Locust tạo tải dao động) |
| Error rate `/ask` | 1.08 % (ảnh: 0.53 %): toàn bộ là 503 giả lập `MOCK_ERROR_RATE=0.01` |
| Latency `/ask` | p50 172.3 ms · **avg 240.0 ms** · **p95 294.7 ms** (ảnh: 290.64 ms) · p99 1858.5 ms |
| Saturation | in-flight = 3 request |
| Requests/s theo instance | `172.20.0.6:8000` = 10.24 · `172.20.0.8:8000` = 10.24 |
| Tokens/s | input 1436.2 · output 757.8 |

**Câu hỏi Exercise 7.4:**

1. **p95 = 294.7 ms, avg = 240.0 ms.** Phân phối lệch phải. Phần lớn request nằm quanh p50 = 172 ms, nhưng ~3 % request bị "slow" thêm 1.5 s (p99 = 1.86 s). Nhóm nhỏ này kéo avg lên 240 ms, **cao hơn ~70 ms so với request điển hình** (p50), trong khi avg vẫn thấp hơn p95. Nghĩa là avg không mô tả đúng ai cả: user bình thường nhận ~170 ms, còn user xui nhận ~1.8 s. Khoảng cách avg ↔ p50 lớn, cộng p99 ≫ p95, là dấu hiệu của **tail latency**: phải giám sát bằng percentile.
2. **Có, Nginx chia đều:** 10.24 và 10.24 req/s cho 2 instance (round-robin trong `upstream agent_cluster`). `server agent:8000 resolve` để Nginx hỏi lại DNS mỗi 10 s, nên khi scale 1 → 3 instance, panel cũng thấy traffic chia 3 (xem 5). Khi 1 instance bị pause (thí nghiệm D), panel cho thấy instance đó về 0 và instance còn lại gánh toàn bộ.
3. **Input tokens/s (1436) > output tokens/s (758)**, gấp ~1.9 lần: prompt gửi LLM gồm system prompt + **tài liệu retrieve được (RAG)** + câu hỏi (≈ 95 token trong trace mẫu), còn câu trả lời chỉ ≈ 55 token. Agent v2 "trả giá" bằng input token để đổi lấy độ chính xác. Lưu ý: panel Tokens/s là biểu đồ **stacked**, nên đường trên cùng là input + output, không phải output.
4. **`curl http://localhost:8080/metrics` → `HTTP/1.1 404 Not Found`.** `nginx.conf` có `location = /metrics { return 404; }`. Metrics lộ thông tin nội bộ (route, lưu lượng, model, chi phí, phiên bản), nên không được public qua cổng vào duy nhất. Prometheus vẫn scrape được vì nó gọi **thẳng** `agent:8000/metrics` trong mạng Docker nội bộ (`dns_sd_configs` tên `agent`), không đi qua Nginx. Kiểm chứng: từ container prometheus, `wget -qO- http://agent:8000/metrics` trả về `http_requests_total{method="POST",route="/ask",status="200"} 18094.0`.

### 4.2 Thí nghiệm "phá hệ thống"

Cách làm: ghi `.env` (giá trị mặc định + 1 thay đổi), `docker compose -p k5-day14-observability up -d --scale agent=2 agent` để tạo lại agent. Tính giờ từ lúc agent mới chạy, theo dõi `GET /api/v1/alerts` mỗi 5 s (D: tính từ lúc `docker pause`). Locust 30 user chạy suốt.

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` (value 0.2038 ≈ 20 %) | **85 s** (pending sau 25 s, rồi `for: 1m`). Đổi 16:38:58 → firing 16:40:23 | **Error rate** (stat đỏ 20.17 %), *Requests/s theo status code* (503 tăng), *LLM calls theo kết quả* (error tăng) |
| B | `MOCK_SLOW_RATE=0.3` | `AgentHighLatencyP95` (value 1.92 s) | **135 s** (pending sau 15 s, rồi `for: 2m`). 16:42:15 → 16:44:30 | **Latency /ask — p50/p95/p99**: p95 1.92 s, trong khi avg chỉ 0.66 s |
| C | `LLM_MODEL=mock-llm-large` | `LLMCostBurnRateHigh` (value $15.74/h lúc firing) | **130 s** (pending sau 10 s, rồi `for: 2m`). 16:44:44 → 16:46:54 | **Cost burn rate (USD/giờ)**: từ $1.79/h tăng dần lên $15.9/h và còn tăng (cửa sổ `rate[5m]` lấp dần). *LLM latency p95 theo model* thêm series `mock-llm-large`; p50 /ask 0.17 → 0.41 s |
| D | `docker pause` 1 instance (`k5-day14-observability-agent-1`) | `AgentInstanceDown` (`instance=172.20.0.6:8000`) | **40 s** (pending sau 10 s, rồi `for: 30s`). 16:40:32 → 16:41:12 | **Instances up** (2 → 1), *Requests/s theo instance* (1 đường về 0). Panel Error rate của agent **không** đổi vì 504 xảy ra ở Nginx |
| E | `AGENT_VERSION=v1` | **Không có** (theo dõi 300 s) | — | *Retrieval hit ratio* mất dữ liệu (v1 không retrieve), *Tokens/s* giảm (input 1216 → ~690/s), bảng *Phiên bản đang chạy* đổi sang v1. Latency và cost còn **đẹp hơn**: p95 0.198 s, cost $1.1/h |

**Quan sát thêm:**

- **Báo động giả bị chặn nhờ `for`.** Mỗi lần `docker compose up -d` tạo lại agent, `AgentInstanceDown` lên **pending** 5–10 s (target cũ biến mất, target mới chưa scrape) rồi tự về inactive, không bao giờ firing vì `for: 30s`.
- **`LLMCostBurnRateHigh` firing ngay ở trạng thái bình thường.** Từ 16:20, trong benchmark 100 user, đến 30 user chi phí vẫn khoảng $2.2–2.4/h > ngưỡng $2/h. Ngưỡng tuyệt đối này quá thấp so với tải của lab: một alert kêu liên tục sẽ bị bỏ qua (alert fatigue). Alert chỉ về inactive trong B (request chậm nên throughput và chi phí giảm xuống $1.8/h), nhờ vậy thí nghiệm C đo được thời gian firing từ trạng thái inactive. Production nên đặt ngưỡng theo ngân sách thật (vd budget tháng / 720 h) và theo dõi **chi phí / request** thay vì tổng tuyệt đối.
- **D: lỗi 504 không hiện trên dashboard agent.** Từ lúc pause, log Nginx ghi `1018 × 200, 238 × 503, **29 × 504**` (`upstream timed out ... while reading response header`). 504 sinh ra ở Nginx khi chờ quá `proxy_read_timeout 10s` một instance đang bị pause. Agent chưa bao giờ xử lý xong request đó nên **không có metric nào của agent đếm nó**: panel Error rate chỉ thấy 503 của thí nghiệm A. Metric chỉ phản ánh những gì *chính service đó* thấy, nên production cần giám sát cả tầng load balancer (metric Nginx / ingress) hoặc synthetic check từ bên ngoài. Dùng `docker pause` chứ không `docker stop`: container stop biến mất khỏi DNS, Prometheus coi như target không tồn tại (không phải `up == 0`), nên sẽ không có alert.

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

Đổi sang v1 là **sự cố chất lượng**, không phải sự cố hạ tầng. Agent vẫn trả 200, nhanh hơn, rẻ hơn, nên mọi Golden Signal đều xanh, thậm chí "tốt lên". Kiểm chứng trong lúc E chạy, eval golden dataset trực tiếp lên stack (qua Nginx):

```text
$ python eval/run_eval.py --url http://localhost:8080 --api-key day14-lab-key --min-accuracy 0.85
  EVAL REPORT — agent v1
  Accuracy      : 4.3%  (1/23)
  Quality gate (accuracy ≥ 85%, không regression): ❌ FAIL
eval exit code = 1
```

Cách phát hiện:

1. **Chặn trước khi deploy:** quality gate eval trong CI (`.github/workflows/eval-quality-gate.yml`, exit code 1 khi accuracy < 85 % hoặc có regression so với baseline). Bản v1 không bao giờ merge được.
2. **Giám sát chất lượng sau deploy:** chạy eval định kỳ (synthetic, vd mỗi 15 phút) lên production và xuất `agent_eval_accuracy` thành metric, alert khi < 85 %. Đây chính là "SLO chất lượng" ở mục 1.
3. **Proxy metric có sẵn:** alert khi *Retrieval hit ratio* giảm mạnh hoặc mất dữ liệu, và khi `agent_info{version}` đổi ngoài quy trình release. Thêm tín hiệu từ người dùng thật (thumbs-down rate, tỉ lệ hỏi lại) và LLM-as-judge chấm mẫu ngẫu nhiên traffic thật.

### 4.3 Debug bằng trace

Chọn trace chậm nhất trong 5 phút có tải (trong số instance còn chạy, để log còn tồn tại; xem ghi chú bên dưới):

- `trace_id`: `fc96cc9c63f98d710f3a45401c8e7fe5`
- Tổng thời gian: **1724.1 ms** (span gốc `POST /ask`, HTTP 200)
- Span chiếm nhiều thời gian nhất: **`chat mock-llm-small` (1695.5 ms, 98.3 %)**. Đây là lời gọi LLM rơi vào tail latency (+1500 ms giả lập). `agent.retrieve_context` chỉ 25.3 ms (1.5 %), nên **không** phải nút thắt. Attributes của span LLM: `gen_ai.request.model=mock-llm-small`, `gen_ai.usage.input_tokens=95`, `gen_ai.usage.output_tokens=55`.

| Span | ms | % |
|---|---:|---:|
| `POST /ask` | 1724.1 | 100.0 |
| └ `agent.run` | 1721.1 | 99.8 |
| &nbsp;&nbsp;├ `agent.retrieve_context` | 25.3 | 1.5 |
| &nbsp;&nbsp;└ `chat mock-llm-small` | 1695.5 | 98.3 |

- Dòng log tương ứng (`docker compose -p k5-day14-observability logs agent | grep fc96cc9c63f98d710f3a45401c8e7fe5`):

```json
{"ts": "2026-11-25T09:31:13.970949+00:00", "level": "INFO", "service": "observable-agent", "instance": "d2d5d40c5c3f", "event": "llm_call", "trace_id": "fc96cc9c63f98d710f3a45401c8e7fe5", "span_id": "2ab12fcdc61d71fe", "request_id": "a29e1e6ebbdb", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 95, "output_tokens": 55, "cost_usd": 4.7e-05, "llm_ms": 1695.8}
{"ts": "2026-11-25T09:31:13.971515+00:00", "level": "INFO", "service": "observable-agent", "instance": "d2d5d40c5c3f", "event": "http_request", "trace_id": "fc96cc9c63f98d710f3a45401c8e7fe5", "span_id": "2ab12fcdc61d71fe", "request_id": "a29e1e6ebbdb", "method": "POST", "route": "/ask", "status": 200, "ms": 1723.0}
```

`llm_ms=1695.8` trong log khớp span LLM 1695.5 ms, và `ms=1723.0` khớp span gốc 1724.1 ms. Log cho biết *chuyện gì* xảy ra, trace cho biết *thời gian nằm ở đâu*, và `trace_id` nối hai thứ.

**Trace lỗi** (ảnh `jaeger-error-trace.png`): `trace_id` `0f10a3eaadeb05a1620ff7bf82162ba8`, 102.9 ms, `POST /ask` → 503. Span `chat mock-llm-small` (78.6 ms) có status ERROR và event `exception`. Log tương ứng:

```json
{"ts": "2026-11-25T09:31:23.712883+00:00", "level": "INFO", "service": "observable-agent", "instance": "62d1ea929bf2", "event": "llm_error", "trace_id": "0f10a3eaadeb05a1620ff7bf82162ba8", "span_id": "0123670b336ce355", "request_id": "b2a0adefc0cc", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
```

Ghi chú: trace chậm nhất *tuyệt đối* trong cửa sổ (`2b149fd8...`, 1768.5 ms, span LLM 1691.8 ms = 95.7 %) thuộc instance thứ 3 của benchmark `scale_3`. Container đó đã bị xoá khi scale về 2, nên `docker compose logs` không còn log của nó: **log nằm trong container mất theo container**. Trace vẫn còn vì đã được gửi sang Jaeger. Production cần gom log tập trung (Loki / Elasticsearch / Cloud Logging).

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

Lệnh (thêm `--no-deps`, xem lưu ý):

```bash
docker compose -p k5-day14-observability up -d --scale agent=1      # rồi agent=3
docker compose -p k5-day14-observability run --rm --no-deps locust -f /mnt/locust/locustfile.py \
  --host http://nginx --headless -u 100 -r 10 -t 2m --csv /mnt/results/scale_1   # scale_3
```

Số dòng `POST /ask` trong `benchmark-results/scale_1_stats.csv` và `scale_3_stats.csv`:

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 60.02 | 190 | 230 | 1700 | 81 / 7149 (1.13 %): 503 giả lập |
| 3 | 59.99 | 190 | 220 | 1700 | 74 / 7146 (1.04 %): 503 giả lập |

(Dòng `Aggregated` gồm cả `/health`: 65.62 và 66.02 req/s.) Số instance được kiểm tra bằng Prometheus `count(up{job="agent"}==1)` ngay trước và sau mỗi lần chạy: 1 → 1 và 3 → 3.

> **Lưu ý quan trọng:** lần chạy đầu dùng đúng lệnh mẫu (không có `--no-deps`) và cho kết quả **sai**. `docker compose run locust` tự đưa service `agent` về `deploy.replicas: 2` của file compose, nên đã **bật thêm** agent-2 lúc bắt đầu `scale_1` (Prometheus: `instances up = 2` từ 16:19:08) và **tắt bớt** agent-3 lúc chạy `scale_3` (16:21:23). Cả hai lần thực chất đều là 2 instance. Đã chạy lại cả hai với `--no-deps`; CSV nộp là của lần chạy đúng (16:23:57–16:28:27).

**Giải thích kết quả:** Scale có cải thiện p95 không? Nút thắt nằm ở đâu?

**Không.** 1 và 3 instance cho RPS (60.0), p50 (190 ms), p95 (230 → 220 ms) và p99 (1700 ms) gần như y hệt; chênh 10 ms ở p95 nằm trong độ phân giải của Locust (làm tròn 10 ms). Lý do: **hệ thống chưa bao giờ bị bão hoà, nên không có gì để scale giải quyết**:

1. **Tải bị giới hạn bởi chính Locust (closed-loop).** 100 user, mỗi user hỏi xong nghỉ `between(0.5, 2)` s (trung bình 1.25 s). Throughput tối đa ≈ 100 / (1.25 s + ~0.24 s thời gian phản hồi) ≈ **67 req/s**, khớp gần đúng với 65.6–66.0 req/s (tổng, gồm `/health`) đo được. Thêm instance không làm user hỏi nhanh hơn.
2. **1 instance còn dư rất nhiều tài nguyên.** CPU của agent khi chạy `scale_1` là `rate(process_cpu_seconds_total[1m])` = **0.127 core**, so với giới hạn 0.5 core; với 3 instance mỗi cái ~0.056 core. In-flight đo lúc 16:25:30 chỉ 17 request, mà agent async xử lý song song được vì phần lớn thời gian là chờ LLM.
3. **Latency do LLM quyết định, không do hàng đợi.** p50 ≈ 190 ms là thời gian gọi LLM mock (120 ms + jitter + 0.2 ms/token). p99 = 1.7 s là 3 % request chậm giả lập (`MOCK_SLOW_RATE=0.03`). Cả hai là đặc tính của upstream, scale ngang agent không thay đổi được.

Nút thắt nằm ở **độ trễ của LLM upstream** (và tail latency của nó), không phải CPU hay số instance. Scale chỉ có ích khi một instance bắt đầu bão hoà (CPU gần 0.5 core, in-flight tăng, p95 tăng theo tải, như ở `load_test.py` c=100 trong mục 2.1 với 1 process). Muốn thấy hiệu quả của scale phải tăng user hoặc giảm `wait_time` tới mức 1 instance quá tải. Muốn giảm p99 thì phải xử lý tail của LLM: timeout + retry/hedging, cache câu trả lời, hoặc model/provider nhanh hơn.

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. **Average nói dối, percentile nói thật.** Với 3 % request chậm, avg chỉ tăng 36.5 ms còn p99 tăng 7.7 lần; với 10 % thì p95 lên 1.69 s trong khi avg mới 338.5 ms. SLO phải đặt theo p95/p99, và so sánh 2 phiên bản phải dùng **cost / câu đúng** (v2 rẻ hơn v1 ~11 lần trên mỗi câu đúng dù tốn ~1.9 lần token/câu).
  2. **Metric hạ tầng xanh ≠ agent tốt.** Thí nghiệm E: đổi sang v1 làm accuracy rơi từ 100 % xuống 4.3 % mà không alert nào kêu, dashboard còn "đẹp" hơn. Với AI agent, eval chất lượng (golden dataset + quality gate trong CI + eval định kỳ) là một trụ cột giám sát ngang hàng với metrics/traces/logs. Tương tự, metric chỉ thấy những gì service đó thấy: 504 ở Nginx (thí nghiệm D) vô hình với agent.
  3. **Benchmark phải tự kiểm chứng điều kiện chạy.** Lệnh mẫu `docker compose run locust` đã âm thầm đổi số instance, làm kết quả scale 1 vs 3 sai hoàn toàn. Chỉ phát hiện được nhờ đối chiếu `up{job="agent"}` trong Prometheus. Tương tự với alert: `LLMCostBurnRateHigh` kêu ngay ở tải bình thường, nghĩa là ngưỡng sai sẽ tạo alert fatigue. Số liệu và ngưỡng nào cũng cần được kiểm tra chéo trước khi tin.
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?
  - **Chất lượng:** `agent_eval_accuracy` (eval định kỳ trên production) + alert < 85 %; alert khi *retrieval hit ratio* giảm mạnh; tỉ lệ feedback xấu từ user.
  - **SLO theo error budget:** alert **burn rate nhiều cửa sổ** (vd 1 h & 5 m) cho error rate và p95, thay vì ngưỡng cố định, để vừa bắt nhanh sự cố lớn vừa không kêu vì spike ngắn.
  - **Tầng ngoài agent:** metric của Nginx / ingress (5xx, đặc biệt 502/504, upstream latency) và **synthetic check** từ bên ngoài gọi `/ask` qua URL public, để bắt được những lỗi agent không thấy (như 504 trong thí nghiệm D).
  - **Chi phí:** cost / request và cost theo user / API key, alert theo % ngân sách ngày/tháng (thay ngưỡng $2/h tuyệt đối), alert khi `agent_info{model}` đổi sang model đắt.
  - **LLM upstream:** tỉ lệ 429/503 của provider, số lần retry, latency p99 của riêng span LLM, và độ dài hàng đợi; alert khi upstream lỗi > x % để bật fallback model.
  - **Log tập trung** (Loki / Cloud Logging) giữ log sau khi container bị xoá, liên kết với Jaeger qua `trace_id`.
