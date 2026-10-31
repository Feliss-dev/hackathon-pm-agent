# Hướng dẫn sử dụng – AI Delivery Planning & Jira Agent

Ứng dụng là một chat agent cho PM: đính kèm BRD hoặc Excel, hỏi bằng tiếng Việt, nhận kế hoạch Jira đã được review.
**Không có gì được tạo trên Jira nếu chưa có người duyệt.**

## 1. Công nghệ đang dùng trong app

| Tuần | Công nghệ | Dùng ở đâu |
|---|---|---|
| 1 | Python, Pydantic, OpenAI API (chat + structured output) | Model dữ liệu, AI đọc rule, trích task từ tài liệu |
| 2 | Function calling, batching, retry | 17 tool có kiểu, 40 task/batch, backoff cho 429/5xx |
| 3 | Embeddings, Hugging Face | `text-embedding-3-small` hoặc model HF đa ngôn ngữ chạy local; đọc to tóm tắt plan bằng HF TTS |
| 4 | Pinecone, LangChain | Vector store Chroma hoặc Pinecone; `StructuredTool`, `ProjectRetriever` |
| 5 | RAG, LangGraph | Hỏi đáp tài liệu có trích dẫn; vòng lặp agent là `StateGraph` có bước chờ người duyệt |
| — | MCP | 63 tool của Jira MCP server, ghi phải được duyệt |

## 2. Cài đặt (làm một lần)

Hướng dẫn đầy đủ từ máy trắng (Git, uv, `.venv`, `.env`, notebook, sự cố): [HUONG_DAN_CAI_DAT.md](HUONG_DAN_CAI_DAT.md). Tóm tắt:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt     # macOS/Linux: .venv/bin/python
cp .env.example .env          # Windows cmd: copy .env.example .env; rồi điền khoá, xem mục 3
.venv\Scripts\python.exe samples\make_samples.py
```

Lần đầu dùng các tính năng Hugging Face (embedding local hoặc đọc to), thư viện tải model về máy (vài trăm MB, cần mạng).

## 3. Cấu hình `.env`

| Biến | Ý nghĩa | Mặc định / ghi chú |
|---|---|---|
| `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` | AI gateway (OpenAI-compatible) | Dùng URL `.../jpe`, **không** dùng `.../jpe/v2` (cache làm agent lặp). Đổi `LLM_BASE_URL`/`LLM_MODEL` để dùng model khác (vd Llama 3) |
| `JIRA_MCP_COMMAND` + `JIRA_URL`, `JIRA_USERNAME`, `JIRA_API_TOKEN` | Jira qua MCP (`uvx mcp-atlassian`) | Hoặc dùng `JIRA_MCP_URL` + `JIRA_MCP_TOKEN` cho server từ xa |
| `EMBEDDING_PROVIDER` | `openai` (gateway), `hf` (Hugging Face local, không cần key), `auto` (gateway nếu có key) | `openai` |
| `EMBEDDING_MODEL` | Tên model OpenAI, hoặc id HF dạng `org/ten` khi `hf` | `text-embedding-3-small` |
| `HF_EMBEDDING_MODEL` | Model HF riêng khi `hf` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (có tiếng Việt) |
| `VECTOR_BACKEND` | `chroma` (local) hoặc `pinecone` (cloud) | `chroma` |
| `PINECONE_API_KEY` | Bắt buộc khi dùng Pinecone | Tuỳ chọn: `PINECONE_INDEX`, `PINECONE_CLOUD`, `PINECONE_REGION` |
| `TTS_MODEL` | Model đọc to | `facebook/mms-tts-vie` (tiếng Việt); `facebook/mms-tts-eng` cho tiếng Anh |
| `DATABASE_URL`, `CHROMA_DIR` | Nơi lưu dữ liệu | `sqlite:///data/app.db`, `data/chroma` |
| `LLM_MAX_RETRIES` | Số lần thử lại khi 429/timeout/5xx | `4` |

Vector của các embedding khác nhau không so sánh được, nên mỗi model có bộ collection riêng. Đổi embedding hay vector store
không làm lẫn hay mất dữ liệu cũ.

## 4. Chạy app

```bash
.venv\Scripts\python.exe -m streamlit run app.py --server.address localhost     # macOS/Linux: .venv/bin/python
```

hoặc nhấp đúp `run_app.bat`, rồi mở `http://localhost:8501`.

- **Khung chat (giữa):** nói yêu cầu, đính kèm file bằng 📎 (`.md .txt .docx .pdf .xlsx .zip`). Dưới ô chat có gợi ý câu tiếp theo.
- **Thanh bên trái:** trạng thái 🟢 AI / Jira / Vector, bật tắt **🔗 Kết nối Jira**, chọn **📁 Bộ rule dự án**, **🕘 Lịch sử**,
  **⚙️ Hệ thống**, **🧩 Jira actions**, bảng review plan, duyệt và đẩy Jira, đồng bộ Excel, 🔊 đọc to. Kéo mép phải để rộng hẹp.

