"""raml-guard: a deterministic CI gate that diffs two RAML 1.0 specs and
classifies every detected change as breaking, non-breaking, or informational.

The diff engine (`raml_parser.py` + `diff_engine.py`) is 100% offline and
deterministic -- no network call, no API key, no LLM involved. The optional
`--summarize`/`explain` feature is the only part of this package that talks
to the Anthropic API, and it is off by default (see `summarizer.py`).
"""

__version__ = "0.1.0"
