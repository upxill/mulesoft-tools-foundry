"""Group parsed log events by correlation ID and reconstruct per-request traces.

This is pure, deterministic bookkeeping -- no LLM, no network. Given a flat
list of :class:`~mule_log_tracer.log_parser.LogEvent` (which in a real log
file arrive interleaved across many concurrent requests, in whatever order
the application happened to write them), this module:

1. Groups events by ``correlation_id``.
2. Sorts each group by ``timestamp`` (stable sort, so events that share an
   identical timestamp keep their original file order).
3. Wraps each event in a :class:`TraceStep` carrying the elapsed time since
   the trace started and the delta since the previous step.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from .log_parser import LogEvent


@dataclass
class TraceStep:
    """One event within a trace, with its computed timing relative to the trace."""

    event: LogEvent
    index: int
    delta_ms: float  # time since the previous step in this trace (0 for the first step)
    elapsed_ms: float  # time since the first step in this trace


@dataclass
class Trace:
    """All events for a single correlation ID, in chronological order.

    ``steps`` is the timestamp-sorted, readable waterfall. ``arrival_events``
    preserves the order these events actually appeared in the source log
    file (i.e. the order they were written/shipped) *before* that sort was
    applied -- ``anomaly.py`` diffs the two to detect out-of-order /
    clock-skew events: a log line that arrived later in the file but claims
    an earlier timestamp than a line that arrived before it.
    """

    correlation_id: str
    steps: List[TraceStep]
    arrival_events: List[LogEvent]

    @property
    def start_time(self):
        return self.steps[0].event.timestamp

    @property
    def end_time(self):
        return self.steps[-1].event.timestamp

    @property
    def duration_ms(self) -> float:
        return self.steps[-1].elapsed_ms if self.steps else 0.0

    @property
    def has_errors(self) -> bool:
        return any(step.event.is_error for step in self.steps)


def build_traces(events: List[LogEvent]) -> List[Trace]:
    """Group events by correlation ID and build a chronologically-ordered Trace each."""
    grouped: Dict[str, List[LogEvent]] = {}
    for event in events:
        grouped.setdefault(event.correlation_id, []).append(event)

    traces: List[Trace] = []
    for correlation_id, group_events in grouped.items():
        # group_events is already in file-arrival order (parse_log_file
        # yields events in file order, and dict insertion here preserves
        # that per correlation ID) -- keep a copy before re-sorting.
        arrival_events = list(group_events)

        # Stable sort: ties keep their original (file-arrival) order.
        ordered = sorted(group_events, key=lambda e: e.timestamp)
        steps: List[TraceStep] = []
        start = ordered[0].timestamp
        previous = start
        for i, event in enumerate(ordered):
            delta_ms = (event.timestamp - previous).total_seconds() * 1000.0
            elapsed_ms = (event.timestamp - start).total_seconds() * 1000.0
            steps.append(TraceStep(event=event, index=i, delta_ms=delta_ms, elapsed_ms=elapsed_ms))
            previous = event.timestamp
        traces.append(Trace(correlation_id=correlation_id, steps=steps, arrival_events=arrival_events))

    return traces