## 5. Quy trình làm việc chính

1. **Phân tích:** đính kèm file rồi gõ, ví dụ *"Phân tích file này và gắn vào Epic KAN-1"*. Agent tự chọn tool: Excel đọc trực tiếp,
   tài liệu do AI trích task. Kết quả: số task, cảnh báo estimate, task nghi trùng, cây Epic → Story → Task → Subtask.
2. **Review (thanh bên):** xem cảnh báo, sửa task bằng lời (*"đổi người làm task X thành Chi"*) hoặc bấm áp dụng estimate gợi ý.
   Mỗi thay đổi tạo **phiên bản plan mới** và cần duyệt lại.
3. **Duyệt:** nhập tên người duyệt, bấm **Approve** (hoặc **Reject**). Approve gắn với mã hash của plan; sửa plan sau đó thì phải duyệt lại.
4. **Đẩy Jira:** nút **🚀 Đẩy Jira** chỉ bật khi plan hiện tại đã Approve và đã đối chiếu trùng với Jira. Lỗi giữa chừng thì chạy lại sẽ tiếp tục, không tạo trùng.

Task thiếu trường bắt buộc (title, estimate, category) hoặc nghi trùng với task đã có Jira key sẽ bị **SKIP** trong plan.

## 6. Hỏi tài liệu (RAG)

Tài liệu đính kèm được cắt đoạn, embed và lưu vào vector store. Hỏi như:

- *"Tài liệu yêu cầu nói gì về đăng ký khách hàng?"*
- *"Dữ liệu cũ được chuyển sang đâu? Ai chịu trách nhiệm?"*

Agent trả lời **chỉ từ các đoạn tìm được** và trích dẫn tên file. Muốn tìm cả tài liệu của các phiên trước: *"tìm trong tất cả các tài liệu cũ"*.
Truy xuất đi qua `ProjectRetriever` (LangChain); câu trả lời có trích dẫn đến từ chuỗi LCEL `prompt | model | parser`.

## 7. Task tương tự và kiểm tra trùng

- *"Trước đây đã có task nào giống 'Create Customer API' chưa? Mất bao lâu?"* → tìm theo **nghĩa** (cosine similarity) trong các plan cũ.
- Task giống plan cũ ≥ 0.88 được cảnh báo (nếu task cũ đã có Jira key thì bị chặn tạo). Task giống ≥ 0.75 được đưa cho AI làm bằng chứng estimate.

## 8. Đồng bộ Excel → task

Sau khi đã phân tích một Excel, đính kèm bản đã sửa rồi nói *"So sánh file Excel mới với task hiện tại"*. Agent liệt kê thêm / sửa / xoá;
chỉ áp dụng khi bạn đồng ý. Dòng được ghép theo ID thật, rồi tên chuẩn hoá, rồi nghĩa (embedding), rồi fuzzy. Task đã có trên Jira sẽ được **cập nhật**
(không tạo mới); task bị xoá chỉ rời khỏi plan, **không bị xoá trên Jira**.

## 9. Jira MCP: toàn bộ tool của Jira cho agent

Agent có đủ tool mà Jira MCP server cung cấp (63 tool với `mcp-atlassian`: tìm bằng JQL, xem issue, sprint, board, link, transition, comment, worklog, version, service desk…).

| Loại | Hành vi |
|---|---|
| **Đọc** | Chạy ngay. Ví dụ: *"Trên Jira KAN có Epic nào?"*, *"Cho tôi xem KAN-22 và các issue con"* |
| **Ghi** (tạo, sửa, xoá, gán, chuyển trạng thái, comment, link, sprint…) | Agent chỉ **đề xuất**. Hành động nằm trong **🧩 Jira actions** ở thanh bên; bấm **Approve** mới thực thi, **Reject** để bỏ |

Chính sách đổi trong `config/settings.yaml`, khoá `jira.agent_write_policy`: `approve` (mặc định), `allow` (agent được ghi thẳng), `deny` (chỉ đọc).
Mọi đề xuất và quyết định được ghi vào audit log. Tạo nhiều issue từ tài liệu thì dùng luồng plan ở mục 5, không dùng tool ghi từng cái.
Cần tắt **🔗 Kết nối Jira** nếu muốn làm việc hoàn toàn offline (không gọi Jira).

## 10. Duyệt từng bước agent (LangGraph)

Trong **⚙️ Hệ thống**, bật **✋ Duyệt từng bước agent**. Agent dừng trước mỗi lượt chạy tool và hiện các tool sắp chạy cùng tham số:

- **▶️ Cho chạy:** tool chạy, agent tiếp tục.
- **⏹️ Không chạy:** tool bị bỏ qua, agent được báo là bạn từ chối và sẽ đổi cách làm.

Gõ câu hỏi mới khi đang dừng sẽ bỏ phần chờ đó. Nên dùng khi muốn kiểm soát chặt, ví dụ trước khi agent áp thay đổi lên plan.

