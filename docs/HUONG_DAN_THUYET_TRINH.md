# Hướng dẫn thuyết trình – AI Delivery Planning & Jira Automation Agent

Thời lượng gợi ý: **12–15 phút** (10 phút trình bày + demo, còn lại hỏi đáp).

---

## 1. Thông điệp chính

Nói ở đầu và nhắc lại ở cuối:

> **"Agent biến tài liệu và Excel thành kế hoạch Jira trong vài phút. AI đọc, phân loại và gợi ý theo rule
> do PM viết; con người duyệt; không có gì lên Jira nếu chưa được Approve."**

Ba ý người nghe cần nhớ:

1. **Tự động hóa phần tốn thời gian:** trích task, phân loại, rà estimate, kiểm tra trùng, dựng cây
   Epic → Story → Task → Subtask.
2. **Rule do người viết bằng ngôn ngữ tự nhiên:** đổi rule không cần sửa code; mỗi quyết định có lý do.
3. **An toàn:** bắt buộc người duyệt, plan khóa bằng mã hash, mọi bước ghi vào audit log.

---

## 2. Cấu trúc slide

| # | Slide | Nội dung chính | Thời gian |
|---|---|---|---|
| 1 | **Vấn đề** | PM mất nhiều giờ chép task từ BRD/Excel sang Jira, phân loại tay, estimate lệch chuẩn, tạo trùng issue, không ai biết vì sao task bị xếp loại đó | 1' |
| 2 | **Giải pháp** | AI agent dạng chat: đính kèm file → agent phân tích → bảng review → người duyệt → đẩy Jira | 1' |
| 3 | **Luồng 7 bước** | Trích xuất → Phân loại → Rà estimate → Kiểm tra → Tóm tắt → **Người duyệt** → Đẩy Jira. Nhấn mạnh bước 6 là cổng bắt buộc | 1' |
| 4 | **Kiến trúc** | Sơ đồ ở mục 4 | 1,5' |
| 5 | **Rule dạng prompt** | Ví dụ rule KAN: *"Task thanh toán là BE"*, *"Họp tối đa 2h"*. AI đọc rule, code so sánh số | 1' |
| 6 | **DEMO** | Xem mục 3 | 4–5' |
| 7 | **An toàn & truy vết** | Approve gắn hash; sửa plan phải duyệt lại; đẩy lỗi chạy lại thì tiếp tục, không tạo trùng; audit log | 1' |
| 8 | **Kết quả & giới hạn** | Xem mục 5 | 1' |
| 9 | **Hướng phát triển** | Nhiều Epic mỗi plan, map Iteration Path sang Sprint, đăng nhập người dùng, REST API cho hệ thống khác | 0,5' |

---

## 3. Kịch bản demo

### 3.1. Chuẩn bị (10 phút trước giờ trình bày)

- [ ] Nhấp đúp `run_app.bat`, mở `http://localhost:8501`, thấy 🟢 AI và 🟢 Jira.
- [ ] Chọn bộ rule **KAN**, bật **🔗 Kết nối Jira**.
- [ ] Mở sẵn tab Jira space KAN để đối chiếu sau khi đẩy.
- [ ] Xóa các issue demo của lần trước, hoặc dùng một space riêng cho demo. Nếu không, lần đẩy mới sẽ bị
      đánh dấu nghi trùng (đúng thiết kế, nhưng làm demo rối).
- [ ] **Đã chạy thử trọn bước Đẩy Jira ít nhất một lần** trước buổi thuyết trình.
- [ ] Chụp sẵn ảnh màn hình từng bước. Mạng hoặc gateway chậm thì chuyển sang ảnh, không chờ.

### 3.2. Các bước demo

**Bước 1 – Chat bằng lời**

Gõ:

```
Phân tích full_hierarchy_task_review.xlsx, gắn vào Epic KAN-1
```

Lời thoại: *"Tôi chỉ nói bằng lời, agent tự chọn các bước cần chạy."*
Mở mục **🔧 Đã chạy N bước** để khán giả thấy các tool agent đã gọi.

**Bước 2 – Bảng review ở thanh bên**

