"""SQL validator: only approved tables/columns, read-only, hard LIMIT."""
from __future__ import annotations

import pytest

from site_ai_widget.sqlqa.validator import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    MAX_SQL_LENGTH,
    SQLRejected,
    validate_sql,
)

# Fictional shop: products / stock / stores approved; customers not approved.
# cost_price is sensitive and never enters the approved column set.
ALLOWED = {
    ("public", "products"): frozenset({"id", "name", "price", "sku", "store_id"}),
    ("public", "stock"): frozenset({"product_id", "store_id", "qty"}),
    ("public", "stores"): frozenset({"id", "name", "city"}),
}

# Same shop plus an approved customers table whose nif / email are sensitive
# (so they are not in the approved column set).
WITH_CUSTOMERS = {
    **ALLOWED,
    ("public", "customers"): frozenset({"id", "name", "city"}),
}


def reason(sql: str, allowed=ALLOWED) -> str:
    with pytest.raises(SQLRejected) as info:
        validate_sql(sql, allowed)
    return info.value.reason


# ---------- accepts ----------


def test_simple_select_is_rewritten_and_quoted():
    out = validate_sql("SELECT name FROM products", ALLOWED)
    assert '"products"."name"' in out
    assert f"LIMIT {DEFAULT_LIMIT}" in out


def test_join_and_aggregate():
    out = validate_sql(
        "SELECT s.name, sum(p.price) FROM products p "
        "JOIN stores s ON s.id = p.store_id GROUP BY s.name",
        ALLOWED,
    )
    assert "SUM" in out.upper()
    assert '"stores"' in out or '"s"' in out


def test_cte_is_allowed():
    out = validate_sql(
        "WITH t AS (SELECT name FROM products) SELECT name FROM t",
        ALLOWED,
    )
    assert "WITH" in out.upper()
    assert '"products"."name"' in out


def test_count_star_is_allowed():
    out = validate_sql("SELECT count(*) FROM products", ALLOWED)
    assert "COUNT(*)" in out.upper()


def test_star_expands_to_approved_columns_only():
    out = validate_sql("SELECT * FROM products", ALLOWED)
    assert '"products"."name"' in out
    assert "cost_price" not in out
    assert "*" not in out.replace("COUNT(*)", "")


# ---------- writes / forbidden ----------


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM products",
        "UPDATE products SET price = 0",
        "INSERT INTO products (name) VALUES ('x')",
        "DROP TABLE products",
        "SELECT name INTO tmp FROM products",
        "SELECT name FROM products FOR UPDATE",
        "WITH d AS (DELETE FROM products RETURNING *) SELECT * FROM d",
    ],
)
def test_writes_are_rejected(sql):
    assert reason(sql) == "forbidden_op"


def test_multiple_statements_are_rejected():
    assert reason("SELECT 1; SELECT 2") == "not_single_select"


# ---------- tables ----------


def test_unapproved_table_is_rejected():
    assert reason("SELECT name FROM customers") == "table_not_allowed"


def test_pg_catalog_is_rejected():
    assert reason("SELECT * FROM pg_catalog.pg_roles") == "table_not_allowed"


def test_generate_series_is_rejected():
    assert reason("SELECT * FROM generate_series(1, 10)") == "table_not_allowed"


# ---------- columns / qualify ----------


def test_sensitive_column_is_rejected():
    assert reason("SELECT cost_price FROM products") == "unknown_column"


def test_unapproved_column_in_where_is_rejected():
    assert reason("SELECT name FROM products WHERE cost_price > 0") == "unknown_column"


def test_detail_names_the_bad_column_for_the_retry():
    with pytest.raises(SQLRejected) as info:
        validate_sql("SELECT cost_price FROM products", ALLOWED)
    assert "cost_price" in info.value.detail


def test_unquoted_names_are_case_insensitive():
    assert '"products"."name"' in validate_sql("SELECT Name FROM Products", ALLOWED)


