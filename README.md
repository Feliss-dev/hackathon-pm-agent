# Trợ Lý AI Lập Kế Hoạch Delivery & Tự Động Hoá Jira — RAG · Function Calling · LangGraph · Jira MCP

Hệ thống AI dạng chat dành cho PM/Delivery lead. Nhận **BRD (`.md .txt .docx .pdf`)** hoặc **file Excel task list**, trích xuất task, **áp rule do con người viết** để phân loại và rà estimate, kiểm tra trùng lặp **theo chữ và theo nghĩa** (embedding + vector DB) dựa trên dữ liệu các dự án trước, dựng cây **Epic → Story → Task → Subtask**, chờ **người duyệt** rồi mới đẩy lên **Jira qua MCP**. Khi Excel thay đổi, hệ thống đồng bộ ngược vào task và cập nhật các issue đã có trên Jira thay vì tạo trùng.

> **Nguyên tắc:** AI đọc và gợi ý; code so sánh số và kiểm tra; con người duyệt. Không có gì lên Jira khi chưa có Approve, và mọi quyết định đều có lý do và được ghi vào audit log.

**Cài đặt từ máy trắng: [docs/HUONG_DAN_CAI_DAT.md](docs/HUONG_DAN_CAI_DAT.md)** · Hướng dẫn sử dụng từng bước: [docs/HUONG_DAN_SU_DUNG.md](docs/HUONG_DAN_SU_DUNG.md) · Mô tả nghiệp vụ: [docs/BUSINESS_DESCRIPTION.md](docs/BUSINESS_DESCRIPTION.md) · Kịch bản thuyết trình: [docs/HUONG_DAN_THUYET_TRINH.md](docs/HUONG_DAN_THUYET_TRINH.md) · Test case: [docs/TEST_CASES.md](docs/TEST_CASES.md) · Notebook ôn Hackathon: [notebooks/hackathon_workshop3.ipynb](notebooks/hackathon_workshop3.ipynb)

---

## 1. Kiến Trúc Hoạt Động (Đối chiếu với sơ đồ RAG chuẩn)

Hệ thống có hai chu trình, khớp với sơ đồ RAG chuẩn.

### Chu trình 1: Chuẩn bị dữ liệu (luồng lập chỉ mục)

| Khối trong sơ đồ | Trong dự án | Module |
|---|---|---|
| Nguồn dữ liệu thô | BRD và Excel mà người dùng đính kèm; Excel/plan của các phiên trước; rule `config/rules.md` và `config/projects/<TÊN>.md` | `samples/`, `config/` |
| **[A] Trích xuất thông tin** | Excel đọc trực tiếp (tự nhận header, ID, parent). Tài liệu do LLM trích task theo JSON schema chặt (structured output) | `excel_io.py`, `extract.py` |
| **[B] Chia đoạn (chunking)** | BRD cắt theo heading rồi theo đoạn (~2400 ký tự, chồng 300, mỗi chunk giữ heading gần nhất). Rule tách theo mục. Task = `title + description` | `vectors.py` (`chunk_markdown`, `split_rules`, `task_text`) |
| **[C] Vector hoá (embedding)** | `text-embedding-3-small` (1536 chiều) qua STU gateway, hoặc Hugging Face `paraphrase-multilingual-MiniLM-L12-v2` (384 chiều, chạy local). Batch 100 text/request, cache theo hash nội dung | `llm.py` (`Embedder`) |
| **[D] Cơ sở dữ liệu vector** | **ChromaDB** (HNSW, cosine) hoặc **Pinecone** (serverless, cosine). 3 collection: `tasks`, `rules`, `docs`; mọi record có `project_key` | `vectors.py`, `pinecone_store.py` |

Dữ liệu quan hệ (runs, task có phiên bản, plan có phiên bản, approval, push result, audit log, tool call, cache AI/embedding) nằm trong **SQLite** (đổi `DATABASE_URL` sang PostgreSQL để scale) — `db.py`.

### Chu trình 2: Sinh câu trả lời có truy xuất — RAG (luồng suy luận)

