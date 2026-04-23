from chatdba.dingtalk.channel import DingTalkInboundMessage
from chatdba.dingtalk.stream_runtime import DingTalkStreamRuntime


def test_runtime_returns_handler_result_for_test_message():
    message = DingTalkInboundMessage(
        message_id="msg-1",
        conversation_id="conv-1",
        sender_id="user-1",
        text="SQL优化 select * from orders",
    )

    runtime = DingTalkStreamRuntime(
        handler=lambda inbound: {"message_id": inbound.message_id}
    )

    assert runtime.handle_test_message(message) == {"message_id": "msg-1"}
