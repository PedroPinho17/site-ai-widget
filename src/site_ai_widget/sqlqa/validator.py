"""Validate LLM-generated SQL against the approved client schema.

Only a single read query may pass. Tables and columns must be in the approved
schema (sensitive columns are never present there). The returned SQL is the
sqlglot rewrite — never the raw LLM text — with comments stripped, identifiers
quoted, and a hard LIMIT.
"""
from __future__ import annotations

import logging
import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError, SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope
from sqlglot.schema import MappingSchema

# sqlglot warnings can include the SQL text; we never log user/LLM SQL.
logging.getLogger("sqlglot").setLevel(logging.CRITICAL)

# (schema_name, table_name) → approved column names (sensitive columns omitted).
type AllowedSchema = dict[tuple[str, str], frozenset[str]]

MAX_SQL_LENGTH = 10_000
DEFAULT_LIMIT = 100
MAX_LIMIT = 500
MAX_OFFSET = 10_000
MAX_DETAIL_LENGTH = 120

FORBIDDEN = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Command,
    exp.Into,
    exp.Lock,
    exp.Merge,
    exp.Alter,
    exp.TruncateTable,
)

# Typed sqlglot function nodes the LLM may use. Everything else is rejected.
# No array_agg / string_agg: they return a whole column in one row, past the LIMIT.
ALLOWED_FUNCTIONS: frozenset[type[exp.Expression]] = frozenset(
    {
        exp.Abs,
        exp.And,
        exp.Avg,
        exp.Case,
        exp.Cast,
        exp.Ceil,
        exp.Coalesce,
        exp.Concat,
        exp.Count,
        exp.CurrentDate,
        exp.CurrentTimestamp,
        exp.DateAdd,
        exp.DateDiff,
        exp.DenseRank,
        exp.Exists,
        exp.Extract,
        exp.Filter,
        exp.Floor,
        exp.Greatest,
        exp.If,
        exp.Lag,
        exp.Lead,
        exp.Least,
        exp.Left,
        exp.Length,
        exp.LogicalAnd,
        exp.LogicalOr,
        exp.Lower,
        exp.Max,
        exp.Min,
        exp.Not,
        exp.Nullif,
        exp.Or,
        exp.Rank,
        exp.Replace,
        exp.Right,
        exp.Round,
        exp.RowNumber,
        exp.StrToDate,
        exp.Substring,
        exp.Sum,
        exp.TimeToStr,
        exp.TimestampTrunc,
        exp.Trim,
        exp.Upper,
    }
)

# Functions sqlglot does not map to a typed node (name compared lowercased).
# left / right / nullif are typed nodes above when called unqualified.
ALLOWED_ANONYMOUS: frozenset[str] = frozenset(
    {
        "age",
        "concat_ws",
        "date",
        "date_part",
        "date_trunc",
        "position",
        "strpos",
        "to_char",
        "to_date",
        "to_timestamp",
    }
)

ALLOWED_CAST_TYPES: frozenset[str] = frozenset(
    {
        "bigint",
        "boolean",
        "bool",
        "bpchar",
        "char",
        "character",
        "date",
        "decimal",
        "double",
        "double precision",
        "float",
        "int",
        "integer",
        "interval",
        "json",
        "jsonb",
        "numeric",
        "real",
        "smallint",
        "text",
        "time",
        "timestamp",
        "timestamptz",
        "uuid",
        "varchar",
    }
)

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


