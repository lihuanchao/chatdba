# DingTalk Runtime And Sender Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a real DingTalk transport layer for ChatDBA that receives Stream-mode chatbot messages, maps them into the existing SQL optimization handler, and sends replies back through the inbound `sessionWebhook`.

**Architecture:** Keep the existing `DingTalkSqlOptimizationHandler` as the app core and add three transport-facing layers around it: a real `sessionWebhook` sender, an SDK adapter that maps callback payloads to `DingTalkInboundMessage`, and a runtime builder plus CLI entrypoint that starts the DingTalk stream client. To avoid pretending that database runtime wiring is complete, the default collector used by the CLI is an explicit fallback collector that raises a clear runtime error when SQL execution reaches evidence collection.

**Tech Stack:** Python 3.11+, standard library `urllib`, optional `dingtalk-stream` SDK import, Pydantic settings, pytest, existing ChatDBA DingTalk handler/task service code.

---

## File Structure

Create or modify these files:

```text
src/chatdba/dingtalk/sender.py
src/chatdba/dingtalk/sdk_runtime.py
src/chatdba/dingtalk/runtime.py
src/chatdba/dingtalk/runner.py
src/chatdba/dingtalk/__init__.py
pyproject.toml
README.md
tests/unit/test_dingtalk_sender.py
tests/unit/test_dingtalk_sdk_runtime.py
tests/unit/test_dingtalk_runtime_builder.py
tests/unit/test_dingtalk_runner.py
tests/integration/test_dingtalk_sdk_callback_flow.py
```

Responsibilities:

- `dingtalk.sender`: send DingTalk text replies to `sessionWebhook`.
- `dingtalk.sdk_runtime`: load the optional DingTalk SDK and map callback payloads into `DingTalkInboundMessage`.
- `dingtalk.runtime`: build the runtime object, register the SDK callback handler, and expose `start()`.
- `dingtalk.runner`: CLI entrypoint that validates settings and starts the runtime.
- `dingtalk.__init__`: export the new runtime-facing helpers.

## Task 1: Session Webhook Sender

**Files:**
- Create: `src/chatdba/dingtalk/sender.py`
- Test: `tests/unit/test_dingtalk_sender.py`

- [ ] **Step 1: Write the failing sender tests**

Create `tests/unit/test_dingtalk_sender.py`:

```python
import io
import json
import urllib.error

import pytest

from chatdba.dingtalk.sender import DingTalkSessionWebhookSender


class RecordingResponse:
    def __init__(self, body: bytes = b"{}"):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def close(self) -> None:
        pass


def test_sender_posts_text_payload_to_session_webhook():
    seen = {}

    def fake_opener(request):
        seen["url"] = request.full_url
        seen["method"] = request.get_method()
        seen["headers"] = dict(request.header_items())
        seen["body"] = request.data
        return RecordingResponse()

    sender = DingTalkSessionWebhookSender(opener=fake_opener)

    sender.send_text(
        conversation_id="conv-1",
        session_webhook="https://example.test/webhook",
        text="hello dingtalk",
    )

    assert seen["url"] == "https://example.test/webhook"
    assert seen["method"] == "POST"
    assert seen["headers"]["Content-type"] == "application/json"
    assert json.loads(seen["body"].decode("utf-8")) == {
        "msgtype": "text",
        "text": {"content": "hello dingtalk"},
    }


def test_sender_requires_session_webhook():
    sender = DingTalkSessionWebhookSender(opener=lambda request: RecordingResponse())

    with pytest.raises(RuntimeError, match="Missing session webhook"):
        sender.send_text(
            conversation_id="conv-1",
            session_webhook=None,
            text="hello dingtalk",
        )


def test_sender_surfaces_http_errors():
    def failing_opener(request):
        raise urllib.error.HTTPError(
            url=request.full_url,
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=io.BytesIO(b'{"errcode":123,"errmsg":"invalid"}'),
        )

    sender = DingTalkSessionWebhookSender(opener=failing_opener)

    with pytest.raises(RuntimeError, match="HTTP 400"):
        sender.send_text(
            conversation_id="conv-1",
            session_webhook="https://example.test/webhook",
            text="hello dingtalk",
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest tests/unit/test_dingtalk_sender.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'chatdba.dingtalk.sender'`.

