import re

import sqlglot
from sqlglot import expressions as exp


SCHEMA_REPLY_PATTERN = re.compile(
    r"^\s*(?:数据库|库名|schema|database)?\s*[:：]?\s*`?([a-zA-Z0-9_$-]+)`?\s*$",
    re.IGNORECASE,
)
SCHEMA_PREFIXED_SQL_PATTERN = re.compile(
    r"^\s*`?(?P<schema>[a-zA-Z0-9_$-]+)`?\s+(?P<sql>select\b.*)$",
    re.IGNORECASE | re.DOTALL,
)


def extract_schema_name_reply(text: str) -> str | None:
    match = SCHEMA_REPLY_PATTERN.match(text.strip())
    if not match:
        return None
    return match.group(1)


def split_schema_prefixed_sql(text: str) -> tuple[str | None, str]:
    stripped = text.strip()
    match = SCHEMA_PREFIXED_SQL_PATTERN.match(stripped)
    if not match:
        return None, stripped
    return match.group("schema"), match.group("sql").strip()


def qualify_unqualified_tables(
    raw_sql: str,
    *,
    schema_name: str,
    table_names: list[str],
) -> str:
    target_names = {name.lower() for name in table_names}
    expression = sqlglot.parse_one(raw_sql, read="mysql")
    for table in expression.find_all(exp.Table):
        if table.db:
            continue
        if table.name.lower() not in target_names:
            continue
        table.set("db", exp.to_identifier(schema_name))
    return expression.sql(dialect="mysql")


def unqualified_table_names(raw_sql: str) -> list[str]:
    expression = sqlglot.parse_one(raw_sql, read="mysql")
    names: list[str] = []
    for table in expression.find_all(exp.Table):
        if table.db or table.name in names:
            continue
        names.append(table.name)
    return names
