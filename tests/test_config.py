from comfyui_job_plugin.config import load_config, read_node_id, write_node_id

_ENV_VARS = [
    "NODE_BROKER_URL",
    "GATEWAY_API_KEY",
    "NODE_NAME",
    "NODE_GPU_NAME",
    "NODE_ID_FILE",
    "COMFYUI_POLL_INTERVAL_MS",
    "HEARTBEAT_INTERVAL_MS",
    "NODE_JOB_TIMEOUT_MS",
]


def _clear_env(monkeypatch):
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def test_env_overrides_toml(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    (tmp_path / "config.toml").write_text(
        'broker_base_url = "http://toml-broker"\napi_key = "toml-key"\n', encoding="utf8"
    )
    monkeypatch.setenv("NODE_BROKER_URL", "http://env-broker")
    cfg = load_config(tmp_path)
    assert cfg.broker_base_url == "http://env-broker"  # env wins over toml
    assert cfg.api_key == "toml-key"  # falls back to toml when env unset
    assert cfg.is_runnable


def test_defaults_when_unset(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    cfg = load_config(tmp_path)
    assert cfg.node_name == "ComfyUI Plugin Node"
    assert cfg.broker_base_url == "https://broker.stableart.io"  # default broker
    assert cfg.poll_interval_ms == 1000
    assert cfg.job_timeout_ms == 120000
    assert not cfg.is_runnable  # broker defaults, but no api key → idle


def test_empty_env_falls_through(tmp_path, monkeypatch):
    # A present-but-empty env var (e.g. `GATEWAY_API_KEY=` from a compose file)
    # must NOT shadow config.toml / the Settings panel — it counts as unset.
    _clear_env(monkeypatch)
    (tmp_path / "config.toml").write_text('api_key = "toml-key"\n', encoding="utf8")
    monkeypatch.setenv("GATEWAY_API_KEY", "")
    monkeypatch.setenv("NODE_BROKER_URL", "   ")  # whitespace also counts as unset
    cfg = load_config(tmp_path)
    assert cfg.api_key == "toml-key"
    assert cfg.broker_base_url == "https://broker.stableart.io"  # default, not ""
    assert cfg.is_runnable


def test_trailing_slash_stripped(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("NODE_BROKER_URL", "https://broker.example/")
    cfg = load_config(tmp_path)
    assert cfg.broker_base_url == "https://broker.example"


def test_node_id_file_defaults_into_plugin_dir(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    cfg = load_config(tmp_path)
    assert cfg.node_id_file == tmp_path / "data" / "node-id"


def test_node_id_roundtrip(tmp_path):
    path = tmp_path / "data" / "node-id"
    assert read_node_id(path) is None
    write_node_id(path, 42)
    assert read_node_id(path) == 42
    # garbage is tolerated as "no id"
    path.write_text("not-a-number", encoding="utf8")
    assert read_node_id(path) is None