- [ ] **Step 3: Implement the sender**

Create `src/chatdba/dingtalk/sender.py`:

```python
import json
import urllib.error
import urllib.request
from typing import Protocol


class RequestOpener(Protocol):
    def __call__(self, request: urllib.request.Request):
        pass


class DingTalkSessionWebhookSender:
    def __init__(self, opener: RequestOpener | None = None) -> None:
        self._opener = opener or urllib.request.urlopen

    def send_text(
        self,
        *,
        conversation_id: str,
        session_webhook: str | None,
        text: str,
    ) -> None:
        if not session_webhook:
            raise RuntimeError(
                f"Missing session webhook for conversation {conversation_id}"
            )

        payload = json.dumps(
            {
                "msgtype": "text",
                "text": {"content": text},
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            session_webhook,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            response = self._opener(request)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore").strip()
            detail = body[:200] if body else exc.reason
            raise RuntimeError(
                f"DingTalk webhook send failed with HTTP {exc.code}: {detail}"
            ) from exc

        close = getattr(response, "close", None)
        if callable(close):
            close()
```

- [ ] **Step 4: Run sender tests to verify they pass**

Run:

```bash
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest tests/unit/test_dingtalk_sender.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/chatdba/dingtalk/sender.py tests/unit/test_dingtalk_sender.py
git commit -m "feat: add dingtalk session webhook sender"
```

Expected: commit succeeds.

## Task 2: SDK Loader And Callback Adapter

**Files:**
- Create: `src/chatdba/dingtalk/sdk_runtime.py`
- Test: `tests/unit/test_dingtalk_sdk_runtime.py`

- [ ] **Step 1: Write the failing SDK runtime tests**

Create `tests/unit/test_dingtalk_sdk_runtime.py`:

```python
from types import SimpleNamespace

import pytest

from chatdba.dingtalk.sdk_runtime import (
    DingTalkSdkImportError,
    DingTalkStreamChatbotHandler,
    load_dingtalk_stream_sdk,
)
from chatdba.domain.models import TaskStatus


class RecordingHandler:
    def __init__(self):
        self.message = None

    def handle(self, message):
        self.message = message
        return SimpleNamespace(
            accepted=True,
            status=TaskStatus.COMPLETED,
            task_id="task-1",
        )


def test_sdk_handler_maps_callback_data_to_inbound_message():
    app_handler = RecordingHandler()
    adapter = DingTalkStreamChatbotHandler(handler=app_handler)

    result = adapter.handle_callback_data(
        {
            "msgId": "msg-1",
            "conversationId": "conv-1",
            "senderId": "user-1",
            "sessionWebhook": "https://example.test/webhook",
            "msgtype": "text",
            "text": {"content": "SQL优化 select * from orders"},
        }
    )

    assert result.task_id == "task-1"
    assert app_handler.message.message_id == "msg-1"
    assert app_handler.message.conversation_id == "conv-1"
    assert app_handler.message.sender_id == "user-1"
    assert app_handler.message.session_webhook == "https://example.test/webhook"
    assert app_handler.message.text == "SQL优化 select * from orders"


def test_sdk_handler_uses_empty_text_for_non_text_payload():
    app_handler = RecordingHandler()
    adapter = DingTalkStreamChatbotHandler(handler=app_handler)

    adapter.handle_callback_data(
        {
            "msgId": "msg-2",
            "conversationId": "conv-1",
            "senderId": "user-1",
            "sessionWebhook": "https://example.test/webhook",
            "msgtype": "picture",
            "content": {"downloadCode": "abc"},
        }
    )

    assert app_handler.message.text == ""


def test_load_dingtalk_stream_sdk_raises_clear_error():
    def fake_import_module(name: str):
        raise ImportError(name)

    with pytest.raises(DingTalkSdkImportError, match="dingtalk-stream is not installed"):
        load_dingtalk_stream_sdk(import_module=fake_import_module)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest tests/unit/test_dingtalk_sdk_runtime.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'chatdba.dingtalk.sdk_runtime'`.

