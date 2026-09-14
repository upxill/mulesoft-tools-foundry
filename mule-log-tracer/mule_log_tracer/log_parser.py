"""Deterministic parsing of Mule/CloudHub-style application logs.

Two input shapes are supported, and both are parsed with plain, tested
Python -- no LLM involved anywhere in this module:

1. **JSON Lines (primary format).** One JSON object per line, modeled on
   Mule's real ``%correlationId%`` / MDC logging pattern combined with
   DataDog/Splunk-style structured JSON log shipping (this is how most
   CloudHub-adjacent log pipelines actually ship logs -- see the README's
   "expected input shape" section for the full field documentation and the
   honest disclaimer that this is a documented, reasonable synthetic
   format, not one universal vendor schema). Required fields:
   ``timestamp`` (ISO-8601), ``correlationId``, ``flowName``, ``level``,
   ``message``. Optional: ``processorPath``, ``event``.

2. **Plaintext fallback.** For logs that were never structured as JSON,
   a line shaped like::

       2026-09-13T08:00:01.000Z INFO [orders-process-flow] correlationId=req-100 - Flow started

   is parsed with :data:`PLAINTEXT_LOG_RE`, a real regex with named
   groups, tolerant of a missing trailing message.

Malformed lines are skipped and counted in :class:`ParseStats` -- a single
bad line never aborts the run.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple


# ---------------------------------------------------------------------------
# Plaintext fallback regex
# ---------------------------------------------------------------------------
# Shape:  <ISO8601 timestamp> <LEVEL> [<flowName>] correlationId=<id> - <message>
# The trailing " - <message>" is optional so a bare event line still parses.
PLAINTEXT_LOG_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}T[\d:.]+(?:Z|[+-]\d{2}:?\d{2})?)"
    r"\s+(?P<level>DEBUG|INFO|WARN|ERROR)"
    r"\s+\[(?P<flow>[^\]]+)\]"
    r"\s+correlationId=(?P<cid>[^\s]+)"
    r"(?:\s+-\s+(?P<message>.*))?$"
)

# Looser last-resort: just grab a correlationId=<token> anywhere on the line,
# so a line that doesn't match the full documented plaintext shape but still
# carries a recognizable correlation id token isn't silently dropped.
CORRELATION_ID_RE = re.compile(r"correlationId=([^\s,;]+)")


@dataclass
class LogEvent:
    """A single normalized log event, regardless of its original format."""

    timestamp: datetime
    correlation_id: str
    flow_name: str
    level: str
    message: str
    processor_path: Optional[str] = None
    event: Optional[str] = None
    source_line: int = 0
    raw: str = ""

    @property
    def is_error(self) -> bool:
        return self.level.upper() == "ERROR"


@dataclass
class ParseStats:
    """Bookkeeping for a parse run -- how much of the file was usable."""

    total_lines: int = 0
    blank_lines: int = 0
    parsed_json: int = 0
    parsed_plaintext: int = 0
    skipped: int = 0
    skipped_reasons: List[str] = field(default_factory=list)

    @property
    def parsed(self) -> int:
        return self.parsed_json + self.parsed_plaintext


def _parse_timestamp(raw: str) -> datetime:
    """Parse an ISO-8601 timestamp, tolerating a trailing 'Z'."""
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def _parse_json_line(line: str, line_number: int) -> LogEvent:
    """Parse one JSON-lines record into a LogEvent. Raises on any problem."""
    obj = json.loads(line)
    if not isinstance(obj, dict):
        raise ValueError("JSON line is not an object")

    missing = [k for k in ("timestamp", "correlationId", "flowName", "level", "message") if k not in obj]
    if missing:
        raise ValueError(f"missing required field(s): {', '.join(missing)}")

    return LogEvent(
        timestamp=_parse_timestamp(str(obj["timestamp"])),
        correlation_id=str(obj["correlationId"]),
        flow_name=str(obj["flowName"]),
        level=str(obj["level"]).upper(),
        message=str(obj["message"]),
        processor_path=obj.get("processorPath"),
        event=obj.get("event"),
        source_line=line_number,
        raw=line,
    )


def _parse_plaintext_line(line: str, line_number: int) -> LogEvent:
    """Parse one plaintext log line into a LogEvent. Raises on any problem."""
    match = PLAINTEXT_LOG_RE.match(line.strip())
    if match:
        groups = match.groupdict()
        return LogEvent(
            timestamp=_parse_timestamp(groups["timestamp"]),
            correlation_id=groups["cid"],
            flow_name=groups["flow"],
            level=groups["level"].upper(),
            message=(groups.get("message") or "").strip(),
            source_line=line_number,
            raw=line,
        )

    # Last resort: a recognizable correlationId=<id> token but not the full
    # documented shape. We can still salvage a trace-worthy event only if we
    # can also find a timestamp; otherwise there is nothing to sort by, so
    # we give up and let the caller count this line as skipped.
    cid_match = CORRELATION_ID_RE.search(line)
    if not cid_match:
        raise ValueError("no correlationId= token found")
    raise ValueError("correlationId= found but line did not match the documented plaintext shape (missing/unparseable timestamp)")


def parse_line(line: str, line_number: int) -> LogEvent:
    """Parse a single raw log line (JSON first, then plaintext fallback)."""
    stripped = line.strip()
    if stripped.startswith("{"):
        return _parse_json_line(stripped, line_number)
    return _parse_plaintext_line(stripped, line_number)


def parse_log_file(path: str) -> Tuple[List[LogEvent], ParseStats]:
    """Parse a log file (JSONL, with plaintext-fallback lines mixed in).

    Returns the list of successfully-parsed events (in file order -- callers
    that need per-trace ordering should sort by timestamp themselves, see
    ``tracer.py``) plus a :class:`ParseStats` describing how many lines were
    skipped and why. A malformed line never raises out of this function.
    """
    events: List[LogEvent] = []
    stats = ParseStats()

    with open(path, "r", encoding="utf-8") as fh:
        for line_number, raw_line in enumerate(fh, start=1):
            stats.total_lines += 1
            if not raw_line.strip():
                stats.blank_lines += 1
                continue

            try:
                event = parse_line(raw_line, line_number)
            except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring
                stats.skipped += 1
                stats.skipped_reasons.append(f"line {line_number}: {exc}")
                continue

            if raw_line.strip().startswith("{"):
                stats.parsed_json += 1
            else:
                stats.parsed_plaintext += 1
            events.append(event)

    return events, stats
