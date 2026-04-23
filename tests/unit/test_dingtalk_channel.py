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


def test_stream_update_buffer_flushes_after_interval():
    buffer = StreamUpdateBuffer(interval_ms=1000)
    buffer.add("hello")
    buffer.add(" world")

    assert buffer.flush(force=True) == "hello world"
    assert buffer.flush(force=True) == ""
