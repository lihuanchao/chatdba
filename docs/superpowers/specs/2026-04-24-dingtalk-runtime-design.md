# DingTalk Runtime And Sender Design

## Goal

Build the first real DingTalk runtime slice for ChatDBA: receive chatbot messages from DingTalk Stream mode, convert them into ChatDBA inbound messages, execute the existing SQL optimization handler, and reply back to the same DingTalk session through the inbound `sessionWebhook`.

This slice makes the DingTalk integration actually runnable while keeping the existing SQL optimization flow in process.

## Scope

In scope:

- Add a real DingTalk text sender that posts text replies to `sessionWebhook`.
- Add a DingTalk Stream SDK adapter that converts SDK chatbot events into `DingTalkInboundMessage`.
- Add a runnable entrypoint to start the DingTalk stream client.
- Keep the existing `DingTalkSqlOptimizationHandler` as the orchestration core.
- Keep SDK imports optional at runtime so tests and local environments without the SDK can still import the package.
- Document the startup command and environment variables needed for DingTalk runtime.

Out of scope:

- Production worker queue separation.
- Real MySQL connection bootstrap and metadata routing.
- Rich message cards, markdown, or interactive cards.
- DingTalk outbound retry scheduling beyond a single synchronous send attempt.
- Multi-tenant DingTalk application routing.

## Architecture

The existing in-process handler remains the core, and two new adapters are added around it.

```text
DingTalk Stream SDK
  -> DingTalkStreamChatbotHandler
  -> DingTalkInboundMessage
  -> DingTalkSqlOptimizationHandler
  -> DingTalkResponder
  -> DingTalkSessionWebhookSender
  -> DingTalk sessionWebhook
```

`DingTalkStreamChatbotHandler` is responsible only for SDK adaptation: extract the message payload, map it into ChatDBA's internal message type, and delegate to the existing handler. `DingTalkSessionWebhookSender` is responsible only for outbound text replies. The entrypoint wires dependencies and starts the long-running client.

## Components

### `DingTalkSessionWebhookSender`

`DingTalkSessionWebhookSender` sends text replies to the `sessionWebhook` carried by the inbound DingTalk message.

Responsibilities:

- Validate that `session_webhook` exists before sending.
- POST a JSON body using DingTalk's text message format:

```json
{
  "msgtype": "text",
  "text": {
    "content": "..."
  }
}
```

- Use standard-library HTTP to avoid adding a new dependency just for text replies.
- Raise a clear runtime error when DingTalk rejects the response or when no session webhook is available.

This sender is plugged into the existing `DingTalkResponder`, so handler code stays unchanged.

### `DingTalkStreamChatbotHandler`

`DingTalkStreamChatbotHandler` is a runtime adapter around the DingTalk Stream Python SDK.

Responsibilities:

- Accept the already-constructed `DingTalkSqlOptimizationHandler`.
- Receive SDK chatbot events.
- Map SDK fields into `DingTalkInboundMessage`:
  - `msgId` -> `message_id`
  - `conversationId` -> `conversation_id`
  - `senderId` -> `sender_id`
  - `sessionWebhook` -> `session_webhook`
  - `text.content` -> `text`
- Ignore non-text message content by converting missing text to an empty string.
- Call `handler.handle(...)`.
- Return the SDK success ack tuple or object expected by the DingTalk SDK.

The SDK-specific code must live in its own module so the rest of the codebase stays importable even when `dingtalk-stream` is not installed.

### `build_dingtalk_runtime()`

A small builder function assembles the runtime dependencies:

- `Settings`
- real DingTalk sender
- `DingTalkResponder`
- current in-process collector dependency
- `OptimizationTaskService`
- `DingTalkSqlOptimizationHandler`
- DingTalk SDK client and chatbot callback handler

The builder returns a runtime object that exposes `start()` for production and keeps tests able to inspect the wired components.

### CLI Entrypoint

Add a dedicated process entrypoint:

```text
chatdba-dingtalk
```

This command loads settings, validates that DingTalk Stream mode is enabled and credentials are present, builds the runtime, and starts the stream client.

The module should also support:

```bash
python -m chatdba.dingtalk.runner
```

## Runtime Constraints

### Optional SDK Import

The current test environment may not have `dingtalk-stream` installed even though it is declared in project dependencies. To keep the package importable:

- Do not import `dingtalk_stream` at module top level in common code.
- Import it inside the SDK-specific builder or adapter module.
- If import fails, raise a targeted runtime error that explains the missing dependency and the command that should install it.

This avoids breaking unit tests that do not need the real SDK.

### In-Process Execution

This slice continues to run SQL optimization in the DingTalk process itself. That is acceptable for the first real runtime because:

- the current workflow is synchronous,
- the handler interface is already stable,
- moving to an async queue later can keep the same handler boundary.

The design deliberately avoids introducing Redis worker complexity in the same change.

## Error Handling

- Missing `sessionWebhook`: sender raises a clear error; responder captures it in send results.
- Missing `dingtalk-stream` package: runtime builder raises a startup error with remediation guidance.
- Missing DingTalk credentials: runner exits with a clear configuration error.
- Non-text DingTalk message: handler still runs but sees empty text, which triggers the existing usage guidance reply.
- DingTalk HTTP error: sender raises a clear runtime error including status code and short response body.

No stack trace or secret should be included in the message sent back to the DingTalk chat.

## Testing Strategy

This feature should be fully covered without opening a real DingTalk connection.

Unit tests:

- `DingTalkSessionWebhookSender` posts the expected URL, headers, and JSON body.
- `DingTalkSessionWebhookSender` fails clearly when webhook is missing.
- SDK adapter converts a fake SDK chatbot message into `DingTalkInboundMessage`.
- Runner builds the correct objects and starts the SDK client.
- Startup fails cleanly when the SDK import is unavailable.

Integration tests:

- A fake SDK message passed through the adapter ends up calling the existing `DingTalkSqlOptimizationHandler`.
- Existing DingTalk E2E tests continue to pass.

## Deployment Notes

Required settings remain:

- `DINGTALK_CLIENT_ID`
- `DINGTALK_CLIENT_SECRET`
- `DINGTALK_STREAM_ENABLED`
- `STREAM_UPDATE_INTERVAL_MS`

Recommended startup:

```bash
pip install -e ".[dev]"
chatdba-dingtalk
```

For production, this process should run separately from the FastAPI API process. That separation is operational, not architectural: both processes can share the same codebase and settings module.

## Future Follow-Up

After this slice is complete, the next production-facing step is to replace the current in-process collector wiring with a real MySQL connection layer and database routing configuration. That work should be a separate spec because it changes the runtime data plane rather than the DingTalk transport layer.
