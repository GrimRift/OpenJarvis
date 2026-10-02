"""Real SDK construction in tests must not start network senders."""

import threading

from posthog import Posthog


def test_real_sdk_clients_leave_no_senders_or_queued_events():
    threads = set(threading.enumerate())
    client = Posthog(project_api_key="test-project-key", flush_interval=0.01)
    try:
        assert client.send is False
        assert client.disabled is True
        assert set(threading.enumerate()) <= threads
        client.capture(event="test_event", distinct_id="test")
        assert client.queue.empty()
    finally:
        client.join()
