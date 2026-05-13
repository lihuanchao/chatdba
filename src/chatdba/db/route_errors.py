AMBIGUOUS_TABLE_MARKER = "以下表名在元数据库中存在重复，请补充库名后重试："
MULTI_INSTANCE_ROUTE_MARKER = "SQL 涉及多个源实例，当前无法路由到单一源库执行证据采集。"


def is_route_resolution_blocker(message: str) -> bool:
    return (
        AMBIGUOUS_TABLE_MARKER in message
        or MULTI_INSTANCE_ROUTE_MARKER in message
    )
