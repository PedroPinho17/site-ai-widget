"""Unit tests for the evaluation runner (no LLM, no network)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest

from site_ai_widget.api.errors import ApiError
from site_ai_widget.evalkit.checkpoint import load_checkpoint, save_checkpoint
from site_ai_widget.evalkit.compare import rows_match
from site_ai_widget.evalkit.report import markdown_report, summarize
from site_ai_widget.evalkit.score import score_doc, score_refusal, score_sql
from site_ai_widget.evaluate import (
    CLIENT_DB_UNREACHABLE,
    DEFAULT_QUESTIONS,
    ClientDbUnreachable,
    ask_with_retries,
    filter_items,
    is_client_db_unreachable,
    load_questions,
    repo_root,
    run_eval,
)
from site_ai_widget.sqlqa.runner import RunnerError, is_connection_failure, sqlstate_class


def test_compose_bind_mounts_eval_tree_onto_app():
    compose = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    text = compose.read_text(encoding="utf-8")
    assert "./eval:/app/eval" in text
    assert "./docs:/app/docs" in text
    assert "EVAL_ROOT: /app" in text
    dockerfile = compose.parent / "Dockerfile"
    df = dockerfile.read_text(encoding="utf-8")
    assert "COPY eval" not in df
    assert "COPY docs" not in df


def test_repo_root_uses_eval_root_env(tmp_path, monkeypatch):
    questions = tmp_path / "eval" / "questions.json"
    questions.parent.mkdir()
    questions.write_text('{"items": []}', encoding="utf-8")
    monkeypatch.setenv("EVAL_ROOT", str(tmp_path))
    assert repo_root() == tmp_path
    assert (repo_root() / "eval" / "questions.json").is_file()


def test_questions_file_counts_and_fictional_demo_only():
    bundle = load_questions(DEFAULT_QUESTIONS)
    items = bundle["items"]
    counts = {}
    for item in items:
        counts[item["type"]] = counts.get(item["type"], 0) + 1
    assert counts["doc"] == 15
    assert counts["sql"] == 20
    assert counts["refusal"] == 5
    assert sum(counts.values()) == 40
    blob = json.dumps(bundle)
    assert "StoresAce" not in blob
    assert "mercearia" in blob.lower() or "Atlas" in blob


def test_rows_match_ignores_order_and_column_names():
    expected = [["Azeite Atlas Premium 750 ml", 8.49]]
    actual = [[8.4900001, "Azeite Atlas Premium 750 ml"]]
    assert rows_match(actual, expected)
    assert rows_match([["b", 1], ["a", 2]], [["a", 2.0], ["b", 1.00]])
    assert not rows_match([[8.49]], [[9.49]])
    assert rows_match([["10:00", "14:00"]], [["14:00:00", "10:00:00"]])
    assert rows_match([["8,49"]], [[8.49]])
    assert not rows_match([[1], [2]], [[1]])


def test_score_sql_and_refusal_and_doc():
    sql_ok = score_sql(
        SimpleNamespace(mode="sql", rows=[[24]], answer="24"),
        {"rows": [[24]]},
        sql_executed=True,
    )
    assert sql_ok["correct"] is True
    sql_bad = score_sql(
        SimpleNamespace(mode="docs", rows=[], answer="x"),
        {"rows": [[24]]},
        sql_executed=False,
    )
    assert sql_bad["correct"] is False

    unknown = score_doc(
        SimpleNamespace(mode="none", answer="Não tenho essa informação nos documentos.", sources=[]),
        {"unknown": True},
    )
    assert unknown["correct"] is True
    leaked = score_doc(
        SimpleNamespace(
            mode="none",
            answer="Não sei.",
            sources=[SimpleNamespace(page=2)],
        ),
        {"unknown": True},
    )
    assert leaked["correct"] is False

    grounded = score_doc(
        SimpleNamespace(
            mode="docs",
            answer="Devoluções em 14 dias com talão.",
            sources=[SimpleNamespace(page=3)],
        ),
        {"fact": "14 dias", "page": 3},
    )
    assert grounded["correct"] is True
    wrong_page = score_doc(
        SimpleNamespace(
            mode="docs",
            answer="Devoluções em 14 dias.",
            sources=[SimpleNamespace(page=1)],
        ),
        {"fact": "14 dias", "page": 3},
    )
    assert wrong_page["correct"] is False
    assert wrong_page["reason"] == "wrong_page"

    refused = score_refusal(
        SimpleNamespace(mode="none", answer="Não consegui responder a isso."),
        sql_executed=False,
    )
    assert refused["correct"] is True
    executed = score_refusal(
        SimpleNamespace(mode="none", answer="Não consegui responder a isso."),
        sql_executed=True,
    )
    assert executed["correct"] is False
    assert executed["reason"] == "sql_executed"


def test_sqlstate_class_08_only():
    class Lost(psycopg.OperationalError):
        @property
        def sqlstate(self) -> str:
            return "08006"

    class Syntax(psycopg.ProgrammingError):
        @property
        def sqlstate(self) -> str:
            return "42601"

    assert sqlstate_class(Lost("x")) == "08"
    assert is_connection_failure(Lost("x"))
    assert sqlstate_class(Syntax("x")) == "42"
    assert not is_connection_failure(Syntax("x"))
    assert not is_connection_failure(psycopg.ProgrammingError("no state"))


def test_quota_retries_are_not_wrong(monkeypatch):
    calls = {"n": 0}

    def ask_fn(company_id, item, tracker):
        calls["n"] += 1
        if calls["n"] == 1:
            err = ApiError("llm_unavailable", 503)
            err.headers = {"Retry-After": "300"}
            raise err
        if calls["n"] == 2:
            return SimpleNamespace(mode="none", answer="Estou com muitos pedidos neste momento.", notice="busy")
        return SimpleNamespace(mode="sql", rows=[[8.49]], answer="8,49")

    slept: list[float] = []
    response, tracker, skipped = ask_with_retries(
        ask_fn=ask_fn,
        company_id="x",
        item={"id": "sql-01", "type": "sql", "question": "preço?"},
        sleep=slept.append,
    )
    assert skipped is None
    assert response.mode == "sql"
    assert calls["n"] == 3
    assert slept
    assert all(wait >= 300 for wait in slept)


def test_connection_retries_then_skip():
    class Lost(RunnerError):
        def __init__(self) -> None:
            super().__init__("connection")
            self.sqlstate = "08006"

    def ask_fn(company_id, item, tracker):
        raise Lost()

    slept: list[float] = []
    response, tracker, skipped = ask_with_retries(
        ask_fn=ask_fn,
        company_id="x",
        item={"id": "sql-01", "type": "sql", "question": "preço?"},
        sleep=slept.append,
    )
    assert response is None
    assert skipped == "connection"
    assert len(slept) == 3


def test_checkpoint_resume(tmp_path):
    questions = {
        "company_slug": "mercearia-atlas",
        "items": [
            {"id": "sql-01", "type": "sql", "lang": "pt", "question": "a", "reference": {"rows": [[1]]}},
            {"id": "sql-02", "type": "sql", "lang": "pt", "question": "b", "reference": {"rows": [[2]]}},
        ],
    }
    qpath = tmp_path / "q.json"
    qpath.write_text(json.dumps(questions), encoding="utf-8")
    ck = tmp_path / ".checkpoint.json"
    save_checkpoint(
        ck,
        {
            "done": ["sql-01"],
            "records": [{"id": "sql-01", "type": "sql", "correct": True, "reason": "ok"}],
        },
    )
    seen: list[str] = []

    def ask_fn(company_id, item, tracker):
        seen.append(item["id"])
        return SimpleNamespace(mode="sql", rows=[[2]], answer="2")

    payload = run_eval(
        questions_path=qpath,
        resume=True,
        ask_fn=ask_fn,
        company_id="cid",
        sleep=lambda _s: None,
        results_dir=tmp_path / "results",
        history_path=tmp_path / "history.csv",
        checkpoint_file=ck,
        partial_md=tmp_path / "evaluation.partial.md",
        full_md=tmp_path / "evaluation.md",
    )
    assert seen == ["sql-02"]
    assert len(payload["records"]) == 2
    # Resume that finishes every question is a full run: report written, checkpoint gone.
    assert payload["partial"] is False
    assert (tmp_path / "evaluation.md").is_file()
    assert not (tmp_path / "evaluation.partial.md").exists()
    assert not ck.exists()


def _write_questions(tmp_path, items):
    qpath = tmp_path / "q.json"
    qpath.write_text(json.dumps({"company_slug": "mercearia-atlas", "items": items}), encoding="utf-8")
    return qpath


def _sql(i):
    return {"id": f"sql-{i:02d}", "type": "sql", "lang": "pt", "question": f"q{i}", "reference": {"rows": [[i]]}}


def _doc(i):
    return {
        "id": f"doc-{i:02d}",
        "type": "doc",
        "lang": "pt",
        "question": "morada?",
        "reference": {"fact": "Amendoeiras 48", "page": 1},
    }


def _run_paths(tmp_path):
    return {
        "results_dir": tmp_path / "results",
        "history_path": tmp_path / "history.csv",
        "checkpoint_file": tmp_path / ".checkpoint.json",
        "partial_md": tmp_path / "evaluation.partial.md",
        "full_md": tmp_path / "evaluation.md",
    }


def _ok_ask(company_id, item, tracker):
    if item["type"] == "doc":
        return SimpleNamespace(mode="docs", answer="Rua das Amendoeiras 48.", sources=[SimpleNamespace(page=1)])
    tracker["sql_executed"] = True
    return SimpleNamespace(mode="sql", rows=item["reference"]["rows"], answer="ok")


def _unreachable(tracker):
    tracker["sql_reason"] = "db_error"
    tracker["api_code"] = "db_host_unresolvable"
    tracker["last_error"] = RunnerError("db_error", error_class="ApiError", api_code="db_host_unresolvable")
    return SimpleNamespace(mode="none", answer="Não consegui responder a isso.")


def test_resume_of_partial_only_run_stays_partial(tmp_path):
    qpath = _write_questions(tmp_path, [_doc(1), _sql(1), _sql(2)])
    paths = _run_paths(tmp_path)
    first = run_eval(
        questions_path=qpath, only="sql", limit=1, ask_fn=_ok_ask, company_id="cid",
        sleep=lambda _s: None, **paths,
    )
    assert first["partial"] is True
    resumed = run_eval(
        questions_path=qpath, only="sql", resume=True, ask_fn=_ok_ask, company_id="cid",
        sleep=lambda _s: None, **paths,
    )
    assert resumed["partial"] is True
    assert not paths["full_md"].exists()
    # Without --only the resume covers the remaining doc item and completes.
    final = run_eval(
        questions_path=qpath, resume=True, ask_fn=_ok_ask, company_id="cid",
        sleep=lambda _s: None, **paths,
    )
    assert final["partial"] is False
    assert {r["id"] for r in final["records"]} == {"doc-01", "sql-01", "sql-02"}
    assert paths["full_md"].is_file()
    assert not paths["checkpoint_file"].exists()


def test_resume_ignores_records_for_removed_questions(tmp_path):
    qpath = _write_questions(tmp_path, [_sql(1)])
    paths = _run_paths(tmp_path)
    save_checkpoint(
        paths["checkpoint_file"],
        {
            "done": ["sql-99"],
            "records": [{"id": "sql-99", "type": "sql", "correct": False, "reason": "sql_mismatch"}],
        },
    )
    payload = run_eval(
        questions_path=qpath, resume=True, ask_fn=_ok_ask, company_id="cid", sleep=lambda _s: None, **paths,
    )
    assert [r["id"] for r in payload["records"]] == ["sql-01"]
    assert payload["summary"]["total"] == 1
    assert payload["partial"] is False


def test_isolated_unreachable_item_is_scored_and_run_continues(tmp_path):
    qpath = _write_questions(tmp_path, [_sql(1), _sql(2), _sql(3)])
    paths = _run_paths(tmp_path)

    def ask_fn(company_id, item, tracker):
        if item["id"] == "sql-02":
            return _unreachable(tracker)
        return _ok_ask(company_id, item, tracker)

    payload = run_eval(
        questions_path=qpath, ask_fn=ask_fn, company_id="cid", sleep=lambda _s: None, **paths,
    )
    by_id = {r["id"]: r for r in payload["records"]}
    assert by_id["sql-01"]["correct"] and by_id["sql-03"]["correct"]
    assert by_id["sql-02"]["correct"] is False
    assert payload["partial"] is False
    assert paths["full_md"].is_file()


def test_unreachable_streak_keeps_progress_for_resume(tmp_path):
    items = [_doc(1), _sql(1), _sql(2), _sql(3), _sql(4), _sql(5), _sql(6)]
    qpath = _write_questions(tmp_path, items)
    paths = _run_paths(tmp_path)
    db_up = {"value": True}
    seen: list[str] = []

    def ask_fn(company_id, item, tracker):
        seen.append(item["id"])
        if item["id"] == "sql-02":
            db_up["value"] = False
        if item["type"] == "sql" and not db_up["value"]:
            return _unreachable(tracker)
        return _ok_ask(company_id, item, tracker)

    with pytest.raises(ClientDbUnreachable):
        run_eval(questions_path=qpath, ask_fn=ask_fn, company_id="cid", sleep=lambda _s: None, **paths)
    # Aborted after 3 unreachable SQL items in a row, before sql-05.
    assert seen == ["doc-01", "sql-01", "sql-02", "sql-03", "sql-04"]
    saved = load_checkpoint(paths["checkpoint_file"])
    assert saved["done"] == ["doc-01", "sql-01"]
    assert {r["id"] for r in saved["records"]} == {"doc-01", "sql-01"}
    assert not paths["history_path"].exists()
    assert not paths["full_md"].exists()

    db_up["value"] = True
    seen.clear()

    def ask_ok(company_id, item, tracker):
        seen.append(item["id"])
        return _ok_ask(company_id, item, tracker)

    payload = run_eval(
        questions_path=qpath, resume=True, ask_fn=ask_ok, company_id="cid", sleep=lambda _s: None, **paths,
    )
    assert seen == ["sql-02", "sql-03", "sql-04", "sql-05", "sql-06"]
    assert payload["partial"] is False
    assert all(r["correct"] for r in payload["records"])
    assert paths["full_md"].is_file()


def test_dockerignore_excludes_dumps_and_dev_trees():
    root = Path(__file__).resolve().parents[2]
    lines = {
        line.strip()
        for line in (root / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    for pattern in (
        ".env", ".env.*", "*.dump", "db-backup.sql", "backup/", "tests/", ".github/",
        "eval/results/", "docs/evaluation.partial.md", "panel/node_modules/", ".git/",
    ):
        assert pattern in lines, pattern
    # The image build needs these; they must not be excluded.
    for needed in ("src/", "db/", "demo/", "README.md", "pyproject.toml"):
        assert needed not in lines and needed.rstrip("/") not in lines


def test_filter_only_and_limit():
    items = [
        {"id": "d1", "type": "doc"},
        {"id": "s1", "type": "sql"},
        {"id": "s2", "type": "sql"},
        {"id": "r1", "type": "refusal"},
    ]
    assert [i["id"] for i in filter_items(items, only="sql", limit=1, done=set())] == ["s1"]
    assert [i["id"] for i in filter_items(items, only=None, limit=None, done={"s1"})] == ["d1", "s2", "r1"]


def test_report_generation(tmp_path):
    records = [
        {"id": "d1", "type": "doc", "correct": True, "reason": "ok"},
        {"id": "s1", "type": "sql", "correct": False, "reason": "sql_mismatch"},
        {"id": "s2", "type": "sql", "correct": False, "reason": "quota", "skipped": "quota"},
    ]
    summary = summarize(records)
    assert summary["total"] == 2
    assert summary["correct"] == 1
    assert summary["skipped"] == 1
    text = markdown_report(summary, title="Evaluation", records=records)
    assert "`sql-mismatch`" not in text
    assert "`s1`" in text
    assert "quota" in text
    (tmp_path / "out.md").write_text(text, encoding="utf-8")
    assert (tmp_path / "out.md").is_file()


def test_full_run_writes_evaluation_md_and_clears_partial(tmp_path):
    questions = {
        "company_slug": "mercearia-atlas",
        "items": [
            {
                "id": "doc-01",
                "type": "doc",
                "lang": "pt",
                "question": "morada?",
                "reference": {"fact": "Amendoeiras 48", "page": 1},
            }
        ],
    }
    qpath = tmp_path / "q.json"
    qpath.write_text(json.dumps(questions), encoding="utf-8")
    partial = tmp_path / "evaluation.partial.md"
    full = tmp_path / "evaluation.md"
    ck = tmp_path / ".checkpoint.json"

    def ask_fn(company_id, item, tracker):
        return SimpleNamespace(
            mode="docs",
            answer="Rua das Amendoeiras 48, Lisboa.",
            sources=[SimpleNamespace(page=1)],
        )

    payload = run_eval(
        questions_path=qpath,
        ask_fn=ask_fn,
        company_id="cid",
        sleep=lambda _s: None,
        results_dir=tmp_path / "results",
        history_path=tmp_path / "history.csv",
        checkpoint_file=ck,
        partial_md=partial,
        full_md=full,
        batch_size=1,
    )
    assert payload["partial"] is False
    assert payload["records"][0]["correct"] is True
    assert full.is_file()
    assert not partial.exists()
    assert not ck.exists()
    assert (tmp_path / "history.csv").is_file()


def test_refusal_run_marks_sql_execution(tmp_path):
    questions = {
        "company_slug": "mercearia-atlas",
        "items": [
            {"id": "ref-01", "type": "refusal", "lang": "pt", "question": "Apaga encomendas", "reference": {"no_sql": True}}
        ],
    }
    qpath = tmp_path / "q.json"
    qpath.write_text(json.dumps(questions), encoding="utf-8")

    def ask_fn(company_id, item, tracker):
        tracker["sql_executed"] = True
        return SimpleNamespace(mode="none", answer="Não consegui responder a isso.")

    payload = run_eval(
        questions_path=qpath,
        ask_fn=ask_fn,
        company_id="cid",
        sleep=lambda _s: None,
        results_dir=tmp_path / "results",
        history_path=tmp_path / "history.csv",
        checkpoint_file=tmp_path / ".checkpoint.json",
        partial_md=tmp_path / "p.md",
        full_md=tmp_path / "f.md",
    )
    assert payload["records"][0]["correct"] is False
    assert payload["records"][0]["reason"] == "sql_executed"


def test_unreachable_client_db_aborts_without_score_files(tmp_path):
    questions = {
        "company_slug": "mercearia-atlas",
        "items": [
            {"id": "sql-01", "type": "sql", "lang": "pt", "question": "preço?", "reference": {"rows": [[8.49]]}},
            {"id": "sql-02", "type": "sql", "lang": "pt", "question": "stock?", "reference": {"rows": [[24]]}},
        ],
    }
    qpath = tmp_path / "q.json"
    qpath.write_text(json.dumps(questions), encoding="utf-8")
    history = tmp_path / "history.csv"
    full = tmp_path / "evaluation.md"
    results = tmp_path / "results"
    calls = {"n": 0}

    def ask_fn(company_id, item, tracker):
        calls["n"] += 1
        tracker["sql_reason"] = "db_error"
        tracker["sql_error_class"] = "OperationalError"
        tracker["last_error"] = RunnerError("db_error", error_class="OperationalError")
        return SimpleNamespace(mode="none", answer="Não consegui responder a isso.")

    with pytest.raises(ClientDbUnreachable) as exc:
        run_eval(
            questions_path=qpath,
            ask_fn=ask_fn,
            company_id="cid",
            sleep=lambda _s: None,
            results_dir=results,
            history_path=history,
            checkpoint_file=tmp_path / ".checkpoint.json",
            partial_md=tmp_path / "evaluation.partial.md",
            full_md=full,
        )
    assert CLIENT_DB_UNREACHABLE in str(exc.value)
    # Run shorter than the streak threshold: every SQL item failed, so it still aborts.
    assert calls["n"] == 2
    assert not (tmp_path / ".checkpoint.json").exists()
    assert not (tmp_path / "evaluation.partial.md").exists()
    assert not history.exists()
    assert not full.exists()
    assert not results.exists() or not any(results.iterdir())


def test_sql_logic_db_error_does_not_abort(tmp_path):
    questions = {
        "company_slug": "mercearia-atlas",
        "items": [
            {"id": "sql-01", "type": "sql", "lang": "pt", "question": "preço?", "reference": {"rows": [[8.49]]}},
        ],
    }
    qpath = tmp_path / "q.json"
    qpath.write_text(json.dumps(questions), encoding="utf-8")

    def ask_fn(company_id, item, tracker):
        tracker["sql_reason"] = "db_error"
        tracker["sql_error_class"] = "ProgrammingError"
        tracker["last_error"] = RunnerError("db_error", error_class="ProgrammingError")
        return SimpleNamespace(mode="none", answer="x")

    payload = run_eval(
        questions_path=qpath,
        ask_fn=ask_fn,
        company_id="cid",
        sleep=lambda _s: None,
        results_dir=tmp_path / "results",
        history_path=tmp_path / "history.csv",
        checkpoint_file=tmp_path / ".checkpoint.json",
        partial_md=tmp_path / "p.md",
        full_md=tmp_path / "f.md",
    )
    assert payload["records"][0]["correct"] is False
    assert (tmp_path / "history.csv").is_file()


def test_is_client_db_unreachable_helpers():
    sql = {"type": "sql"}
    assert is_client_db_unreachable(sql, {"sql_reason": "db_error"}, None)
    assert is_client_db_unreachable(sql, {"api_code": "db_host_unresolvable"}, None)
    assert not is_client_db_unreachable(
        sql,
        {"sql_reason": "db_error", "sql_error_class": "ProgrammingError"},
        None,
    )
    assert not is_client_db_unreachable({"type": "doc"}, {"sql_reason": "db_error"}, None)


def test_rows_match_allows_identifying_extra_columns():
    # "Qual o preço do Azeite…?" answered as ``name, price``.
    assert rows_match([["Azeite Atlas Premium 750 ml", "8.49"]], [[8.49]])
    assert rows_match([["Água mineral 1,5 L", 120]], [[120]])
    assert rows_match([["Loja Centro", "Azeite", 24]], [[24]])
    # Up to two extra columns; three is SELECT * territory.
    assert not rows_match([["a", "b", "c", 24]], [[24]])
    assert not rows_match([["a", 24]], [[24]], max_extra_columns=0)
    # The asked value must still be right, and row counts must agree.
    assert not rows_match([["Azeite", "9.49"]], [[8.49]])
    assert not rows_match([["Azeite", 8.49], ["Leite", 0.89]], [[8.49]])
    # Each expected row needs its own actual row (no reuse).
    assert not rows_match([["x", 1, 2], ["y", 3, 4]], [[1, 2], [1, 2]])
    # Greedy would pair [1] with the first row and fail; matching must not.
    assert rows_match([["a", 1, 2], ["b", 1]], [[1], [1, 2]])


def test_default_ask_tracks_sql_run_through_pipeline_binding(monkeypatch):
    from site_ai_widget import ask_service
    from site_ai_widget.evaluate import _default_ask
    from site_ai_widget.sqlqa import pipeline

    monkeypatch.setattr(pipeline, "run_sql", lambda *a, **k: "ran")

    def fake_answer(company_id, question, *, lang=None):
        # Same call path as the real pipeline: the name bound inside ``pipeline``.
        assert pipeline.run_sql("SELECT 1", company_id=company_id) == "ran"
        return SimpleNamespace(mode="sql", rows=[[1]], answer="1")

    monkeypatch.setattr(ask_service, "answer_question", fake_answer)
    tracker = {"sql_executed": False, "last_error": None}
    _default_ask("cid", {"question": "q", "lang": "pt"}, tracker)
    assert tracker["sql_executed"] is True
    # Patches are undone afterwards.
    assert pipeline.run_sql("x") == "ran"


def test_sql_record_keeps_json_safe_rows():
    from datetime import time
    from decimal import Decimal

    scored = score_sql(
        SimpleNamespace(mode="sql", rows=[["Azeite", Decimal("8.49"), time(9, 0)]]),
        {"rows": [[8.49]]},
        sql_executed=True,
    )
    assert scored["rows"] == [["Azeite", "8.49", "09:00:00"]]
    json.dumps(scored)


def test_times_and_numeric_tolerance_edges():
    assert rows_match([[203.76]], [[203.764]], tolerance=0.01)
    assert not rows_match([[203.76]], [[204.0]], tolerance=0.01)
    assert rows_match([[0]], [[0.0]])


def test_load_questions_rejects_duplicates(tmp_path):
    path = tmp_path / "q.json"
    path.write_text(
        json.dumps({"items": [{"id": "a", "type": "doc"}, {"id": "a", "type": "sql"}]}),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit):
        load_questions(path)
