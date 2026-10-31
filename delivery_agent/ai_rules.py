"""Apply the human-written rules (Markdown prompt) to tasks with the LLM.

For every task the model returns a category (from the fixed list), the reason (which rule applied), and an
estimate review (ok / suggested value / reason). Results are cached per (rules, task content) - in memory and,
when a `cache` is given, in the database - so unchanged tasks never call the model again, even after a restart.

Scaling:
- tasks are sent in batches of BATCH_SIZE and the batches run in parallel (MAX_PARALLEL), each with retry/backoff;
- `rules_for(tasks)` may return only the rules relevant to a batch (vector retrieval) instead of the whole file;
- `evidence(tasks)` may add similar tasks from earlier runs (vector lookup) that the model can cite.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass

from .estimate import fmt_hours, parse_hours
from .llm import LLMConfigError, get_client, structured_completion
from .models import CATEGORIES, Task

BATCH_SIZE = 40
MAX_PARALLEL = 4
RULES_RETRIEVAL_MIN_CHARS = 6000   # below this the whole rule file is sent: retrieval would only lose context

SYSTEM_PROMPT = """You read a delivery team's rules and say what they mean for each task. The rules were written by
people (project managers) and are the only source of truth.

How to apply them, for every task:
1. If the rules contain a project-specific part (PHẦN A), check it FIRST. When a project rule matches the task it
   decides, even if a common rule would also match (e.g. "payment tasks are BE even if they mention test" beats a
   common "unit test -> UT" rule).
2. Otherwise apply the common rules (PHẦN B) in their stated priority order.

