"""Write eval JSON, history.csv and markdown summaries."""
from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_type: dict[str, Counter[str]] = {}
    skipped = 0
    for rec in records:
        kind = str(rec.get("type") or "other")
        bucket = by_type.setdefault(kind, Counter())
        if rec.get("skipped"):
            skipped += 1
            bucket["skipped"] += 1
            continue
        bucket["total"] += 1
        if rec.get("correct"):
            bucket["correct"] += 1
    types: dict[str, Any] = {}
    totals = Counter()
    for kind, counts in sorted(by_type.items()):
        total = int(counts["total"])
        correct = int(counts["correct"])
        types[kind] = {
            "total": total,
            "correct": correct,
            "accuracy": (correct / total) if total else None,
            "skipped": int(counts["skipped"]),
        }
        totals["total"] += total
        totals["correct"] += correct
    return {
        "total": int(totals["total"]),
        "correct": int(totals["correct"]),
        "accuracy": (totals["correct"] / totals["total"]) if totals["total"] else None,
        "skipped": skipped,
        "by_type": types,
    }


def markdown_report(summary: dict[str, Any], *, title: str, records: list[dict[str, Any]]) -> str:
    lines = [
        f"# {title}",
        "",
        f"- Total scored: {summary.get('total', 0)}",
        f"- Correct: {summary.get('correct', 0)}",
        f"- Accuracy: {summary.get('accuracy')}",
        f"- Skipped (quota/connection): {summary.get('skipped', 0)}",
        "",
        "## By type",
        "",
    ]
    for kind, stats in (summary.get("by_type") or {}).items():
        lines.append(
            f"- `{kind}`: {stats['correct']}/{stats['total']} "
            f"(acc={stats['accuracy']}, skipped={stats['skipped']})"
        )
    failed = [r for r in records if not r.get("skipped") and not r.get("correct")]
    lines.extend(["", "## Failures", ""])
    if not failed:
        lines.append("None.")
    else:
        for rec in failed:
            lines.append(f"- `{rec.get('id')}` ({rec.get('type')}): {rec.get('reason')}")
    lines.append("")
    return "\n".join(lines)


def write_results_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_history(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "timestamp",
        "total",
        "correct",
        "accuracy",
        "skipped",
        "doc_accuracy",
        "sql_accuracy",
        "refusal_accuracy",
        "partial",
    ]
    new_file = not path.is_file()
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        writer.writerow(row)


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
