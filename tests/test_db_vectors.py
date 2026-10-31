"""Persistence (SQLite) and vector lookup (Chroma) through the agent session."""
from delivery_agent.models import Task
from delivery_agent.vectors import chunk_markdown, split_rules


def test_repository_versions_tasks_and_plans(repo, tmp_path):
    run = repo.create_run("KAN", "", tmp_path)
    repo.replace_tasks(run, [Task(id="T1", title="A"), Task(id="T2", title="B")])
    repo.replace_tasks(run, [Task(id="T1", title="A changed")])
    assert [t.title for t in repo.load_tasks(run)] == ["A changed"]
    assert repo.task_versions(run) == {"T1": 2, "T2": 1}            # T2 kept as deleted history
    assert repo.save_plan(run, {"x": 1}, "h1") == 1
    assert repo.save_plan(run, {"x": 1}, "h1") == 1                 # same plan -> same version
    assert repo.save_plan(run, {"x": 2}, "h2") == 2
    assert repo.latest_plan(run) == {"x": 2, "version": 2}
    assert repo.list_runs("KAN")[0]["plan_version"] == 2


def test_assessment_cache_survives_a_new_assessor(repo):
    from delivery_agent.ai_rules import RuleAssessor, RuleReading
    calls = []

    class Counting(RuleAssessor):
        def _call(self, tasks):
            calls.append(len(tasks))
            return {t.id: RuleReading("BE", "api rule", None, 16, "BE 16h") for t in tasks}

    tasks = [Task(id=f"T{i}", title=f"Create API {i}") for i in range(3)]
    Counting("rules", client=object(), cache=repo.cache("assessment")).assess(tasks)
    Counting("rules", client=object(), cache=repo.cache("assessment")).assess(tasks)   # e.g. after a restart
    assert calls == [3]


def test_chunking_keeps_headings_and_rule_order():
    text = "# Title\n\nintro\n\n## Part 1\n\n" + ("word " * 800) + "\n\n## Part 2\n\nshort"
    chunks = chunk_markdown(text, max_chars=1000, overlap=100)
    assert all(c.startswith("#") for c in chunks) and len(chunks) >= 4
    rules = open("config/rules.md", encoding="utf-8").read()
    parts = split_rules(rules)
    assert any("Review Code" in p for p in parts) and any("Communication" in p for p in parts)


def test_relevant_rules_come_back_in_priority_order(vectors):
    rules = open("config/rules.md", encoding="utf-8").read()
    vectors.index_rules("__common__", rules)
    picked = vectors.relevant_rules("__common__", ["review merge request code", "unit tests pytest"], k_per_text=3)
    order = [split_rules(rules).index(p) for p in picked]
    assert order == sorted(order)
    assert vectors.index_rules("__common__", rules) == len(split_rules(rules))    # unchanged -> no re-index


def test_session_persists_and_finds_history(make_session, workbook):
    first = make_session()
    first.analyze([str(workbook)])
    assert first.plan["version"] == 1
    assert first.repo.latest_plan(first.run_id)["summary"] == first.plan["summary"]
    assert first.repo.audit(first.run_id)                            # audit in the DB too

    # A second run on the same work: semantic duplicates against the first run, which only warn (not in Jira)
    second = make_session()
    second.analyze([str(workbook)])
    history = [i for i in second.plan["validation_issues"] if i["scope"] == "history"]
    assert len(history) == 14 and all(i["severity"] == "Low" for i in history)
    assert second.plan["summary"]["counts"]["to_create"] == first.plan["summary"]["counts"]["to_create"]
    hits = second.search_similar_tasks("Customer API endpoint", k=3)["similar_tasks"]
    assert any("Customer API" in h["title"] for h in hits)

    # Once the first run's tasks are in Jira, the same work is blocked
    first.after_push({"created": {n["ref"]: f"KAN-{i}" for i, n in enumerate(first.plan["planned_issues"])
                                  if n["task_id"] and not n["skip_reason"]}, "linked": [], "skipped": {}})
    third = make_session()
    third.analyze([str(workbook)])
    blocked = [n for n in third.plan["planned_issues"] if n["skip_reason"].startswith("Possible Duplicate of KAN-")]
    assert blocked

    # Reopen the first run from the database
    reopened = make_session(run_id=first.run_id[:8])
    assert reopened.plan["version"] == first.plan["version"]
    assert {t.jira_key for t in reopened.tasks} - {""}


def test_ask_requirements_returns_cited_passages(make_session):
    s = make_session()
    s.add_file("brd.md", open("samples/brd_customer_portal.md", "rb").read())
    out = s.ask_requirements("payment", k=3)
    assert out["passages"] and all(p["file"] == "brd.md" for p in out["passages"])


def test_vector_outage_does_not_break_the_plan(make_session, workbook):
    class Down:
        model = "down"

        def embed(self, texts):
            raise ConnectionError("embedding endpoint unreachable")
    import chromadb
    from delivery_agent.vectors import VectorStore
    s = make_session(vectors=VectorStore(Down(), client=chromadb.EphemeralClient()))
    s.analyze([str(workbook)])
    assert s.plan["summary"]["counts"]["tasks_extracted"] == 14
    assert "unreachable" in s.vector_warning and "vector_lookup" in s.state()
