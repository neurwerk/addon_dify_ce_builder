import sys
from contextlib import contextmanager, nullcontext
from types import ModuleType, SimpleNamespace

import pytest

from overlay.scripts import setup_model_provider


def test_require_single_tenant_returns_the_only_tenant():
    tenant = SimpleNamespace(id="tenant-1", name="shared")

    assert setup_model_provider._require_single_tenant([tenant]) is tenant


@pytest.mark.parametrize("tenants", [[], [object(), object()]])
def test_require_single_tenant_rejects_non_single_workspace_state(tenants):
    message = rf"exactly one tenant.*found {len(tenants)}"
    with pytest.raises(RuntimeError, match=message):
        setup_model_provider._require_single_tenant(tenants)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_require_llm_proxy_api_key_rejects_missing_or_blank_values(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("LLM_PROXY_API_KEY", raising=False)
    else:
        monkeypatch.setenv("LLM_PROXY_API_KEY", value)

    with pytest.raises(RuntimeError, match="LLM_PROXY_API_KEY is required"):
        setup_model_provider._require_llm_proxy_api_key()


def test_require_llm_proxy_api_key_returns_trimmed_value(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_API_KEY", "  gateway-key  ")

    assert setup_model_provider._require_llm_proxy_api_key() == "gateway-key"


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("MODEL_PROVIDER_CREDENTIALS", "not-json"),
        ("MODEL_PROVIDER_CREDENTIALS", "[]"),
        ("MODEL_SETTINGS", "not-json"),
        ("MODEL_SETTINGS", "[]"),
    ],
)
def test_configuration_loaders_reject_malformed_objects(monkeypatch, variable, value):
    monkeypatch.setenv(variable, value)

    loader = (
        setup_model_provider._load_credentials_config
        if variable == "MODEL_PROVIDER_CREDENTIALS"
        else setup_model_provider._load_settings_config
    )
    with pytest.raises(ValueError):
        loader()


def _configure_successful_run(monkeypatch):
    app = SimpleNamespace(app_context=nullcontext)
    app_factory = ModuleType("app_factory")
    app_factory.create_flask_app_with_configs = lambda: app
    app_factory.initialize_extensions = lambda _: None
    app_factory._auto_setup = lambda _: None
    monkeypatch.setitem(sys.modules, "app_factory", app_factory)
    monkeypatch.setenv("LLM_PROXY_API_KEY", "gateway-key")
    monkeypatch.setattr(
        setup_model_provider,
        "_resolve_tenant",
        lambda: SimpleNamespace(id="tenant-1"),
    )
    monkeypatch.setattr(setup_model_provider, "_ensure_tenant_rsa", lambda _: None)
    monkeypatch.setattr(setup_model_provider, "_wait_for_daemon", lambda _: True)
    monkeypatch.setattr(setup_model_provider, "_ensure_plugins", lambda _: None)
    monkeypatch.setattr(setup_model_provider, "_load_settings_config", lambda: {})
    monkeypatch.setattr(setup_model_provider, "_load_credentials_config", lambda: {})
    monkeypatch.setattr(setup_model_provider, "_bootstrap_lock", nullcontext)
    return app_factory


def _configure_lock_database(monkeypatch, acquire_results):
    events = []

    class Connection:
        def __init__(self):
            self.acquire_results = iter(acquire_results)
            self.locked = False

        def __enter__(self):
            events.append("connection-enter")
            return self

        def __exit__(self, *_):
            events.append("connection-exit")

        def scalar(self, statement, parameters):
            assert parameters == {"lock_key": setup_model_provider._BOOTSTRAP_LOCK_KEY}
            if "pg_try_advisory_lock" in statement:
                acquired = next(self.acquire_results)
                self.locked = acquired
                events.append("acquire")
                return acquired
            assert "pg_advisory_unlock" in statement
            events.append("release")
            was_locked = self.locked
            self.locked = False
            return was_locked

        def commit(self):
            events.append("commit")

        def invalidate(self):
            events.append("invalidate")
            self.locked = False

    connection = Connection()
    engine = SimpleNamespace(connect=lambda: connection)
    extensions = ModuleType("extensions")
    extensions.__path__ = []
    ext_database = ModuleType("extensions.ext_database")
    ext_database.db = SimpleNamespace(engine=engine)
    sqlalchemy = ModuleType("sqlalchemy")
    sqlalchemy.text = lambda statement: statement
    monkeypatch.setitem(sys.modules, "extensions", extensions)
    monkeypatch.setitem(sys.modules, "extensions.ext_database", ext_database)
    monkeypatch.setitem(sys.modules, "sqlalchemy", sqlalchemy)
    return connection, events


