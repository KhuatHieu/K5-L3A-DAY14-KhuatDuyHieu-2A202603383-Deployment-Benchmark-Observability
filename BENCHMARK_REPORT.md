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

_(Phần này điền ở Bước 2.)_

### 4.1 Screenshot

- [ ] Dashboard Grafana khi đang có tải (thấy rõ Golden Signals)
- [ ] Một trace chậm trong Jaeger (thấy span nào chiếm thời gian)
- [ ] Một trace lỗi trong Jaeger (có event `exception`)
- [ ] Trang Alerts của Prometheus với ít nhất 1 alert đang **firing**

### 4.2 Thí nghiệm "phá hệ thống"

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | | | |
| B | `MOCK_SLOW_RATE=0.3` | | | |
| C | `LLM_MODEL=mock-llm-large` | | | |
| D | `docker pause` 1 instance | | | |
| E | `AGENT_VERSION=v1` | | | |

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

...

### 4.3 Debug bằng trace

Chọn 1 trace chậm nhất bạn tìm được:

- `trace_id`: ...
- Tổng thời gian: ... ms
- Span chiếm nhiều thời gian nhất: ... (... ms, ...%)
- Dòng log tương ứng (grep theo `trace_id`):

```json

```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | | | | | |
| 3 | | | | | |

**Giải thích kết quả:** Scale có cải thiện p95 không? Nút thắt nằm ở đâu?

...

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. ...
  2. ...
  3. ...
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?

...
