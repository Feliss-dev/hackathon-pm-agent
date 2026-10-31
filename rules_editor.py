"""Rules dialog for the Streamlit app.

Rules are plain-language text written by people (config/rules.md + config/projects/<NAME>.md); the AI reads them
to suggest categories and estimates. Technical settings (Jira mapping, required fields, duplicate threshold) are
edited with forms and stored as config/projects/<NAME>.yaml (only the differences from config/settings.yaml).
"""
from __future__ import annotations

import copy
import math

import pandas as pd
import streamlit as st

from delivery_agent.ai_rules import RuleAssessor
from delivery_agent.config import (FIELD_CHOICES, combine_rules, diff_config, list_projects, load_config,
                                   read_common_rules, read_project_rules, save_project_settings, save_rules,
                                   validate_config)
from delivery_agent.models import CATEGORIES, Task

PROJECT_TEMPLATE = """# Rule riêng dự án {name}

<!-- Viết bằng ngôn ngữ tự nhiên. Rule ở đây ưu tiên hơn rule chung. Ví dụ: -->
- Mọi task về thanh toán (payment, VNPay, MoMo) là **BE**.
- Họp tối đa **2h** mỗi buổi.
"""


def _csv(text) -> list[str]:
    if text is None or (isinstance(text, float) and math.isnan(text)):
        return []
    return [p.strip() for p in str(text).split(",") if p.strip()]


def _validation_form(cfg: dict) -> dict:
    val = copy.deepcopy(cfg["validation"])
    val["required_fields"] = st.multiselect("Trường bắt buộc (thiếu thì cảnh báo)", FIELD_CHOICES, val.get("required_fields", []))
    val["blocking_fields"] = st.multiselect("Trường chặn tạo issue (thiếu thì SKIP)", val["required_fields"],
                                            [f for f in val.get("blocking_fields", val["required_fields"]) if f in val["required_fields"]],
                                            help="Bắt buộc nhưng không chặn → vẫn tạo; ví dụ thiếu assignee thì tạo chưa gán")
    val["duplicate_threshold"] = st.slider("Ngưỡng nghi trùng (% giống nhau của tiêu đề)", 50, 100, int(val.get("duplicate_threshold", 85)))
    return val


def _jira_form(cfg: dict) -> dict:
    jira = copy.deepcopy(cfg["jira"])
    c1, c2 = st.columns(2)
    jira["project_key"] = c1.text_input("Project key", jira["project_key"]).strip()
    jira["sprint_field"] = c2.text_input("Sprint field", jira.get("sprint_field", "customfield_10020"))
    c1, c2, c3 = st.columns(3)
    jira["epic_issue_type"] = c1.text_input("Loại Epic", jira["epic_issue_type"])
    jira["story_issue_type"] = c2.text_input("Loại Story", jira["story_issue_type"])
    jira["subtask_issue_type"] = c3.text_input("Loại Subtask", jira["subtask_issue_type"])
    jira["task_parent"] = st.radio("Task gắn vào", ["epic", "story"], index=0 if jira.get("task_parent", "epic") == "epic" else 1,
                                   horizontal=True, help="epic: parent là Epic + link Relates tới Story (Jira chuẩn). "
                                                         "story: parent là Story (cần hierarchy tuỳ chỉnh)")
    jira["common_labels"] = _csv(st.text_input("Label gắn cho mọi issue", ", ".join(jira.get("common_labels", []))))
    st.markdown("**Map loại task → Jira**")
    rows = [{"Loại": k, "Tên hiển thị": v.get("type_name", ""), "Issue type": v.get("issue_type", "Task"),
             "Labels": ", ".join(v.get("labels", [])), "Components": ", ".join(v.get("components", []))}
            for k, v in jira["category_mapping"].items()]
    edited = st.data_editor(pd.DataFrame(rows), num_rows="dynamic", hide_index=True, width="stretch", key="rx-map",
                            column_config={"Loại": st.column_config.SelectboxColumn(options=CATEGORIES + ["default"], required=True)})
    jira["category_mapping"] = {r["Loại"]: {"type_name": r.get("Tên hiển thị") or f"{r['Loại']} Task",
                                            "issue_type": r.get("Issue type") or "Task", "labels": _csv(r.get("Labels")),
                                            "components": _csv(r.get("Components"))}
                                for r in edited.to_dict("records") if r.get("Loại")}
    st.caption("Components phải có sẵn trong Jira project (space team-managed không có Components → để trống).")
    return jira