| Bước | Trong dự án | Module |
|---|---|---|
| **[1] Truy vấn** | Câu hỏi/yêu cầu của PM trong chat, hoặc Excel/BRD mới | `app.py`, `cli.py` |
| **[2] Tìm kiếm vector** | Embed câu hỏi rồi k-NN cosine trong collection phù hợp, lọc theo `project_key` (và `run_id` khi cần) | `vectors.py` |
| **[3] Dữ liệu liên quan** | Passage BRD kèm file/chunk/độ liên quan; task tương tự kèm category và estimate cuối; chỉ những rule liên quan khi file rule dài (> 6000 ký tự) | `agent.py`, `ai_rules.py` |
| **[4] LLM / Tổng hợp ngữ cảnh** | Ghép *rule (người viết)* + *task cần xét* + *task tương tự làm bằng chứng* → LLM trả category, lý do, ngưỡng giờ, giờ mặc định (JSON schema). Với hỏi đáp: chuỗi LCEL `prompt \| model \| parser` trả lời **chỉ từ passage** và trích dẫn `[file#chunk]` | `ai_rules.py`, `lc.py` |
| **[5] Phản hồi & ghi log** | **Code** so sánh estimate với ngưỡng, kiểm tra trường bắt buộc và trùng lặp, dựng Epic/Story/Task/Subtask, ghi audit; người **Approve** (gắn SHA-256 của plan) rồi push lên Jira qua MCP | `planner.py`, `validate.py`, `approval.py`, `push.py`, `jira_mcp.py` |

```
        ┌───────────────────── CHU TRÌNH 1: LẬP CHỈ MỤC ─────────────────────┐
 BRD/Excel ─► [A] extract ─► [B] chunk ─► [C] embed ─► [D] vector DB (Chroma | Pinecone)
 rule.md  ──────────────────► split ───► embed ───►      ▲ tasks · rules · docs (project_key)
                                                          │
        ┌───────────────────── CHU TRÌNH 2: SUY LUẬN ───┼─────────────────┐
 PM chat ─► LangGraph agent ─► function calling (17 tool + 63 tool Jira MCP)
                │                 │            │              │
                │       ask_requirements  search_similar  analyze ──► AI đọc rule (structured output)
                │        (RAG + trích dẫn)  (cosine)         │          ▼
                │                                     CODE: so estimate · validate · trùng (fuzzy + nghĩa)
                │                                              ▼
                │                                    Plan Epic→Story→Task→Subtask (có phiên bản)
                ▼                                              ▼
        ✋ duyệt từng bước (tuỳ chọn)         Người Approve (hash) ─► Push Jira (MCP, có checkpoint)
```

---

## 2. Cấu Trúc Thư Mục Dự Án

