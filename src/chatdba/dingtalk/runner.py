from chatdba.dingtalk.runtime import build_dingtalk_runtime

try:
    from chatdba.config.settings import Settings
except ImportError as exc:
    Settings = None
    _SETTINGS_IMPORT_ERROR = exc
else:
    _SETTINGS_IMPORT_ERROR = None


def main() -> None:
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
    runtime.start()


if __name__ == "__main__":
    main()