- [ ] **Step 3: Implement the SDK loader and adapter**

Create `src/chatdba/dingtalk/sdk_runtime.py`:

```python
import importlib
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from chatdba.dingtalk.channel import DingTalkInboundMessage


class DingTalkSdkImportError(RuntimeError):
    pass


@dataclass(frozen=True)
class DingTalkSdkBundle:
    stream_module: Any
    chatbot_module: Any


class AppMessageHandler(Protocol):
    def handle(self, message: DingTalkInboundMessage):
        pass


def load_dingtalk_stream_sdk(
    *,
    import_module: Callable[[str], Any] = importlib.import_module,
) -> DingTalkSdkBundle:
    try:
        stream_module = import_module("dingtalk_stream")
        chatbot_module = import_module("dingtalk_stream.chatbot")
    except ImportError as exc:
        raise DingTalkSdkImportError(
            "dingtalk-stream is not installed. "
            'Run `pip install dingtalk-stream` before starting the DingTalk runtime.'
        ) from exc

    return DingTalkSdkBundle(
        stream_module=stream_module,
        chatbot_module=chatbot_module,
    )


class DingTalkStreamChatbotHandler:
    def __init__(self, *, handler: AppMessageHandler) -> None:
        self._handler = handler

    def handle_callback_data(self, callback_data: dict[str, Any]):
        text_payload = callback_data.get("text")
        text = ""
        if isinstance(text_payload, dict):
            text = str(text_payload.get("content", "")).strip()

        message = DingTalkInboundMessage(
            message_id=str(callback_data.get("msgId", "")),
            conversation_id=str(callback_data.get("conversationId", "")),
            sender_id=str(callback_data.get("senderId", "")),
            text=text,
            session_webhook=_as_optional_string(callback_data.get("sessionWebhook")),
        )
        return self._handler.handle(message)


def _as_optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    if text:
        return text
    return None
```

- [ ] **Step 4: Run SDK runtime tests to verify they pass**

Run:

```bash
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest tests/unit/test_dingtalk_sdk_runtime.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/chatdba/dingtalk/sdk_runtime.py tests/unit/test_dingtalk_sdk_runtime.py
git commit -m "feat: add dingtalk sdk callback adapter"
```

Expected: commit succeeds.

## Task 3: Runtime Builder And Fallback Collector

**Files:**
- Create: `src/chatdba/dingtalk/runtime.py`
- Modify: `src/chatdba/dingtalk/__init__.py`
- Test: `tests/unit/test_dingtalk_runtime_builder.py`

- [ ] **Step 1: Write the failing runtime builder tests**

Create `tests/unit/test_dingtalk_runtime_builder.py`:

```python
import asyncio
from types import SimpleNamespace

import pytest

from chatdba.dingtalk.runtime import (
    UnsupportedMysqlCollector,
    build_dingtalk_runtime,
)
from chatdba.dingtalk.sdk_runtime import DingTalkSdkBundle


class FakeCredential:
    def __init__(self, client_id, client_secret):
        self.client_id = client_id
        self.client_secret = client_secret


class FakeClient:
    def __init__(self, credential):
        self.credential = credential
        self.registrations = []
        self.started = False

    def register_callback_handler(self, topic, handler):
        self.registrations.append((topic, handler))

    def start_forever(self):
        self.started = True


class FakeAckMessage:
    STATUS_OK = "OK"


class FakeChatbotHandler:
    pass


class FakeChatbotMessage:
    TOPIC = "/v1.0/im/bot/messages/get"


class FakeSender:
    def __init__(self):
        self.messages = []

    def send_text(self, *, conversation_id, session_webhook, text):
        self.messages.append(
            {
                "conversation_id": conversation_id,
                "session_webhook": session_webhook,
                "text": text,
            }
        )


def make_sdk_bundle() -> DingTalkSdkBundle:
    stream_module = SimpleNamespace(
        Credential=FakeCredential,
        DingTalkStreamClient=FakeClient,
        AckMessage=FakeAckMessage,
        ChatbotHandler=FakeChatbotHandler,
    )
    chatbot_module = SimpleNamespace(ChatbotMessage=FakeChatbotMessage)
    return DingTalkSdkBundle(
        stream_module=stream_module,
        chatbot_module=chatbot_module,
    )


def make_settings():
    return SimpleNamespace(
        dingtalk_client_id="client-id",
        dingtalk_client_secret="client-secret",
        stream_update_interval_ms=1000,
    )


def test_build_runtime_registers_chatbot_handler_and_starts_client():
    class FakeCollector:
        def collect(self, sql, tables):
            return {"sql": sql, "tables": tables}

    runtime = build_dingtalk_runtime(
        settings=make_settings(),
        collector=FakeCollector(),
        sender=FakeSender(),
        sdk_bundle=make_sdk_bundle(),
    )

    assert runtime.client.credential.client_id == "client-id"
    assert runtime.client.credential.client_secret == "client-secret"
    assert runtime.client.registrations[0][0] == FakeChatbotMessage.TOPIC

    runtime.start()

    assert runtime.client.started is True


def test_build_runtime_uses_explicit_fallback_collector_when_none_is_provided():
    runtime = build_dingtalk_runtime(
        settings=make_settings(),
        sender=FakeSender(),
        sdk_bundle=make_sdk_bundle(),
    )

    with pytest.raises(RuntimeError, match="MySQL runtime collector is not configured"):
        runtime.collector.collect("select 1", [])


def test_registered_sdk_callback_handler_acks_and_delegates_to_app_handler():
    runtime = build_dingtalk_runtime(
        settings=make_settings(),
        collector=UnsupportedMysqlCollector(),
        sender=FakeSender(),
        sdk_bundle=make_sdk_bundle(),
    )
    callback_handler = runtime.client.registrations[0][1]
    callback = SimpleNamespace(
        data={
            "msgId": "msg-1",
            "conversationId": "conv-1",
            "senderId": "user-1",
            "sessionWebhook": "https://example.test/webhook",
            "msgtype": "text",
            "text": {"content": "SQL优化 select * from orders"},
        }
    )

    status, message = asyncio.run(callback_handler.process(callback))

    assert status == "OK"
    assert message == "OK"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest tests/unit/test_dingtalk_runtime_builder.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'chatdba.dingtalk.runtime'`.

- [ ] **Step 3: Implement the runtime builder**

Create `src/chatdba/dingtalk/runtime.py`:

```python
from dataclasses import dataclass
from typing import Any

from chatdba.dingtalk.handler import DingTalkSqlOptimizationHandler
from chatdba.dingtalk.responder import DingTalkResponder
from chatdba.dingtalk.sdk_runtime import (
    DingTalkSdkBundle,
    DingTalkStreamChatbotHandler,
    load_dingtalk_stream_sdk,
)
from chatdba.dingtalk.sender import DingTalkSessionWebhookSender
from chatdba.tasks.service import OptimizationTaskService


class UnsupportedMysqlCollector:
    def collect(self, sql: str, tables: list[object]):
        raise RuntimeError(
            "MySQL runtime collector is not configured for the DingTalk runtime yet."
        )


@dataclass(frozen=True)
class DingTalkSdkRuntime:
    client: Any
    callback_handler: Any
    app_handler: DingTalkSqlOptimizationHandler
    collector: object
    sender: object

    def start(self) -> None:
        self.client.start_forever()


def build_dingtalk_runtime(
    *,
    settings,
    collector: object | None = None,
    sender: object | None = None,
    sdk_bundle: DingTalkSdkBundle | None = None,
) -> DingTalkSdkRuntime:
    bundle = sdk_bundle or load_dingtalk_stream_sdk()
    runtime_collector = collector or UnsupportedMysqlCollector()
    runtime_sender = sender or DingTalkSessionWebhookSender()

    responder = DingTalkResponder(runtime_sender)
    task_service = OptimizationTaskService(collector=runtime_collector)
    app_handler = DingTalkSqlOptimizationHandler(
        task_service=task_service,
        responder=responder,
        stream_interval_ms=settings.stream_update_interval_ms,
    )
    adapter = DingTalkStreamChatbotHandler(handler=app_handler)
    callback_handler = create_sdk_callback_handler(
        bundle=bundle,
        adapter=adapter,
    )

    credential = bundle.stream_module.Credential(
        settings.dingtalk_client_id,
        settings.dingtalk_client_secret,
    )
    client = bundle.stream_module.DingTalkStreamClient(credential)
    client.register_callback_handler(
        bundle.chatbot_module.ChatbotMessage.TOPIC,
        callback_handler,
    )
    return DingTalkSdkRuntime(
        client=client,
        callback_handler=callback_handler,
        app_handler=app_handler,
        collector=runtime_collector,
        sender=runtime_sender,
    )


def create_sdk_callback_handler(
    *,
    bundle: DingTalkSdkBundle,
    adapter: DingTalkStreamChatbotHandler,
):
    class CallbackHandler(bundle.stream_module.ChatbotHandler):
        async def process(self, callback):
            adapter.handle_callback_data(callback.data)
            return bundle.stream_module.AckMessage.STATUS_OK, "OK"

    return CallbackHandler()
```

Modify `src/chatdba/dingtalk/__init__.py`:

```python
"""DingTalk helpers for chatdba."""

from chatdba.dingtalk.channel import (
    DingTalkInboundMessage,
    StreamUpdateBuffer,
    extract_sql_from_message,
)
from chatdba.dingtalk.runtime import (
    DingTalkSdkRuntime,
    UnsupportedMysqlCollector,
    build_dingtalk_runtime,
)
from chatdba.dingtalk.sender import DingTalkSessionWebhookSender
from chatdba.dingtalk.stream_runtime import DingTalkStreamRuntime

__all__ = [
    "DingTalkInboundMessage",
    "DingTalkSdkRuntime",
    "DingTalkSessionWebhookSender",
    "DingTalkStreamRuntime",
    "StreamUpdateBuffer",
    "UnsupportedMysqlCollector",
    "build_dingtalk_runtime",
    "extract_sql_from_message",
]
```

- [ ] **Step 4: Run runtime builder tests to verify they pass**

Run:

```bash
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest tests/unit/test_dingtalk_runtime_builder.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/chatdba/dingtalk/runtime.py src/chatdba/dingtalk/__init__.py tests/unit/test_dingtalk_runtime_builder.py
git commit -m "feat: build dingtalk sdk runtime"
```

Expected: commit succeeds.

## Task 4: Runner And CLI Entrypoint

**Files:**
- Create: `src/chatdba/dingtalk/runner.py`
- Modify: `pyproject.toml`
- Test: `tests/unit/test_dingtalk_runner.py`

- [ ] **Step 1: Write the failing runner tests**

Create `tests/unit/test_dingtalk_runner.py`:

```python
from types import SimpleNamespace

import pytest

from chatdba.dingtalk.runner import main


def test_main_builds_runtime_and_starts_it(monkeypatch):
    started = {"value": False}

    class FakeRuntime:
        def start(self):
            started["value"] = True

    monkeypatch.setattr(
        "chatdba.dingtalk.runner.Settings",
        lambda: SimpleNamespace(
            dingtalk_stream_enabled=True,
            dingtalk_client_id="client-id",
            dingtalk_client_secret="client-secret",
            stream_update_interval_ms=1000,
        ),
    )
    monkeypatch.setattr(
        "chatdba.dingtalk.runner.build_dingtalk_runtime",
        lambda *, settings: FakeRuntime(),
    )

    main()

    assert started["value"] is True


def test_main_exits_when_stream_mode_is_disabled(monkeypatch):
    monkeypatch.setattr(
        "chatdba.dingtalk.runner.Settings",
        lambda: SimpleNamespace(
            dingtalk_stream_enabled=False,
            dingtalk_client_id="client-id",
            dingtalk_client_secret="client-secret",
            stream_update_interval_ms=1000,
        ),
    )

    with pytest.raises(SystemExit, match="DINGTALK_STREAM_ENABLED must be true"):
        main()


def test_main_exits_when_credentials_are_missing(monkeypatch):
    monkeypatch.setattr(
        "chatdba.dingtalk.runner.Settings",
        lambda: SimpleNamespace(
            dingtalk_stream_enabled=True,
            dingtalk_client_id="",
            dingtalk_client_secret="",
            stream_update_interval_ms=1000,
        ),
    )

    with pytest.raises(SystemExit, match="DingTalk client credentials are required"):
        main()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest tests/unit/test_dingtalk_runner.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'chatdba.dingtalk.runner'`.

- [ ] **Step 3: Implement the runner and CLI script**

Create `src/chatdba/dingtalk/runner.py`:

```python
from chatdba.config.settings import Settings
from chatdba.dingtalk.runtime import build_dingtalk_runtime


def main() -> None:
    settings = Settings()
    if not settings.dingtalk_stream_enabled:
        raise SystemExit("DINGTALK_STREAM_ENABLED must be true to start DingTalk runtime.")
    if not settings.dingtalk_client_id or not settings.dingtalk_client_secret:
        raise SystemExit("DingTalk client credentials are required to start DingTalk runtime.")

    runtime = build_dingtalk_runtime(settings=settings)
    runtime.start()


if __name__ == "__main__":
    main()
```

Modify `pyproject.toml` by adding:

```toml
[project.scripts]
chatdba-dingtalk = "chatdba.dingtalk.runner:main"
```

- [ ] **Step 4: Run runner tests to verify they pass**

Run:

```bash
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest tests/unit/test_dingtalk_runner.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/chatdba/dingtalk/runner.py pyproject.toml tests/unit/test_dingtalk_runner.py
git commit -m "feat: add dingtalk runtime entrypoint"
```

Expected: commit succeeds.

## Task 5: Callback Flow Integration And Documentation

**Files:**
- Create: `tests/integration/test_dingtalk_sdk_callback_flow.py`
- Modify: `README.md`

- [ ] **Step 1: Write the integration test**

Create `tests/integration/test_dingtalk_sdk_callback_flow.py`:

```python
import asyncio
from types import SimpleNamespace

from chatdba.dingtalk.runtime import create_sdk_callback_handler
from chatdba.dingtalk.sdk_runtime import DingTalkSdkBundle, DingTalkStreamChatbotHandler
from chatdba.domain.models import TaskStatus


class FakeAckMessage:
    STATUS_OK = "OK"


class FakeChatbotHandler:
    pass


def make_sdk_bundle() -> DingTalkSdkBundle:
    stream_module = SimpleNamespace(
        AckMessage=FakeAckMessage,
        ChatbotHandler=FakeChatbotHandler,
    )
    chatbot_module = SimpleNamespace(ChatbotMessage=SimpleNamespace(TOPIC="topic"))
    return DingTalkSdkBundle(
        stream_module=stream_module,
        chatbot_module=chatbot_module,
    )


class RecordingAppHandler:
    def __init__(self):
        self.messages = []

    def handle(self, message):
        self.messages.append(message)
        return SimpleNamespace(
            accepted=True,
            status=TaskStatus.COMPLETED,
            task_id="task-1",
        )


def test_sdk_callback_handler_processes_message_and_returns_ack():
    app_handler = RecordingAppHandler()
    adapter = DingTalkStreamChatbotHandler(handler=app_handler)
    callback_handler = create_sdk_callback_handler(
        bundle=make_sdk_bundle(),
        adapter=adapter,
    )

    status, message = asyncio.run(
        callback_handler.process(
            SimpleNamespace(
                data={
                    "msgId": "msg-1",
                    "conversationId": "conv-1",
                    "senderId": "user-1",
                    "sessionWebhook": "https://example.test/webhook",
                    "msgtype": "text",
                    "text": {"content": "SQL优化 select * from orders"},
                }
            )
        )
    )

    assert status == "OK"
    assert message == "OK"
    assert app_handler.messages[0].conversation_id == "conv-1"
    assert app_handler.messages[0].text == "SQL优化 select * from orders"
```

