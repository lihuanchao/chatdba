import hashlib

import sqlglot
from sqlglot import expressions as exp

from chatdba.domain.models import SqlFeatures, TableReference
from chatdba.sql.safety import validate_select_only


def _fingerprint(sql: str) -> str:
    normalized = " ".join(sql.lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def parse_sql_features(raw_sql: str) -> SqlFeatures:
    sql = validate_select_only(raw_sql)
    expression = sqlglot.parse_one(sql, read="mysql")
    tables: list[TableReference] = []
    for table in expression.find_all(exp.Table):
        tables.append(
            TableReference(
                schema_name=table.db or None,
                table_name=table.name,
                alias=table.alias_or_name if table.alias else None,
            )
        )
    order_by = [
        ordered.sql(dialect="mysql")
        for ordered in (expression.args.get("order") or exp.Order()).expressions
    ]
    group_by = [
        grouped.sql(dialect="mysql")
        for grouped in (expression.args.get("group") or exp.Group()).expressions
    ]
    predicates = [
        where.this.sql(dialect="mysql")
        for where in expression.find_all(exp.Where)
    ]
    joins = [join.sql(dialect="mysql") for join in expression.find_all(exp.Join)]
    return SqlFeatures(
        fingerprint=_fingerprint(expression.sql(dialect="mysql")),
        statement_type=expression.key,
        tables=tables,
        predicates=predicates,
        joins=joins,
        order_by=order_by,
        group_by=group_by,
        has_limit=expression.args.get("limit") is not None,
    )