- Chỉ vào số liệu: 20 task, 23 issue sẽ tạo, 0 bị skip.
- Mở tab **🌳 Cây Jira**: 3 Story, các Task và Subtask.
- Cột **Lý do phân loại**: *"Mỗi quyết định đều có lý do. Ví dụ màn hình thanh toán là BE vì rule riêng của
  dự án KAN."*

**Bước 3 – Rule do người viết**

- Bấm **⚙️ Xem / sửa rule** → tab **📁 Rule dự án**, cho xem rule viết bằng tiếng Việt.
- Sang tab **🧪 Thử rule**, nhập tiêu đề *"Weekly status meeting"*, estimate *3h*, bấm **Thử**.
- Kết quả: vượt giới hạn 2h của KAN.
- Lời thoại: *"Rule chỉ là một câu tiếng Việt. PM tự sửa được, không cần dev."*

**Bước 4 – Sửa plan**

Gõ trong chat (thay bằng email Jira của bạn):

```
Giao task "Xây dựng API đăng nhập" cho ten-ban@email.com
```

Hoặc sửa trực tiếp trong bảng task rồi bấm **💾 Lưu**.
Chỉ vào thông báo *"Plan đã thay đổi, cần duyệt lại"*: *"Sửa xong thì phê duyệt cũ mất hiệu lực."*

**Bước 5 – Duyệt và đẩy**

- Bấm **Approve** → **🚀 Đẩy Jira**.
- Khung xanh dưới nút hiện link các issue đã tạo.
- Chuyển sang tab Jira, F5: *"23 issue, đủ cha con, có label `ai-planned` và estimate."*

**Bước 6 – Kết thúc bằng audit**

Mở tab **📜 Audit**: *"Mọi bước, kể cả ai duyệt và lúc nào, đều được ghi lại."*

### 3.3. Lưu ý khi demo

- Tên người làm trong file mẫu (An, Chi…) không phải tài khoản Jira nên issue sẽ **chưa gán người**.
  Cứ nói đây là chủ đích: app không chặn chỉ vì thiếu người làm.
- Khi chờ AI chạy, **nói về điều đang xảy ra** ("agent đang áp rule cho 20 task…") để tránh khoảng lặng.

---

## 4. Kiến trúc

```
          ┌──────────── Streamlit (Python) ────────────┐
Người ──► │  Chat  │  Review  │  Duyệt/Đẩy  │  Sửa rule │
dùng      └────┬───────────────────────────────┬───────┘
               ▼                               ▼
        Agent (tool calling)            Cổng Approve (hash)
     ┌─────────┼──────────┬──────────┐         │
     ▼         ▼          ▼          ▼         ▼
 Trích xuất  Áp rule   Kiểm tra   Excel    Đẩy Jira
 (AI+schema) (AI đọc   (code:     (openpyxl) (MCP client)
             rule.md,  thiếu,                    │
             code so   trùng ≥85%)               ▼
             sánh số)                      mcp-atlassian ─► Jira REST
     └──── AI gateway: DeepSeek-V4.1-Flash (chuẩn OpenAI) ────┘
```

### Công nghệ

| Thành phần | Công nghệ |
|---|---|
| Ngôn ngữ | Python 3.12 |
| Giao diện | Streamlit, pandas |
| AI | AI gateway chuẩn OpenAI (DeepSeek-V4.1-Flash) qua SDK `openai`: tool calling cho agent, JSON schema cho áp rule và trích xuất |
| Kết nối Jira | MCP (Model Context Protocol): SDK `mcp` làm client, `mcp-atlassian` làm server, gọi Jira REST API |
| Excel / tài liệu | openpyxl, python-docx, pypdf |
| Kiểm tra trùng | rapidfuzz (độ giống tiêu đề ≥ 85%) |
| Test | pytest (33 test) |

Không dùng LangChain hay framework agent; vòng lặp agent tự viết.

### Phân chia việc giữa AI và code

| Việc | Ai làm |
|---|---|
| Trích task từ tài liệu | AI (kết quả theo JSON schema) |
| Phân loại task, lý do | AI đọc rule do người viết |
| Giới hạn / gợi ý estimate | AI đọc từ rule → **code so sánh số** |
| Thiếu trường bắt buộc, nghi trùng | Code |
| Dựng cây Jira, đẩy Jira | Code |
| Duyệt | **Con người** |

