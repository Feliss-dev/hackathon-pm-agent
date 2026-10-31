# Test Cases — Nhóm 3 (Hackathon)

## 1. Tổng quan

| Hạng mục | Giá trị |
|---|---|
| Lệnh chạy | `.venv\Scripts\python.exe -m pytest -q` (macOS/Linux: `.venv/bin/python -m pytest -q`) |
| Số test tự động | **68** (33 từ workshop 1, 16 từ workshop 3, 19 từ Hackathon) |
| Kết quả lần chạy gần nhất | **68 passed** (31/10/2026) |
| Điều kiện | Không cần mạng, không cần khoá API: SQLite in-memory, ChromaDB tạm, embedder giả, Jira MCP giả, Pinecone in-memory |
| Kiểm tra bằng tay | Mục 3: dùng AI gateway, Jira và Pinecone thật |

Số test theo file: `test_rules` 11 · `test_pipeline` 9 · `test_db_vectors` 7 · `test_graph_lc` 7 · `test_excel_workshop` 6 · `test_jira_tools` 6 · `test_pinecone_speech` 6 · `test_config` 4 · `test_retry` 4 · `test_ai_rules` 3 · `test_tools` 3 · `test_sync` 2.

## 2. Test case tự động

| ID | Nhóm | Các bước | Kết quả mong đợi | Test | Kết quả |
|---|---|---|---|---|---|
| TC01 | Function calling | Dựng schema các tool | Tên không trùng; không còn `$ref`/`$defs`; thuộc tính tên `title` được giữ; không tool nào của dự án ghi Jira | `test_tools::test_schemas_are_plain_json_schema` | Pass |
| TC02 | Function calling | Gọi `update_tasks` với field `category`; gọi tool không tồn tại | Cả hai trả `{"error": ...}` về cho model; mỗi lần gọi được lưu vào `tool_calls` | `test_tools::test_invalid_arguments_go_back_to_the_model` | Pass |
| TC03 | Vòng lặp agent | Model gọi `analyze`, rồi `show_plan`, rồi trả lời | Tool chạy đúng thứ tự; lịch sử user/assistant/tool; CURRENT STATE được làm mới mỗi vòng | `test_tools::test_agent_loop_runs_tools_and_answers` | Pass |
| TC04 | Retry | API trả 429 hai lần rồi thành công | 3 lần gọi, 2 ghi chú retry | `test_retry::test_rate_limit_is_retried_then_succeeds` | Pass |
| TC05 | Retry | API luôn trả 503 | Bỏ cuộc sau số lần thử tối đa | `test_retry::test_server_error_retried_but_gives_up` | Pass |
| TC06 | Retry | API trả 401 | Không thử lại, báo lỗi ngay | `test_retry::test_client_errors_are_not_retried` | Pass |
| TC07 | Batching | Embed 250 text, rồi 10 text cũ + 1 text mới | Các request 100 + 100 + 50; lần sau chỉ gửi 1 text (nhờ cache) | `test_retry::test_embeddings_are_batched_and_cached` | Pass |
| TC08 | CSDL | Lưu task hai lần, một task đổi, một task bị xoá | Task đổi lên version 2; task xoá còn lại trong lịch sử; plan giống nhau giữ nguyên version | `test_db_vectors::test_repository_versions_tasks_and_plans` | Pass |
| TC09 | Cache AI | Đánh giá lại cùng các task bằng assessor mới (như sau khi khởi động lại) | LLM chỉ được gọi một lần | `test_db_vectors::test_assessment_cache_survives_a_new_assessor` | Pass |
| TC10 | RAG trên rule | Truy xuất rule cho task "review" và "unit test" | Chỉ rule liên quan, đúng thứ tự ưu tiên gốc; rule không đổi thì không index lại | `test_db_vectors::test_relevant_rules_come_back_in_priority_order` | Pass |
| TC11 | Trùng theo nghĩa | Phân tích cùng một Excel ở lần chạy 1 rồi lần chạy 2 | Lần 2: 14 cảnh báo "history" (mức Low, không chặn) | `test_db_vectors::test_session_persists_and_finds_history` | Pass |
| TC12 | Trùng theo nghĩa | Lần 1 đã đẩy lên Jira; phân tích lại cùng Excel | Task trùng bị bỏ qua với lý do "Possible Duplicate of KAN-x" | cùng test trên | Pass |
| TC13 | Lịch sử | Mở lại lần chạy 1 bằng 8 ký tự đầu của id | Cùng version plan; task giữ Jira key | cùng test trên | Pass |
| TC14 | RAG tài liệu | Đính kèm BRD, đặt câu hỏi | Trả về các đoạn kèm tên file | `test_db_vectors::test_ask_requirements_returns_cited_passages` | Pass |
| TC15 | Chịu lỗi | Dịch vụ embedding ngừng hoạt động | Plan vẫn được dựng; cảnh báo hiện trong state / thanh bên | `test_db_vectors::test_vector_outage_does_not_break_the_plan` | Pass |
| TC16 | Đồng bộ Excel | Xoá một dòng, đổi estimate và người làm, thêm một dòng | 1 thêm, 1 xoá, 2 thay đổi trường, 12 không đổi; chưa áp dụng gì | `test_sync::test_diff_only_reports` | Pass |
| TC17 | Đồng bộ Excel | Cùng thay đổi, áp dụng với task đã có trên Jira (KAN-7) | Plan lên v2; node KAN-7 là "update assignee+estimate"; push cập nhật KAN-7 và không tạo mới; push lần hai không cập nhật lại | `test_sync::test_apply_creates_new_plan_version_and_updates_jira_tasks` | Pass |
| TC18 | Hồi quy workshop 1 | Pipeline, rule, Excel, kiểm tra workshop của workshop 1 | Tất cả đạt | `test_pipeline.py`, `test_rules.py`, `test_ai_rules.py`, `test_config.py`, `test_excel_workshop.py` | Pass |
| TC19 | Agent LangGraph | Model giả gọi `analyze`; chạy với `interrupt=True` | Đồ thị dừng trước tool; lịch sử của người gọi không đổi; `resume(approve=True)` chạy tool và kết thúc | `test_graph_lc::test_pause_before_tools_leaves_history_clean_then_resume` | Pass |
| TC20 | Agent LangGraph | Như trên nhưng người dùng từ chối | Tool không chạy; model thấy "declined" và trả lời | `test_graph_lc::test_decline_tells_the_model_and_skips_the_tool` | Pass |
| TC21 | Agent LangGraph | Model liên tục gọi tool; tham số sai hoặc JSON hỏng | Bỏ cuộc sau `MAX_TOOL_ROUNDS` với thông báo chuẩn; lỗi tham số quay lại cho model | `test_graph_lc::test_round_limit_gives_up`, `test_bad_arguments_reach_the_model_as_errors` | Pass |
| TC22 | Agent LangGraph | Callback `confirm` nhận danh sách tool sắp chạy | `confirm` được gọi với đúng các tool call đang chờ | `test_graph_lc::test_confirm_callback_receives_the_pending_tool_calls` | Pass |
| TC23 | LangChain | Retriever, chuỗi LCEL, `ask_requirements(answer=true)`, registry thành `StructuredTool` | Document có file / chunk / điểm; câu trả lời trích dẫn đoạn; tool khớp registry | `test_graph_lc::test_retriever_chain_and_grounded_answer`, `test_langchain_tools_mirror_the_registry` | Pass |
| TC24 | Pinecone | `VectorStore(backend="pinecone")` trên index in-memory | Kết quả như Chroma: lọc theo project / run, văn bản đoạn đi qua metadata, rule không index lại, giữ thứ tự rule | `test_pinecone_speech::test_tasks_filter_by_project_and_run`, `test_docs_rules_and_stats`, `test_backend_selection_errors` | Pass |
| TC25 | Đọc to (TTS) | Đọc số thành chữ tiếng Việt, câu tóm tắt, ghi file wav, `speak_plan` | Số được đọc thành chữ; wav được ghi; file nằm trong mục tải về | `test_pinecone_speech::test_viet_numbers_and_summary_text`, `test_synthesize_writes_a_wav_and_session_offers_it` | Pass |
| TC26 | Embedding | `hf` / `openai` / `auto` khi có và không có khoá | Tên model OpenAI không bao giờ dùng cho `hf`; `auto` quay về Hugging Face khi không có khoá | `test_pinecone_speech::test_embedding_provider_and_model_resolution` | Pass |
| TC27 | Jira MCP tools | Bridge MCP giả: tool đọc chạy ngay, tool ghi vào hàng chờ; duyệt / từ chối / lỗi; chính sách allow / deny | Chưa duyệt thì không ghi gì; mọi quyết định được ghi audit | `test_jira_tools.py` (6 test) | Pass |

