"""Offline evaluation runner: SQL denotation, document grounding, refusals.

Usage (from the repo root, with the demo company and Groq keys already configured):

    python -m site_ai_widget.evaluate

This module does not print secrets. It is safe to import in unit tests with a
fake ``ask_fn``. Do not run it against a live LLM from automated tests.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from site_ai_widget.api.errors import ApiError
from site_ai_widget.evalkit.checkpoint import (
    CHECKPOINT_NAME,
    clear_checkpoint,
    load_checkpoint,
    save_checkpoint,
)
from site_ai_widget.evalkit.report import append_history, markdown_report, summarize, utc_stamp, write_results_json
from site_ai_widget.evalkit.score import is_busy_answer, score_item
from site_ai_widget.llm.client import MIN_COOLDOWN, retry_after
from site_ai_widget.sqlqa.runner import is_connection_failure

log = logging.getLogger("site_ai_widget.eval")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUESTIONS = REPO_ROOT / "eval" / "questions.json"
RESULTS_DIR = REPO_ROOT / "eval" / "results"
HISTORY_CSV = REPO_ROOT / "eval" / "history.csv"
CHECKPOINT = REPO_ROOT / "eval" / CHECKPOINT_NAME
PARTIAL_MD = REPO_ROOT / "docs" / "evaluation.partial.md"
FULL_MD = REPO_ROOT / "docs" / "evaluation.md"

MAX_QUOTA_RETRIES = 8
MAX_CONNECTION_RETRIES = 3

AskFn = Callable[..., Any]


def load_questions(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise SystemExit("questions file must be an object with an items array")
    ids = [item.get("id") for item in data["items"]]
    if len(ids) != len(set(ids)):
        raise SystemExit("duplicate question ids")
    return data


def filter_items(
    items: list[dict[str, Any]],
    *,
    only: str | None,
    limit: int | None,
    done: set[str],
) -> list[dict[str, Any]]:
    out = []
    for item in items:
        if only and item.get("type") != only:
            continue
        if item.get("id") in done:
            continue
        out.append(item)
        if limit is not None and len(out) >= limit:
            break
    return out


def _quota_wait(error: BaseException | None) -> float:
    if isinstance(error, ApiError) and error.headers:
        raw = error.headers.get("Retry-After")
        if raw:
            try:
                return max(MIN_COOLDOWN, float(raw))
            except ValueError:
                pass
    if error is not None:
        return max(MIN_COOLDOWN, retry_after(error))
    return MIN_COOLDOWN


def _company_id(slug: str) -> str:
    from site_ai_widget.db.pool import connection

    with connection() as conn:
        row = conn.execute("SELECT id::text FROM companies WHERE slug = %s", (slug,)).fetchone()
    if not row:
        raise SystemExit(f"company slug not found: {slug}")
    return str(row[0])


def _default_ask(company_id: str, item: dict[str, Any], tracker: dict[str, Any]) -> Any:
    from site_ai_widget.ask_service import answer_question
    from site_ai_widget.sqlqa import pipeline
    from site_ai_widget.sqlqa import runner as sql_runner

    original_run = sql_runner.run_sql
    original_sql = pipeline.answer_with_sql

    def wrapped_run(*args: Any, **kwargs: Any):
        tracker["sql_executed"] = True
        try:
            return original_run(*args, **kwargs)
        except Exception as exc:
            tracker["last_error"] = exc
            raise

    def wrapped_sql(*args: Any, **kwargs: Any):
        out = original_sql(*args, **kwargs)
        tracker["sql_reason"] = getattr(out, "reason", None)
        return out

    sql_runner.run_sql = wrapped_run  # type: ignore[method-assign]
    pipeline.answer_with_sql = wrapped_sql  # type: ignore[method-assign]
    try:
        return answer_question(
            company_id,
            item["question"],
            lang=item.get("lang"),
        )
    finally:
        sql_runner.run_sql = original_run  # type: ignore[method-assign]
        pipeline.answer_with_sql = original_sql  # type: ignore[method-assign]


def ask_with_retries(
    *,
    ask_fn: AskFn,
    company_id: str,
    item: dict[str, Any],
    sleep: Callable[[float], None],
) -> tuple[Any | None, dict[str, bool], str | None]:
    """Run one item. Quota and SQLSTATE 08 wait and retry; they are not 'wrong'."""
    tracker: dict[str, Any] = {"sql_executed": False, "last_error": None}
    quota_tries = 0
    conn_tries = 0
    while True:
        tracker["sql_executed"] = False
        tracker["last_error"] = None
        try:
            response = ask_fn(company_id, item, tracker)
        except ApiError as exc:
            if exc.code == "llm_unavailable":
                quota_tries += 1
                if quota_tries > MAX_QUOTA_RETRIES:
                    return None, tracker, "quota"
                sleep(_quota_wait(exc))
                continue
            raise
        except Exception as exc:
            if is_connection_failure(exc) or getattr(exc, "reason", None) == "connection":
                conn_tries += 1
                if conn_tries > MAX_CONNECTION_RETRIES:
                    return None, tracker, "connection"
                sleep(1.0)
                continue
            raise
        if is_busy_answer(response):
            quota_tries += 1
            if quota_tries > MAX_QUOTA_RETRIES:
                return None, tracker, "quota"
            sleep(_quota_wait(None))
            continue
        reason = getattr(response, "reason", None) if not isinstance(response, dict) else response.get("reason")
        if reason == "connection":
            conn_tries += 1
            if conn_tries > MAX_CONNECTION_RETRIES:
                return None, tracker, "connection"
            sleep(1.0)
            continue
        last = tracker.get("last_error")
        sql_reason = tracker.get("sql_reason")
        if sql_reason == "connection" or (
            last is not None
            and (is_connection_failure(last) or getattr(last, "reason", None) == "connection")
        ):
            conn_tries += 1
            if conn_tries > MAX_CONNECTION_RETRIES:
                return None, tracker, "connection"
            sleep(1.0)
            continue
        return response, tracker, None


def _record_payload(item: dict[str, Any], scored: dict[str, Any], *, skipped: str | None) -> dict[str, Any]:
    rec = {
        "id": item["id"],
        "type": item["type"],
        "lang": item.get("lang"),
        "correct": False if skipped else bool(scored.get("correct")),
        "reason": skipped or scored.get("reason"),
        "skipped": skipped,
    }
    if "sql_executed" in scored:
        rec["sql_executed"] = scored["sql_executed"]
    if "pages" in scored:
        rec["pages"] = scored["pages"]
    return rec


def run_eval(
    *,
    questions_path: Path = DEFAULT_QUESTIONS,
    only: str | None = None,
    limit: int | None = None,
    delay: float = 0.0,
    batch_size: int = 5,
    resume: bool = False,
    ask_fn: AskFn | None = None,
    company_id: str | None = None,
    company_slug: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
    results_dir: Path = RESULTS_DIR,
    history_path: Path = HISTORY_CSV,
    checkpoint_file: Path = CHECKPOINT,
    partial_md: Path = PARTIAL_MD,
    full_md: Path = FULL_MD,
) -> dict[str, Any]:
    bundle = load_questions(questions_path)
    items: list[dict[str, Any]] = list(bundle["items"])
    done: set[str] = set()
    records: list[dict[str, Any]] = []
    if resume:
        saved = load_checkpoint(checkpoint_file)
        done = set(saved["done"])
        records = list(saved["records"])
    if batch_size < 1:
        batch_size = 1
    if delay < 0:
        delay = 0.0
    pending = filter_items(items, only=only, limit=limit, done=done)
    slug = company_slug or bundle.get("company_slug") or "mercearia-atlas"
    cid = company_id
    ask = ask_fn
    if ask is None:
        load_dotenv()
        cid = cid or _company_id(str(slug))
        ask = _default_ask
    assert ask is not None

    stamp = utc_stamp()
    results_path = results_dir / f"{stamp}.json"
    full_run = only is None and limit is None and not done and len(pending) == len(items)

    for index, item in enumerate(pending, start=1):
        if delay and index > 1:
            sleep(delay)
        response, tracker, skipped = ask_with_retries(
            ask_fn=ask,
            company_id=cid or "",
            item=item,
            sleep=sleep,
        )
        if skipped:
            scored = {"correct": False, "reason": skipped, "sql_executed": tracker.get("sql_executed")}
        else:
            scored = score_item(item, response, sql_executed=bool(tracker.get("sql_executed")))
        rec = _record_payload(item, scored, skipped=skipped)
        records.append(rec)
        done.add(item["id"])
        save_checkpoint(
            checkpoint_file,
            {"done": sorted(done), "records": records, "results_path": str(results_path)},
        )
        if index % max(1, batch_size) == 0 or index == len(pending):
            summary = summarize(records)
            partial_md.parent.mkdir(parents=True, exist_ok=True)
            partial_md.write_text(
                markdown_report(summary, title="Evaluation (partial)", records=records),
                encoding="utf-8",
            )

    summary = summarize(records)
    payload = {
        "timestamp": stamp,
        "company_slug": slug,
        "partial": not full_run,
        "summary": summary,
        "records": records,
    }
    write_results_json(results_path, payload)
    by_type = summary.get("by_type") or {}
    append_history(
        history_path,
        {
            "timestamp": stamp,
            "total": summary.get("total"),
            "correct": summary.get("correct"),
            "accuracy": summary.get("accuracy"),
            "skipped": summary.get("skipped"),
            "doc_accuracy": (by_type.get("doc") or {}).get("accuracy"),
            "sql_accuracy": (by_type.get("sql") or {}).get("accuracy"),
            "refusal_accuracy": (by_type.get("refusal") or {}).get("accuracy"),
            "partial": not full_run,
        },
    )
    if full_run:
        full_md.parent.mkdir(parents=True, exist_ok=True)
        full_md.write_text(
            markdown_report(summary, title="Evaluation", records=records),
            encoding="utf-8",
        )
        partial_md.unlink(missing_ok=True)
        clear_checkpoint(checkpoint_file)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the site-ai-widget evaluation set.")
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--delay", type=float, default=0.0, help="Seconds to wait between items.")
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--only", choices=("doc", "sql", "refusal"), default=None)
    parser.add_argument("--company-slug", default=None, help="Override questions file slug.")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = build_parser().parse_args(argv)
    payload = run_eval(
        questions_path=args.questions,
        only=args.only,
        limit=args.limit,
        delay=args.delay,
        batch_size=args.batch_size,
        resume=args.resume,
        company_slug=args.company_slug,
    )
    summary = payload["summary"]
    log.info(
        "eval done total=%s correct=%s skipped=%s",
        summary.get("total"),
        summary.get("correct"),
        summary.get("skipped"),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