```
workshop3/
├── delivery_agent/                 # Package lõi
│   ├── models.py                   # Pydantic: Task, PlannedIssue, ValidationIssue, EstimateFinding, AuditEntry
│   ├── excel_io.py                 # [A] Đọc Excel (tự nhận header), ghi cột Product (không ghi đè file gốc)
│   ├── extract.py                  # [A] Trích task từ BRD bằng LLM (structured output) / Claude
│   ├── ai_rules.py                 # [4] AI đọc rule người viết → category + ngưỡng giờ (batch 40, song song 4)
│   ├── validate.py                 # [5] Trường bắt buộc, trùng trong batch / Jira / Epic / lịch sử (fuzzy + nghĩa)
│   ├── planner.py                  # [5] Dựng Epic→Story→Task→Subtask, estimate review, tóm tắt
│   ├── approval.py                 # [5] Cổng duyệt, gắn SHA-256 của plan
│   ├── push.py + jira_mcp.py       # [5] Đẩy Jira qua MCP (checkpoint, resume), tìm trùng, update, link
│   ├── jira_tools.py               # 63 tool của Jira MCP cho agent: đọc chạy ngay, ghi chờ duyệt
│   ├── llm.py                      # [C] OpenAI client, with_retry (tenacity), structured_completion, Embedder
│   ├── vectors.py                  # [B·D] Chunking + VectorStore (Chroma | Pinecone): tasks / rules / docs
│   ├── pinecone_store.py           # [D] Pinecone cùng giao diện với Chroma + index in-memory cho test
│   ├── lc.py                       # LangChain: ProjectRetriever, chuỗi RAG LCEL, StructuredTool
│   ├── graph.py                    # LangGraph: StateGraph agent ↔ tools, MemorySaver, interrupt_before
│   ├── tools.py                    # Registry 17 tool, tham số Pydantic → JSON schema, validate, lỗi trả về model
│   ├── agent.py                    # AgentSession (trạng thái = 1 run trong DB), system prompt, run_turn
│   ├── sync.py                     # Đồng bộ Excel → task (diff added/changed/removed)
│   ├── speech.py                   # Đọc to tóm tắt plan bằng Hugging Face TTS
│   ├── db.py                       # SQLAlchemy: runs, tasks, plans, approvals, audit, tool_calls, cache
│   └── config.py · estimate.py · audit.py · workshop.py · cli.py
├── app.py                          # Streamlit: chat + sidebar (review, duyệt, Jira actions, hệ thống, đọc to)
├── rules_editor.py                 # Hộp thoại xem/sửa rule trong app
├── config/
│   ├── rules.md                    # Rule chung do người viết (thứ tự ưu tiên, ngưỡng giờ, giờ mặc định)
│   ├── settings.yaml               # Cài đặt kỹ thuật: Jira mapping, header Excel, ngưỡng trùng, agent_write_policy
│   └── projects/KAN.md · KAN.yaml  # Rule và cài đặt riêng dự án (ưu tiên hơn rule chung)
├── samples/                        # BRD và Excel mẫu (make_samples.py tạo lại)
├── notebooks/hackathon_workshop3.ipynb   # Chạy trọn dự án, dùng đúng module của app
├── docs/                           # Mô tả nghiệp vụ, hướng dẫn cài đặt và sử dụng, thuyết trình, test case
├── tests/                          # 68 test, không cần mạng
├── requirements.txt · .env.example · run_app.bat
└── data/ (gitignore)               # app.db (SQLite) và chroma/ (vector)
```

---

## 3. Phân Tích Chi Tiết Từng Module

### 3.1 `excel_io.py` và `extract.py` — Trích xuất thông tin
- **Excel:** tự nhận hàng header, các biến thể cột (Title 1/Title 2, Original Estimate, Assigned To, Parent…), ID thật (`#1002`) hoặc ID theo dòng (`Sheet!R5`), quan hệ cha–con. Ghi cột *Product* ra **file mới**, bỏ qua ô chứa công thức; file gốc không bao giờ bị ghi đè.
- **Tài liệu:** `.md .txt .docx .pdf` → LLM trích task theo JSON schema chặt (`structured_completion` qua Chat Completions + `response_format`). Prompt cấm bịa estimate. Có thể đổi sang Claude bằng `LLM_PROVIDER=anthropic`.

### 3.2 `ai_rules.py` — AI đọc rule do người viết
- Rule viết bằng Markdown (`config/rules.md` + `config/projects/<TÊN>.md`); rule riêng dự án được xét **trước** và thắng rule chung.
- Mỗi task trả về `category` (enum cố định 18 loại), `category_reason` (rule nào quyết định), `max_hours`, `default_hours`, `estimate_rule`.
- **Batch 40 task/request, chạy song song 4 batch**; kết quả cache trong DB theo (hash rule, nội dung task) nên khởi động lại không gọi lại model. Task bị model bỏ sót được hỏi lại một lần.
- **AI không so sánh estimate.** `assess()` trong code mới so giờ hiện tại với `max_hours` / `default_hours` để ra cảnh báo (thiếu estimate → High; vượt ngưỡng → Medium kèm gợi ý).

### 3.3 `validate.py` và `planner.py` — Kiểm tra và dựng plan
- **Trường bắt buộc:** `title, assignee, estimate, category`; trường *blocking* (`title, estimate, category`) làm task bị **SKIP**, thiếu assignee chỉ cảnh báo (tạo chưa gán người).
- **Trùng lặp theo chữ:** `rapidfuzz` ≥ 85% (trong batch, với issue đang có trên Jira, với Story trong Epic đích). **Theo nghĩa:** cosine ≥ 0.88 với task của các phiên cũ; nếu task cũ đã có Jira key thì chặn tạo.
- **Hierarchy:** Epic → Story → Task → Subtask, ánh xạ category → loại issue, label, component, sprint, estimate, dependency link. Mặc định task gắn vào Epic và nối với Story bằng link `Relates` (Jira chuẩn chỉ cho Epic làm cha của Task).

