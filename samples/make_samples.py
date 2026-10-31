"""Generate sample inputs: a task tracking workbook (with formulas/formatting) and a workshop package."""
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

HERE = Path(__file__).parent

ROWS = [
    # Story, Task, Description, Assignee, Estimate, Dependency
    ("Customer Onboarding", "Gather onboarding requirements", "Workshop with business to capture user stories", "An (BA)", 16, ""),
    ("Customer Onboarding", "Design customer onboarding architecture", "Solution design for onboarding services", "Binh (SA)", 24, "Gather onboarding requirements"),
    ("Customer Onboarding", "Create Customer API", "REST endpoint to create and update customers", "Chi (BE)", 24, "Design customer onboarding architecture"),
    ("Customer Onboarding", "Build customer registration screen", "React form with validation", "Dung (FE)", 32, "Create Customer API"),
    ("Customer Onboarding", "Write unit tests for Customer API", "pytest coverage >= 80%", "Chi (BE)", 8, "Create Customer API"),
    ("Customer Onboarding", "Create test cases for onboarding", "Positive/negative scenarios", "Em (QA)", 12, ""),
    ("Customer Onboarding", "Execute test cycle 1", "Run SIT test cases", "Em (QA)", 16, "Create test cases for onboarding"),
    ("Customer Onboarding", "Code review for onboarding module", "Review merge requests", "Binh (SA)", 20, ""),
    ("Data Migration", "Migrate legacy customer data", "ETL from Oracle to PostgreSQL", "Giang", "", ""),
    ("Data Migration", "Backfill missing customer emails", "", "Giang", 6, "Migrate legacy customer data"),
    ("Project Management", "Weekly status meeting", "Sprint status with client", "Hoa (PM)", 6, ""),
    ("Project Management", "Reply to client clarification emails", "", "Hoa (PM)", 10, ""),
    ("Project Management", "Prepare deployment guide document", "Operations manual", "Binh (SA)", 30, ""),
    ("Customer Onboarding", "Create the Customer API", "Same work as the API task", "", 16, ""),
]