# ---------- functions / casts ----------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT pg_sleep(1) FROM products",
        "SELECT set_config('a', 'b', true) FROM products",
        "SELECT version() FROM products",
        "SELECT dblink('x', 'select 1') FROM products",
        "SELECT row_to_json(p) FROM products p",
        "SELECT name FROM products WHERE name SIMILAR TO 'a%'",
    ],
)
def test_blocked_functions_are_rejected(sql):
    assert reason(sql) == "function_not_allowed"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT name FROM products WHERE name OPERATOR(evil.===) 'x'",
        "SELECT name FROM products WHERE name OPERATOR(pg_catalog.~) '(a+)+b'",
        "SELECT name FROM products WHERE name OPERATOR(pg_catalog.||) 'x' = 'ax'",
    ],
)
def test_operator_syntax_is_rejected(sql):
    assert reason(sql) == "operator_not_allowed"


def test_plain_operators_still_pass():
    assert validate_sql("SELECT name || 'x' FROM products", ALLOWED)
    assert validate_sql("SELECT price + 1 FROM products", ALLOWED)
    assert validate_sql("SELECT name FROM products WHERE name = 'x'", ALLOWED)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT name FROM products WHERE name <-> 'x' < 1",
        "SELECT name FROM products WHERE name % 'x'",
        "SELECT name FROM products WHERE price % 2 = 0",
        "SELECT name FROM products WHERE price # 1 > 0",
        "SELECT name FROM products WHERE price & 1 = 1",
        "SELECT name FROM products WHERE price ^ 2 > 1",
        'SELECT name FROM products WHERE name COLLATE "C"',
        "SELECT substring(name FROM '(a+)+b') FROM products",
        "SELECT substring(name FROM name) FROM products",
    ],
)
def test_disallowed_binary_ops_and_regex_substring_are_rejected(sql):
    expected = (
        "function_not_allowed" if "substring" in sql.lower() else "operator_not_allowed"
    )
    assert reason(sql) == expected


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT name || 'x' FROM products",
        "SELECT price + 1 - 2 * 3 / 4 FROM products",
        "SELECT name FROM products WHERE name = 'x'",
        "SELECT name FROM products WHERE name <> 'x'",
        "SELECT name FROM products WHERE price < 1",
        "SELECT name FROM products WHERE price <= 1",
        "SELECT name FROM products WHERE price > 1",
        "SELECT name FROM products WHERE price >= 1",
        "SELECT name FROM products WHERE name LIKE 'a%'",
        "SELECT name FROM products WHERE name ILIKE 'a%'",
        "SELECT name FROM products WHERE name NOT LIKE 'a%'",
        r"SELECT name FROM products WHERE name LIKE 'a\%' ESCAPE '\'",
        "SELECT name FROM products WHERE name IN ('a', 'b')",
        "SELECT name FROM products WHERE price BETWEEN 1 AND 10",
        "SELECT name FROM products WHERE name IS NULL",
        "SELECT name FROM products WHERE name IS NOT DISTINCT FROM 'x'",
        "SELECT name FROM products WHERE name = 'x' AND price > 1 OR price < 0",
        "SELECT name FROM products WHERE NOT name IS NULL",
        "SELECT name FROM products WHERE price > ALL (SELECT price FROM products)",
        "SELECT name FROM products WHERE price = ANY (SELECT price FROM products)",
        "SELECT CASE WHEN price > 0 THEN name ELSE sku END FROM products",
        "SELECT substring(name FROM 2 FOR 3) FROM products",
        "SELECT substring(name, 2, 3) FROM products",
    ],
)
def test_allowed_operators_and_safe_substring_still_pass(sql):
    assert validate_sql(sql, ALLOWED)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 'pg_roles'::regclass FROM products",
        "SELECT 1::oid FROM products",
    ],
)
def test_catalog_casts_are_rejected(sql):
    assert reason(sql) == "cast_not_allowed"


def test_normal_cast_is_allowed():
    out = validate_sql("SELECT cast(price AS numeric) FROM products", ALLOWED)
    assert out == (
        'SELECT CAST("products"."price" AS DECIMAL) AS "price" '
        'FROM "public"."products" AS "products" LIMIT 100'
    )


