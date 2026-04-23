import sqlglot


class UnsafeSqlError(ValueError):
    pass


def validate_select_only(raw_sql: str) -> str:
    sql = raw_sql.strip()
    if not sql:
        raise UnsafeSqlError("SQL is empty")
    statements = sqlglot.parse(sql, read="mysql")
    if len(statements) != 1:
        raise UnsafeSqlError("Only one SQL statement is allowed")
    statement = statements[0]
    if statement.key != "select":
        raise UnsafeSqlError("Only SELECT SQL is allowed")
    return sql
