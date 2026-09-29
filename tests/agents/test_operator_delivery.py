"""An Operator's result reaches the user (30 September).

A scheduled tick had no way to reach the phone: the server's scheduler system
has no channel backend, so `channel_send` answers "No channel backend
configured", and it would depend on the model spelling the chat id. With
``config["deliver_to"] = "telegram"`` the executor sends the finished report
itself, through the configured ``[notifications] channel``.
"""

from __future__ import annotations

from openjarvis.agents._stubs import AgentResult
from openjarvis.core.events import EventBus


def _setup(tmp_path, config):
    from openjarvis.agents.executor import AgentExecutor
    from openjarvis.agents.manager import AgentManager

    mgr = AgentManager(str(tmp_path / "agents.db"))
    executor = AgentExecutor(mgr, EventBus())
    agent = mgr.create_agent("Morning brief", agent_type="orchestrator", config=config)
    mgr.start_tick(agent["id"])
    return mgr, executor, agent


def test_a_finished_report_is_sent_to_telegram(tmp_path, monkeypatch):
    from openjarvis.agents import executor as executor_module

    sent = []
    monkeypatch.setattr(
        executor_module,
        "_send_to_phone",
        lambda title, text: sent.append((title, text)) or True,
    )
    mgr, executor, agent = _setup(tmp_path, {"deliver_to": "telegram"})
    result = AgentResult(content="School: nothing due.\nInbox: 2 need replies.")
    executor._finalize_tick(agent["id"], result, error=None, duration=1.0)
    assert sent == [("Morning brief", "School: nothing due.\nInbox: 2 need replies.")]
    assert result.metadata["delivered_to"] == "telegram"
    mgr.close()


def test_nothing_is_sent_without_deliver_to(tmp_path, monkeypatch):
    from openjarvis.agents import executor as executor_module

    sent = []
    monkeypatch.setattr(
        executor_module,
        "_send_to_phone",
        lambda title, text: sent.append((title, text)) or True,
        raising=False,
    )
    mgr, executor, agent = _setup(tmp_path, {})
    executor._finalize_tick(
        agent["id"], AgentResult(content="x"), error=None, duration=1.0
    )
    assert sent == []
    mgr.close()


def test_a_failed_send_is_recorded_not_hidden(tmp_path, monkeypatch):
    from openjarvis.agents import executor as executor_module

    monkeypatch.setattr(executor_module, "_send_to_phone", lambda title, text: False)
    mgr, executor, agent = _setup(tmp_path, {"deliver_to": "telegram"})
    result = AgentResult(content="report")
    executor._finalize_tick(agent["id"], result, error=None, duration=1.0)
    assert result.metadata["delivered_to"] == "failed: telegram"
    # The run itself still counts; only the delivery failed.
    assert mgr.get_agent(agent["id"])["total_runs"] == 1
    mgr.close()


def test_an_empty_report_is_not_sent(tmp_path, monkeypatch):
    from openjarvis.agents import executor as executor_module

    sent = []
    monkeypatch.setattr(
        executor_module,
        "_send_to_phone",
        lambda title, text: sent.append(text) or True,
    )
    mgr, executor, agent = _setup(tmp_path, {"deliver_to": "telegram"})
    result = AgentResult(content="   ")
    executor._finalize_tick(agent["id"], result, error=None, duration=1.0)
    assert sent == []
    assert result.metadata["delivered_to"] == "skipped: empty report"
    mgr.close()