# ---------- whole row / star ----------


def test_whole_row_select_is_rejected():
    assert reason("SELECT p FROM products p") == "whole_row"


# ---------- limits ----------


def test_missing_limit_becomes_default():
    out = validate_sql("SELECT name FROM products", ALLOWED)
    assert f"LIMIT {DEFAULT_LIMIT}" in out


def test_limit_above_max_is_clamped():
    out = validate_sql(f"SELECT name FROM products LIMIT {MAX_LIMIT + 100}", ALLOWED)
    assert f"LIMIT {MAX_LIMIT}" in out


def test_limit_all_becomes_default():
    out = validate_sql("SELECT name FROM products LIMIT ALL", ALLOWED)
    assert f"LIMIT {DEFAULT_LIMIT}" in out


def test_fetch_first_becomes_limit():
    out = validate_sql("SELECT name FROM products FETCH FIRST 10 ROWS ONLY", ALLOWED)
    assert "LIMIT 10" in out
    assert "FETCH" not in out.upper()


# ---------- input limits ----------


def test_empty_sql_is_rejected():
    assert reason("   ") == "empty"


def test_too_long_sql_is_rejected():
    assert reason("SELECT " + "1 + " * MAX_SQL_LENGTH + "1") == "too_long"


def test_garbage_is_rejected():
    with pytest.raises(SQLRejected) as info:
        validate_sql("SELEC name FRM products", ALLOWED)
    assert info.value.reason == "parse_error"
    assert "\x1b" not in info.value.detail


def test_no_approved_tables_rejects_everything():
    with pytest.raises(SQLRejected) as info:
        validate_sql("SELECT 1", {})
    assert info.value.reason == "no_approved_tables"


def test_comments_are_dropped():
    out = validate_sql("SELECT name FROM products -- ; DROP TABLE products", ALLOWED)
    assert "DROP" not in out


# ---------- security / crash hardening ----------


def test_schema_qualified_table_is_not_confused_with_cte_name():
    """A CTE named customers must not unlock public.customers."""
    sql = "WITH customers AS (SELECT 1 AS x) SELECT count(*) FROM public.customers"
    assert reason(sql) == "table_not_allowed"


def test_catalog_qualified_table_is_rejected():
    assert reason("SELECT name FROM otherdb.public.products") == "table_not_allowed"


def test_unterminated_string_is_parse_error():
    assert reason("SELECT 'abc") == "parse_error"


def test_limit_float_falls_back_to_default():
    out = validate_sql("SELECT name FROM products LIMIT 1.5", ALLOWED)
    assert f"LIMIT {DEFAULT_LIMIT}" in out


def test_limit_scientific_falls_back_to_default():
    out = validate_sql("SELECT name FROM products LIMIT 1e3", ALLOWED)
    assert f"LIMIT {DEFAULT_LIMIT}" in out


def test_recursive_cte_is_rejected():
    sql = (
        "WITH RECURSIVE t AS (SELECT id, name FROM products "
        "UNION ALL SELECT id, name FROM products) SELECT name FROM t"
    )
    assert reason(sql) == "recursive_cte"


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("''", "not_single_select"),  # a bare string literal is not a query
        ("'", "parse_error"),
        ("(", "parse_error"),
        (")", "parse_error"),
        ("()", "not_single_select"),
        ("((((", "parse_error"),
        (";;;;", "not_single_select"),
        (";;", "not_single_select"),
        ("$$", "parse_error"),
        ("$tag$hello$tag$", "not_single_select"),
        ("SELECT (", "parse_error"),
        ("SELECT )", "parse_error"),
        ("SELECT ((((SELECT 1", "parse_error"),
        ("SELECT name FROM", "parse_error"),
        ("SELECT 😀 FROM products", "unknown_column"),
        ("/*", "parse_error"),
        ("SELECT /* unterminated", "parse_error"),
    ],
)
def test_odd_inputs_are_rejected_with_the_expected_reason(sql, expected):
    assert reason(sql) == expected


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        # An empty projection is valid in Postgres (one row, no columns): harmless.
        ("SELECT", "SELECT LIMIT 100"),
        # The NUL byte is kept as-is; Postgres itself rejects NUL in text.
        (
            "SELECT name FROM products WHERE name = '\x00'",
            'SELECT "products"."name" AS "name" FROM "public"."products" AS "products" '
            "WHERE \"products\".\"name\" = '\x00' LIMIT 100",
        ),
        (
            "SELECT name FROM products WHERE name = U&'\\+01F600'",
            'SELECT "products"."name" AS "name" FROM "public"."products" AS "products" '
            "WHERE \"products\".\"name\" = U&'\\+01F600' LIMIT 100",
        ),
    ],
)
def test_odd_inputs_that_are_accepted(sql, expected):
    assert validate_sql(sql, ALLOWED) == expected


