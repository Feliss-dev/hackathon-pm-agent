# BRD – Customer Portal Phase 2

Tài liệu này được viết theo cấu trúc **Tính năng → Công việc → Bước con** để agent sinh ra đầy đủ
Story → Task → Subtask trên Jira. Mỗi công việc ghi rõ người làm (PIC) và estimate.

## 1. Bối cảnh
Ngân hàng mở rộng cổng khách hàng: đăng nhập bảo mật bằng OTP, thanh toán phí dịch vụ qua VNPay và chuyển dữ liệu
khách hàng từ Oracle 11g sang PostgreSQL.

## 2. Tính năng: Đăng nhập & bảo mật

### Công việc 2.1 – Phân tích yêu cầu đăng nhập và quy tắc mật khẩu
- PIC: An · Estimate: 8h
- Kết quả: tài liệu quy tắc khoá tài khoản, độ dài mật khẩu, thời hạn OTP.

### Công việc 2.2 – Xây dựng API đăng nhập
- PIC: Chi · Estimate: 16h
- Tiêu chí chấp nhận: API `/auth/login` trả JWT; sai 5 lần thì khoá 15 phút.
- Bước con:
  - Thiết kế bảng `user_session` lưu refresh token – Chi, 4h
  - Giới hạn số lần đăng nhập sai (rate limit) – Chi, 4h
  - Viết unit test cho API đăng nhập, độ phủ ≥ 80% – Chi, 6h

### Công việc 2.3 – Xây dựng màn hình đăng nhập
- PIC: Dung · Estimate: 16h
- Bước con:
  - Form nhập OTP và đếm ngược gửi lại – Dung, 6h
  - Hiển thị lỗi và trạng thái khoá tài khoản – Dung, 4h

### Công việc 2.4 – Viết test case đăng nhập
- PIC: Em · Estimate: 8h

### Công việc 2.5 – Chạy kiểm thử SIT đăng nhập
- PIC: Em · Estimate: 12h · Phụ thuộc: Công việc 2.2, 2.3

## 3. Tính năng: Thanh toán VNPay

### Công việc 3.1 – Thiết kế luồng tích hợp VNPay
- PIC: Binh · Estimate: 12h
- Kết quả: sequence diagram, cách xử lý callback và hoàn tiền.

### Công việc 3.2 – Xây dựng API tạo giao dịch VNPay
- PIC: Chi · Estimate: 16h · Phụ thuộc: Công việc 3.1
- Bước con:
  - Xử lý IPN callback và cập nhật trạng thái đơn – Chi, 8h
  - Đối soát giao dịch cuối ngày (batch 23:00) – Chi, 8h

### Công việc 3.3 – Màn hình chọn phương thức và kết quả thanh toán
- PIC: Dung · Estimate: 12h

### Công việc 3.4 – Review code module thanh toán
- PIC: Binh · Estimate: 6h

## 4. Tính năng: Chuyển đổi dữ liệu khách hàng

### Công việc 4.1 – Migrate dữ liệu khách hàng từ Oracle sang PostgreSQL
- PIC: Giang · Estimate: 3d (khoảng 1,2 triệu bản ghi)
- Bước con:
  - Viết script ETL và mapping cột – Giang, 8h
  - Chạy thử migrate trên môi trường staging – Giang, 6h

### Công việc 4.2 – Backfill email còn thiếu từ file CRM
- PIC: Giang · Estimate: 6h · Phụ thuộc: Công việc 4.1