@st.dialog("⚙️ Rule của dự án", width="large")
def rules_dialog(session, on_saved) -> None:
    """`on_saved(project_name)` is called after a successful save."""
    current = session.project
    st.markdown("Rule là **văn bản do bạn viết**. AI đọc rule để gợi ý *loại task* và *estimate* cho từng task "
                "(kèm lý do); thiếu trường / nghi trùng vẫn do code kiểm tra.")

    t_common, t_project, t_try, t_tech = st.tabs(["📝 Rule chung", "📁 Rule dự án", "🧪 Thử rule", "⚙️ Cấu hình kỹ thuật"])
    with t_common:
        st.caption("Áp dụng cho mọi dự án · `config/rules.md`")
        common_orig = read_common_rules()
        common = st.text_area("Rule chung", common_orig, height=420, key="rx-common", label_visibility="collapsed")
    with t_project:
        if current:
            st.caption(f"Chỉ áp dụng cho dự án **{current}**, ưu tiên hơn rule chung · `config/projects/{current}.md`")
            name = current
        else:
            st.info("Đang dùng rule chung. Đặt tên để tạo dự án mới với rule riêng.")
            name = st.text_input("Tên dự án mới", placeholder="vd KAN, BANK", key="rx-new-name").strip()
        project_orig = read_project_rules(current) if current else ""
        project_text = st.text_area("Rule dự án", project_orig or (PROJECT_TEMPLATE.format(name=name) if name else ""),
                                    height=360, key="rx-project", label_visibility="collapsed", disabled=not name)
    with t_try:
        st.caption("Thử với rule **đang soạn** (chưa cần lưu).")
        c1, c2 = st.columns([3, 1])
        title = c1.text_input("Tiêu đề task", placeholder="Viết unit test cho API thanh toán VNPay", key="rx-try-title")
        estimate = c2.text_input("Estimate", placeholder="vd 6h", key="rx-try-est")
        c1, c2 = st.columns([3, 1])
        desc = c1.text_input("Mô tả (tuỳ chọn)", key="rx-try-desc")
        assignee = c2.text_input("Người làm", key="rx-try-assignee")
        if st.button("🧪 Thử", disabled=not title.strip()):
            rules = combine_rules(common, project_text if name else "", name)
            with st.spinner("AI đang áp rule..."):
                try:
                    a = RuleAssessor(rules).assess([Task(id="probe", title=title, description=desc,
                                                         assignee=assignee, estimate=estimate)])["probe"]
                    st.success(f"Loại: **{a.category}** — {a.category_reason}")
                    if a.estimate_ok:
                        st.info(f"Estimate ổn. Giới hạn theo rule: {a.max_hours or 'không có'}h · gợi ý khi thiếu: {a.default_hours or '-'}h")
                    else:
                        st.warning(f"Estimate: {a.estimate_reason} → gợi ý **{a.suggested_estimate or '-'}**")
                except Exception as exc:
                    st.error(f"Không áp được rule: {exc}")
    with t_tech:
        st.caption("Cấu hình kỹ thuật (không phải rule): lưu theo dự án, chỉ ghi phần khác `config/settings.yaml`.")
        cfg = copy.deepcopy(session.config)
        cfg["validation"] = _validation_form(cfg)
        cfg["jira"] = _jira_form(cfg)

    # ---- save
    st.divider()
    common_changed = common.strip() != common_orig.strip()
    project_changed = bool(name) and project_text.strip() != project_orig.strip()
    settings_changed = bool(diff_config(session.config, cfg))
    problems = validate_config(cfg)
    if settings_changed and not name:
        problems.append("Cấu hình kỹ thuật được lưu theo dự án: hãy đặt tên dự án ở tab 'Rule dự án'")
    changes = [label for label, changed in (("rule chung", common_changed), (f"rule dự án {name}", project_changed),
                                             ("cấu hình kỹ thuật", settings_changed)) if changed]
    if problems:
        st.error("\n".join(f"- {p}" for p in problems))
    elif changes:
        st.info("Sẽ lưu: " + ", ".join(changes) + (" · Rule chung ảnh hưởng mọi dự án." if common_changed else ""))
    if st.button("💾 Lưu & áp dụng", type="primary", disabled=bool(problems) or not changes):
        try:
            if common_changed:
                save_rules(common)
            if project_changed:
                save_rules(project_text, project=name)
            if settings_changed:
                save_project_settings(name, cfg, load_config())
            on_saved(name if (project_changed or settings_changed or current) else "")
            st.toast("Đã lưu rule (bản cũ giữ ở .bak)")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    st.caption(f"Dự án đã có: {', '.join(list_projects()) or 'chưa có'}")
