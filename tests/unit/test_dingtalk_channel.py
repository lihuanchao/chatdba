from chatdba.dingtalk.channel import DingTalkInboundMessage, extract_sql_from_message, StreamUpdateBuffer


def test_extract_sql_from_mentioned_message():
    message = DingTalkInboundMessage(
        message_id="msg-1",
        conversation_id="conv-1",
        sender_id="user-1",
        text="@ChatDBA optimize ```sql\nselect * from orders\n```",
        session_webhook="https://example.test/webhook",
    )

    assert extract_sql_from_message(message) == "select * from orders"


def test_extract_sql_preserves_mysql_variables():
    message = DingTalkInboundMessage(
        message_id="msg-2",
        conversation_id="conv-1",
        sender_id="user-1",
        text="@ChatDBA optimize select * from t where id = @user_id",
        session_webhook="https://example.test/webhook",
    )

    assert extract_sql_from_message(message) == "select * from t where id = @user_id"


def test_stream_update_buffer_respects_interval_before_forced_flush():
    clock_values = iter([0, 500, 1500])
    buffer = StreamUpdateBuffer(interval_ms=1000, clock_ms=lambda: next(clock_values))
    buffer.add("hello")
    buffer.add(" world")

    assert buffer.flush() == ""
    assert buffer.flush() == "hello world"
    assert buffer.flush(force=True) == ""
