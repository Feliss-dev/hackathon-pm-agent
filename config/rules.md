# Rule chung cho mọi dự án

Rule do con người viết. AI đọc rule này để gợi ý loại (category) và estimate cho từng task, kèm lý do.
Rule riêng của dự án (config/projects/<TÊN>.md) được thêm phía sau và **ưu tiên hơn** rule chung.

## 1. Phân loại task

Dựa vào tiêu đề, mô tả và người làm. Xét các rule **theo thứ tự từ trên xuống, rule khớp đầu tiên thắng**.
Không rule nào khớp thì chọn **Other**.

1. **Create Test Cases** – viết, chuẩn bị, thiết kế test case / kịch bản kiểm thử.
2. **Execute Test** – chạy, thực hiện test (SIT, regression run, test cycle).
3. **UT** – viết unit test (JUnit, pytest, Jest, độ phủ unit test).
4. **Review Code** – review code, pull request, merge request.
5. **Baseline Branch** – tạo/cắt branch, baseline branch cho release.
6. **Backfill** – bổ sung (backfill) dữ liệu còn thiếu.
7. **Dev Study** – nghiên cứu, spike, PoC, tìm hiểu công nghệ, tự học.
8. **Projection** – dự báo, forecast, capacity planning.
9. **Migration** – chuyển đổi / di chuyển dữ liệu hoặc hệ thống, ETL, cut-over.
10. **Testing** – các việc kiểm thử khác: QA, UAT, kiểm tra hiệu năng, kiểm tra chất lượng.
11. **BA** – phân tích nghiệp vụ, thu thập yêu cầu, user story, use case, quy tắc nghiệp vụ, BRD.
12. **SA** – thiết kế kiến trúc, solution design, HLD/LLD, thiết kế kỹ thuật tổng thể.
13. **FE** – giao diện: màn hình, form, trang, component, React/Angular/Vue.
14. **BE** – API, service, database, xử lý phía server, batch job, tích hợp hệ thống.
15. **Communication** – email, trả lời/làm rõ với khách hàng, thông báo, follow-up.
16. **Management** – họp, daily/stand-up, retro, sprint planning, báo cáo trạng thái, quản lý.
17. **Document** – viết tài liệu, hướng dẫn, manual, slide, báo cáo.
18. Nếu tiêu đề và mô tả không đủ để quyết định, dùng vai trò của người làm: (BA) → BA, (FE) → FE,
    (BE) → BE, (QA)/tester → Testing.

## 2. Estimate

Estimate tính bằng giờ (1 ngày = 8h, 1 tuần = 40h).

**Estimate đáng ngờ** – cảnh báo và gợi ý giảm về mức tối đa khi:
- Việc trao đổi/communication đơn giản > 8h
- Review code > 16h
- Họp (meeting) > 4h
- Sửa bug > 40h (nên tách nhỏ)
- Viết tài liệu > 24h

**Task thiếu estimate** – gợi ý theo loại:
BA 16h · SA 16h · FE 16h · BE 16h · UT 8h · Testing 16h · Migration 24h · Projection 8h ·
Review Code 4h · Communication 2h · Management 4h · Document 8h · Backfill 8h · Dev Study 8h ·
Execute Test 8h · Create Test Cases 8h · Baseline Branch 2h · Other 8h.
Nếu tài liệu hoặc mô tả đã nêu khối lượng cụ thể thì ưu tiên con số đó.
