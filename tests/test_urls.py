from comfyui_job_plugin.urls import build_connection_url, to_websocket_url


def test_to_websocket_url_upgrades_scheme():
    assert to_websocket_url("http://broker:8081") == "ws://broker:8081"
    assert to_websocket_url("https://broker.example") == "wss://broker.example"


def test_to_websocket_url_passthrough():
    assert to_websocket_url("ws://broker:8081") == "ws://broker:8081"
    assert to_websocket_url("wss://broker") == "wss://broker"


def test_build_connection_url_includes_all_params():
    url = build_connection_url(
        "https://broker.example/",
        name="My Node",
        gpu="RTX 5090",
        node_id=17,
        protocol_version=1,
    )
    assert url.startswith("wss://broker.example/nodes/connect?")
    assert "name=My+Node" in url
    assert "nodeId=17" in url
    assert "gpu=RTX+5090" in url
    assert "protocol=1" in url


def test_build_connection_url_omits_node_id_when_unknown():
    url = build_connection_url(
        "http://localhost:8081",
        name="n",
        gpu="",
        node_id=None,
        protocol_version=1,
    )
    assert "nodeId" not in url
    assert "gpu=" not in url  # empty gpu is omitted
    assert url.startswith("ws://localhost:8081/nodes/connect?")
    assert "protocol=1" in url
