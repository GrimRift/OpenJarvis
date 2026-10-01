"""AgentScheduler — cron/interval tick scheduling for managed agents."""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Any, Callable

from openjarvis.core.events import EventType

if TYPE_CHECKING:
    from openjarvis.agents.executor import AgentExecutor
    from openjarvis.agents.manager import AgentManager

logger = logging.getLogger(__name__)


def _next_cron_fire(cron_expr: str, now: float | None = None) -> float:
    """Calculate the next fire time for a cron expression.

    Uses croniter if available, otherwise falls back to a simple
    interval-based approximation.
    """
    try:
        from croniter import croniter
    except ImportError:
        # Fallback: treat as hourly interval
        logger.warning("croniter not installed, treating cron as 3600s interval")
        return (now or time.time()) + 3600

    base = now or time.time()
    import datetime

    dt = datetime.datetime.fromtimestamp(base)
    cron = croniter(cron_expr, dt)
    next_dt = cron.get_next(datetime.datetime)
    return next_dt.timestamp()


# A scheduled run missed because Sage was off or the PC slept runs once when
# Sage is back -- the same day and less than this late. Older misses are
# skipped, never stacked: a 06:30 brief at 21:00 is noise.
CATCH_UP_WINDOW_SECONDS = 10 * 3600
# How far behind its slot a fire can be before it counts as missed rather
# than merely delayed by the tick before it (a brief takes 1-3 minutes and
# the loop runs ticks one at a time).
_LATE_AFTER_SECONDS = 15 * 60


def _prev_cron_fire(cron_expr: str, now: float) -> float | None:
    """The cron's most recent fire time at or before *now*, if computable."""
    try:
        from croniter import croniter
    except ImportError:
        return None
    import datetime

    dt = datetime.datetime.fromtimestamp(now)
    return croniter(cron_expr, dt).get_prev(datetime.datetime).timestamp()


def _hhmm(ts: float) -> str:
    import datetime

    return datetime.datetime.fromtimestamp(ts).strftime("%H:%M")


def _catch_up_allowed(slot: float, now: float, agent: dict) -> bool:
    """Whether the missed *slot* should still run now.

    Not if a run (scheduled or Run now) already happened after it, not if the
    agent did not exist yet, and only the same day within the window.
    """
    import datetime

    if now - slot >= CATCH_UP_WINDOW_SECONDS or slot > now:
        return False
    if datetime.date.fromtimestamp(slot) != datetime.date.fromtimestamp(now):
        return False
    last_run = agent.get("last_run_at") or 0.0
    created = agent.get("created_at") or 0.0
    return last_run < slot and created < slot