# ---------- H1: every column must resolve to an approved source ----------


@pytest.mark.parametrize(
    "sql",
    [
        # Per-row oracle on a sensitive column through an aggregate in HAVING.
        "SELECT name FROM customers WHERE id IN "
        "(SELECT id FROM customers GROUP BY id HAVING max(nif) LIKE '0%')",
        "SELECT city FROM customers GROUP BY city HAVING min(email) LIKE 'a%'",
        "SELECT city FROM customers GROUP BY city HAVING count(nif) > 0",
        "SELECT city FROM customers GROUP BY city HAVING count(nonexistent) > 0",
    ],
)
def test_unresolved_columns_in_having_are_rejected(sql):
    assert reason(sql, WITH_CUSTOMERS) == "column_not_allowed"


def test_approved_column_in_having_is_allowed():
    out = validate_sql(
        "SELECT city FROM customers GROUP BY city HAVING max(name) > 'a'", WITH_CUSTOMERS
    )
    assert 'HAVING MAX("customers"."name") > \'a\'' in out


def test_order_by_alias_inside_an_expression_is_rejected():
    """Postgres resolves names inside ORDER BY expressions against input columns."""
    assert reason("SELECT name AS n FROM products ORDER BY length(n)") == "column_not_allowed"


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        (
            "SELECT name AS n FROM products ORDER BY n",
            'SELECT "products"."name" AS "n" FROM "public"."products" AS "products" '
            'ORDER BY "n" LIMIT 100',
        ),
        (
            "SELECT name FROM products ORDER BY 1",
            'SELECT "products"."name" AS "name" FROM "public"."products" AS "products" '
            'ORDER BY "name" LIMIT 100',
        ),
        (
            "SELECT name, price FROM products ORDER BY 2",
            'SELECT "products"."name" AS "name", "products"."price" AS "price" '
            'FROM "public"."products" AS "products" ORDER BY "price" LIMIT 100',
        ),
        (
            "SELECT name FROM products UNION SELECT name FROM stores ORDER BY name",
            'SELECT "products"."name" AS "name" FROM "public"."products" AS "products" '
            'UNION SELECT "stores"."name" AS "name" FROM "public"."stores" AS "stores" '
            'ORDER BY "name" LIMIT 100',
        ),
    ],
)
def test_order_by_output_alias_is_allowed(sql, expected):
    assert validate_sql(sql, ALLOWED) == expected


def test_correlated_subquery_and_lateral_still_work():
    """The source check looks the qualifier up in outer scopes too."""
    out = validate_sql(
        "SELECT p.name FROM products p "
        "WHERE EXISTS (SELECT 1 FROM stock s WHERE s.product_id = p.id)",
        ALLOWED,
    )
    assert '"s"."product_id" = "p"."id"' in out
    out = validate_sql(
        "SELECT p.name, x.c FROM products p, "
        "LATERAL (SELECT count(*) AS c FROM stock s WHERE s.product_id = p.id) x",
        ALLOWED,
    )
    assert '"x"."c" AS "c"' in out


