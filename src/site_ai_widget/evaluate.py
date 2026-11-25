"""Offline evaluation runner: SQL denotation, document grounding, refusals.

Run inside the Compose **app** container so the stored client DSN hostname
``clientdb`` resolves (host port 5436 is not that name):

    docker compose up -d app
    docker compose exec app python -m site_ai_widget.evaluate --delay 0.5 --batch-size 5

This module does not print secrets. It is safe to import in unit tests with a
fake ``ask_fn``. Do not run it against a live LLM from automated tests.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
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
from site_ai_widget.sqlqa.runner import is_connection_failure, sqlstate_class

log = logging.getLogger("site_ai_widget.eval")


def repo_root() -> Path:
    """Directory that holds ``eval/questions.json`` (host bind-mount or source tree).

    Inside Compose the package is installed under ``/opt/venv``, so ``__file__``
    is not the git root. Prefer ``EVAL_ROOT``, then a source-tree layout, then
    cwd / ``/app`` when those contain the questions file.
    """
    configured = (os.environ.get("EVAL_ROOT") or "").strip()
    if configured:
        return Path(configured)
    here = Path(__file__).resolve()
    if here.parent.name == "site_ai_widget" and here.parent.parent.name == "src":
        return here.parents[2]
    for candidate in (Path.cwd(), Path("/app")):
        if (candidate / "eval" / "questions.json").is_file():
            return candidate
    return Path.cwd()


def _eval_paths(root: Path | None = None) -> dict[str, Path]:
    base = root or repo_root()
    return {
        "questions": base / "eval" / "questions.json",
        "results": base / "eval" / "results",
        "history": base / "eval" / "history.csv",
        "checkpoint": base / "eval" / CHECKPOINT_NAME,
        "partial_md": base / "docs" / "evaluation.partial.md",
        "full_md": base / "docs" / "evaluation.md",
    }


DEFAULT_QUESTIONS = _eval_paths()["questions"]

MAX_QUOTA_RETRIES = 8
MAX_CONNECTION_RETRIES = 3
# Abort after this many SQL items in a row could not reach the client DB. One
# blip is scored as that item's failure; a dead DB stops the run early.
UNREACHABLE_ABORT_STREAK = 3
UNREACHABLE_API_CODES = frozenset(
    {
        "db_host_unresolvable",
        "db_unreachable",
        "db_connection_failed",
        "db_timeout",
        "db_host_not_allowed",
    }
)
SQL_LOGIC_ERROR_CLASSES = frozenset({"ProgrammingError", "DataError", "IntegrityError", "NotSupportedError"})
CLIENT_DB_UNREACHABLE = "client DB unreachable"

AskFn = Callable[..., Any]


class ClientDbUnreachable(SystemExit):
    """Eval aborted: the client database could not be reached."""

    def __init__(self) -> None:
        super().__init__(CLIENT_DB_UNREACHABLE)


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
            tracker["sql_error_class"] = getattr(exc, "error_class", None) or type(exc).__name__
            tracker["sqlstate_class"] = getattr(exc, "sqlstate_class", None) or sqlstate_class(exc)
            tracker["api_code"] = getattr(exc, "api_code", None)
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


def is_client_db_unreachable(
    item: dict[str, Any],
    tracker: dict[str, Any],
    skipped: str | None,
) -> bool:
    """True when SQL never reached the client database (DNS, TCP, SQLSTATE 08)."""
    if item.get("type") != "sql":
        return False
    if skipped == "connection":
        return True
    last = tracker.get("last_error")
    api_code = tracker.get("api_code") or getattr(last, "api_code", None)
    if api_code in UNREACHABLE_API_CODES:
        return True
    state = tracker.get("sqlstate_class") or getattr(last, "sqlstate_class", None)
    if state == "08" or sqlstate_class(last) == "08":
        return True
    if tracker.get("sql_reason") == "connection" or getattr(last, "reason", None) == "connection":
        return True
    error_class = tracker.get("sql_error_class") or getattr(last, "error_class", None)
    if error_class in SQL_LOGIC_ERROR_CLASSES:
        return False
    return bool(tracker.get("sql_reason") == "db_error" or getattr(last, "reason", None) == "db_error")


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
    results_dir: Path | None = None,
    history_path: Path | None = None,
    checkpoint_file: Path | None = None,
    partial_md: Path | None = None,
    full_md: Path | None = None,
) -> dict[str, Any]:
    paths = _eval_paths()
    results_dir = results_dir or paths["results"]
    history_path = history_path or paths["history"]
    checkpoint_file = checkpoint_file or paths["checkpoint"]
    partial_md = partial_md or paths["partial_md"]
    full_md = full_md or paths["full_md"]
    bundle = load_questions(questions_path)
    items: list[dict[str, Any]] = list(bundle["items"])
    done: set[str] = set()
    records: list[dict[str, Any]] = []
    if resume:
        saved = load_checkpoint(checkpoint_file)
        # Ignore answers to questions no longer in the set (edited questions.json).
        known = {i["id"] for i in items}
        done = {x for x in saved["done"] if x in known}
        records = [r for r in saved["records"] if r.get("id") in known]
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
    # SQL items in a row (this process) that never reached the client DB.
    # A reachable SQL item resets it; doc/refusal items do not touch it.
    unreachable_streak: list[str] = []
    sql_attempted = 0

    def _abort_unreachable() -> None:
        """Drop the unreachable items so ``--resume`` retries them; keep the rest."""
        dropped = set(unreachable_streak)
        kept = [r for r in records if r.get("id") not in dropped]
        records[:] = kept
        done.difference_update(dropped)
        log.error("%s", CLIENT_DB_UNREACHABLE)
        if done:
            save_checkpoint(
                checkpoint_file,
                {"done": sorted(done), "records": records, "results_path": str(results_path)},
            )
            partial_md.parent.mkdir(parents=True, exist_ok=True)
            partial_md.write_text(
                markdown_report(summarize(records), title="Evaluation (partial)", records=records),
                encoding="utf-8",
            )
        else:
            clear_checkpoint(checkpoint_file)
            partial_md.unlink(missing_ok=True)
        raise ClientDbUnreachable()

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
        if item.get("type") == "sql":
            sql_attempted += 1
            if is_client_db_unreachable(item, tracker, skipped):
                unreachable_streak.append(item["id"])
                if len(unreachable_streak) >= UNREACHABLE_ABORT_STREAK:
                    _abort_unreachable()
            else:
                unreachable_streak.clear()
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

    if sql_attempted and len(unreachable_streak) == sql_attempted:
        # Every SQL item in this process failed to reach the DB (fewer than the
        # streak threshold, e.g. ``--limit 2``): still not a score.
        _abort_unreachable()

    # Full = no --only/--limit and every question answered, whether in one go
    # or across ``--resume`` runs.
    answered = {r.get("id") for r in records}
    full_run = only is None and limit is None and all(i["id"] in answered for i in items)

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
    parser.add_argument("--questions", type=Path, default=None)
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
    try:
        payload = run_eval(
            questions_path=args.questions or _eval_paths()["questions"],
            only=args.only,
            limit=args.limit,
            delay=args.delay,
            batch_size=args.batch_size,
            resume=args.resume,
            company_slug=args.company_slug,
        )
    except ClientDbUnreachable as exc:
        log.error("%s", exc)
        return 2
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