---

## 5. Kết quả và giới hạn

### Số liệu có thể dẫn

- File mẫu sinh **23 issue** (3 Story, 12 Task, 8 Subtask) trong khoảng 15–20 giây.
- Áp rule: mỗi lô 40 task mất khoảng 2–5 giây.
- **33 test tự động** pass, gồm test đẩy qua MCP với Jira giả.

### Giới hạn – nên chủ động nói

- AI có thể hiểu sai rule viết mơ hồ → viết rule rõ hơn và kiểm tra bằng tab **Thử rule**.
- Mỗi plan chỉ gắn 1 Epic; dòng Epic trong file Azure DevOps chưa được tạo thành Epic trên Jira.
- App chạy trên máy cá nhân (localhost), chưa có đăng nhập nên chưa chia sẻ cho nhiều người.
- Mọi lần phân tích đều cần AI gateway; gateway lỗi thì không phân tích được.

---

## 6. Câu hỏi thường gặp

| Câu hỏi | Gợi ý trả lời |
|---|---|
| *AI sai thì sao?* | AI chỉ **gợi ý** kèm lý do; người duyệt từng task trước khi đẩy. So sánh estimate và kiểm tra trùng do **code** làm |
| *Sao không dùng regex / rule cứng?* | Bản đầu dùng regex: dễ vỡ, PM không tự sửa được. Rule bằng lời thì PM tự viết và mỗi quyết định có lý do |
| *Có lỡ tạo issue rác trên Jira không?* | Không Approve thì không đẩy được; sửa plan phải duyệt lại; agent **không có tool ghi lên Jira**, chỉ người bấm nút mới đẩy |
| *Đẩy giữa chừng bị lỗi?* | Tiến độ được lưu sau mỗi issue; bấm lại thì làm tiếp, không tạo trùng |
| *Vì sao dùng MCP?* | Chuẩn mở để AI và app nối với công cụ ngoài; đổi sang Jira Server hay MCP server khác chỉ cần sửa cấu hình |
| *Bảo mật key?* | Key nằm trong `.env`, không commit; app chỉ mở trên localhost; trợ lý được dặn không in key ra |
| *Chi phí, tốc độ?* | Gom cả lô task vào một lần gọi AI; có cache nên sửa estimate hay người làm không phải gọi AI lại |
| *Đổi rule có cần dev không?* | Không. Mở **⚙️ Xem / sửa rule**, viết bằng tiếng Việt, bấm Thử, rồi Lưu; bản cũ giữ ở `.bak` |

---

## 7. Mẹo trình bày

- **Mở bằng nỗi đau, không mở bằng công nghệ:** *"Tuần trước tôi mất 3 tiếng để chép 40 task từ BRD sang Jira…"*
- **Demo sớm**, khoảng phút thứ 5. Khán giả tin cái họ thấy hơn slide.
- Nói chậm ở **bước Approve**: đây là điểm khác biệt giữa "AI tự làm" và "AI hỗ trợ, người quyết định".
- Kết thúc bằng thông điệp chính và một câu kêu gọi, ví dụ: *"Bước tiếp theo: thử với một dự án thật trong 2 tuần."*

---

## 8. Phụ lục – file và lệnh hay dùng

| Mục | Đường dẫn / lệnh |
|---|---|
| Mở app | `run_app.bat` hoặc `.venv\Scripts\streamlit run app.py --server.address localhost` |
| Rule chung | `config/rules.md` |
| Rule dự án KAN | `config/projects/KAN.md` |
| Cấu hình kỹ thuật | `config/settings.yaml`, `config/projects/KAN.yaml` |
| File demo đủ Subtask | `samples/full_hierarchy_task_review.xlsx`, `samples/brd_full_hierarchy.md` |
| Kết quả mỗi phiên | `out/chat/<thời gian>/` (`plan.json`, `plan.md`, `audit.jsonl`, `plan.result.json`) |
| Đẩy plan đã duyệt bằng lệnh | `.venv\Scripts\python -m delivery_agent push out\chat\<thời gian>\plan.json` |
| Chạy test | `.venv\Scripts\python -m pytest -q` |
