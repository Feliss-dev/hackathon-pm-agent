"""Chat UI for the Delivery Planning Agent.   Run:  streamlit run app.py --server.address localhost

Layout: the chat owns the main area; the plan review, approval and push live in the sidebar, which the user can
drag wider/narrower or collapse (Streamlit's native sidebar - on phones it becomes an overlay).
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

from delivery_agent.agent import AgentSession, suggest_prompts  # noqa: E402
from delivery_agent.approval import (ApprovalError, approval_path, plan_hash,  # noqa: E402
                                     record_decision, require_approval)
from delivery_agent.config import list_projects, load_config  # noqa: E402
from delivery_agent.graph import AgentGraph, TurnResult  # noqa: E402
from delivery_agent.planner import render_tree  # noqa: E402
from delivery_agent.push import push_with_mcp, result_path  # noqa: E402
from delivery_agent.tools import REGISTRY  # noqa: E402
from rules_editor import rules_dialog  # noqa: E402

st.set_page_config(page_title="Delivery Planning Agent", page_icon="🗂️", layout="wide",
                   initial_sidebar_state=560)   # px; drag the sidebar edge to resize, « to collapse
st.markdown("""<style>
.block-container {max-width: 1000px; padding-top: 2.5rem; padding-bottom: 1rem;}
section[data-testid="stSidebar"] .block-container {max-width: none;}
section[data-testid="stSidebar"] [data-testid="stMetricValue"] {font-size: 1.4rem;}
</style>""", unsafe_allow_html=True)

JIRA_URL = os.getenv("JIRA_URL", "").rstrip("/")
AI_OK = bool(os.getenv("LLM_API_KEY") or os.getenv("API_KEY"))
JIRA_OK = bool(os.getenv("JIRA_URL") and os.getenv("JIRA_API_TOKEN"))
TOOL_LABELS = REGISTRY.labels()   # one source: the tool registry
WELCOME = ("Xin chào! Tôi là **Delivery Planning Agent**. Hãy đính kèm tài liệu (BRD, design .md/.docx/.pdf) hoặc "
           "file Excel task list bằng nút 📎 ở ô chat, rồi nói tôi cần làm gì. Ví dụ: *\"Phân tích file này và gắn "
           "vào Epic KAN-1\"*. Kết quả chi tiết, nút **duyệt và đẩy lên Jira** nằm ở thanh bên trái "
           "(kéo mép để rộng/hẹp, bấm « để thu gọn).")
EDITABLE = {"Tiêu đề": "title", "Story": "story", "Người làm": "assignee", "Estimate": "estimate"}

ss = st.session_state
ss.setdefault("project", "")    # rules profile, kept across "Chat mới"
if "session" not in ss:
    ss.session = AgentSession(load_config(project=ss.project or None), offline=not JIRA_OK, project=ss.project,
                              created_by=os.getenv("USERNAME", ""))
ss.setdefault("messages", [])   # OpenAI-format history for the model
ss.setdefault("display", [])    # what the chat shows: {"role", "content", "tools": [...]}
ss.setdefault("pending", None)
ss.setdefault("graph", None)        # LangGraph agent waiting for a person's OK (human-in-the-loop)
ss.setdefault("paused", None)       # the tool calls it is waiting on
ss.setdefault("turn_tools", [])     # tools run so far in the current turn
ss.setdefault("hitl", False)
session: AgentSession = ss.session


# ================================================================ helpers
def approval_status() -> tuple[str, dict | None]:
    path = approval_path(session.plan_path)
    if not session.plan or not path.exists():
        return "none", None
    decision = json.loads(path.read_text(encoding="utf-8"))
    if decision.get("plan_hash") != plan_hash(session.plan):
        return "stale", decision
    return decision.get("decision", "none"), decision


def switch_vector(provider: str, backend: str) -> None:
    """Change the embedding provider / vector store. Collections are per embedding model, so nothing is mixed up."""
    import delivery_agent.vectors as vectors
    os.environ["EMBEDDING_PROVIDER"], os.environ["VECTOR_BACKEND"] = provider, backend
    vectors._default = None
    session._vectors, session.vector_warning = None, ""


def say(content: str) -> None:
    """Add an assistant message that did not come from the model (e.g. push results)."""
    ss.display.append({"role": "assistant", "content": content, "tools": []})
    ss.messages.append({"role": "assistant", "content": content})
    session.save_chat(ss.messages, ss.display)


def open_run(run_id: str) -> None:
    """Reopen an earlier run from the database: files, tasks, latest plan and the chat."""
    run = session.repo.get_run(run_id)
    ss.project = run.profile or ""
    ss.session = AgentSession(load_config(project=ss.project or None), offline=not JIRA_OK, project=ss.project,
                              run_id=run_id)
    ss.messages, ss.display = ss.session.load_chat()
    ss.pop("push_feedback", None)


def use_project(name: str) -> None:
    """Switch the rules profile; an existing plan is re-evaluated with the new rules."""
    ss.project = name
    had_plan = bool(session.plan)
    session.apply_config(load_config(project=name or None), name)
    label = name or "mặc định"
    say(f"📁 Đang dùng bộ rule **{label}** (project `{session.config['jira']['project_key']}`)."
        + (" Plan đã được tính lại theo rule mới, cần Approve lại." if had_plan else ""))


def task_rows(plan: dict) -> list[dict]:
    """One row per task with its Jira mapping, status and warnings - the review table."""
    nodes = {n["task_id"]: n for n in plan["planned_issues"] if n["task_id"]}
    reasons = {tid: a.get("category_reason", "") for tid, a in plan.get("assessments", {}).items()}
    warn: dict[str, list[str]] = {}
    for f in plan["estimate_findings"]:
        hint = f" → gợi ý {f['suggestedEstimate']}" if f["suggestedEstimate"] else ""
        warn.setdefault(f["task_id"], []).append(f"Estimate: {f['reason']}{hint}")
    for i in plan["validation_issues"]:
        warn.setdefault(i["task_id"], []).append(f"{i['kind']}: {i['detail']}")
    rows = []
    for t in plan["tasks"]:
        n = nodes.get(t["id"], {})
        if n.get("skip_reason"):
            status = f"⛔ SKIP – {n['skip_reason']}"
        else:
            status = "⚠️ Tạo (có cảnh báo)" if warn.get(t["id"]) else "✅ Tạo"
        rows.append({"ID": t["id"], "Trạng thái": status, "Tiêu đề": t["title"], "Loại": t["category"],
                     "Người làm": t["assignee"], "Estimate": t["estimate"], "Story": t["story"],
                     "Jira type": n.get("type_name", ""), "Lý do phân loại": reasons.get(t["id"], ""),
                     "Cảnh báo": " | ".join(warn.get(t["id"], []))})
    return rows


# ================================================================ sidebar sections
def approval_section(plan: dict) -> None:
    status, decision = approval_status()
    if status == "approved":
        st.success(f"Đã duyệt bởi **{decision['approver']}**")
    elif status == "rejected":
        st.error(f"Đã từ chối bởi **{decision['approver']}**")
    elif status == "stale":
        st.warning("Plan đã thay đổi sau khi duyệt, cần duyệt lại.")
    elif session.approval_requested:
        st.info("Agent đã chuẩn bị xong. Xem lại bảng bên dưới rồi duyệt.")

    approver = st.text_input("Người duyệt", value=os.getenv("USERNAME", ""))
    b1, b2, b3 = st.columns(3)
    if b1.button("Approve", type="primary", width="stretch", disabled=not approver.strip()):
        record_decision(session.plan_path, plan, approver.strip(), True, session.audit, on_record=session.record_approval)
        say(f"✅ **{approver}** đã duyệt plan. Bấm **🚀 Đẩy Jira** để tạo issue.")
        st.rerun()
    if b2.button("Reject", width="stretch", disabled=not approver.strip()):
        record_decision(session.plan_path, plan, approver.strip(), False, session.audit, on_record=session.record_approval)
        say(f"❌ **{approver}** đã từ chối plan. Bạn muốn sửa gì? Tôi có thể chỉnh task hoặc phân tích lại.")
        st.rerun()
    jira_checked = plan["jira_duplicate_check"]
    push_disabled = status != "approved" or session.offline or not JIRA_OK or not jira_checked
    if b3.button("🚀 Đẩy Jira", type="primary", width="stretch", disabled=push_disabled):
        try:
            require_approval(session.plan_path, plan)
            with st.spinner("Đang tạo issue trên Jira..."):
                session.after_push(asyncio.run(push_with_mcp(plan, session.plan_path, session.config, session.audit)))
            nodes = {n["ref"]: n for n in plan["planned_issues"]}
            lines = [f"- [{key}]({JIRA_URL}/browse/{key}) — {nodes[ref]['type_name']}: {nodes[ref]['summary']}"
                     for ref, key in session.push_state["created"].items() if ref in nodes]
            message = f"🚀 Đã tạo **{len(lines)}** issue trên Jira:\n" + "\n".join(lines)
            if session.push_state.get("updated"):
                message += f"\n\n✏️ Đã cập nhật {len(session.push_state['updated'])} issue có sẵn (thay đổi từ Excel)."
            ss.push_feedback = ("success", message)
            say(message)
        except ApprovalError as exc:
            ss.push_feedback = ("error", f"Không thể đẩy: {exc}")
            say(ss.push_feedback[1])
        except Exception as exc:
            session.last_error = f"{type(exc).__name__}: {exc}"
            session.audit.record("push_error", reason=session.last_error)
            if result_path(session.plan_path).exists():
                session.after_push(json.loads(result_path(session.plan_path).read_text(encoding="utf-8")))
            done = len((session.push_state or {}).get("created", {}))
            ss.push_feedback = ("error", f"⚠️ Đẩy bị dừng sau {done} issue: {session.last_error}\n\n"
                                         "Bấm lại **Đẩy Jira** để tiếp tục (không tạo trùng) hoặc hỏi trợ lý.")
            say(ss.push_feedback[1])
        st.rerun()
    # Result of the last push, shown right under the button (the chat may be scrolled elsewhere).
    feedback = ss.get("push_feedback")
    if feedback:
        (st.success if feedback[0] == "success" else st.error)(feedback[1])
    # Always say why the push button is disabled.
    if not JIRA_OK:
        st.caption("🔒 Chưa cấu hình Jira trong .env nên chưa thể đẩy.")
    elif not jira_checked:
        st.warning("🔒 Plan này được tạo khi tắt kết nối Jira, chưa kiểm tra trùng với Jira. "
                   "Kiểm tra trước, sau đó Approve lại để đẩy.")
        if st.button("🔄 Kiểm tra trùng với Jira", width="stretch"):
            try:
                with st.spinner("Đang đối chiếu với Jira..."):
                    result = session.recheck_with_jira()
                lines = "\n".join(f"- {line}" for line in result["summary"])
                say(f"🔄 Đã đối chiếu {result['jira_issues_compared']} issue trên Jira và cập nhật plan:\n{lines}\n\n"
                    "Hãy **Approve** lại để đẩy lên Jira.")
            except Exception as exc:
                session.last_error = f"{type(exc).__name__}: {exc}"
                say(f"⚠️ Không kết nối được Jira: `{session.last_error}`")
            st.rerun()
    elif session.offline:
        st.caption("🔒 Đang tắt kết nối Jira. Bật **🔗 Kết nối Jira** ở đầu thanh bên để đẩy.")
    elif status != "approved":
        st.caption("🔒 Cần Approve phiên bản plan hiện tại trước khi đẩy.")


def review_section(plan: dict) -> None:
    t_tasks, t_tree, t_est, t_val, t_audit = st.tabs(["📋 Task", "🌳 Cây Jira", "⏱️ Estimate", "❗ Kiểm tra", "📜 Audit"])
    with t_tasks:
        missing = [f for f in plan["estimate_findings"] if f["rule"] == "missing_estimate"]
        if missing:
            if st.button(f"⏱️ Áp estimate gợi ý cho {len(missing)} task thiếu estimate", type="primary", width="stretch"):
                result = session.apply_suggested_estimates(only_missing=True)
                changed = ", ".join(f"{c['task_id']} → {c['new']}" for c in result.get("changed", []))
                ss.messages.append({"role": "user", "content": "[I applied suggested estimates from the review panel]"})
                say(f"⏱️ Đã áp estimate gợi ý: {changed}. Plan đã cập nhật, cần Approve lại.")
                st.rerun()
        only_problems = st.toggle("Chỉ hiện task có vấn đề")
        rows = task_rows(plan)
        if only_problems:
            rows = [r for r in rows if not r["Trạng thái"].startswith("✅")]
        if not rows:
            st.success("Không có task nào cần xem.")
        else:
            df = pd.DataFrame(rows)
            edited = st.data_editor(
                df, hide_index=True, width="stretch", key=f"editor-{plan_hash(plan)[:8]}-{only_problems}",
                disabled=[col for col in df.columns if col not in EDITABLE],
                column_config={"ID": st.column_config.TextColumn(pinned=True),
                               "Lý do phân loại": st.column_config.TextColumn(width="large"),
                               "Cảnh báo": st.column_config.TextColumn(width="large")})
            original = {r["ID"]: r for r in rows}
            updates = [{"task_id": r["ID"], "field": EDITABLE[col], "value": str(r[col] or "")}
                       for r in edited.to_dict("records") for col in EDITABLE
                       if str(r[col] or "") != str(original[r["ID"]][col] or "")]
            st.caption("Sửa trực tiếp Tiêu đề / Người làm / Estimate / Story rồi bấm Lưu.")
            if st.button(f"💾 Lưu {len(updates)} thay đổi", disabled=not updates):
                session.update_tasks(updates)
                note = "; ".join(f"{u['task_id']} {u['field']} = {u['value']}" for u in updates)
                ss.messages.append({"role": "user", "content": f"[I edited tasks in the review table: {note}]"})
                say(f"📝 Đã lưu {len(updates)} thay đổi từ bảng và cập nhật plan (cần duyệt lại).")
                st.rerun()
    with t_tree:
        st.code("\n".join(render_tree(plan)), language=None)
    with t_est:
        if plan["estimate_findings"]:
            df = pd.DataFrame(plan["estimate_findings"]).rename(columns={
                "task_id": "ID", "task": "Task", "severity": "Mức", "currentEstimate": "Hiện tại",
                "suggestedEstimate": "Gợi ý", "reason": "Lý do"})
            st.dataframe(df[["ID", "Task", "Mức", "Hiện tại", "Gợi ý", "Lý do"]], hide_index=True, width="stretch")
        else:
            st.success("Không có cảnh báo estimate.")
    with t_val:
        if plan["validation_issues"]:
            df = pd.DataFrame(plan["validation_issues"]).rename(columns={
                "task_id": "ID", "task": "Task", "kind": "Loại", "severity": "Mức", "detail": "Chi tiết"})
            st.dataframe(df[["ID", "Task", "Loại", "Mức", "Chi tiết"]], hide_index=True, width="stretch")
        else:
            st.success("Không có lỗi kiểm tra.")
    with t_audit:
        entries = session.repo.audit(session.run_id)
        if entries:
            st.dataframe(pd.DataFrame(entries), hide_index=True, width="stretch")
        calls = session.repo.tool_calls(session.run_id)
        if calls:
            st.caption("Function calls của agent")
            st.dataframe(pd.DataFrame(calls), hide_index=True, width="stretch")


def sync_section() -> None:
    """Re-import an edited Excel: show the differences, then apply them as a new plan version."""
    report = session.sync_report
    with st.expander("🔄 Đồng bộ từ Excel đã sửa", expanded=bool(report and not report["applied"])):
        up = st.file_uploader("File Excel task list đã chỉnh sửa", type=["xlsx", "xlsm"], key=f"sync-{session.run_id}")
        if up is not None and st.button("🔍 So sánh với task hiện tại", width="stretch"):
            name = session.add_file(up.name, up.getvalue()).name
            try:
                result = session.sync_excel(name, apply=False)
                ss.messages.append({"role": "user", "content": f"[I compared the edited Excel {name} with the current tasks]"})
                say(f"🔄 So sánh **{name}**: {result['summary']}. Xem bảng ở thanh bên rồi bấm **Áp dụng**.")
            except Exception as exc:
                say(f"⚠️ Không đồng bộ được: `{type(exc).__name__}: {exc}`")
            st.rerun()
        if not report:
            st.caption("Tải lên file Excel sau khi sửa (thêm/xoá dòng, đổi estimate, người làm...). "
                       "Task đã có trên Jira sẽ được **cập nhật**, không tạo trùng.")
            return
        st.markdown(f"**{report['file']}** — {report['summary']}" + (" ✅ đã áp dụng" if report["applied"] else ""))
        rows = ([{"Thay đổi": "➕ Thêm", "Task": a["title"], "Trường": "", "Cũ": "", "Mới": "", "Jira": ""}
                 for a in report["added"]]
                + [{"Thay đổi": "✏️ Sửa", "Task": c["title"], "Trường": c["field"], "Cũ": c["old"], "Mới": c["new"],
                    "Jira": c["jira_key"]} for c in report["changed"]]
                + [{"Thay đổi": "➖ Bỏ", "Task": x["title"], "Trường": "", "Cũ": "", "Mới": "", "Jira": x["jira_key"]}
                   for x in report["removed"]])
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        if report["has_changes"] and not report["applied"] and st.button("✅ Áp dụng thay đổi", type="primary", width="stretch"):
            result = session.sync_excel(report["file"], apply=True)
            ss.messages.append({"role": "user", "content": f"[I applied the Excel sync from {report['file']}]"})
            say(f"✅ Đã áp dụng thay đổi từ **{report['file']}** ({result['summary']}). Plan đã cập nhật, cần Approve lại.")
            st.rerun()


def jira_actions_section() -> None:
    """Write actions the agent proposed through the Jira MCP tools: nothing runs until a person approves it."""
    toolbox = session._jira_toolbox      # created when the agent first needs it, not on every page load
    if not toolbox or not toolbox.actions:
        return
    pending = toolbox.pending()
    with st.expander(f"🧩 Jira actions ({len(pending)} chờ duyệt)", expanded=bool(pending)):
        approver = st.text_input("Người duyệt", value=os.getenv("USERNAME", ""), key="jira_action_approver")
        for a in toolbox.actions.values():
            icon = {"pending": "⏳", "executed": "✅", "rejected": "🚫", "failed": "❌"}[a["status"]]
            st.markdown(f"{icon} **{a['tool']}** `{a['id']}`")
            st.code(json.dumps(a["args"], ensure_ascii=False, indent=1), language="json")
            if a["status"] == "pending":
                b1, b2 = st.columns(2)
                if b1.button("Approve", key=f"ja_ok_{a['id']}", type="primary", width="stretch", disabled=not approver.strip()):
                    out = session.decide_jira_action(a["id"], True, approver.strip())
                    say(f"✅ **{approver}** đã duyệt `{a['tool']}` → {out.get('status')}"
                        + (f": {out['error']}" if out.get("error") else ""))
                    st.rerun()
                if b2.button("Reject", key=f"ja_no_{a['id']}", width="stretch", disabled=not approver.strip()):
                    session.decide_jira_action(a["id"], False, approver.strip())
                    say(f"🚫 **{approver}** đã từ chối `{a['tool']}`.")
                    st.rerun()
            elif a["status"] == "failed":
                st.caption(a.get("error", ""))


# ================================================================ sidebar
with st.sidebar:
    s1, s2 = st.columns([3, 2], vertical_alignment="center")
    s1.markdown(f"{'🟢' if AI_OK else '🔴'} AI &nbsp; {'🟢' if JIRA_OK else '🔴'} Jira `{session.config['jira']['project_key']}`"
                f" &nbsp; {'🟠' if session.vector_warning else '🟢'} Vector", help=session.vector_warning or None)
    if s2.button("🆕 Chat mới", width="stretch"):
        for k in ("session", "messages", "display", "pending", "push_feedback"):
            ss.pop(k, None)
        st.rerun()
    connected = st.toggle("🔗 Kết nối Jira", value=not session.offline, disabled=not JIRA_OK,
                          help="Bật: đối chiếu trùng với Jira và cho phép đẩy sau khi Approve. "
                               "Tắt: chỉ phân tích trên máy, không gọi Jira, không đẩy được.")
    session.offline = not connected

    # ---- rules profile per project
    projects = list_projects()
    options = [""] + projects
    p1, p2 = st.columns([3, 2], vertical_alignment="bottom")
    chosen = p1.selectbox("📁 Bộ rule dự án", options, index=options.index(session.project) if session.project in options else 0,
                          format_func=lambda n: n or "Chỉ rule chung")
    if chosen != session.project:
        use_project(chosen)
        st.rerun()
    if p2.button("⚙️ Xem / sửa rule", width="stretch"):
        rules_dialog(session, on_saved=use_project)

    # ---- history: every run is stored in the database
    runs = [r for r in session.repo.list_runs(limit=30) if r["run_id"] != session.run_id and (r["tasks"] or r["title"])]
    if runs:
        label = {r["run_id"]: f"{r['updated_at'][:16].replace('T', ' ')} · {r['project_key']} · "
                              f"{r['title'][:40] or '(chưa đặt tên)'} · {r['tasks']} task" for r in runs}
        def reopen():
            if ss.history_pick:
                open_run(ss.history_pick)
            ss.history_pick = ""

        st.selectbox("🕘 Lịch sử", [""] + list(label), key="history_pick", on_change=reopen,
                     format_func=lambda k: label.get(k, "Mở lại phiên trước..."))

    with st.expander("⚙️ Hệ thống: embedding · vector · agent"):
        providers = ["openai", "hf", "auto"]
        cur_p = os.getenv("EMBEDDING_PROVIDER", "openai").lower()
        backends = ["chroma"] + (["pinecone"] if os.getenv("PINECONE_API_KEY") else [])
        cur_b = os.getenv("VECTOR_BACKEND", "chroma").lower()
        sel_p = st.selectbox("Embedding", providers, index=providers.index(cur_p) if cur_p in providers else 0,
                             format_func={"openai": "OpenAI (STU gateway)", "hf": "Hugging Face (local, đa ngôn ngữ)",
                                          "auto": "Tự chọn (gateway nếu có key)"}.get,
                             help="Đổi embedding sẽ dùng bộ vector riêng cho model đó; dữ liệu cũ không bị lẫn.")
        sel_b = st.selectbox("Vector store", backends, index=backends.index(cur_b) if cur_b in backends else 0,
                             format_func={"chroma": "ChromaDB (local)", "pinecone": "Pinecone (cloud)"}.get,
                             help="Pinecone cần PINECONE_API_KEY trong .env.")
        if (sel_p, sel_b) != (cur_p, cur_b):
            switch_vector(sel_p, sel_b)
            st.rerun()
        ss.hitl = st.toggle("✋ Duyệt từng bước agent (human-in-the-loop)", value=ss.hitl,
                            help="Bật: agent dừng trước mỗi lượt chạy tool (LangGraph interrupt) và chờ bạn bấm cho chạy.")
        st.caption(f"Agent chạy trên LangGraph · RAG qua LangChain retriever · TTS: `{os.getenv('TTS_MODEL', 'facebook/mms-tts-vie')}`")

    jira_actions_section()

    plan = session.plan
    if not plan and not session.workshop_report:
        st.info("Bảng review sẽ hiện ở đây sau khi agent phân tích. Kéo mép phải của thanh này để rộng/hẹp.")
    if plan:
        st.markdown("#### 📊 Review kế hoạch")
        c = plan["summary"]["counts"]
        m = st.columns(4)
        m[0].metric("Task", c["tasks_extracted"])
        m[1].metric("Cảnh báo", c["estimation_warnings"] + c["possible_duplicates"])
        m[2].metric("Sẽ tạo", c["to_create"])
        m[3].metric("Skip", c["skipped"])
        if not plan["jira_duplicate_check"]:
            st.caption("⚠️ Plan này chưa đối chiếu trùng với Jira (tạo khi tắt kết nối).")
        with st.container(border=True):
            st.markdown("**✅ Duyệt & đẩy Jira**")
            approval_section(plan)
        review_section(plan)
        sync_section()
        if st.button("🔊 Đọc to tóm tắt plan", width="stretch", help="Hugging Face text-to-speech, chạy trên máy"):
            try:
                with st.spinner("Đang tạo giọng nói (lần đầu tải model)..."):
                    ss.audio = session.speak_plan()
            except Exception as exc:
                ss.audio = {"error": f"{type(exc).__name__}: {exc}"}
        audio = ss.get("audio")
        if audio and audio.get("error"):
            st.error(audio["error"])
        elif audio and session.downloads.get(audio["audio_file"], Path()).exists():
            st.audio(session.downloads[audio["audio_file"]].read_bytes(), format="audio/wav")
            st.caption(audio["text"])

    if session.workshop_report:
        r = session.workshop_report
        with st.expander(f"🎓 Workshop: {r['readiness']} ({r['score']})", expanded=True):
            st.dataframe(pd.DataFrame([{"Mục": x["check"], "Đạt": "✅" if x["passed"] else "❌",
                                        "Chi tiết": x["detail"] or ", ".join(x.get("files", []))} for x in r["checks"]]),
                         hide_index=True, width="stretch")

    if session.downloads:
        with st.expander("⬇️ Tải về"):
            for name, path in sorted(session.downloads.items()):
                if path.exists():
                    st.download_button(name, path.read_bytes(), file_name=name, width="stretch")
            log = session.run_dir / "audit.jsonl"
            if log.exists():
                st.download_button("audit.jsonl", log.read_bytes(), file_name="audit.jsonl", width="stretch")


# ================================================================ main: chat
st.markdown("### 🗂️ Delivery Planning Agent")
if session.plan:
    c = session.plan["summary"]["counts"]
    st.caption(f"📋 Plan hiện tại: {c['tasks_extracted']} task · {c['to_create']} sẽ tạo / {c['skipped']} skip · "
               f"{c['estimation_warnings']} cảnh báo estimate — chi tiết ở thanh bên trái")

chat_box = st.container(height=560)
with chat_box:
    with st.chat_message("assistant"):
        st.markdown(WELCOME)
    for msg in ss.display:
        with st.chat_message(msg["role"]):
            if msg.get("tools"):
                with st.expander(f"🔧 Đã chạy {len(msg['tools'])} bước"):
                    for t in msg["tools"]:
                        st.markdown(f"{'✅' if t['ok'] else '⚠️'} **{t['label']}** `{t['args']}`")
            st.markdown(msg["content"])
    if ss.paused:
        with st.chat_message("assistant"):
            st.warning("✋ Agent muốn chạy các bước sau. Bạn cho chạy chứ?")
            for tc in ss.paused:
                st.markdown(f"- **{TOOL_LABELS.get(tc['function']['name'], tc['function']['name'])}** "
                            f"`{tc['function']['arguments'][:150]}`")
            b1, b2 = st.columns(2)
            if b1.button("▶️ Cho chạy", type="primary", key="hitl_ok", width="stretch"):
                ss.resume_choice = True
            if b2.button("⏹️ Không chạy", key="hitl_no", width="stretch"):
                ss.resume_choice = False

choice = st.pills("Gợi ý", suggest_prompts(session), key=f"sugg-{len(ss.display)}", label_visibility="collapsed")
if choice:
    ss.pending = choice

prompt = st.chat_input("Nhập yêu cầu, hoặc 📎 đính kèm file...", accept_file="multiple",
                       file_type=["md", "txt", "docx", "pdf", "xlsx", "xlsm", "zip"])
text, attached = "", []
if prompt:
    text = prompt.text if hasattr(prompt, "text") else str(prompt)
    attached = [session.add_file(f.name, f.getvalue()).name for f in getattr(prompt, "files", [])]
elif ss.pending:
    text, ss.pending = ss.pending, None

def run_step(step) -> None:
    """Drive the LangGraph agent one step (a new turn or a resume) with a live status box."""
    with chat_box:
        with st.chat_message("assistant"):
            status = st.status("Đang suy nghĩ...", expanded=True)

            def on_tool(name, args, result):
                ok = "error" not in result
                label = TOOL_LABELS.get(name, name)
                ss.turn_tools.append({"label": label, "args": json.dumps(args, ensure_ascii=False)[:120], "ok": ok})
                status.write(f"{'✅' if ok else '⚠️'} {label}" + ("" if ok else f" — {result['error'][:200]}"))
                status.update(label=f"Đang chạy: {label}...")

            try:
                result = step(on_tool)
            except Exception as exc:
                result = TurnResult(text=f"Xin lỗi, tôi gặp lỗi khi gọi AI: `{type(exc).__name__}: {exc}`")
                ss.graph = None
            done = not result.paused
            status.update(label=f"Xong — {len(ss.turn_tools)} bước" if ss.turn_tools else "Xong",
                          state="complete" if done else "running", expanded=not done)
    if result.paused:
        ss.paused = result.paused
    else:
        ss.display.append({"role": "assistant", "content": result.text, "tools": ss.turn_tools})
        ss.graph, ss.paused, ss.turn_tools = None, None, []
        session.save_chat(ss.messages, ss.display)
    st.rerun()


if "resume_choice" in ss and ss.graph is not None:
    choice_ok = ss.pop("resume_choice")
    ss.paused = None

    def resume(on_tool):
        ss.graph.on_tool = on_tool
        return ss.graph.resume(approve=choice_ok)
    run_step(resume)

if text or attached:
    if not text:
        text = f"Tôi vừa đính kèm {', '.join(attached)}. Hãy phân tích giúp tôi."
    shown = text + (f"\n\n📎 {', '.join(attached)}" if attached else "")
    ss.display.append({"role": "user", "content": shown})
    ss.messages.append({"role": "user", "content": text + (f"\n\n[Attached files: {', '.join(attached)}]" if attached else "")})
    with chat_box:
        with st.chat_message("user"):
            st.markdown(shown)
    ss.graph, ss.paused, ss.turn_tools = None, None, []     # a new question drops a pause that was never answered

    def start(on_tool):
        ss.graph = AgentGraph(session, interrupt=ss.hitl, on_tool=on_tool)
        return ss.graph.run(ss.messages)
    run_step(start)