### 3.4 `llm.py` — Lớp gọi model
- `get_client()`: OpenAI SDK trỏ vào STU gateway (đổi `LLM_BASE_URL` / `LLM_MODEL` để dùng model khác, ví dụ Llama 3).
- `with_retry()`: backoff luỹ thừa + jitter chỉ cho 429 / timeout / connection / 5xx; 4xx fail ngay. Retry của SDK bị tắt để chính sách nằm ở một chỗ và ghi được vào audit log.
- `structured_completion()`: JSON schema chặt qua Chat Completions (gateway **bỏ qua** `json_schema` của Responses API).
- `Embedder`: provider `openai` / `hf` / `auto`; batch 100; cache theo hash; vector của model khác nhau tách collection riêng.

### 3.5 `vectors.py` và `pinecone_store.py` — Cơ sở dữ liệu vector
- 3 collection `tasks` (title+description, metadata category/estimate/jira_key), `rules` (từng mục rule, giữ thứ tự ưu tiên), `docs` (chunk BRD kèm file/chunk).
- `VECTOR_BACKEND=chroma` (mặc định, cosine HNSW, lưu `data/chroma`) hoặc `pinecone` (một index, mỗi collection một *namespace*, `project_key` là filter). `PineconeCollection` có cùng giao diện với Chroma nên toàn bộ nghiệp vụ chạy trên cả hai.
- Pinecone nhất quán chậm nên code không tin `count()` ngay sau khi ghi. Embedding/vector lỗi thì plan **vẫn chạy** (sidebar báo 🟠).

### 3.6 `lc.py` — LangChain
`ProjectRetriever` (`BaseRetriever` bọc `search_docs`), `rag_chain` (`retriever | format_docs | prompt | model | parser`), `default_chat_model()` (`ChatOpenAI` cùng gateway/khoá), `langchain_tools()` (mỗi tool của registry thành `StructuredTool`, Pydantic là `args_schema`). Tool `ask_requirements` của agent lấy passage qua đúng retriever này; `answer=true` chạy thêm chuỗi để có câu trả lời trích dẫn.

### 3.7 `graph.py` — LangGraph
`AgentGraph` là `StateGraph` với state `messages / pending / rounds`: `START → agent → (tools → agent)* → END`, `give_up` khi vượt 8 vòng. Có `MemorySaver`, và với `interrupt=True` đồ thị **dừng trước node `tools`**: `run()` trả về các tool sắp chạy, `resume(approve=True)` cho chạy, `resume(approve=False)` báo cho model là người dùng từ chối. Lịch sử hội thoại của người gọi chỉ được cập nhật khi lượt kết thúc, nên bỏ dở một lần dừng không làm hỏng hội thoại. `agent.run_turn` và app Streamlit đều chạy trên lớp này.

### 3.8 `tools.py` và `jira_tools.py` — Gọi hàm (function calling)
- **17 tool của dự án**, mỗi tool khai báo một lần bằng Pydantic: JSON schema gửi cho model sinh tự động (đã inline `$ref`), tham số được validate trước khi chạy, gọi sai trả `{"error": ...}` để model tự sửa, mỗi lần gọi được đo thời gian và lưu bảng `tool_calls`.
- **63 tool của Jira MCP** (với `mcp-atlassian`) được khám phá lúc chạy qua một kết nối giữ mở. **Đọc** (server đánh dấu `readOnlyHint`) chạy ngay; **ghi** chỉ được *đề xuất* (`pending_approval`), người bấm Approve trong **🧩 Jira actions** mới thực thi. Chính sách `jira.agent_write_policy`: `approve` (mặc định) / `allow` / `deny`; mọi đề xuất và quyết định vào audit log.

| Nhóm | Tool |
|---|---|
| Phân tích và plan | `list_files`, `analyze`, `show_plan`, `update_tasks`, `apply_suggested_estimates`, `assess_task`, `recheck_with_jira`, `prepare_jira_push` |
| Tra cứu (RAG / vector) | `ask_requirements`, `search_similar_tasks`, `search_jira`, `list_runs`, `open_run` |
| Excel / file | `sync_excel`, `fill_excel_product`, `check_workshop` |
| Giọng nói | `read_plan_aloud` |
| Jira MCP | `jira_*` (đọc: 38 tool, ghi: 25 tool) |

