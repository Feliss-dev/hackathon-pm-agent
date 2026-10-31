import openpyxl

from delivery_agent.audit import AuditLog
from delivery_agent.excel_io import read_tasks, update_product_column
from delivery_agent.workshop import validate_package


def _snapshot(path):
    wb = openpyxl.load_workbook(path)
    cells = {}
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                cells[(ws.title, c.coordinate)] = (c.value, c.font.bold, c.fill.fgColor.rgb, c.number_format)
    return wb.sheetnames, cells


def test_only_product_column_changes(config, workbook, tmp_path, assessor):
    out = tmp_path / "out.xlsx"
    audit = AuditLog()
    stats = update_product_column(workbook, out, config, assessor, audit)
    assert stats["updated"] == 14
    assert assessor.calls == 1                         # one AI call for the whole workbook

    sheets_before, before = _snapshot(workbook)
    sheets_after, after = _snapshot(out)
    assert sheets_before == sheets_after == ["Tasks", "Summary"]
    changed = {k for k in before.keys() | after.keys() if before.get(k) != after.get(k)}
    assert changed and all(sheet == "Tasks" and coord.startswith("H") for sheet, coord in changed)

    ws = openpyxl.load_workbook(out)["Tasks"]
    assert ws["H6"].value == "BE"                        # Create Customer API
    assert ws["I6"].value == '=IF(F6="","",F6/8)'          # formulas preserved
    assert openpyxl.load_workbook(out)["Summary"]["B1"].value.startswith("=Tasks!F")
    assert ws["A3"].fill.fgColor.rgb.endswith("1F4E78")   # header formatting preserved
    assert len(audit.entries) == 14 and audit.entries[0].classification == "BA"


def test_formula_product_cells_are_left_alone(config, workbook, tmp_path, assessor):
    wb = openpyxl.load_workbook(workbook)
    wb["Tasks"]["H4"] = '="BA"'
    wb.save(workbook)
    stats = update_product_column(workbook, tmp_path / "o.xlsx", config, assessor)
    assert stats["skipped_formula"] == 1
    assert openpyxl.load_workbook(tmp_path / "o.xlsx")["Tasks"]["H4"].value == '="BA"'


def test_read_tasks(config, workbook):
    tasks = read_tasks(workbook, config)
    assert len(tasks) == 14
    api = next(t for t in tasks if t.title == "Create Customer API")
    assert api.estimate == "24" and api.assignee == "Chi (BE)" and api.story == "Customer Onboarding"
    assert api.dependency == ["Design customer onboarding architecture"]


def test_workshop_readiness(config, tmp_path):
    from make_samples import make_workshop
    make_workshop(tmp_path / "pkg")
    report = validate_package(tmp_path / "pkg", config)
    assert report["missing"] == ["Estimate completed"]       # one task in the sample has no estimate
    assert report["readiness"] == "Partially Ready"
    (tmp_path / "empty").mkdir()
    assert validate_package(tmp_path / "empty", config)["readiness"] == "Not Ready"


def test_read_azure_devops_task_review(config, tmp_path):
    from make_samples import make_review_workbook
    path = tmp_path / "review.xlsx"
    make_review_workbook(path)
    tasks = {t.title: t for t in read_tasks(path, config)}
    assert "Customer registration" not in tasks          # User Story rows group tasks, they are not tasks
    assert "Old prototype screen" not in tasks           # State = Removed is skipped
    api = tasks["Create Customer API"]
    assert (api.id, api.story, api.assignee, api.estimate) == ("#1002", "Customer registration", "Chi", "24")
    assert tasks["Migrate legacy customer data"].story == "Data migration"
    # no Parent ID: the Title 2 level still places it under the last Title 1 row
    assert tasks["Weekly status meeting"].story == "Data migration"
    assert tasks["Create test cases for registration"].estimate == ""


def test_task_under_task_becomes_subtask(config, tmp_path):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["Work Item Type", "ID", "Parent", "Title 1", "Title 2", "Title 3"])
    ws.append(["User Story", 1, None, "Login", None, None])
    ws.append(["Task", 2, 1, None, "Create login API", None])
    ws.append(["Task", 3, 2, None, None, "Add rate limiting"])
    wb.save(tmp_path / "t.xlsx")
    tasks = {t.title: t for t in read_tasks(tmp_path / "t.xlsx", config)}
    assert tasks["Add rate limiting"].parent_title == "#2"
    assert tasks["Add rate limiting"].story == "Login"