# ---------- M1: schema-qualified function calls ----------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT evil.left(name, 2) FROM products",
        "SELECT pg_catalog.left(name, 2) FROM products",
        "SELECT evil.age(current_date)",
    ],
)
def test_schema_qualified_functions_are_rejected(sql):
    assert reason(sql) == "function_not_allowed"


def test_unqualified_left_still_works():
    out = validate_sql("SELECT left(name, 2) FROM products", ALLOWED)
    assert 'LEFT("products"."name", 2)' in out


# ---------- joins ----------


def test_natural_join_is_rejected():
    assert reason("SELECT p.name FROM products p NATURAL JOIN stock s") == "join_not_allowed"


def test_using_with_an_unapproved_column_is_rejected():
    sql = "SELECT p.name FROM products p JOIN stock s USING (cost_price)"
    assert reason(sql) == "unknown_column"


def test_union_with_an_unapproved_table_is_rejected():
    sql = "SELECT name FROM products UNION SELECT name FROM customers"
    assert reason(sql) == "table_not_allowed"


# ---------- L2: row-count bypass and OFFSET ----------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT array_agg(name) FROM products",
        "SELECT string_agg(name, ',') FROM products",
    ],
)
def test_whole_column_aggregates_are_rejected(sql):
    assert reason(sql) == "function_not_allowed"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT name FROM products OFFSET 999999999999",
        "SELECT name FROM products OFFSET 10001",
        "SELECT name FROM products OFFSET 1.5",
        "SELECT name FROM (SELECT name FROM products OFFSET 999999) s",
    ],
)
def test_large_or_odd_offset_is_rejected(sql):
    assert reason(sql) == "offset_not_allowed"


def test_small_offset_is_allowed():
    out = validate_sql("SELECT name FROM products OFFSET 20", ALLOWED)
    assert out == (
        'SELECT "products"."name" AS "name" FROM "public"."products" AS "products" '
        "LIMIT 100 OFFSET 20"
    )


# ---------- pinned rewrites ----------


def test_table_statement_expands_to_approved_columns_only():
    assert validate_sql("TABLE products", ALLOWED) == (
        'SELECT "products"."id" AS "id", "products"."name" AS "name", '
        '"products"."price" AS "price", "products"."sku" AS "sku", '
        '"products"."store_id" AS "store_id" '
        'FROM "public"."products" AS "products" LIMIT 100'
    )


def test_count_of_alias_star_is_rejected():
    """count(t.*) is not the bare count(*): it is a whole-row reference."""
    with pytest.raises(SQLRejected) as info:
        validate_sql("SELECT count(s.*) FROM stores s", ALLOWED)
    assert info.value.reason == "star_not_allowed"
    assert info.value.detail == "SELECT * is only allowed inside count(*)."


# ---------- L3: detail never carries the SQL ----------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT name FROM products WHERE",
        "SELECT name FROM products WHERE name = 'secret-literal-123",
        "SELEC name FRM products WHERE sku = 'secret-literal-123'",
    ],
)
def test_parse_error_detail_never_contains_the_sql(sql):
    with pytest.raises(SQLRejected) as info:
        validate_sql(sql, ALLOWED)
    detail = info.value.detail
    assert info.value.reason == "parse_error"
    assert sql not in detail
    assert "secret-literal-123" not in detail
    assert "\n" not in detail
    assert len(detail) <= 120


# ---------- idempotence ----------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT name FROM products",
        "SELECT * FROM products",
        "SELECT s.name, sum(p.price) FROM products p JOIN stores s ON s.id = p.store_id GROUP BY s.name",
        "WITH t AS (SELECT name FROM products) SELECT name FROM t ORDER BY name",
        "SELECT name FROM products UNION SELECT name FROM stores ORDER BY 1",
        "SELECT name FROM products OFFSET 20 LIMIT 5",
    ],
)
def test_validating_the_output_again_changes_nothing(sql):
    once = validate_sql(sql, ALLOWED)
    assert validate_sql(once, ALLOWED) == once


