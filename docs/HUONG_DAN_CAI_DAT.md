# Hướng dẫn cài đặt từ máy trắng

Tài liệu này dẫn từng bước từ một máy **chưa cài gì** đến khi chạy được ứng dụng và notebook. Có lệnh cho **Windows** và **macOS / Linux**.

## 0. Tổng quan

| Hạng mục | Giá trị |
|---|---|
| Cần cài sẵn | **Git** và **uv** (uv tự tải Python 3.12, không cần cài Python riêng) |
| Không cần | Node.js, Docker, PostgreSQL, tài khoản cloud (trừ khi bạn chọn dùng) |
| Dung lượng đĩa | khoảng 4–6 GB (thư viện, torch, model Hugging Face) |
| RAM | từ 8 GB trở lên |
| Thời gian | cài thư viện 3–10 phút; lần chạy đầu tải thêm model vài phút |
| Mạng | cần Internet khi cài và ở lần chạy đầu |
| Khoá bắt buộc | `LLM_API_KEY` (AI gateway). Jira và Pinecone là tuỳ chọn |

## 1. Cài công cụ nền

### 1.1 Git

| Hệ điều hành | Lệnh |
|---|---|
| Windows | `winget install --id Git.Git -e` (hoặc tải ở https://git-scm.com/download/win) |
| macOS | `xcode-select --install` hoặc `brew install git` |
| Ubuntu / Debian | `sudo apt update && sudo apt install -y git` |

Kiểm tra: `git --version`.

### 1.2 uv (quản lý Python và thư viện)

| Hệ điều hành | Lệnh |
|---|---|
| Windows (PowerShell) | `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"` |
| macOS / Linux | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Cách khác | `winget install --id astral-sh.uv -e` (Windows) hoặc `brew install uv` (macOS) |

**Đóng terminal rồi mở lại** để `uv` nằm trong PATH, sau đó kiểm tra: `uv --version`.

`uv` cũng cung cấp lệnh `uvx`, dùng để mở Jira MCP server (`mcp-atlassian`) mà không cần cài riêng.

> Muốn tự cài Python thay vì để uv tải: cài **Python 3.12** từ https://www.python.org/downloads/ và nhớ tick **Add python.exe to PATH**. Dự án chạy tốt trên Python 3.12.

### 1.3 VS Code (chỉ cần nếu muốn chạy notebook)

Cài VS Code, rồi cài hai extension **Python** và **Jupyter** (cùng của Microsoft).

## 2. Lấy mã nguồn

```bash
git clone https://github.com/Feliss-dev/hackathon-pm-agent.git
cd hackathon-pm-agent
```

(Nếu bạn dùng repo khác thì thay URL tương ứng.) Mọi lệnh bên dưới chạy trong thư mục dự án vừa clone.

## 3. Tạo môi trường ảo `.venv`

`.venv` là một thư mục chứa riêng Python và các thư viện của dự án, để không ảnh hưởng đến Python của hệ thống. Tạo bằng uv:

```bash
uv venv --python 3.12 .venv
```

Lệnh này tự tải Python 3.12 nếu máy chưa có, và tạo thư mục `.venv/`.

### Kích hoạt (không bắt buộc)

Kích hoạt chỉ để gõ ngắn `python`, `streamlit`. Nếu không kích hoạt, bạn gọi thẳng chương trình trong `.venv` (ví dụ `.venv\Scripts\python.exe`).

| Terminal | Lệnh kích hoạt |
|---|---|
| Windows PowerShell | `.venv\Scripts\Activate.ps1` |
| Windows cmd | `.venv\Scripts\activate.bat` |
| Git Bash (Windows) | `source .venv/Scripts/activate` |
| macOS / Linux | `source .venv/bin/activate` |

Kích hoạt xong, dòng lệnh có tiền tố `(.venv)`. Thoát bằng `deactivate`.

Nếu PowerShell báo *"running scripts is disabled on this system"*, chạy một lần:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, rồi kích hoạt lại.

## 4. Cài thư viện

Đường dẫn Python trong `.venv`: Windows `.venv\Scripts\python.exe`; macOS / Linux `.venv/bin/python`.

```bash
# Windows
uv pip install --python .venv\Scripts\python.exe -r requirements.txt

# macOS / Linux
uv pip install --python .venv/bin/python -r requirements.txt
```

(Đã kích hoạt `.venv` thì chỉ cần `uv pip install -r requirements.txt`.)

Bước này tải khá nhiều vì có `torch` (cho Hugging Face). Các nhóm thư viện:

| Nhóm | Thư viện |
|---|---|
| AI và LLM | `openai`, `anthropic`, `tenacity`, `pydantic` |
| Agent và RAG | `langgraph`, `langchain-core`, `langchain-openai`, `langchain-community`, `langchain-text-splitters` |
| Vector | `chromadb`, `pinecone` |
| Hugging Face | `sentence-transformers`, `transformers`, `torch`, `scipy` |
| Jira | `mcp` |
| Dữ liệu | `sqlalchemy`, `pandas`, `openpyxl`, `python-docx`, `pypdf`, `rapidfuzz`, `pyyaml` |
| Giao diện | `streamlit` |
| Khác | `python-dotenv`, `pytest`, `ipykernel`, `nbclient`, `nbformat` |

Kiểm tra:

```bash
uv pip list --python .venv/bin/python        # Windows: .venv\Scripts\python.exe
```

> **Dùng pip thay uv:** `python -m venv .venv`, kích hoạt, rồi `pip install -r requirements.txt`. (Venv tạo bằng uv không có sẵn `pip`, nên dùng `uv pip`.)

## 5. Cấu hình `.env`

```bash
cp .env.example .env          # Windows cmd: copy .env.example .env
```

Mở `.env` bằng trình soạn thảo và điền. **Tối thiểu chỉ cần `LLM_API_KEY`.**

| Biến | Bắt buộc | Lấy ở đâu |
|---|---|---|
| `LLM_API_KEY` | **Có** | Khoá AI gateway STU do khoá học cấp |
| `LLM_BASE_URL` | Không (đã có mặc định) | Giữ `https://aiportalapi.stu-platform.live/jpe`. **Không** dùng `.../jpe/v2` |
| `JIRA_URL` | Không | Địa chỉ Jira, ví dụ `https://ten-cong-ty.atlassian.net` |
| `JIRA_USERNAME` | Không | Email tài khoản Atlassian |
| `JIRA_API_TOKEN` | Không | https://id.atlassian.com/manage-profile/security/api-tokens → **Create API token** |
| `JIRA_MCP_COMMAND` | Không | Giữ `uvx mcp-atlassian` (cần `uv`) |
| `PINECONE_API_KEY` | Không | https://app.pinecone.io → **API Keys** (gói Starter miễn phí) |
| `EMBEDDING_PROVIDER` | Không | `openai` (mặc định), `hf` (chạy local, không cần key) hoặc `auto` |
| `VECTOR_BACKEND` | Không | `chroma` (mặc định) hoặc `pinecone` |

Không có Jira thì để trống các biến `JIRA_*`: app vẫn chạy, chỉ không đối chiếu và không đẩy lên Jira (trạng thái hiện 🔴 Jira).

**An toàn:** `.env` đã nằm trong `.gitignore` nên không bị commit. Không dán khoá vào chat, ảnh chụp màn hình hay issue.

## 6. Tạo dữ liệu mẫu

```bash
.venv\Scripts\python.exe samples\make_samples.py      # macOS / Linux: .venv/bin/python samples/make_samples.py
```

Lệnh này tạo thêm các file mẫu không nằm trong git (ví dụ `samples/task_tracking.xlsx`, `samples/workshop_package/`).

## 7. Chạy ứng dụng

**Windows:** nhấp đúp `run_app.bat`, hoặc:

```bash
.venv\Scripts\python.exe -m streamlit run app.py --server.address localhost
```

**macOS / Linux:**

```bash
.venv/bin/python -m streamlit run app.py --server.address localhost
```

Mở `http://localhost:8501`. Dừng app bằng `Ctrl+C`. Cổng 8501 bận thì thêm `--server.port 8502`.

Thư mục `data/` (SQLite và ChromaDB) tự được tạo ở lần chạy đầu.

## 8. Kiểm tra đã cài đúng

1. **Test tự động** (không cần mạng, không cần khoá):
   ```bash
   .venv\Scripts\python.exe -m pytest -q          # macOS / Linux: .venv/bin/python -m pytest -q
   ```
   Kết quả mong đợi: `68 passed`.
2. **App:** thanh trạng thái thấy 🟢 AI (và 🟢 Jira nếu đã điền Jira).
3. **Thử một câu:** gõ `Phân tích file mẫu task_review.xlsx`. Agent trả về 6 task, vài cảnh báo estimate và cây Epic → Story → Task.

## 9. Chạy notebook

1. Mở thư mục dự án bằng VS Code, mở [notebooks/hackathon_workshop3.ipynb](../notebooks/hackathon_workshop3.ipynb).
2. Góc phải trên chọn **Select Kernel → Python Environments →** `.venv`.
3. Bấm **Run All**. Cell đầu kiểm tra khoá và các dịch vụ.
4. Chưa có khoá: đặt `REQUIRE_LIVE = False` ở cell Setup để chạy bản offline.

Dùng Jupyter Lab riêng: đăng ký kernel một lần bằng
`.venv\Scripts\python.exe -m ipykernel install --user --name hackathon --display-name "Hackathon (.venv)"`, rồi chọn kernel **Hackathon (.venv)**.

## 10. Lần chạy đầu tiên: những gì được tải về

| Khi nào | Tải gì | Lưu ở đâu |
|---|---|---|
| Dùng embedding Hugging Face (`hf`/`auto` không key) | `paraphrase-multilingual-MiniLM-L12-v2` (khoảng 470 MB) | `~/.cache/huggingface` |
| Bấm 🔊 Đọc to hoặc chạy cell TTS | `facebook/mms-tts-vie` (khoảng 150 MB) | `~/.cache/huggingface` |
| Lần đầu mở Jira MCP | `mcp-atlassian` qua `uvx` (vài chục giây) | cache của uv |
| Lần đầu dùng Pinecone | tạo index trên tài khoản Pinecone (khoảng 10–60 giây) | đám mây Pinecone |

Muốn đặt cache Hugging Face sang ổ khác: đặt biến môi trường `HF_HOME`.

## 11. Cập nhật và dọn dẹp

```bash
git pull
uv pip install --python .venv\Scripts\python.exe -r requirements.txt     # cài thêm thư viện mới nếu có
```

Làm lại từ đầu: xoá thư mục `.venv/` (và `data/` nếu muốn xoá lịch sử và vector), rồi làm lại từ mục 3.

## 12. Xử lý sự cố

| Triệu chứng | Nguyên nhân | Cách xử lý |
|---|---|---|
| `uv: command not found` | Terminal chưa nạp PATH | Đóng và mở lại terminal; Windows có thể cần đăng xuất rồi đăng nhập lại |
| `uvx` không tìm thấy khi mở Jira | Chưa cài uv hoặc PATH chưa có | Cài uv (mục 1.2), mở lại terminal |
| `Activate.ps1 cannot be loaded` | PowerShell chặn script | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| `ModuleNotFoundError: ...` | Chạy bằng Python ngoài `.venv` | Dùng `.venv\Scripts\python.exe` hoặc kích hoạt `.venv`; trong notebook chọn đúng kernel `.venv` |
| `streamlit` không được nhận diện | Chưa kích hoạt `.venv` | Dùng `python -m streamlit run app.py` như mục 7 |
| Cài thư viện rất chậm hoặc báo hết đĩa | `torch` lớn | Giải phóng đĩa (cần 4–6 GB); thử lại, uv sẽ tiếp tục từ cache |
| Lỗi SSL / proxy khi tải | Mạng công ty chặn | Đặt `HTTPS_PROXY`; thử `set UV_NATIVE_TLS=1` (Windows) hoặc `export UV_NATIVE_TLS=1` |
| Lỗi tải model Hugging Face | Mất mạng hoặc bị giới hạn | Thử lại; chạy với embedding `openai` nếu chỉ cần chat |
| 🔴 AI, lỗi 401 | Thiếu hoặc sai `LLM_API_KEY` | Điền lại khoá trong `.env`, khởi động lại app |
| Agent lặp cùng một tool | Dùng URL `.../jpe/v2` | Đổi `LLM_BASE_URL` về `.../jpe` |
| Port 8501 đã dùng | App cũ còn chạy | Đóng app cũ hoặc dùng `--server.port 8502` |
| Lỗi đường dẫn quá dài trên Windows | Clone vào thư mục quá sâu | Clone gần gốc ổ đĩa, ví dụ `C:\work\` |
| `UnsupportedOperation: fileno` khi mở Jira MCP | stderr của kernel Jupyter | Đã xử lý trong code (log ghi vào `jira_mcp_server.log` trong thư mục temp); cập nhật code bằng `git pull` |

Cách dùng ứng dụng sau khi cài xong: [HUONG_DAN_SU_DUNG.md](HUONG_DAN_SU_DUNG.md).
