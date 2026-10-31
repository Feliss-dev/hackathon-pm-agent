# Test Cases — Workshop 3

Automated: `.venv/Scripts/python -m pytest -q` runs 49 tests (33 from workshop 1 plus 16 new). The new tests need
no network or API key.

| ID | Area | Steps | Expected | Automated |
|---|---|---|---|---|
| TC01 | Function calling | Build the tool schemas | Unique names; no `$ref`/`$defs`; a property named `title` is kept; no tool can write to Jira | `test_tools::test_schemas_are_plain_json_schema` |
| TC02 | Function calling | Call `update_tasks` with field=`category`; call an unknown tool | Both return `{"error": ...}` to the model; every call is stored in `tool_calls` | `test_tools::test_invalid_arguments_go_back_to_the_model` |
| TC03 | Agent loop | Model asks for `analyze`, then `show_plan`, then answers | Tools run in order; history is user/assistant/tool/...; CURRENT STATE refreshed | `test_tools::test_agent_loop_runs_tools_and_answers` |
| TC04 | Retry | API returns 429 twice, then OK | 3 calls, 2 retry notes | `test_retry::test_rate_limit_is_retried_then_succeeds` |
| TC05 | Retry | API returns 503 every time | Gives up after the max number of attempts | `test_retry::test_server_error_retried_but_gives_up` |
| TC06 | Retry | API returns 401 | No retry, error raised at once | `test_retry::test_client_errors_are_not_retried` |
| TC07 | Batching | Embed 250 texts, then 10 old texts + 1 new text | Requests of 100 + 100 + 50; then only 1 text sent (cache) | `test_retry::test_embeddings_are_batched_and_cached` |
| TC08 | DB | Save tasks twice with one changed and one removed | Changed → version 2; removed kept as history; same plan → same version | `test_db_vectors::test_repository_versions_tasks_and_plans` |
| TC09 | DB cache | Assess the same tasks with a new assessor (as after a restart) | The LLM is called once | `test_db_vectors::test_assessment_cache_survives_a_new_assessor` |
| TC10 | Rules RAG | Retrieve the rules for "review" and "unit test" tasks | Only matching rules, in their original priority order; unchanged rules are not re-indexed | `test_db_vectors::test_relevant_rules_come_back_in_priority_order` |
| TC11 | Semantic duplicates | Analyse the same Excel in run 1, then in run 2 | Run 2: 14 "history" warnings (Low, not blocking) | `test_db_vectors::test_session_persists_and_finds_history` |
| TC12 | Semantic duplicates | Run 1 pushed to Jira; analyse the same Excel again | Matching tasks are skipped as "Possible Duplicate of KAN-x" | same test |
| TC13 | History | Reopen run 1 by its first 8 characters | Same plan version, tasks keep their Jira keys | same test |
| TC14 | Requirements RAG | Attach the BRD, ask a question | Passages returned with the file name | `test_db_vectors::test_ask_requirements_returns_cited_passages` |
| TC15 | Resilience | Embedding endpoint down | The plan is still built; warning shown in state / sidebar | `test_db_vectors::test_vector_outage_does_not_break_the_plan` |
| TC16 | Excel sync | Delete a row, change estimate and assignee, add a row | 1 added, 1 removed, 2 field changes, 12 unchanged; nothing applied yet | `test_sync::test_diff_only_reports` |
| TC17 | Excel sync | Same edit with apply=true on a task already in Jira (KAN-7) | Plan v2; the KAN-7 node is "update assignee+estimate"; push updates KAN-7 and creates nothing for it; a second push does not update again | `test_sync::test_apply_creates_new_plan_version_and_updates_jira_tasks` |
| TC18 | Workshop 1 regression | Workshop 1 pipeline, rules, Excel and workshop checks | All pass | `test_pipeline.py`, `test_rules.py`, `test_ai_rules.py`, `test_config.py`, `test_excel_workshop.py` |
| TC19 | LangGraph agent | Scripted model asks for `analyze`; run with `interrupt=True` | Graph stops before the tool; caller's history untouched; `resume(approve=True)` runs it and finishes | `test_graph_lc::test_pause_before_tools_leaves_history_clean_then_resume` |
| TC20 | LangGraph agent | Same, but the person declines | Tool is not run; the model sees "declined" and answers | `test_graph_lc::test_decline_tells_the_model_and_skips_the_tool` |
| TC21 | LangGraph agent | A model that never stops calling tools | Gives up after `MAX_TOOL_ROUNDS` with the standard message; bad JSON / invalid arguments reach the model as errors | `test_graph_lc::test_round_limit_gives_up`, `test_bad_arguments_reach_the_model_as_errors` |
| TC22 | LangChain | Retriever, LCEL chain, `ask_requirements(answer=true)`, registry as `StructuredTool` | Documents carry file / chunk / score; the answer cites passages; tools mirror the registry | `test_graph_lc::test_retriever_chain_and_grounded_answer`, `test_langchain_tools_mirror_the_registry` |
| TC23 | Pinecone backend | `VectorStore(backend="pinecone")` on an in-memory index | Same results as Chroma: project / run filters, doc text round-trip, rules not re-indexed, original rule order | `test_pinecone_speech.py` |
| TC24 | Text-to-speech | Vietnamese number spelling, summary text, wav file, `speak_plan` | Numbers read as words; wav written; file offered in downloads | `test_pinecone_speech::test_viet_numbers_and_summary_text`, `test_synthesize_writes_a_wav_and_session_offers_it` |
| TC25 | Embedding provider | `hf` / `openai` / `auto` with and without keys | An OpenAI model name is never used for `hf`; `auto` falls back to Hugging Face without a key | `test_pinecone_speech::test_embedding_provider_and_model_resolution` |
| TC26 | Jira MCP tools | Fake MCP bridge: read runs, write is queued, approve / reject / failure, policy allow / deny | Nothing is written before approval; decisions are audited | `test_jira_tools.py` |

