from comfyui_job_plugin.broker_client import compute_backoff_ms


def test_backoff_is_exponential():
    assert compute_backoff_ms(0) == 1000
    assert compute_backoff_ms(1) == 2000
    assert compute_backoff_ms(2) == 4000
    assert compute_backoff_ms(3) == 8000


def test_backoff_caps_at_30s():
    assert compute_backoff_ms(5) == 30_000
    assert compute_backoff_ms(50) == 30_000
    # The exponent is capped, so a huge attempt count stays bounded and cheap
    # (without the cap this would compute a multi-million-digit integer).
    assert compute_backoff_ms(1_000_000) == 30_000