### 3.9 `approval.py`, `push.py`, `jira_mcp.py` — Duyệt và đẩy Jira
- Approval là file riêng gắn **SHA-256 của plan**; `push` từ chối nếu thiếu approval, bị Reject, hoặc plan đổi sau khi duyệt. **Không có cờ bỏ qua duyệt.**
- Push qua MCP (`jira_search`, `jira_create_issue`, `jira_create_issue_link`, `jira_update_issue`), có checkpoint `plan.result.json` để chạy lại tiếp tục, không tạo trùng. Người được gán không tìm thấy thì issue vẫn được tạo, chưa gán người.

### 3.10 `sync.py` — Đồng bộ Excel → task
Ghép dòng Excel mới với task đã lưu theo thứ tự **ID thật → tên chuẩn hoá → nghĩa (embedding, cosine ≥ 0.90) → fuzzy ≥ 85**; còn lại là *added*, task cũ không ai nhận là *removed*. Áp dụng tạo **phiên bản plan mới** (cần duyệt lại); task đã có Jira key được **update** (`pending_update`), không tạo mới; task bị xoá chỉ rời khỏi plan, **không xoá trên Jira**.

### 3.11 `speech.py` — Đọc to tóm tắt plan
`transformers.pipeline("text-to-speech")` với `facebook/mms-tts-vie` (đổi bằng `TTS_MODEL`); số được đọc thành chữ tiếng Việt; xuất `plan_summary.wav`.

### 3.12 `db.py` và `agent.py` — Lưu trữ và phiên làm việc
Mỗi chat là một **run**: file, task (có version và Jira key), plan (mỗi lần sửa là một version), approval, push result, audit log, tool call, lịch sử chat, cache. Mở lại từ **🕘 Lịch sử**. `AgentSession.state()` đưa trạng thái vào prompt mỗi vòng (`CURRENT STATE`) để model không bịa kết quả.

---

## 4. Các Công Cụ & Công Nghệ Được Áp Dụng

| # | Công nghệ | Vai trò | Tuần học |
|---|---|---|---|
| 1 | Python 3.12, Pydantic v2 | Runtime, model dữ liệu, sinh JSON schema | 1 |
| 2 | OpenAI Python SDK + STU gateway | Chat, function calling, structured output (`DeepSeek-V4.1-Flash`, có thể đổi model) | 1–2 |
| 3 | tenacity | Retry backoff + jitter | 2 |
| 4 | ThreadPoolExecutor | 4 batch rule song song, 40 task/batch | 2 |
| 5 | Hugging Face sentence-transformers | Embedding local đa ngôn ngữ (384 chiều) | 3 |
| 6 | Hugging Face transformers (MMS-TTS) | Đọc to tóm tắt plan | 3 |
| 7 | text-embedding-3-small | Embedding 1536 chiều qua gateway | 3 |
| 8 | ChromaDB (HNSW, cosine) | Vector store local, mặc định | 4 |
| 9 | Pinecone (serverless) | Vector store cloud, tuỳ chọn | 4 |
| 10 | LangChain (core, openai, text-splitters) | Retriever, chuỗi RAG LCEL, `StructuredTool`, `ChatOpenAI` | 4 |
| 11 | LangGraph | `StateGraph` agent, `MemorySaver`, `interrupt_before` | 5 |
| 12 | RAG | Hỏi đáp BRD có trích dẫn, rule và task retrieval | 5 |
| 13 | MCP (`mcp`, `mcp-atlassian`) | Đọc, ghi Jira; 63 tool cho agent | — |
| 14 | SQLAlchemy 2 (SQLite → PostgreSQL) | Lưu run, task, plan, audit, cache | — |
| 15 | rapidfuzz | Trùng lặp theo chữ | — |
| 16 | openpyxl, python-docx, pypdf, pandas | Đọc/ghi Excel, đọc Word/PDF | — |
| 17 | Streamlit | Giao diện chat + sidebar | — |
| 18 | pytest | 68 test không cần mạng | — |

---