## 11. Đọc to tóm tắt plan

Bấm **🔊 Đọc to tóm tắt plan** (thanh bên, sau khi có plan) hoặc nói *"đọc to tóm tắt plan"*. Model Hugging Face chạy trên máy bạn, tạo file `plan_summary.wav`
(cũng có trong **⬇️ Tải về**). Lần đầu chờ tải model.

## 12. Đổi embedding và vector store

Trong **⚙️ Hệ thống**:

- **Embedding:** OpenAI (gateway) / Hugging Face (local, đa ngôn ngữ, không cần key) / tự chọn.
- **Vector store:** ChromaDB (local) hoặc Pinecone (chỉ hiện khi có `PINECONE_API_KEY`). Index Pinecone tự tạo ở lần dùng đầu (vài chục giây).

Lựa chọn trong UI chỉ áp dụng cho phiên đang mở; muốn cố định thì đặt `EMBEDDING_PROVIDER` / `VECTOR_BACKEND` trong `.env`.
Pinecone nhất quán chậm: dữ liệu vừa ghi có thể mất vài giây mới tìm thấy.

## 13. Rule do người viết

Rule viết bằng ngôn ngữ tự nhiên trong `config/rules.md` (chung) và `config/projects/<TÊN>.md` (riêng dự án, ưu tiên hơn). Trong app mở **⚙️ Xem / sửa rule**
để sửa, thử một task theo rule đang sửa, và đổi cài đặt kỹ thuật. Lưu thì bản cũ giữ thành `.bak` và plan được tính lại.

## 14. Dòng lệnh (CLI)

```bash
python -m delivery_agent --profile KAN plan samples/brd_customer_portal.md samples/task_review.xlsx --epic KAN-1 --out out/run1
python -m delivery_agent approve out/run1/plan.json --approver "Lan (PM)"
python -m delivery_agent push out/run1/plan.json
python -m delivery_agent --profile KAN runs                      # các phiên đã lưu
python -m delivery_agent --profile KAN sync edited.xlsx --run-id <id> --apply
python -m delivery_agent --profile KAN index-rules               # dựng lại chỉ mục rule
```

## 15. Notebook ôn tập Hackathon

[notebooks/hackathon_workshop3.ipynb](../notebooks/hackathon_workshop3.ipynb) chạy trọn dự án và dùng đúng các module của app
(`vectors.py`, `pinecone_store.py`, `lc.py`, `graph.py`, `speech.py`, `jira_tools.py`). Mở trong VS Code, chọn kernel `.venv`, bấm **Run All**.
Mặc định gọi dịch vụ thật (cần khoá trong `.env`); đặt `REQUIRE_LIVE = False` ở cell Setup để chạy offline. Cell cuối section 8 có
`APPROVE_JIRA_WRITE = False`: đổi thành `True` mới thực sự ghi lên Jira.

## 16. Kiểm thử

```bash
.venv/Scripts/python.exe -m pytest -q
```

Bộ test không cần mạng (SQLite in-memory, Chroma tạm, embedder giả, Jira MCP giả). Danh sách: [TEST_CASES.md](TEST_CASES.md).

## 17. Xử lý sự cố

| Triệu chứng | Nguyên nhân thường gặp | Cách xử lý |
|---|---|---|
| 🔴 AI | Thiếu hoặc sai `LLM_API_KEY` | Điền khoá trong `.env`, chạy lại app |
| 🟠 Vector | Embedding hoặc Pinecone lỗi (kế hoạch vẫn chạy, chỉ mất tìm theo nghĩa) | Xem chi tiết khi rê chuột; đổi sang Hugging Face (không cần key) trong ⚙️ Hệ thống |
| Agent lặp cùng một tool | Dùng gateway `.../jpe/v2` | Đổi `LLM_BASE_URL` về `.../jpe` |
| Nút Đẩy Jira bị khoá | Chưa Approve plan hiện tại, hoặc chưa đối chiếu trùng với Jira | Approve lại, bật 🔗 Kết nối Jira, nói *"kiểm tra trùng với Jira"* |
| Không thấy tool Jira trong agent | Chưa có `JIRA_MCP_COMMAND` hoặc đang tắt Kết nối Jira | Cấu hình `.env`, bật 🔗 Kết nối Jira (lần đầu mở kết nối mất vài giây) |
| `UnsupportedOperation: fileno` khi mở Jira MCP trong Jupyter | stderr của kernel không có file descriptor | Đã xử lý: log của server ghi vào `jira_mcp_server.log` trong thư mục temp |
| Đọc to báo lỗi | Chưa cài `transformers`/`torch`/`scipy`, hoặc chưa có mạng ở lần tải model đầu | `uv pip install -r requirements.txt`, kiểm tra mạng |
| `VECTOR_BACKEND=pinecone needs PINECONE_API_KEY` | Thiếu khoá | Thêm `PINECONE_API_KEY` vào `.env` |
