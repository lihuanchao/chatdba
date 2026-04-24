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
