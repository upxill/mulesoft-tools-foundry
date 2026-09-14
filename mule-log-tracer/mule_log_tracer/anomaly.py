"""Deterministic anomaly detection over a reconstructed :class:`~mule_log_tracer.tracer.Trace`.

Three rules, all pure Python, no LLM, no network:

(a) **Error rule** -- any event in the trace with ``level == "ERROR"``.
(b) **Slow-step rule** -- any gap between two chronologically consecutive
    events in the trace exceeding ``slow_threshold_ms`` (default 2000ms).
(c) **Out-of-order / clock-skew rule** -- events for the same correlation ID
    whose timestamps are NOT monotonically non-decreasing in the order they
    actually arrived in the source log file. This is checked against the
    file's original arrival order (``Trace.arrival_events``), not the
    timestamp-sorted waterfall (``Trace.steps``) -- sorting by timestamp
    would trivially "fix" the order and hide the very signal this rule
    exists to surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

from .log_parser import LogEvent
from .tracer import Trace

DEFAULT_SLOW_THRESHOLD_MS = 2000.0


@dataclass
class SlowStep:
    step_index: int  # index into trace.steps of the step the gap ends at
    delta_ms: float


@dataclass
class OutOfOrderPair:
    earlier_arrival: LogEvent  # arrived first in the file
    later_arrival: LogEvent  # arrived later in the file, but has an earlier (or equal-then-earlier) timestamp


@dataclass
class Anomalies:
    error_step_indices: List[int] = field(default_factory=list)
    slow_steps: List[SlowStep] = field(default_factory=list)
    out_of_order_pairs: List[OutOfOrderPair] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return bool(self.error_step_indices)

    @property
    def has_slow_steps(self) -> bool:
        return bool(self.slow_steps)

    @property
    def has_out_of_order(self) -> bool:
        return bool(self.out_of_order_pairs)

    @property
    def is_anomalous(self) -> bool:
        return self.has_errors or self.has_slow_steps or self.has_out_of_order


def detect_anomalies(trace: Trace, slow_threshold_ms: float = DEFAULT_SLOW_THRESHOLD_MS) -> Anomalies:
    """Run all three deterministic anomaly rules against one trace."""
    anomalies = Anomalies()

    # (a) error rule
    for step in trace.steps:
        if step.event.is_error:
            anomalies.error_step_indices.append(step.index)

    # (b) slow-step rule -- gap between chronologically consecutive events
    for step in trace.steps[1:]:
        if step.delta_ms > slow_threshold_ms:
            anomalies.slow_steps.append(SlowStep(step_index=step.index, delta_ms=step.delta_ms))

    # (c) out-of-order rule -- checked against file arrival order, not the
    # timestamp-sorted waterfall.
    arrival = trace.arrival_events
    for i in range(1, len(arrival)):
        previous_event = arrival[i - 1]
        current_event = arrival[i]
        if current_event.timestamp < previous_event.timestamp:
            anomalies.out_of_order_pairs.append(
                OutOfOrderPair(earlier_arrival=previous_event, later_arrival=current_event)
            )

    return anomalies
