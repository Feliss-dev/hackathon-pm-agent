# Business Description — Nhóm 3

**Chủ đề:** Trợ lý AI đọc yêu cầu, tách thành task, ước lượng và kiểm tra theo rule của nhóm, rồi đưa vào Jira sau khi con người duyệt.

**Tên sản phẩm:** AI Delivery Planning & Jira Automation Agent (Hackathon — Nhóm 3)

## 1. Thành viên và phân công

| # | Họ tên | Account | Phần việc (theo Task list của nhóm) |
|---|---|---|---|
| 1 | Nguyễn Ngọc Anh | AnhNN249 | Giao diện import requirement (FE) |
| 2 | Trần Nguyễn Huy | HuyTN19 | Code để rule chỉ áp dụng cho dự án được chọn |
| 3 | Hoàng Minh Thành | ThanhHM17 | Agent, function calling, retry/batching, DB, vector + RAG, đồng bộ Excel, LangGraph/LangChain, Pinecone, Jira MCP, Hugging Face (embedding và đọc to), notebook, tài liệu và test |
| 4 | Nguyễn Bình Minh | MinhNB3 | Rule ưu tiên task, điền start date / end date |
| 5 | Nguyễn Nhật Minh | MinhNN76 | Skill tạo và cập nhật các rule |
| 6 | Nguyễn Thế Trung | TrungNT229 | Rule tách task riêng cho dự án (naming convention, prefix, postfix) |
| 7 | Đoàn Mạnh Dương | DuongDM12 | Skill đọc requirement, sinh task và estimate theo template |
| 8 | Phùng Minh Quân | QuanPM3 | Rule estimate |

Chi tiết từng việc, estimate và trạng thái: file `docs/task_list.xlsx`.

## 2. Bài toán

PM và Delivery lead mất nhiều giờ biến BRD và file Excel thành issue Jira bằng tay:

- Task được chép thủ công; **phân loại và estimate không theo quy ước của nhóm**.
- **Cùng một việc bị tạo hai lần**, ở các sprint khác nhau hoặc do những người khác nhau.
- Khi kế hoạch trong Excel đổi, **Jira lệch dần so với Excel** mà không ai hay.
- Không ai giải thích được về sau *vì sao* một task có loại và estimate như vậy.
- Rule của nhóm nằm trong đầu từng người, không có chỗ cho AI hay hệ thống áp dụng nhất quán.

## 3. Giải pháp

Một **chat agent (Streamlit)** cho PM. PM đính kèm BRD hoặc Excel và yêu cầu bằng tiếng Việt, ví dụ *"Phân tích file này và gắn vào Epic KAN-1"*. Agent làm các việc sau:

1. **Trích xuất task:** Excel đọc trực tiếp; BRD (`.md .txt .docx .pdf`) do LLM trích theo JSON schema chặt, không được bịa estimate.
2. **Phân loại và rà estimate theo rule do người viết** (Markdown: rule chung và rule riêng từng dự án). AI chỉ *đọc rule*; **code mới so sánh số**. Mỗi quyết định có lý do. Task tương tự ở các plan cũ được đưa ra làm bằng chứng.
3. **Kiểm tra:** trường bắt buộc, trùng lặp **theo chữ** (trong batch, với Jira, với Epic đích) và **theo nghĩa** (embedding, so với các plan trước), estimate vượt ngưỡng trong rule.
4. **Dựng cây Jira** Epic → Story → Task → Subtask (loại issue, label, component, sprint, estimate, link phụ thuộc).
5. **Chờ người duyệt.** Phê duyệt gắn với mã hash SHA-256 của plan: sửa plan thì phải duyệt lại.
6. **Đẩy lên Jira qua MCP**, có checkpoint để chạy lại khi lỗi giữa chừng mà không tạo trùng.
7. **Đồng bộ Excel → Jira:** khi Excel đổi, agent liệt kê thêm / sửa / xoá; áp dụng thì **cập nhật** issue đã có thay vì tạo mới, không xoá gì trên Jira.
8. **Hỏi đáp tài liệu có trích dẫn (RAG)** và **tìm task tương tự** để trả lời "đã làm việc này chưa, mất bao lâu".
9. **Dùng đủ công cụ của Jira MCP:** agent có 63 tool Jira (tìm bằng JQL, xem issue, sprint, board, link, comment, transition…). Tool đọc chạy ngay; tool ghi chỉ được *đề xuất* và chờ người duyệt.

Toàn bộ dữ liệu (phiên làm việc, task có phiên bản, plan có phiên bản, phê duyệt, audit log, kết quả đẩy Jira) lưu trong cơ sở dữ liệu. Có thể mở lại phiên cũ, và kho tri thức (task, rule, tài liệu) lớn dần theo từng dự án.

## 4. Người dùng và giá trị