For EACH task return:
- category: exactly one of the allowed categories, chosen by the rules. If no rule fits, "Other".
- category_reason: one short sentence (in the rules' language) naming the rule that decided it.
- max_hours: the maximum estimate in hours the rules allow for this kind of task (e.g. a meeting limit), or null when
  no rule sets a limit. A project limit replaces the common one.
- default_hours: the estimate in hours the rules suggest when the task has no estimate (from the rules' table for its
  category, or a number stated in the task description).
- estimate_rule: one short sentence naming the rule(s) behind max_hours / default_hours.

A task may come with `similar_past_tasks` (tasks of earlier plans with their final category and estimate). They are
evidence only: the rules decide. When they agree with your reading, you may mention them in estimate_rule
(e.g. "giống task cũ X: 16h").

Do not compare estimates yourself and do not invent numbers that the rules do not give. Return every task id once."""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"assessments": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "category": {"type": "string", "enum": CATEGORIES},
            "category_reason": {"type": "string"},
            "max_hours": {"type": ["number", "null"]},
            "default_hours": {"type": ["number", "null"]},
            "estimate_rule": {"type": "string"},
        },
        "required": ["id", "category", "category_reason", "max_hours", "default_hours", "estimate_rule"],
        "additionalProperties": False,
    }}},
    "required": ["assessments"],
    "additionalProperties": False,
}


@dataclass
class RuleReading:
    """What the rules say about one task, as read by the AI."""
    category: str
    category_reason: str
    max_hours: float | None
    default_hours: float | None
    estimate_rule: str


@dataclass
class Assessment:
    category: str
    category_reason: str
    estimate_ok: bool = True
    suggested_estimate: str = ""
    estimate_reason: str = ""
    max_hours: float | None = None
    default_hours: float | None = None

    def dump(self) -> dict:
        return asdict(self)


class AssessmentError(RuntimeError):
    pass


class RuleAssessor:
    def __init__(self, rules_text: str, client=None, hours_per_day: float = 8, cache=None,
                 evidence: Callable[[list[Task]], dict[str, list[dict]]] | None = None,
                 rules_for: Callable[[list[Task]], str] | None = None,
                 on_retry: Callable[[str], None] | None = None):
        self.rules_text = rules_text
        self.hpd = hours_per_day
        self._client = client
        self._cache: dict[str, RuleReading] = {}
        self.store = cache            # persistent cache: get_many(keys) / put_many({key: dict})
        self.evidence = evidence
        self.rules_for = rules_for
        self.on_retry = on_retry
        self._rules_hash = hashlib.sha256(rules_text.encode("utf-8")).hexdigest()[:16]

    @property
    def client(self):
        if self._client is None:
            try:
                self._client = get_client()
            except LLMConfigError as exc:
                raise AssessmentError("Chưa có LLM_API_KEY (hoặc API_KEY) trong .env - AI cần để áp rule") from exc
        return self._client

    def _key(self, t: Task) -> str:
        raw = json.dumps([self._rules_hash, t.title, t.description, t.assignee], ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _task_payload(self, t: Task, similar: list[dict] | None = None) -> dict:
        out = {"id": t.id, "title": t.title, "description": t.description[:600], "assignee": t.assignee}
        if similar:
            out["similar_past_tasks"] = similar
        return out

    def _rules(self, tasks: list[Task]) -> str:
        if self.rules_for and len(self.rules_text) >= RULES_RETRIEVAL_MIN_CHARS:
            return self.rules_for(tasks) or self.rules_text
        return self.rules_text

    def _call(self, tasks: list[Task]) -> dict[str, RuleReading]:
        similar = self.evidence(tasks) if self.evidence else {}
        payload = json.dumps([self._task_payload(t, similar.get(t.id)) for t in tasks], ensure_ascii=False)
        response = structured_completion(
            self.client, system=SYSTEM_PROMPT,
            user=(f"<rules>\n{self._rules(tasks)}\n</rules>\n\nAllowed categories: {', '.join(CATEGORIES)}\n\n"
                  f"<tasks>\n{payload}\n</tasks>"),
            name="task_assessments", schema=OUTPUT_SCHEMA, temperature=0,
            what=f"rule assessment ({len(tasks)} tasks)", on_retry=self.on_retry)
        try:
            items = json.loads(response.choices[0].message.content)["assessments"]
        except (json.JSONDecodeError, KeyError, TypeError, IndexError) as exc:
            raise AssessmentError(f"AI trả kết quả không đọc được: {exc}") from exc
        out = {}
        for it in items:
            category = it["category"] if it["category"] in CATEGORIES else "Other"
            out[str(it["id"])] = RuleReading(category, it.get("category_reason", ""), it.get("max_hours"),
                                             it.get("default_hours"), it.get("estimate_rule", ""))
        return out

    def _run_batch(self, batch: list[Task]) -> dict[str, RuleReading]:
        results = self._call(batch)
        missing = [t for t in batch if t.id not in results]
        if missing:  # one retry for tasks the model skipped
            results.update(self._call(missing))
        return {t.id: results.get(t.id) or RuleReading(
            "Other", "AI không trả kết quả cho task này - cần người xem lại", None, None, "") for t in batch}

    def read_rules(self, tasks: list[Task]) -> dict[str, RuleReading]:
        """What the rules say about each task (AI). Unchanged tasks come from the cache."""
        if self.store:
            wanted = [self._key(t) for t in tasks if self._key(t) not in self._cache]
            for key, value in self.store.get_many(wanted).items():
                self._cache[key] = RuleReading(**value)
        todo = list({self._key(t): t for t in tasks if self._key(t) not in self._cache}.values())
        batches = [todo[i:i + BATCH_SIZE] for i in range(0, len(todo), BATCH_SIZE)]
        if len(batches) > 1:
            with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(batches))) as pool:
                results = list(pool.map(self._run_batch, batches))
        else:
            results = [self._run_batch(b) for b in batches]
        new = {}
        for batch, readings in zip(batches, results):
            for t in batch:
                new[self._key(t)] = readings[t.id]
        self._cache.update(new)
        if self.store and new:
            self.store.put_many({k: asdict(v) for k, v in new.items()})
        return {t.id: self._cache[self._key(t)] for t in tasks}

    def assess(self, tasks: list[Task]) -> dict[str, Assessment]:
        """Category from the AI's reading of the rules; the estimate comparison itself is done here in code."""
        readings = self.read_rules(tasks)
        out = {}
        for t in tasks:
            r = readings[t.id]
            hours = parse_hours(t.estimate, self.hpd)
            ok, suggested, reason = True, "", ""
            if not str(t.estimate).strip():
                ok = False
                suggested = fmt_hours(r.default_hours) if r.default_hours else ""
                reason = f"Estimate trống; theo rule gợi ý {suggested or '(rule không nêu)'}" + (f" ({r.estimate_rule})" if r.estimate_rule else "")
            elif hours is not None and r.max_hours and hours > r.max_hours:
                ok = False
                suggested = fmt_hours(r.max_hours)
                reason = f"{fmt_hours(hours)} vượt mức tối đa {suggested} theo rule" + (f" ({r.estimate_rule})" if r.estimate_rule else "")
            out[t.id] = Assessment(r.category, r.category_reason, ok, suggested, reason, r.max_hours, r.default_hours)
        return out
