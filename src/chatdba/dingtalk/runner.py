import logging

from chatdba.dingtalk.runtime import build_dingtalk_runtime

try:
    from chatdba.config.settings import Settings
except ImportError as exc:
    Settings = None
    _SETTINGS_IMPORT_ERROR = exc
else:
    _SETTINGS_IMPORT_ERROR = None

LOGGER = logging.getLogger(__name__)


def _ensure_logging() -> None:
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)-8s %(message)s [%(filename)s:%(lineno)d]",
    )


def main() -> None:
    _ensure_logging()
    if Settings is None:
        raise SystemExit(
            "ChatDBA settings dependencies are not installed. "
            "Install project dependencies before starting DingTalk runtime."
        ) from _SETTINGS_IMPORT_ERROR

    settings = Settings()
    if not settings.dingtalk_stream_enabled:
        raise SystemExit(
            "DINGTALK_STREAM_ENABLED must be true to start DingTalk runtime."
        )
    if not settings.dingtalk_client_id or not settings.dingtalk_client_secret:
        raise SystemExit(
            "DingTalk client credentials are required to start DingTalk runtime."
        )

    runtime = build_dingtalk_runtime(settings=settings)
    LOGGER.info("ChatDBA DingTalk runtime started.")
    runtime.start()


if __name__ == "__main__":
    main()