def make_workbook(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Tasks"
    ws["A1"] = "Task Tracking - Customer Portal"
    ws["A1"].font = Font(bold=True, size=14)
    headers = ["No", "Story", "Task", "Description", "Assignee", "Estimate (h)", "Dependency", "Product", "Estimate (d)"]
    ws.append([])
    ws.append(headers)
    for cell in ws[3]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
    for i, (story, task, desc, who, est, dep) in enumerate(ROWS, 1):
        r = ws.max_row + 1
        ws.append([i, story, task, desc, who, est, dep, None, f"=IF(F{r}=\"\",\"\",F{r}/8)"])
    last = ws.max_row
    ws.cell(row=last + 2, column=5, value="Total")
    ws.cell(row=last + 2, column=6, value=f"=SUM(F4:F{last})")
    ws.column_dimensions["C"].width = 42

    summary = wb.create_sheet("Summary")
    summary["A1"], summary["B1"] = "Total hours", f"=Tasks!F{last + 2}"
    wb.save(path)


REVIEW_HEADERS = ["Work Item Type", "ID", "Parent", "Title 1", "Title 2", "Assigned To", "State", "Story Points",
                  "Original Estimate", "Completed Work", "Remaining Work", "Iteration Path"]
REVIEW_ROWS = [
    ("User Story", 1001, None, "Customer registration", None, "An", "Active", 8, None, None, None, r"Portal\Sprint 1"),
    ("Task", 1002, 1001, None, "Create Customer API", "Chi", "Active", None, 24, 8, 16, r"Portal\Sprint 1"),
    ("Task", 1003, 1001, None, "Build registration screen", "Dung", "New", None, 32, 0, 32, r"Portal\Sprint 1"),
    ("Task", 1004, 1001, None, "Code review registration module", "Binh", "New", None, 20, 0, 20, r"Portal\Sprint 1"),
    ("Task", 1005, 1001, None, "Create test cases for registration", "Em", "New", None, None, None, None, r"Portal\Sprint 1"),
    ("Task", 1006, 1001, None, "Old prototype screen", "Dung", "Removed", None, 8, 0, 0, r"Portal\Sprint 1"),
    ("User Story", 1010, None, "Data migration", None, "Giang", "New", 13, None, None, None, r"Portal\Sprint 2"),
    ("Task", 1011, 1010, None, "Migrate legacy customer data", "Giang", "New", None, 40, 0, 40, r"Portal\Sprint 2"),
    ("Task", 1012, None, None, "Weekly status meeting", "Hoa", "New", None, 6, 0, 6, r"Portal\Sprint 2"),
]


def make_review_workbook(path: Path) -> None:
    """Azure DevOps 'task review' export: tree via Title 1 / Title 2 and the Parent ID column."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Task Review"
    ws.append(REVIEW_HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in REVIEW_ROWS:
        ws.append(list(row))
    wb.save(path)


FULL_HEADERS = ["Work Item Type", "ID", "Parent", "Title 1", "Title 2", "Title 3", "Description", "Assigned To",
                "State", "Original Estimate", "Iteration Path"]
# Story (Title 1) -> Task (Title 2) -> Subtask (Title 3). Every task has an owner and an estimate in hours,
# so nothing is skipped and the whole tree can be pushed.
FULL_ROWS = [
    ("User Story", 2001, None, "Đăng nhập & bảo mật", None, None, "Khách hàng đăng nhập bằng email/OTP", "An", "New", None, r"Portal\Sprint 3"),
    ("Task", 2002, 2001, None, "Phân tích yêu cầu đăng nhập và quy tắc mật khẩu", None, "Quy tắc khoá tài khoản, OTP", "An", "New", 8, r"Portal\Sprint 3"),
    ("Task", 2003, 2001, None, "Xây dựng API đăng nhập", None, "REST API /auth/login trả JWT", "Chi", "New", 16, r"Portal\Sprint 3"),
    ("Task", 2004, 2003, None, None, "Thiết kế bảng user_session", "Lưu refresh token", "Chi", "New", 4, r"Portal\Sprint 3"),
    ("Task", 2005, 2003, None, None, "Giới hạn số lần đăng nhập sai (rate limit)", "Khoá 15 phút sau 5 lần sai", "Chi", "New", 4, r"Portal\Sprint 3"),
    ("Task", 2006, 2003, None, None, "Viết unit test cho API đăng nhập", "Độ phủ >= 80%", "Chi", "New", 6, r"Portal\Sprint 3"),
    ("Task", 2007, 2001, None, "Xây dựng màn hình đăng nhập", None, "Form email + mật khẩu, nhập OTP", "Dung", "New", 16, r"Portal\Sprint 3"),
    ("Task", 2008, 2007, None, None, "Form nhập OTP và đếm ngược gửi lại", "", "Dung", "New", 6, r"Portal\Sprint 3"),
    ("Task", 2009, 2007, None, None, "Hiển thị lỗi và trạng thái khoá tài khoản", "", "Dung", "New", 4, r"Portal\Sprint 3"),
    ("Task", 2010, 2001, None, "Viết test case đăng nhập", None, "Kịch bản đúng/sai/khoá tài khoản", "Em", "New", 8, r"Portal\Sprint 3"),
    ("Task", 2011, 2001, None, "Chạy kiểm thử SIT đăng nhập", None, "", "Em", "New", 12, r"Portal\Sprint 3"),

    ("User Story", 2020, None, "Thanh toán VNPay", None, None, "Khách hàng thanh toán phí dịch vụ qua VNPay", "Binh", "New", None, r"Portal\Sprint 4"),
    ("Task", 2021, 2020, None, "Thiết kế luồng tích hợp VNPay", None, "Sequence diagram, xử lý callback", "Binh", "New", 12, r"Portal\Sprint 4"),
    ("Task", 2022, 2020, None, "Xây dựng API tạo giao dịch VNPay", None, "Ký checksum, tạo URL thanh toán", "Chi", "New", 16, r"Portal\Sprint 4"),
    ("Task", 2023, 2022, None, None, "Xử lý IPN callback và cập nhật trạng thái đơn", "", "Chi", "New", 8, r"Portal\Sprint 4"),
    ("Task", 2024, 2022, None, None, "Đối soát giao dịch cuối ngày", "Batch job 23:00", "Chi", "New", 8, r"Portal\Sprint 4"),
    ("Task", 2025, 2020, None, "Màn hình chọn phương thức và kết quả thanh toán", None, "", "Dung", "New", 12, r"Portal\Sprint 4"),
    ("Task", 2026, 2020, None, "Review code module thanh toán", None, "Review merge request", "Binh", "New", 6, r"Portal\Sprint 4"),

    ("User Story", 2030, None, "Chuyển đổi dữ liệu khách hàng", None, None, "Oracle 11g -> PostgreSQL", "Giang", "New", None, r"Portal\Sprint 4"),
    ("Task", 2031, 2030, None, "Migrate dữ liệu khách hàng từ Oracle sang PostgreSQL", None, "~1.2 triệu bản ghi", "Giang", "New", 24, r"Portal\Sprint 4"),
    ("Task", 2032, 2031, None, None, "Viết script ETL và mapping cột", "", "Giang", "New", 8, r"Portal\Sprint 4"),
    ("Task", 2033, 2031, None, None, "Chạy thử migrate trên môi trường staging", "", "Giang", "New", 6, r"Portal\Sprint 4"),
    ("Task", 2034, 2030, None, "Backfill email còn thiếu từ file CRM", None, "", "Giang", "New", 6, r"Portal\Sprint 4"),
]


def make_full_hierarchy_workbook(path: Path) -> None:
    """Task review template with three levels (Story -> Task -> Subtask) - generates the full Jira tree."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Task Review"
    ws.append(FULL_HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
    for row in FULL_ROWS:
        ws.append(list(row))
        level_fill = {"User Story": "DDEBF7"}.get(row[0])
        if level_fill:
            for cell in ws[ws.max_row]:
                cell.fill = PatternFill("solid", fgColor=level_fill)
                cell.font = Font(bold=True)
    for col, width in zip("ABCDEFGHIJK", (14, 7, 8, 30, 45, 45, 36, 12, 9, 17, 16)):
        ws.column_dimensions[col].width = width
    wb.save(path)


def make_workshop(root: Path) -> None:
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    (root / "README.md").write_text("Repo: https://gitlab.example.com/team/customer-portal\n", encoding="utf-8")
    (root / "business_description.md").write_text("# Customer portal\nOnboarding flow.\n", encoding="utf-8")
    (root / "test_cases.md").write_text("| TC | Steps |\n", encoding="utf-8")
    make_workbook(root / "task_list.xlsx")


if __name__ == "__main__":
    make_workbook(HERE / "task_tracking.xlsx")
    make_review_workbook(HERE / "task_review.xlsx")
    make_full_hierarchy_workbook(HERE / "full_hierarchy_task_review.xlsx")
    make_workshop(HERE / "workshop_package")
    print("Samples written to", HERE)