def test_bootstrap_lock_acquires_and_releases_dedicated_connection(monkeypatch):
    connection, events = _configure_lock_database(monkeypatch, [False, True])
    monkeypatch.setattr(setup_model_provider.time, "monotonic", lambda: 10.0)
    monkeypatch.setattr(
        setup_model_provider.time,
        "sleep",
        lambda seconds: events.append(f"sleep-{seconds:g}"),
    )

    with setup_model_provider._bootstrap_lock(timeout_seconds=1):
        assert connection.locked
        events.append("mutation")

    assert not connection.locked
    assert events == [
        "connection-enter",
        "acquire",
        "commit",
        "sleep-1",
        "acquire",
        "commit",
        "mutation",
        "release",
        "commit",
        "connection-exit",
    ]


def test_bootstrap_lock_times_out_without_mutation_or_release(monkeypatch):
    connection, events = _configure_lock_database(monkeypatch, [False])
    monkeypatch.setenv("DIFY_BOOTSTRAP_LOCK_TIMEOUT_SECONDS", "0.25")
    monotonic_values = iter([10.0, 10.25])
    monkeypatch.setattr(setup_model_provider.time, "monotonic", lambda: next(monotonic_values))

    with pytest.raises(TimeoutError, match=r"within 0.25 seconds.*another bootstrap"):
        with setup_model_provider._bootstrap_lock():
            pytest.fail("mutation ran without the lock")

    assert not connection.locked
    assert events == ["connection-enter", "acquire", "commit", "connection-exit"]


def test_main_exits_nonzero_with_safe_message_when_lock_times_out(monkeypatch, caplog):
    _configure_successful_run(monkeypatch)
    secret = "gateway-key-must-not-be-logged"
    monkeypatch.setenv("LLM_PROXY_API_KEY", secret)

    @contextmanager
    def unavailable_lock():
        raise TimeoutError(
            "Could not acquire the Dify bootstrap lock within 1800 seconds; "
            "another bootstrap process may still be running."
        )
        yield

    monkeypatch.setattr(setup_model_provider, "_bootstrap_lock", unavailable_lock)

    with pytest.raises(SystemExit) as exc_info:
        setup_model_provider.main()

    assert exc_info.value.code == 1
    assert "Could not acquire the Dify bootstrap lock" in caplog.text
    assert secret not in caplog.text


def test_bootstrap_lock_releases_when_mutation_raises(monkeypatch):
    connection, events = _configure_lock_database(monkeypatch, [True])

    with pytest.raises(RuntimeError, match="mutation failure"):
        with setup_model_provider._bootstrap_lock(timeout_seconds=1):
            assert connection.locked
            raise RuntimeError("mutation failure")

    assert not connection.locked
    assert "release" in events
    assert events[-1] == "connection-exit"


