"""Optional, off-by-default LLM summary of a trace's anomalies (`--explain`).

Everything else in this project -- parsing, correlation-ID grouping,
timeline reconstruction, and anomaly detection -- is pure deterministic
Python (see ``log_parser.py``, ``tracer.py``, ``anomaly.py``) and needs no
API key at all. This module is the one genuinely optional exception: when a
user passes ``--explain`` on a trace that has errors/anomalies, it asks
Claude for a one-paragraph plain-English summary of what likely went wrong.

If no Anthropic credentials are available, ``explain_trace`` is never
called with a real client -- the CLI instead calls :func:`dry_run_explain`,
which synthesizes an equivalent, honestly-labeled summary directly from the
same deterministic anomaly data, so the rest of the reporting pipeline can
still be exercised end to end without a network call.
"""

from __future__ import annotations

from typing import Any

from .anomaly import Anomalies
from .tracer import Trace

DEFAULT_MODEL = "claude-opus-5"

SYSTEM_PROMPT = (
    "You are an experienced MuleSoft production support engineer. You are "
    "given a deterministically-reconstructed request trace (a sequence of "
    "log events for one correlation ID) plus a list of anomalies a rules "
    "engine already detected in it (errors, slow steps, out-of-order "
    "timestamps). Write exactly one plain-English paragraph summarizing "
    "what likely went wrong and, if useful, what to check next. Base your "
    "summary ONLY on the trace data and anomalies given to you -- never "
    "invent log lines, systems, or root causes that aren't supported by "
    "the given data."
)


def build_prompt(trace: Trace, anomalies: Anomalies) -> str:
    lines = [f"Trace for correlationId={trace.correlation_id}:"]
    for step in trace.steps:
        flag = ""
        if step.event.is_error:
            flag = " [ERROR]"
        for slow in anomalies.slow_steps:
            if slow.step_index == step.index:
                flag += f" [SLOW +{slow.delta_ms:.0f}ms]"
        lines.append(
            f"  step {step.index + 1} @ +{step.elapsed_ms:.0f}ms (delta +{step.delta_ms:.0f}ms) "
            f"[{step.event.level}] {step.event.flow_name}: {step.event.message}{flag}"
        )

    lines.append("")
    lines.append("Detected anomalies:")
    if anomalies.has_errors:
        lines.append(f"  - {len(anomalies.error_step_indices)} ERROR-level event(s)")
    if anomalies.has_slow_steps:
        for slow in anomalies.slow_steps:
            lines.append(f"  - slow step at index {slow.step_index + 1}: +{slow.delta_ms:.0f}ms gap")
    if anomalies.has_out_of_order:
        lines.append(f"  - {len(anomalies.out_of_order_pairs)} out-of-order/clock-skew event pair(s)")
    if not anomalies.is_anomalous:
        lines.append("  - none (this trace is healthy)")

    lines.append("")
    lines.append("Write the one-paragraph summary now.")
    return "\n".join(lines)


def explain_trace(client: Any, trace: Trace, anomalies: Anomalies, model: str = DEFAULT_MODEL) -> str:
    """Make a real `client.messages.create(...)` call to summarize a trace."""
    prompt = build_prompt(trace, anomalies)
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        thinking={"type": "adaptive"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in response.content if b.type == "text")


def dry_run_explain(trace: Trace, anomalies: Anomalies) -> str:
    """Synthesize an equivalent summary directly from the anomaly data, with
    no Anthropic API call. Used when no credentials are available.

    The returned text is always prefixed with a `[DRY RUN ...]` label so it
    is never mistaken for a real model response.
    """
    if not anomalies.is_anomalous:
        return (
            "[DRY RUN: no Anthropic API call made] This trace has no detected "
            "anomalies -- no ERROR-level events, no slow steps, and no "
            "out-of-order timestamps -- so there is nothing to explain."
        )

    parts = []
    if anomalies.has_errors:
        error_steps = [s for s in trace.steps if s.event.is_error]
        first_error = error_steps[0]
        parts.append(
            f"an ERROR-level event in flow '{first_error.event.flow_name}' "
            f"at +{first_error.elapsed_ms:.0f}ms ('{first_error.event.message}')"
        )
    if anomalies.has_slow_steps:
        worst = max(anomalies.slow_steps, key=lambda s: s.delta_ms)
        worst_step = trace.steps[worst.step_index]
        parts.append(
            f"a slow step of +{worst.delta_ms:.0f}ms before flow "
            f"'{worst_step.event.flow_name}' ('{worst_step.event.message}')"
        )
    if anomalies.has_out_of_order:
        parts.append(
            f"{len(anomalies.out_of_order_pairs)} log line(s) that arrived out of "
            "chronological order relative to their own timestamps, which suggests "
            "clock skew across the systems/nodes emitting this trace's log lines"
        )

    body = "; and ".join(parts)
    return (
        f"[DRY RUN: scripted from the deterministic anomaly data, not from Claude] "
        f"This trace shows {body}. Recommend correlating with the affected "
        f"flow(s)/connector(s) directly and checking the surrounding infrastructure "
        f"(downstream service health, network latency, node clocks) as the next "
        f"debugging step."
    )
