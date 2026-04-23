from collections.abc import Callable
from typing import Any

from chatdba.dingtalk.channel import DingTalkInboundMessage


MessageHandler = Callable[[DingTalkInboundMessage], Any]


class DingTalkStreamRuntime:
    def __init__(self, handler: MessageHandler) -> None:
        self._handler = handler

    def handle_test_message(self, message: DingTalkInboundMessage) -> Any:
        return self._handler(message)
