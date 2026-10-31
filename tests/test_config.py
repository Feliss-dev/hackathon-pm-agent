import pytest

from delivery_agent import config as cfgmod


@pytest.fixture
def projects_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(cfgmod, "DEFAULT_RULES", tmp_path / "rules.md")
    (tmp_path / "rules.md").write_text("# Common\n- meetings max 4h\n", encoding="utf-8")
    return tmp_path / "projects"


def test_project_rules_come_first_and_override(projects_dir):
    assert cfgmod.load_rules() == "# Common\n- meetings max 4h\n"
    cfgmod.save_rules("- meetings max 2h", project="P")
    combined = cfgmod.load_rules("P")
    assert combined.index("meetings max 2h") < combined.index("meetings max 4h")
    assert "PHẦN A" in combined and "ghi đè" in combined
    assert cfgmod.list_projects() == ["P"]


def test_save_rules_keeps_backup_and_rejects_empty_common(projects_dir):
    cfgmod.save_rules("# Common v2")
    assert cfgmod.read_common_rules().startswith("# Common v2")
    assert (cfgmod.DEFAULT_RULES.with_name("rules.md.bak")).read_text(encoding="utf-8").startswith("# Common\n")
    with pytest.raises(ValueError):
        cfgmod.save_rules("   ")


def test_settings_overlay_stores_only_differences(projects_dir):
    base = cfgmod.load_config()
    edited = cfgmod.load_config()
    edited["validation"]["duplicate_threshold"] = 90
    edited["jira"]["project_key"] = "QX"
    path = cfgmod.save_project_settings("Q", edited, base)
    text = path.read_text(encoding="utf-8")
    assert "duplicate_threshold: 90" in text and "excel" not in text
    merged = cfgmod.load_config(project="Q")
    assert merged["jira"]["project_key"] == "QX" and merged["jira"]["story_issue_type"] == base["jira"]["story_issue_type"]


def test_invalid_settings_and_names(projects_dir):
    bad = cfgmod.load_config()
    bad["validation"]["blocking_fields"] = ["owner"]
    bad["jira"]["project_key"] = "bad key"
    problems = cfgmod.validate_config(bad)
    assert len(problems) == 2
    with pytest.raises(ValueError):
        cfgmod.save_project_settings("R", bad)
    with pytest.raises(ValueError, match="Tên dự án"):
        cfgmod.project_path("../evil")
