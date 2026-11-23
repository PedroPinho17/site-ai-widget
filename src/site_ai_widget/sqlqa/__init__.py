"""Phase 4: safe text-to-SQL over the approved client schema."""

from site_ai_widget.sqlqa.runner import RunnerError, RunResult, run_sql

__all__ = ["RunResult", "RunnerError", "run_sql"]