## 5. Luồng Từ Đầu Đến Cuối (Từng Bước)

```
GIAI ĐOẠN A — LẬP CHỈ MỤC (khi đính kèm tài liệu / sau mỗi lần dựng plan)
──────────────────────────────────────────────────────────────────────────
Bước 1 [extract.py / excel_io.py]   BRD → LLM trích task (JSON schema) · Excel → đọc trực tiếp
Bước 2 [vectors.py]                 BRD → chunk theo heading (~2400 ký tự, chồng 300) · task → title+description
Bước 3 [llm.py Embedder]            Chunk/task → vector (1536 chiều OpenAI hoặc 384 chiều HF), cache theo hash
Bước 4 [vectors.py | pinecone_store]  Upsert vào Chroma/Pinecone kèm project_key, run_id, category, estimate, jira_key

GIAI ĐOẠN B — SUY LUẬN (mỗi lượt PM nhắn)
──────────────────────────────────────
Bước 5 [graph.py]       LangGraph: model chọn tool (function calling); tuỳ chọn dừng chờ người cho chạy
Bước 6 [ai_rules.py]    Rule (người viết) + task + task tương tự (bằng chứng) → LLM trả category, lý do, ngưỡng giờ
Bước 7 [ai_rules.py]    CODE so giờ với ngưỡng → cảnh báo thiếu/vượt estimate + gợi ý
Bước 8 [validate.py]    Trường bắt buộc; trùng theo chữ (≥85%) và theo nghĩa (≥0.88); Jira, Epic, lịch sử
Bước 9 [planner.py]     Epic → Story → Task → Subtask; SKIP task thiếu trường / trùng; tóm tắt cho review
Bước 10 [approval.py]    Người Approve → lưu quyết định gắn SHA-256 của plan (sửa plan = duyệt lại)
Bước 11 [push.py]        Push qua Jira MCP, checkpoint; task đã có key thì update
Bước 12 [sync.py]        Excel đã sửa → diff → áp dụng → plan version mới (update Jira, không tạo trùng)
```

---

## 6. Hướng Dẫn Demo (Từng Bước)

### Bước 0 — Cài đặt (làm một lần)

Máy mới hoàn toàn: làm theo hướng dẫn chi tiết [docs/HUONG_DAN_CAI_DAT.md](docs/HUONG_DAN_CAI_DAT.md) (cài Git, uv, tạo `.venv`, điền `.env`, xử lý sự cố). Tóm tắt:

| Cần có | Ghi chú |
|---|---|
| **Git** và **uv** | uv tự tải Python 3.12, không cần cài Python riêng. Cài uv: Windows `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"`; macOS/Linux `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Đĩa 4–6 GB, RAM từ 8 GB, Internet | Có `torch` và model Hugging Face |
| `LLM_API_KEY` | Bắt buộc. Jira, Pinecone là tuỳ chọn; thiếu Jira thì app chạy ở chế độ offline |

```bash
git clone https://github.com/Feliss-dev/hackathon-pm-agent.git && cd hackathon-pm-agent
uv venv --python 3.12 .venv

# Windows
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
copy .env.example .env
.venv\Scripts\python.exe samples\make_samples.py

# macOS / Linux
uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env
.venv/bin/python samples/make_samples.py
```

Mở `.env` và điền `LLM_API_KEY` (cùng thông tin Jira nếu có). Kiểm tra cài đặt: `python -m pytest -q` phải báo `68 passed` (dùng `.venv\Scripts\python.exe` hoặc `.venv/bin/python`).

### Bước 1 — Khởi động
```bash
.venv\Scripts\python.exe -m streamlit run app.py --server.address localhost   # Windows; hoặc nhấp đúp run_app.bat
.venv/bin/python -m streamlit run app.py --server.address localhost            # macOS / Linux
```
Mở `http://localhost:8501`. Thanh trạng thái phải là 🟢 AI · 🟢 Jira `KAN` · 🟢 Vector.

### Bước 2 — Phân tích Excel
Chọn **📁 Bộ rule dự án = KAN**, gõ: `Phân tích file mẫu task_review.xlsx và gắn vào Epic KAN-1`.
Xem thanh bên: 6 task, cảnh báo estimate (review code 20h vượt 16h; họp 6h vượt 4h), task thiếu estimate bị SKIP, cây Epic → Story → Task.

