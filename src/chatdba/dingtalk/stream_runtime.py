from collections.abc import Callable

from chatdba.dingtalk.channel import DingTalkInboundMessage


MessageHandler = Callable[[DingTalkInboundMessage], None]


class DingTalkStreamRuntime:
    def __init__(self, handler: MessageHandler) -> None:
        self._handler = handler

    def handle_test_message(self, message: DingTalkInboundMessage) -> None:
        self._handler(message)