class SQLRejected(Exception):
    """SQL rejected by the validator.

    ``reason`` is a short code for logs and tests. ``detail`` is a short hint for
    the LLM retry step: at most 120 characters, a single line, and never the SQL
    text itself (parse errors give only sqlglot's description, without the
    highlighted query). Do not log ``detail``; log ``reason`` only.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(reason)


def validate_sql(sql: str, allowed: AllowedSchema) -> str:
    """Parse, qualify against ``allowed``, and return safe executable SQL.

    Raises:
        SQLRejected: empty/too long, parse error, write ops, unknown table/column,
            blocked function/cast/join, whole-row select, etc. Never raises anything else.
    """
    try:
        return _validate_sql(sql, allowed)
    except SQLRejected:
        raise
    except Exception as exc:
        raise SQLRejected("validate_error", _safe_detail(str(exc))) from None


def _validate_sql(sql: str, allowed: AllowedSchema) -> str:
    text = sql.strip()
    if not text:
        raise SQLRejected("empty", "SQL is empty.")
    if len(text) > MAX_SQL_LENGTH:
        raise SQLRejected("too_long", "SQL is too long.")
    if not allowed:
        raise SQLRejected("no_approved_tables", "No approved tables are available.")

    query = _parse_single_select(text)

    _check_no_recursive_cte(query)
    _check_joins(query)
    _check_tables(query, allowed)
    _check_functions(query)
    _check_casts(query)

    query = _qualify_with_approved_schema(query, allowed)
    _check_columns(query, allowed)
    _check_field_access(query)
    _check_tables(query, allowed)
    _check_no_whole_rows(query)
    _check_offsets(query)
    _enforce_limit(query)

    return query.sql(dialect="postgres", comments=False)


def _parse_single_select(sql: str) -> exp.Query:
    try:
        statements = sqlglot.parse(sql, read="postgres")
    except ParseError as exc:
        raise SQLRejected("parse_error", _parse_error_detail(exc)) from None
    except SqlglotError:
        # Tokenizer errors quote the surrounding SQL text: give a generic hint only.
        raise SQLRejected("parse_error", "Could not read the SQL (check quotes and brackets).") from None

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise SQLRejected("not_single_select", "Only a single SELECT is allowed.")
    statement = statements[0]
    if isinstance(statement, FORBIDDEN) or any(statement.find_all(*FORBIDDEN)):
        raise SQLRejected("forbidden_op", "Write or locking operations are not allowed.")
    if not isinstance(statement, exp.Query):
        raise SQLRejected("not_single_select", "Only a SELECT / UNION / WITH query is allowed.")
    return statement


def _check_no_recursive_cte(query: exp.Expression) -> None:
    for with_node in query.find_all(exp.With):
        if with_node.args.get("recursive"):
            raise SQLRejected("recursive_cte", "WITH RECURSIVE is not allowed.")


def _check_joins(query: exp.Expression) -> None:
    """NATURAL joins match on every same-named real column, approved or not."""
    for join in query.find_all(exp.Join):
        kinds = {str(join.args.get(key) or "").upper() for key in ("method", "kind")}
        if "NATURAL" in kinds:
            raise SQLRejected("join_not_allowed", "NATURAL JOIN is not allowed; use JOIN ... ON.")


def _qualify_with_approved_schema(query: exp.Query, allowed: AllowedSchema) -> exp.Query:
    """Resolve columns against approved names only; expand ``*``; quote identifiers."""
    nested: dict[str, dict[str, dict[str, str]]] = {}
    for (schema_name, table_name), columns in allowed.items():
        # Sorted: `*` expands in this order, and frozenset order changes between
        # processes (hash randomization). Sorting keeps the output SQL stable.
        nested.setdefault(schema_name, {})[table_name] = {col: "text" for col in sorted(columns)}

    mapping = MappingSchema(nested, normalize=False, dialect="postgres")
    try:
        return qualify(
            query,
            schema=mapping,
            dialect="postgres",
            db="public",
            quote_identifiers=True,
            identify=True,
            expand_stars=True,
            validate_qualify_columns=True,
        )
    except OptimizeError as exc:
        raise SQLRejected("unknown_column", _safe_detail(str(exc))) from None
    except Exception as exc:  # fail closed on any unexpected qualify failure
        raise SQLRejected("qualify_error", _safe_detail(str(exc))) from None


def _check_columns(query: exp.Query, allowed: AllowedSchema) -> None:
    """Every column must be resolved to an approved source column.

    qualify() leaves a column it could not resolve unqualified (for example inside
    an aggregate in HAVING), and Postgres would then resolve it against the real
    table — including sensitive columns. So:

    1. An unqualified column is allowed only as a bare output-alias reference in its
       query's own ORDER BY (``ORDER BY total``). Inside an expression, a window or an
       aggregate, Postgres resolves the name against input columns: rejected.
    2. Defense in depth: a qualified column must exist in its source — an approved
       table's approved columns, or the output columns of a CTE / subquery. The
       qualifier is looked up in the column's scope and then its parent scopes
       (correlated subqueries and LATERAL refer to outer tables).
    """
    for column in query.find_all(exp.Column):
        if isinstance(column.this, exp.Star):
            continue
        if not column.table and not _is_order_by_alias(column):
            raise SQLRejected(
                "column_not_allowed",
                f"Column {column.name} must belong to an approved table.",
            )

    for scope in traverse_scope(query):
        for column in scope.columns:
            if not column.table or isinstance(column.this, exp.Star):
                continue
            source = _find_source(scope, column.table)
            if isinstance(source, exp.Table):
                approved = allowed.get((source.db, source.name), frozenset())
                ok = column.name in approved
            elif isinstance(source, Scope):
                ok = column.name in _output_columns(source.expression)
            else:
                ok = False
            if not ok:
                raise SQLRejected(
                    "column_not_allowed",
                    f"Column {column.name} is not an approved column.",
                )


def _check_field_access(query: exp.Query) -> None:
    """Reject Postgres field notation: ``x.f`` runs the one-argument function ``f(x)``.

    ``(price).pg_sleep`` or ``('server_version').current_setting`` would call any
    function in the client database, past the function allowlist. On the qualified
    tree every legit column is an ``exp.Column`` with at most ``table.column``, so:

    - an ``exp.Column`` with a schema or catalog part (``p.name.current_setting`` is
      table ``p``, column ``name``, then a function) → column_not_allowed;
    - any ``exp.Dot`` left (``(expr).name``) is field access → field_access_not_allowed.
    """
    for column in query.find_all(exp.Column):
        if column.args.get("db") or column.args.get("catalog"):
            raise SQLRejected(
                "column_not_allowed",
                "A column reference may be at most table.column.",
            )
    if any(query.find_all(exp.Dot)):
        raise SQLRejected(
            "field_access_not_allowed",
            "Field access like (x).name is not allowed; call an allowed function instead.",
        )


def _is_order_by_alias(column: exp.Column) -> bool:
    ordered = column.parent
    if not isinstance(ordered, exp.Ordered) or ordered.this is not column:
        return False
    order = ordered.parent
    if not isinstance(order, exp.Order):
        return False
    owner = order.parent
    return isinstance(owner, exp.Query) and column.name in owner.named_selects


def _output_columns(node: exp.Expression) -> set[str]:
    """Output column names of a CTE / subquery / LATERAL source.

    A LATERAL source's scope expression is the Lateral wrapper; its columns are the
    inner query's. An explicit column alias list (``AS x(a, b)``) wins.
    """
    while isinstance(node, (exp.Lateral, exp.Subquery)):
        alias = node.args.get("alias")
        if alias is not None and alias.columns:
            return {c.name for c in alias.columns}
        node = node.this
    if isinstance(node.parent, exp.Subquery):
        alias = node.parent.args.get("alias")
        if alias is not None and alias.columns:
            return {c.name for c in alias.columns}
    return set(node.named_selects) if isinstance(node, exp.Query) else set()


def _find_source(scope: Scope, name: str) -> exp.Table | Scope | None:
    current: Scope | None = scope
    while current is not None:
        if name in current.sources:
            return current.sources[name]
        current = current.parent
    return None


def _check_tables(query: exp.Query, allowed: AllowedSchema) -> None:
    cte_names = {_fold_ident(c.args.get("alias")) or c.alias_or_name for c in query.find_all(exp.CTE)}
    approved = set(allowed)

    for table in query.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            raise SQLRejected(
                "table_not_allowed",
                "Table functions and non-table sources are not allowed.",
            )
        if table.catalog:
            raise SQLRejected(
                "table_not_allowed",
                "Cross-database (catalog-qualified) table names are not allowed.",
            )
        name = _fold_ident(table.this) or table.name
        # A CTE reference has no schema and no catalog. Schema-qualified names
        # (e.g. public.customers) must always be in the approved set — even when
        # a CTE happens to share the same table name.
        if name in cte_names and not table.db and not table.catalog:
            continue
        db_ident = table.args.get("db")
        schema_name = _fold_ident(db_ident) if db_ident is not None else "public"
        if not schema_name:
            schema_name = "public"
        if (schema_name, name) not in approved:
            raise SQLRejected(
                "table_not_allowed",
                f"Table {schema_name}.{name} is not approved.",
            )


def _fold_ident(node: exp.Expression | None) -> str | None:
    """Postgres folds unquoted identifiers to lowercase; quoted ones keep their case."""
    if node is None:
        return None
    if isinstance(node, exp.TableAlias):
        node = node.this
    if isinstance(node, exp.Identifier):
        return node.name if node.quoted else node.name.lower()
    if isinstance(node, exp.Expression) and hasattr(node, "name"):
        return str(node.name).lower()
    return None


def _check_functions(query: exp.Expression) -> None:
    # OPERATOR(schema.op) is exp.Operator, not exp.Func — it would run any operator.
    if any(query.find_all(exp.Operator)):
        raise SQLRejected("operator_not_allowed", "OPERATOR(...) is not allowed.")
    # SIMILAR TO is regex-like (same class of risk as ~); not a Func node.
    if any(query.find_all(exp.SimilarTo)):
        raise SQLRejected("function_not_allowed", "SIMILAR TO is not allowed.")
    for func in query.find_all(exp.Func):
        # schema.func(...) parses as Dot(schema, func): it would run whatever function
        # of that name exists in that schema, so the name allowlist does not apply.
        if isinstance(func.parent, exp.Dot):
            raise SQLRejected("function_not_allowed", "Schema-qualified function calls are not allowed.")
        if isinstance(func, exp.Anonymous):
            name = str(func.name).lower()
            if name not in ALLOWED_ANONYMOUS:
                raise SQLRejected("function_not_allowed", f"Function {name} is not allowed.")
            continue
        if type(func) not in ALLOWED_FUNCTIONS:
            label = func.sql_name().lower() if hasattr(func, "sql_name") else type(func).__name__
            raise SQLRejected("function_not_allowed", f"Function {label} is not allowed.")


def _check_casts(query: exp.Expression) -> None:
    for cast in query.find_all(exp.Cast):
        to = cast.to
        if to is None:
            raise SQLRejected("cast_not_allowed", "Cast type is not allowed.")
        type_name = _cast_type_name(to)
        if type_name not in ALLOWED_CAST_TYPES:
            raise SQLRejected("cast_not_allowed", f"Cast to {type_name} is not allowed.")


def _cast_type_name(dtype: exp.DataType) -> str:
    raw = dtype.this
    if isinstance(raw, exp.DataType.Type):
        return raw.name.lower().replace("_", " ")
    if isinstance(raw, str):
        return raw.lower().replace("_", " ")
    return dtype.sql(dialect="postgres").lower()


def _check_no_whole_rows(query: exp.Expression) -> None:
    """Reject whole-row references and any ``*`` that qualify() did not expand.

    A table alias used as a value (``SELECT t``, ``row_to_json(t)``, ``t::text``,
    ``array_agg(t)``) carries every column of the row, approved or not. Projection
    stars were already expanded to approved columns by qualify(), so the only ``*``
    still allowed is the bare one in ``count(*)``; ``count(t.*)`` is rejected.
    """
    if any(query.find_all(exp.TableColumn)):
        raise SQLRejected(
            "whole_row",
            "Selecting a whole row is not allowed; name the approved columns.",
        )
    for star in query.find_all(exp.Star):
        if not isinstance(star.parent, exp.Count):
            raise SQLRejected(
                "star_not_allowed",
                "SELECT * is only allowed inside count(*).",
            )


def _integer_limit(expr: exp.Expression | None) -> int | None:
    """Return n only for integer literals made of digits (rejects 1.5, 1e3, …)."""
    if not isinstance(expr, exp.Literal) or not expr.is_number:
        return None
    raw = str(expr.this)
    if not raw.isdigit():
        return None
    return int(raw)


def _check_offsets(query: exp.Expression) -> None:
    """OFFSET only as a small digit-only literal: a huge OFFSET makes the server scan rows."""
    for offset in query.find_all(exp.Offset):
        n = _integer_limit(offset.expression)
        if n is None or n > MAX_OFFSET:
            raise SQLRejected(
                "offset_not_allowed",
                f"OFFSET must be a whole number up to {MAX_OFFSET}.",
            )


def _enforce_limit(query: exp.Query) -> None:
    """No LIMIT → 100; above 500 → 500; LIMIT ALL → 100; FETCH FIRST n → LIMIT n."""
    limit_node = query.args.get("limit")
    n = DEFAULT_LIMIT

    if isinstance(limit_node, exp.Fetch):
        parsed = _integer_limit(limit_node.args.get("count"))
        if parsed is not None:
            n = min(max(parsed, 0), MAX_LIMIT)
    elif isinstance(limit_node, exp.Limit):
        expr = limit_node.expression
        if isinstance(expr, exp.Var) and str(expr.this).upper() == "ALL":
            n = DEFAULT_LIMIT
        else:
            parsed = _integer_limit(expr)
            n = min(max(parsed, 0), MAX_LIMIT) if parsed is not None else DEFAULT_LIMIT

    query.set("limit", exp.Limit(expression=exp.Literal.number(n)))


def _parse_error_detail(exc: ParseError) -> str:
    """sqlglot's description of the first parse error, without the quoted SQL."""
    errors = getattr(exc, "errors", None) or []
    first = errors[0] if errors else {}
    description = str(first.get("description") or "Could not parse the SQL.")
    line, col = first.get("line"), first.get("col")
    where = f" (line {line}, col {col})" if line and col else ""
    return _safe_detail(description + where)


def _safe_detail(message: str) -> str:
    """One short line for the LLM retry: no ANSI/control chars, no quoted SQL.

    sqlglot puts the highlighted query after the first newline, so only the first
    line is kept, then cut to MAX_DETAIL_LENGTH characters.
    """
    cleaned = _ANSI.sub("", message)
    first_line = cleaned.split("\n", 1)[0]
    printable = "".join(ch for ch in first_line if ord(ch) >= 32 and ch != "\x7f")
    return printable[:MAX_DETAIL_LENGTH]