### Bước 3 — Hỏi tài liệu và tìm task tương tự (RAG)
Đính kèm `samples/brd_customer_portal.md`, hỏi: `Tài liệu yêu cầu nói gì về đăng ký khách hàng?` → câu trả lời có trích dẫn `brd_customer_portal.md`.
Hỏi: `Trước đây đã có task nào giống 'Create Customer API' chưa?` → danh sách task tương tự kèm cosine, category, estimate.

### Bước 4 — Sửa, duyệt, đẩy Jira
Bấm **Áp estimate gợi ý** (plan lên version 2, cần duyệt lại) → nhập tên người duyệt → **Approve** → bật **🔗 Kết nối Jira**, nói `kiểm tra trùng với Jira` → **🚀 Đẩy Jira**.

### Bước 5 — Đồng bộ Excel đã sửa
Sửa estimate một task và thêm một dòng trong Excel, đính kèm lại, nói `So sánh file Excel mới với task hiện tại` → bảng added/changed/removed → đồng ý áp dụng. Task đã đẩy được **update**, không tạo trùng.

### Bước 6 — Jira MCP đầy đủ cho agent
Gõ: `Trên Jira KAN có Epic nào? Sau đó thêm comment "test" vào KAN-22.` → Epic hiện ngay; comment xuất hiện ở **🧩 Jira actions** chờ **Approve**, chưa có gì lên Jira.

### Bước 7 — Duyệt từng bước agent (LangGraph)
**⚙️ Hệ thống → ✋ Duyệt từng bước agent**, hỏi lại. Agent dừng trước mỗi lượt chạy tool; **▶️ Cho chạy** hoặc **⏹️ Không chạy**.

### Bước 8 — Đổi embedding / vector store, đọc to
**⚙️ Hệ thống:** chọn *Hugging Face (local)* hoặc *Pinecone* (cần `PINECONE_API_KEY`). Bấm **🔊 Đọc to tóm tắt plan** để nghe và tải `plan_summary.wav`.

### Bước 9 — Notebook và CLI
- Notebook: mở [notebooks/hackathon_workshop3.ipynb](notebooks/hackathon_workshop3.ipynb), chọn kernel `.venv`, **Run All** (đặt `REQUIRE_LIVE = False` để chạy offline).
- CLI:
```bash
python -m delivery_agent --profile KAN plan samples/brd_customer_portal.md samples/task_review.xlsx --epic KAN-1 --out out/run1
python -m delivery_agent approve out/run1/plan.json --approver "Lan (PM)"
python -m delivery_agent push out/run1/plan.json
python -m delivery_agent --profile KAN runs
python -m delivery_agent --profile KAN sync edited.xlsx --run-id <id> --apply
python -m delivery_agent --profile KAN index-rules
python -m delivery_agent classify "Write unit tests for Customer API"
python -m delivery_agent plan samples/task_tracking.xlsx --offline --no-prompt     # không gọi Jira
```

---

## 7. An Toàn Và Những Điều Cần Biết

- **Không có cờ bỏ qua duyệt.** `push` từ chối khi approval thiếu, bị Reject, hoặc plan đổi sau khi duyệt.
- Task nghi trùng hoặc thiếu trường bắt buộc **không được tạo** (hiện `SKIP`) trừ khi dùng `--include-duplicates` / `--allow-incomplete`; plan vẫn phải được duyệt.
- Tool ghi Jira của agent chỉ được đề xuất; thực thi sau khi người Approve (trừ khi đặt `jira.agent_write_policy: allow`).
- LLM chỉ trích task và đọc rule; **không bịa estimate**, mọi so sánh số do code làm. Khoá API không bao giờ hiện trong câu trả lời.
- **Jira hierarchy:** Jira chuẩn chỉ cho Epic làm cha của Task. Mặc định (`jira.task_parent: epic`) task gắn vào Epic và nối với Story bằng `Relates`; đặt `task_parent: story` nếu Jira của bạn có hierarchy tuỳ biến.
- **Excel:** chỉ ghi ô Product; ô có công thức bị bỏ qua. openpyxl giữ công thức, style, merged cell và mọi sheet nhưng làm mất chart/ảnh nhúng, slicer, pivot cache; file gốc không bị ghi đè.
- **Hướng link:** kiểm tra link `Blocks` đúng chiều (inward/outward khác nhau theo loại link). **Sprint field** mặc định `customfield_10020` (Jira Cloud), đổi bằng `jira.sprint_field`.
- Dùng URL gateway chuẩn `.../jpe`; URL cache `.../jpe/v2` phát lại câu trả lời cũ và làm agent lặp cùng một tool.
- Gateway bỏ qua `json_schema` của Responses API, vì vậy mọi structured output đi qua Chat Completions `response_format`.
- Các ngưỡng cosine mặc định (0.88 trùng lịch sử, 0.75 bằng chứng estimate, 0.90 ghép dòng Excel) hợp với embedding OpenAI; với cặp task chỉ diễn đạt lại thì điểm thường chỉ 0.77–0.87, hạ ngưỡng nếu cần (`validation.semantic_threshold`, `sync.SEMANTIC_MATCH`).

