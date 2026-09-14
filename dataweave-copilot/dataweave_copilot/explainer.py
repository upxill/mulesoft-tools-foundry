"""``explain`` command: read a real ``.dwl`` file from disk and ask Claude
for a structured plain-English explanation.

The explanation is always grounded in the literal file content read from
disk at call time -- this module never fabricates what a script does
without first reading it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

DEFAULT_MODEL = "claude-opus-5"

SYSTEM_PROMPT = (
    "You are an expert MuleSoft DataWeave 2.0 engineer explaining a real "
    "transformation script to another engineer. Base your explanation ONLY "
    "on the literal script text given to you -- never invent behavior the "
    "script doesn't actually have. Structure your answer with exactly these "
    "section headings, in this order: 'Overall Purpose', 'Key Steps', "
    "'Notable Functions & Selectors', and 'Edge Cases It May Not Handle Well'."
)


def read_script(path: str) -> str:
    """Read the real file content from disk. Raises FileNotFoundError if
    it doesn't exist -- callers must not fabricate an explanation for a
    file that couldn't actually be read."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"No such file: {path}")
    return p.read_text()


def build_prompt(script_content: str, filename: str) -> str:
    return (
        f"Here is the real content of the DataWeave script `{filename}`, "
        f"read directly from disk:\n\n```\n{script_content}\n```\n\n"
        "Explain what it does."
    )


def explain(client: Any, script_path: str, model: str = DEFAULT_MODEL) -> str:
    content = read_script(script_path)
    prompt = build_prompt(content, Path(script_path).name)
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        thinking={"type": "adaptive"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in response.content if b.type == "text")