| Người dùng | Giá trị |
|---|---|
| PM / Delivery lead | Có plan sau vài chục giây thay vì vài giờ; phân loại và estimate nhất quán; không tạo issue trùng; luôn biết Jira có khớp Excel không |
| Thành viên nhóm | Task rõ ràng, có tiêu chí chấp nhận và nguồn gốc trong tài liệu |
| QA / Kiểm toán | Mọi quyết định có lý do và được ghi lại: ai duyệt gì, khi nào, đã đổi gì |
| Quản lý | Rule nằm trong file Markdown mà PM tự sửa, không cần sửa code |

Số đo thực tế khi chạy bằng AI gateway: đọc rule cho 6 task mất khoảng 15 giây; trích 18 task từ một BRD mẫu mất khoảng 34 giây; dựng plan từ Excel mất khoảng 1–2 giây khi kết quả đọc rule đã nằm trong cache.

## 5. Phạm vi Hackathon

Hackathon gom công nghệ của cả 5 tuần học vào một sản phẩm chạy được:

| Tuần | Công nghệ | Dùng trong sản phẩm |
|---|---|---|
| 1 | Python, Pydantic, OpenAI API (chat, structured output) | Model dữ liệu, AI đọc rule, trích task từ tài liệu |
| 2 | Function calling, batching, retry | 17 tool có kiểu, 40 task/batch chạy song song, backoff cho 429/5xx |
| 3 | Hugging Face, embeddings | Embedding OpenAI hoặc Hugging Face đa ngôn ngữ chạy local; đọc to tóm tắt plan bằng HF TTS |
| 4 | Pinecone, LangChain | Vector store ChromaDB hoặc Pinecone; retriever và tool của LangChain |
| 5 | RAG, LangGraph | Hỏi đáp BRD có trích dẫn; agent là LangGraph có thể dừng chờ người duyệt từng bước |
| — | MCP | Đọc, ghi Jira; 63 tool cho agent với cổng duyệt khi ghi |

## 6. Kiến trúc tóm tắt

```
BRD / Excel ──► Trích xuất ──► Chia đoạn ──► Embedding ──► Vector DB (ChromaDB | Pinecone)
rule (Markdown) ───────────────────────────────────────────────▲
                                                               │ tìm theo nghĩa
PM chat ──► Agent LangGraph ──► function calling (17 tool + 63 tool Jira MCP)
                   │                  │
                   │          AI đọc rule ─► CODE so estimate, kiểm tra, tìm trùng
                   ▼                  ▼
         (tuỳ chọn) duyệt         Plan Epic→Story→Task→Subtask ─► Người Approve (hash) ─► Push Jira (MCP)
         từng bước
```

Chi tiết từng module: `README.md`.

## 7. Nguyên tắc an toàn

- **Không có gì lên Jira khi chưa có người duyệt**, và không có cờ bỏ qua bước duyệt.
- Plan bị sửa sau khi duyệt → phê duyệt cũ vô hiệu (so hash).
- Tool ghi Jira của agent chỉ được đề xuất; thực thi khi người bấm duyệt (chính sách `approve` mặc định).
- LLM không bịa estimate, mọi so sánh số do code làm; khoá API không bao giờ xuất hiện trong câu trả lời.
- Task thiếu trường bắt buộc hoặc nghi trùng bị bỏ qua (SKIP) trong plan, trừ khi PM chủ động cho phép.
- Mọi bước ghi vào audit log.

## 8. Giới hạn hiện tại

- Chất lượng phân loại và estimate phụ thuộc rule do nhóm viết; rule mơ hồ cho kết quả mơ hồ.
- Ngưỡng tương đồng theo nghĩa cần chỉnh theo từng embedding (cặp task chỉ diễn đạt lại thường chỉ đạt 0.77–0.87).
- Một plan gắn với một Epic; chưa map Iteration Path sang Sprint tự động.
- Chưa có đăng nhập người dùng (người duyệt tự nhập tên).
- Pinecone nhất quán chậm: dữ liệu vừa ghi có thể mất vài giây mới tìm thấy.
- Jira chuẩn chỉ cho Epic làm cha của Task, nên task gắn vào Epic và nối với Story bằng link `Relates`.

## 9. Hướng phát triển

Nhiều Epic trong một plan; map Iteration Path sang Sprint; đăng nhập và phân quyền người duyệt; REST API cho hệ thống khác; hỗ trợ Azure DevOps; ước lượng giờ theo trọng số tương đồng từ lịch sử; thêm bộ đánh giá chất lượng phân loại (có nhãn chuẩn).

## 10. Tài liệu liên quan

`README.md` · `docs/HUONG_DAN_CAI_DAT.md` · `docs/HUONG_DAN_SU_DUNG.md` · `docs/TEST_CASES.md` · `docs/HUONG_DAN_THUYET_TRINH.md` · `docs/task_list.xlsx` · `notebooks/hackathon_workshop3.ipynb`