class AgentScheduler:
    """Schedules managed agent ticks based on cron/interval configs.

    Runs a background thread that checks for due agents and dispatches
    ticks to the executor.
    """

    def __init__(
        self,
        manager: AgentManager,
        executor: AgentExecutor | Any,
        tick_interval: float = 1.0,
        event_bus: Any = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._manager = manager
        self._executor = executor
        self._tick_interval = tick_interval
        self._bus = event_bus
        # Looked up per call, so tests that patch this module's `time` work.
        self._clock = clock or (lambda: time.time())
        # (agent_id, slot) already caught up in this process, so a failed
        # catch-up is not retried every second.
        self._caught_up: set[tuple[str, float]] = set()
        # agent_id -> {schedule_type, schedule_value, next_fire}
        self._agents: dict[str, dict] = {}
        self._tick_counts: dict[str, int] = {}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    @property
    def registered_agents(self) -> set[str]:
        with self._lock:
            return set(self._agents.keys())

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def register_agent(self, agent_id: str) -> None:
        """Register an agent for scheduling."""
        agent = self._manager.get_agent(agent_id)
        if agent is None:
            raise ValueError(f"Agent {agent_id} not found")

        config = agent.get("config", {})
        schedule_type = config.get("schedule_type", "manual")
        schedule_value = config.get("schedule_value", 0)

        now = self._clock()
        late_for: float | None = None
        if schedule_type == "cron":
            next_fire = _next_cron_fire(str(schedule_value), now)
            # Sage was off at the last slot: run it now, once, marked late.
            slot = _prev_cron_fire(str(schedule_value), now)
            if slot is not None and _catch_up_allowed(slot, now, agent):
                late_for, next_fire = slot, now
                logger.info(
                    "Agent %s missed its %s run; catching up now",
                    agent_id,
                    _hhmm(slot),
                )
        elif schedule_type == "interval":
            next_fire = now + float(schedule_value)
        else:
            next_fire = float("inf")  # Manual: never auto-fires

        with self._lock:
            self._agents[agent_id] = {
                "schedule_type": schedule_type,
                "schedule_value": schedule_value,
                "next_fire": next_fire,
                "late_for": late_for,
            }

        logger.info(
            "Registered agent %s (%s), next fire: %s",
            agent_id,
            schedule_type,
            next_fire,
        )

    def deregister_agent(self, agent_id: str) -> None:
        """Remove an agent from scheduling."""
        with self._lock:
            self._agents.pop(agent_id, None)
        logger.info("Deregistered agent %s", agent_id)

    def start(self) -> None:
        """Start the scheduler background thread."""
        if self.is_running:
            return
        if self._bus:
            self._bus.subscribe(EventType.AGENT_TICK_END, self._on_tick_event)
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="agent-scheduler"
        )
        self._thread.start()
        logger.info("Agent scheduler started")

    def request_stop(self) -> None:
        """Prevent new scheduled ticks without waiting for the worker."""

        self._stop_event.set()
        if self._bus:
            self._bus.unsubscribe(EventType.AGENT_TICK_END, self._on_tick_event)

    def wait_stopped(self, timeout: float = 10.0) -> bool:
        """Wait for an active tick to finish, retaining live thread state."""

        thread = self._thread
        if thread is None:
            return True
        if thread is threading.current_thread():
            return False
        thread.join(timeout=timeout)
        if thread.is_alive():
            logger.warning("Agent scheduler did not stop within %.1fs", timeout)
            return False
        if self._thread is thread:
            self._thread = None
        return True

    def stop(self, timeout: float = 10.0) -> None:
        """Stop dispatching and wait for the scheduler worker."""

        self.request_stop()
        if self.wait_stopped(timeout=timeout):
            logger.info("Agent scheduler stopped")

    def _loop(self) -> None:
        """Main scheduler loop."""
        last_reconcile = 0.0
        reconcile_interval = 30
        while not self._stop_event.is_set():
            try:
                self._check_due_agents()
                now = time.time()
                if now - last_reconcile >= reconcile_interval:
                    self._reconcile()
                    last_reconcile = now
            except Exception:
                logger.exception("Scheduler tick error")
            self._stop_event.wait(self._tick_interval)

    def _check_due_agents(self) -> None:
        """Check all registered agents and fire those that are due."""
        now = self._clock()

        with self._lock:
            due = [
                (aid, dict(info))
                for aid, info in self._agents.items()
                if info["next_fire"] <= now
            ]

        for agent_id, info in due:
            if self._stop_event.is_set():
                break
            agent = self._manager.get_agent(agent_id)
            if agent is None or agent["status"] in (
                "paused",
                "archived",
                "running",
                "budget_exceeded",
                "stalled",
            ):
                continue

            late_for = info.get("late_for")
            if (
                late_for is None
                and info["schedule_type"] == "cron"
                and now - info["next_fire"] > _LATE_AFTER_SECONDS
            ):
                # The server stayed up but the PC slept through the slot.
                late_for = info["next_fire"]
                if not _catch_up_allowed(late_for, now, agent):
                    logger.info(
                        "Agent %s missed its %s run; too late to catch up",
                        agent_id,
                        _hhmm(late_for),
                    )
                    self._reschedule(agent_id, info, now)
                    continue
            if late_for is not None:
                if (agent_id, late_for) in self._caught_up:
                    self._reschedule(agent_id, info, now)
                    continue
                self._caught_up.add((agent_id, late_for))

            logger.info(
                "Firing tick for agent %s%s",
                agent_id,
                f" (late: due {_hhmm(late_for)})" if late_for else "",
            )
            try:
                if late_for is None:
                    self._executor.execute_tick(agent_id)
                else:
                    self._executor.execute_tick(agent_id, late_for=late_for)
            except Exception:
                logger.exception("Error executing tick for agent %s", agent_id)

            self._reschedule(agent_id, info, now)

    def _reschedule(self, agent_id: str, info: dict, now: float) -> None:
        """Set the next fire time after a slot was fired or skipped."""
        with self._lock:
            if agent_id in self._agents:
                self._agents[agent_id]["late_for"] = None
                if info["schedule_type"] == "cron":
                    self._agents[agent_id]["next_fire"] = _next_cron_fire(
                        str(info["schedule_value"]),
                        now,
                    )
                elif info["schedule_type"] == "interval":
                    self._agents[agent_id]["next_fire"] = now + float(
                        info["schedule_value"]
                    )
                # Manual: stays at inf

    def _reconcile(self) -> None:
        """Check running agents for stalls and handle retries."""
        agents = self._manager.list_agents()
        now = time.time()

        for agent in agents:
            if agent["status"] != "running":
                continue

            config = agent.get("config", {})
            timeout = config.get("timeout_seconds", 0)
            if timeout <= 0:
                continue

            last_activity = agent.get("last_activity_at")
            if last_activity is None:
                continue

            if now - last_activity <= timeout:
                continue

            # Agent is stalled
            max_retries = config.get("max_stall_retries", 5)
            current_retries = agent.get("stall_retries", 0)

            if current_retries >= max_retries:
                self._manager.update_agent(agent["id"], status="error")
                logger.warning(
                    "Agent %s stall retries exhausted (%d/%d), setting error",
                    agent["id"],
                    current_retries,
                    max_retries,
                )
            else:
                self._manager.end_tick(agent["id"])  # Release concurrency guard
                self._manager.update_agent(
                    agent["id"],
                    stall_retries=current_retries + 1,
                )
                if self._bus:
                    self._bus.publish(
                        EventType.AGENT_STALL_DETECTED,
                        {
                            "agent_id": agent["id"],
                            "last_activity_at": last_activity,
                            "stall_retries": current_retries + 1,
                        },
                    )
                logger.warning(
                    "Agent %s stalled (retry %d/%d)",
                    agent["id"],
                    current_retries + 1,
                    max_retries,
                )

    # -- Learning tick counting ------------------------------------------------

    def _on_tick_completed(self, agent_id: str) -> None:
        """Track completed ticks and trigger learning if schedule is met."""
        self._tick_counts[agent_id] = self._tick_counts.get(agent_id, 0) + 1

        agent = self._manager.get_agent(agent_id)
        if agent is None:
            return

        config = agent.get("config", {})
        if not config.get("learning_enabled", False):
            return

        schedule = config.get("learning_schedule", "every_20_ticks")
        if schedule.startswith("every_"):
            try:
                threshold = int(schedule.split("_")[1].replace("ticks", ""))
            except (IndexError, ValueError):
                threshold = 20
        else:
            return

        if self._tick_counts[agent_id] >= threshold:
            self._tick_counts[agent_id] = 0
            if self._bus:
                self._bus.publish(
                    EventType.AGENT_LEARNING_STARTED,
                    {
                        "agent_id": agent_id,
                    },
                )
            logger.info(
                "Learning triggered for agent %s after %d ticks",
                agent_id,
                threshold,
            )

    def _on_tick_event(self, event: Any) -> None:
        """Handle AGENT_TICK_END to count ticks."""
        agent_id = event.data.get("agent_id")
        if agent_id and event.data.get("status") == "ok":
            self._on_tick_completed(agent_id)