- [ ] **Step 2: Run integration test**

Run:

```bash
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest tests/integration/test_dingtalk_sdk_callback_flow.py -v
```

Expected: PASS because Tasks 1-4 already provide the callback bridge.

- [ ] **Step 3: Update README for the real DingTalk runtime**

Append this section to `README.md`:

````markdown
## Real DingTalk Runtime

ChatDBA now includes a real DingTalk transport layer for Stream mode. The runtime
receives chatbot callback payloads from DingTalk, maps them into
`DingTalkInboundMessage`, and replies through the inbound `sessionWebhook`.

Start the runtime with:

```bash
chatdba-dingtalk
```

Equivalent module form:

```bash
python -m chatdba.dingtalk.runner
```

Required settings:

```text
DINGTALK_STREAM_ENABLED=true
DINGTALK_CLIENT_ID=replace-with-client-id
DINGTALK_CLIENT_SECRET=replace-with-client-secret
```

Current runtime boundary:

- DingTalk transport is real.
- SQL optimization still uses the current in-process collector wiring.
- Until a production MySQL collector is configured, SQL requests received from
  DingTalk will fail with a clear collector configuration message instead of
  silently pretending to optimize against a real database.
````

- [ ] **Step 4: Run integration and full test suite**

Run:

```bash
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest tests/integration/test_dingtalk_sdk_callback_flow.py -v
PYTHONPATH=src /tmp/chatdba-venv/bin/pytest -q
```

Expected: both commands PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add README.md tests/integration/test_dingtalk_sdk_callback_flow.py
git commit -m "test: cover dingtalk runtime callback flow"
```

Expected: commit succeeds.

## Task 6: Final Verification

**Files:**
- No code files

- [ ] **Step 1: Run local check script**

Run:

```bash
PYTHON_BIN=/tmp/chatdba-venv/bin/python ./scripts/run-local-checks.sh
```

Expected: all tests pass.

- [ ] **Step 2: Confirm git status**

Run:

```bash
git status --short --branch
```

Expected: clean feature branch with no uncommitted changes.

- [ ] **Step 3: Summarize commits**

Run:

```bash
git log --oneline main..HEAD
```

Expected: shows the design, plan, and runtime implementation commits.

## Self-Review Checklist

Spec coverage:

- Real `sessionWebhook` sending is covered by Task 1.
- Optional DingTalk SDK import is covered by Task 2.
- SDK payload mapping into `DingTalkInboundMessage` is covered by Task 2 and Task 5.
- Runtime builder and callback registration are covered by Task 3.
- CLI startup command is covered by Task 4.
- Documentation for startup and runtime boundary is covered by Task 5.

Type consistency:

- `DingTalkInboundMessage` is reused from `chatdba.dingtalk.channel`.
- `DingTalkSqlOptimizationHandler` is reused from `chatdba.dingtalk.handler`.
- `DingTalkResponder` is reused from `chatdba.dingtalk.responder`.
- `OptimizationTaskService` is reused from `chatdba.tasks.service`.
- `DingTalkSdkBundle` is defined in Task 2 and reused by Tasks 3 and 5.
- `UnsupportedMysqlCollector` is defined in Task 3 and used by Task 5 documentation wording.

Execution order:

- Task 1 must run before Task 3 because the runtime builder depends on the real sender.
- Task 2 must run before Task 3 because the runtime builder depends on the SDK adapter and loader.
- Task 3 must run before Task 4 because the runner starts the built runtime.
- Task 5 validates the callback flow after Tasks 1-4 exist.
