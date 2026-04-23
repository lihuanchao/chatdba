import pytest

from chatdba.sql.safety import UnsafeSqlError, validate_select_only


def test_validate_select_only_accepts_single_select():
    assert validate_select_only("select * from orders where id = 1") == "select * from orders where id = 1"


@pytest.mark.parametrize(
    "sql",
    [
        "update orders set status = 1",
        "delete from orders",
        "select * from orders; drop table orders",
        "create index idx_orders_id on orders(id)",
    ],
)
def test_validate_select_only_rejects_unsafe_sql(sql):
    with pytest.raises(UnsafeSqlError):
        validate_select_only(sql)