## Manual checks (real STU gateway)

| ID | Steps | Expected |
|---|---|---|
| M01 | `python -m delivery_agent --profile KAN index-rules` | `common rules: 26 chunks`, `KAN rules: 3 chunks` |
| M02 | Chat: "Phân tích file mẫu task_review.xlsx, rồi cho biết trước đây đã có task nào giống 'Create Customer API' chưa" | Tools `list_files` → `analyze` → `search_similar_tasks`; the answer cites the earlier run |
| M03 | Attach `brd_customer_portal.md`, ask "Yêu cầu đăng ký khách hàng là gì?" | `ask_requirements` returns section 2.1 of the BRD with the file name |
| M04 | Restart the app → **🕘 Lịch sử** → pick an earlier run | Chat, plan and review table come back |
| M05 | Sidebar **🔄 Đồng bộ từ Excel đã sửa** → upload the edited Excel → So sánh → Áp dụng | Diff table; the plan version goes up; Approve is required again |
| M06 | Sidebar **⚙️ Hệ thống** → turn on **✋ Duyệt từng bước**, ask "Phân tích task_review.xlsx" | The chat stops with the tool list; **▶️ Cho chạy** continues until the answer |
| M07 | Chat: "Trên Jira KAN có Epic nào? Thêm comment 'test' vào KAN-22" | Epics listed at once; the comment appears under **🧩 Jira actions**; nothing on Jira until **Approve** |
| M08 | Sidebar **🔊 Đọc to tóm tắt plan** | A player appears; `plan_summary.wav` is in **⬇️ Tải về** |
| M09 | **⚙️ Hệ thống** → Vector store = Pinecone (needs `PINECONE_API_KEY`), then analyse and ask about the BRD | Index created on first use; RAG answers and similar-task lookups still work |
