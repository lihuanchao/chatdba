from chatdba.dingtalk.channel import (
    DingTalkInboundMessage,
    StreamUpdateBuffer,
    extract_sql_from_message,
    extract_template_id_and_clean_text,
)


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


def test_stream_update_buffer_separates_progress_lines_with_markdown_paragraphs():
    buffer = StreamUpdateBuffer(interval_ms=0, clock_ms=lambda: 0)

    buffer.add("正在解析 SQL...\n")
    buffer.add("已生成诊断结论...\n")
    buffer.add("已生成优化报告...\n")

    assert buffer.flush(force=True) == (
        "正在解析 SQL...\n\n"
        "已生成诊断结论...\n\n"
        "已生成优化报告...\n"
    )


def test_extract_template_id_and_clean_text_from_control_line():
    template_id, cleaned = extract_template_id_and_clean_text(
        "模板ID: abc-template\nSQL优化\nselect * from orders;"
    )

    assert template_id == "abc-template"
    assert cleaned == "SQL优化\nselect * from orders;"


def test_extract_sql_ignores_template_control_line():
    message = DingTalkInboundMessage(
        message_id="msg-3",
        conversation_id="conv-1",
        sender_id="user-1",
        text="template_id=my-template\nSQL优化 select * from orders where id = 1",
        session_webhook="https://example.test/webhook",
    )

    assert extract_sql_from_message(message) == "select * from orders where id = 1"