## 3. Kiểm tra bằng tay (AI gateway, Jira và Pinecone thật)

| ID | Các bước | Kết quả mong đợi | Trạng thái |
|---|---|---|---|
| M01 | `python -m delivery_agent --profile KAN index-rules` | `common rules: 26 chunks`, `KAN rules: 3 chunks` | Chưa chạy lại ở lần kiểm tra cuối |
| M02 | Chat: "Phân tích file mẫu task_review.xlsx, rồi cho biết trước đây đã có task nào giống 'Create Customer API' chưa" | Các tool `list_files` → `analyze` → `search_similar_tasks`; câu trả lời nêu lần chạy trước | Đạt: agent thật gọi `list_files`, `analyze`, `ask_requirements`, `search_similar_tasks` |
| M03 | Đính kèm `brd_customer_portal.md`, hỏi "Yêu cầu đăng ký khách hàng là gì?" | `ask_requirements` trả mục 2.1 của BRD kèm tên file | Đạt: câu trả lời trích đúng mục 2.1 và 1 của BRD |
| M04 | Khởi động lại app → **🕘 Lịch sử** → chọn một lần chạy cũ | Chat, plan và bảng review hiện lại | Chưa chạy lại trên giao diện (phần lưu trữ đã có test TC13) |
| M05 | Thanh bên **🔄 Đồng bộ từ Excel đã sửa** → tải Excel đã sửa → So sánh → Áp dụng | Bảng khác biệt; version plan tăng; phải Approve lại | Đạt ở mức module và notebook (ghép theo nghĩa); chưa bấm trên giao diện |
| M06 | **⚙️ Hệ thống** → bật **✋ Duyệt từng bước**, hỏi "Phân tích task_review.xlsx" | Chat dừng và hiện danh sách tool; **▶️ Cho chạy** tiếp tục đến câu trả lời | Đạt qua Streamlit AppTest với LLM và Jira thật (dừng ở `list_files` và `analyze`, rồi trả lời) |
| M07 | Chat: "Trên Jira KAN có Epic nào? Thêm comment 'test' vào KAN-22" | Epic hiện ngay; comment nằm ở **🧩 Jira actions**; chưa gì lên Jira cho đến khi **Approve** | Đạt: Epic KAN-1 hiện ra; comment nằm trong hàng chờ, **chưa duyệt, chưa ghi lên Jira** |
| M08 | Thanh bên **🔊 Đọc to tóm tắt plan** | Hiện trình phát; `plan_summary.wav` có trong **⬇️ Tải về** | Đạt ở mức module và notebook (tạo wav 8 giây, model tiếng Việt); chưa bấm nút trên giao diện |
| M09 | **⚙️ Hệ thống** → Vector store = Pinecone (cần `PINECONE_API_KEY`), rồi phân tích và hỏi về BRD | Index được tạo ở lần dùng đầu; RAG và tìm task tương tự vẫn chạy | Đạt ở mức `VectorStore` trên Pinecone thật (ghi, truy vấn có filter, xoá); chưa chọn trên giao diện |
| M10 | Máy mới: làm theo `docs/HUONG_DAN_CAI_DAT.md` đến hết mục 8 | 68 test pass; app mở được | Đạt trên Windows với bản sạch (không `.env`, không `data/`); chưa thử macOS/Linux |
| M11 | Notebook `hackathon_workshop3.ipynb`: Run All với kernel `.venv` | Không lỗi; Jira MCP, Pinecone, LLM, TTS đều chạy | Đạt (kernel Jupyter thật, khoảng 4 phút, 0 lỗi) |

## 4. Cách tái hiện nhanh

```bash
.venv\Scripts\python.exe -m pytest -q                              # 68 test, không cần mạng
.venv\Scripts\python.exe -m pytest tests/test_graph_lc.py -q       # riêng LangGraph và LangChain
.venv\Scripts\python.exe -m pytest tests/test_jira_tools.py -q     # riêng Jira MCP tools và cổng duyệt
```