---

## 8. Cấu Hình Môi Trường (Biến môi trường)

| Biến | Mặc định | Mô tả |
|---|---|---|
| `LLM_API_KEY` (hoặc `API_KEY`) | — | Khoá STU gateway (bắt buộc cho chat, đọc rule, trích tài liệu) |
| `LLM_BASE_URL` | `https://aiportalapi.stu-platform.live/jpe` | Endpoint OpenAI-compatible |
| `LLM_MODEL` | `DeepSeek-V4.1-Flash` | Model chat |
| `LLM_PROVIDER` | `openai` | `anthropic` để dùng Claude trích tài liệu (cần `ANTHROPIC_API_KEY`) |
| `LLM_MAX_RETRIES` | `4` | Số lần thử lại khi 429/timeout/5xx |
| `JIRA_MCP_COMMAND` | `uvx mcp-atlassian` | Server MCP stdio (kèm `JIRA_URL`, `JIRA_USERNAME`, `JIRA_API_TOKEN`) |
| `JIRA_MCP_URL`, `JIRA_MCP_TOKEN` | — | Hoặc server MCP Streamable-HTTP từ xa |
| `EMBEDDING_PROVIDER` | `openai` | `openai` / `hf` (local, không cần key) / `auto` |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Model OpenAI, hoặc id HF dạng `org/ten` khi `hf` |
| `HF_EMBEDDING_MODEL` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | Model HF khi `hf` |
| `VECTOR_BACKEND` | `chroma` | `chroma` hoặc `pinecone` |
| `PINECONE_API_KEY` | — | Bắt buộc khi dùng Pinecone; tuỳ chọn `PINECONE_INDEX` (`delivery-agent`), `PINECONE_CLOUD` (`aws`), `PINECONE_REGION` (`us-east-1`) |
| `TTS_MODEL` | `facebook/mms-tts-vie` | Model đọc to (`facebook/mms-tts-eng` cho tiếng Anh) |
| `DATABASE_URL` | `sqlite:///data/app.db` | Đổi sang PostgreSQL để scale |
| `CHROMA_DIR` | `data/chroma` | Thư mục Chroma |

Cài đặt kỹ thuật khác nằm ở `config/settings.yaml` (ghi đè theo dự án trong `config/projects/<TÊN>.yaml`): `jira.project_key`, `jira.agent_write_policy`, `validation.*`, mapping category → loại issue.

## 9. Kiểm Thử

```bash
.venv/Scripts/python.exe -m pytest -q        # 68 test, không cần mạng
```

Test pipeline chạy MCP client thật với một Jira MCP server giả trong tiến trình: tìm trùng, tạo hierarchy, link, cổng duyệt, phát hiện sửa plan, resume sau lỗi. Test vector dùng SQLite in-memory, Chroma tạm và embedder giả; Pinecone được test bằng index in-memory cùng giao diện. Danh sách test case: [docs/TEST_CASES.md](docs/TEST_CASES.md).

---

*Trợ lý AI lập kế hoạch Delivery & Jira — LLM chuẩn OpenAI · Embedding (OpenAI / Hugging Face) · ChromaDB / Pinecone · LangChain · LangGraph · Jira MCP*