def test_run_checks_state_and_mutates_only_while_locked(monkeypatch):
    app_factory = _configure_successful_run(monkeypatch)
    state = {"locked": False}
    stages = []

    @contextmanager
    def lock():
        assert not state["locked"]
        state["locked"] = True
        try:
            yield
        finally:
            state["locked"] = False

    def record(stage, result=None):
        assert state["locked"], f"{stage} ran without the bootstrap lock"
        stages.append(stage)
        return result

    app_factory._auto_setup = lambda _: record("auto-setup")
    monkeypatch.setattr(setup_model_provider, "_bootstrap_lock", lock)
    monkeypatch.setattr(
        setup_model_provider,
        "_resolve_tenant",
        lambda: record("tenant", SimpleNamespace(id="tenant-1")),
    )
    monkeypatch.setattr(
        setup_model_provider,
        "_require_llm_proxy_api_key",
        lambda: record("api-key", "gateway-key"),
    )
    monkeypatch.setattr(setup_model_provider, "_ensure_tenant_rsa", lambda _: record("rsa"))
    monkeypatch.setattr(
        setup_model_provider,
        "_wait_for_daemon",
        lambda _: record("daemon", True),
    )
    monkeypatch.setattr(setup_model_provider, "_ensure_plugins", lambda _: record("plugins"))
    monkeypatch.setattr(
        setup_model_provider, "_load_settings_config", lambda: record("settings", {})
    )
    monkeypatch.setattr(
        setup_model_provider,
        "_load_credentials_config",
        lambda: record(
            "credentials",
            {"provider": {"model": "model", "credentials": {}}},
        ),
    )
    monkeypatch.setattr(setup_model_provider, "_provision", lambda *_: record("provider"))

    setup_model_provider._run()

    assert not state["locked"]
    assert stages == [
        "auto-setup",
        "tenant",
        "api-key",
        "rsa",
        "daemon",
        "plugins",
        "settings",
        "credentials",
        "provider",
    ]


def test_main_completes_when_all_required_setup_stages_succeed(monkeypatch):
    _configure_successful_run(monkeypatch)

    assert setup_model_provider.main() is None


@pytest.mark.parametrize("stage", ["rsa", "daemon", "plugin", "provider"])
def test_main_exits_nonzero_when_required_setup_stage_fails(monkeypatch, stage):
    _configure_successful_run(monkeypatch)

    if stage == "rsa":
        monkeypatch.setattr(
            setup_model_provider,
            "_ensure_tenant_rsa",
            lambda _: (_ for _ in ()).throw(RuntimeError("rsa failure")),
        )
    elif stage == "daemon":
        monkeypatch.setattr(setup_model_provider, "_wait_for_daemon", lambda _: False)
    elif stage == "plugin":
        monkeypatch.setattr(
            setup_model_provider,
            "_ensure_plugins",
            lambda _: (_ for _ in ()).throw(RuntimeError("plugin failure")),
        )
    else:
        monkeypatch.setattr(
            setup_model_provider,
            "_load_credentials_config",
            lambda: {"provider": {"model": "model", "credentials": {}}},
        )
        monkeypatch.setattr(
            setup_model_provider,
            "_provision",
            lambda *_: (_ for _ in ()).throw(RuntimeError("provisioning failure")),
        )

    with pytest.raises(SystemExit, match="1") as exc_info:
        setup_model_provider.main()

    assert exc_info.value.code == 1


def test_provision_tolerates_idempotent_duplicate_operations(monkeypatch):
    calls = []

    class Service:
        def update_model_credential(self, **_):
            calls.append("update")
            raise ValueError("same credential")

        def add_model_credential_to_model_list(self, **_):
            calls.append("add")
            raise ValueError("same credential")

        def enable_model(self, **_):
            calls.append("enable")
            raise ValueError("already enabled")

        def update_default_model_of_model_type(self, **_):
            calls.append("default")

    services = ModuleType("services")
    services.__path__ = []
    provider_service = ModuleType("services.model_provider_service")
    provider_service.ModelProviderService = Service
    monkeypatch.setitem(sys.modules, "services", services)
    monkeypatch.setitem(sys.modules, "services.model_provider_service", provider_service)
    monkeypatch.setattr(
        setup_model_provider,
        "_resolve_credential_id",
        lambda *_: "credential-1",
    )
    config = {
        "provider": "openai_api_compatible",
        "model": "remote/model",
        "credentials": {"api_key": "key"},
        "as_default": True,
    }

    setup_model_provider._provision("tenant-1", config)
    setup_model_provider._provision("tenant-1", config)

    assert calls == ["update", "add", "enable", "default"] * 2