# ---------- H1: Postgres field notation runs functions (x.f == f(x)) ----------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT (price).pg_sleep FROM products",
        "SELECT ('server_version').current_setting FROM products",
        "SELECT (name).pg_read_file FROM products",
        "SELECT (name).set_config FROM products",
        "SELECT name FROM products ORDER BY (name).upper",
        "SELECT name FROM products WHERE (price).pg_sleep IS NULL",
        'SELECT (name)."pg_sleep" FROM products',
        "SELECT ((name)).upper FROM products",
        "SELECT (name || 'x').upper FROM products",
        "SELECT (lower(name)).upper FROM products",
        "SELECT (SELECT name FROM products LIMIT 1).upper",
        # table.column.function: qualify() turns these into field access too
        "SELECT p.name.current_setting FROM products p",
        "SELECT products.name.upper FROM products",
    ],
)
def test_field_notation_is_rejected(sql):
    assert reason(sql) == "field_access_not_allowed"


def test_column_with_more_than_two_parts_is_rejected():
    """Defense in depth: a Column with a schema/catalog part never reaches the output.

    On the raw parse, p.name.current_setting is one Column (db=p, table=name).
    """
    import sqlglot

    from site_ai_widget.sqlqa.validator import _check_field_access

    tree = sqlglot.parse_one("SELECT p.name.current_setting FROM products p", read="postgres")
    with pytest.raises(SQLRejected) as info:
        _check_field_access(tree)
    assert info.value.reason == "column_not_allowed"


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        (
            "SELECT p.name, s.qty FROM products p JOIN stock s ON s.product_id = p.id",
            'SELECT "p"."name" AS "name", "s"."qty" AS "qty" FROM "public"."products" AS "p" '
            'JOIN "public"."stock" AS "s" ON "s"."product_id" = "p"."id" LIMIT 100',
        ),
        (
            "SELECT count(*) FROM products",
            'SELECT COUNT(*) AS "_col_0" FROM "public"."products" AS "products" LIMIT 100',
        ),
        (
            "SELECT upper(name) AS n FROM products ORDER BY n",
            'SELECT UPPER("products"."name") AS "n" FROM "public"."products" AS "products" '
            'ORDER BY "n" LIMIT 100',
        ),
    ],
)
def test_normal_dotted_columns_still_pass(sql, expected):
    """table.column references are Columns, not field access."""
    assert validate_sql(sql, ALLOWED) == expected


# ---------- unary (prefix) operators ----------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT ~price FROM products",
        "SELECT name FROM products WHERE ~price < 0",
    ],
)
def test_unary_operators_outside_the_allowlist_are_rejected(sql):
    assert reason(sql) == "operator_not_allowed"


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        (
            "SELECT -price FROM products",
            'SELECT -"products"."price" AS "_col_0" FROM "public"."products" AS "products" LIMIT 100',
        ),
        (
            "SELECT name FROM products WHERE NOT (price > 1)",
            'SELECT "products"."name" AS "name" FROM "public"."products" AS "products" '
            'WHERE NOT ("products"."price" > 1) LIMIT 100',
        ),
        (
            # sqlglot drops a unary plus while parsing: nothing reaches Postgres.
            "SELECT +price FROM products",
            'SELECT "products"."price" AS "price" FROM "public"."products" AS "products" LIMIT 100',
        ),
    ],
)
def test_allowed_unary_operators_still_pass(sql, expected):
    assert validate_sql(sql, ALLOWED) == expected


# ---------- position / strpos (StrPosition) ----------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT position('a' IN name) FROM products",
        "SELECT strpos(name, 'a') FROM products",
    ],
)
def test_position_and_strpos_are_allowed(sql):
    assert validate_sql(sql, ALLOWED) == (
        'SELECT POSITION(\'a\' IN "products"."name") AS "_col_0" '
        'FROM "public"."products" AS "products" LIMIT 100'
    )


def test_schema_qualified_strpos_is_still_rejected():
    assert reason("SELECT evil.strpos(name, 'a') FROM products") == "function_not_allowed"
