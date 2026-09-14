# mulesoft-tools-foundary

Four independent, working CLI tools for MuleSoft development productivity —
API governance, testing, dependency hygiene, and production debugging. Each
subdirectory is a complete, standalone project with its own README, tests,
license, and quickstart; this repo just brings them together in one place.

Every tool's core function is designed to work **without any Anthropic API
key or network access to Claude** — parsing, diffing, static analysis, and
live third-party API calls (Maven Central) are all deterministic. Where a
tool has an *optional* LLM-powered enhancement, it's opt-in and clearly
labeled, and each project's own README states plainly what was verified with
a live run versus what's a disclosed dry-run (this environment didn't always
have Anthropic API credentials available during development) — check the
"Verification" / "Honesty note" / "Limitations" section in a project's own
README before relying on its claims.

## Tools

| Project | What it does | Needs an API key? |
|---|---|---|
| [`raml-guard/`](raml-guard/) | CI-gating CLI that diffs two RAML/OAS spec versions and classifies every change as breaking, non-breaking, or informational — catches accidental breaking changes before they reach Anypoint Exchange consumers. | No (core diff engine). Optional `--summarize` LLM release-note writer. |
| [`mule-munit-scaffold/`](mule-munit-scaffold/) | Reads real Mule 4 flow XML and generates MUnit test skeletons with a mock already wired up for every outbound connector call in the flow — automates the most tedious, most-skipped part of Mule testing. | No (core scaffold). Optional `--enrich` LLM mock/assertion writer. |
| [`mule-connector-audit/`](mule-connector-audit/) | Parses a project's `pom.xml`, queries the live Maven Central API for each connector's actual latest version and release date, and flags stale or possibly-abandoned connectors. | No — fully live-verified against the real Maven Central API. Optional `--explain` LLM summary. |
| [`mule-log-tracer/`](mule-log-tracer/) | Groups interleaved Mule/CloudHub log lines by correlation ID and reconstructs a readable, ordered per-request waterfall trace, flagging slow steps, errors, and out-of-order events. | No (core trace/anomaly engine). Optional `--explain` LLM summary. |

## Using a tool

Each project is fully self-contained — its own `pyproject.toml`, tests, and
dependencies. Pick one and `cd` in:

```bash
git clone https://github.com/upxill/mulesoft-tools-foundary.git
cd mulesoft-tools-foundary/<project-name>
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/
```

Then follow that project's own README for its specific CLI usage and
examples. None of these four tools require an Anthropic API key to run their
core function — only the optional LLM-enhancement flags each one documents
do.

## License

Each project carries its own MIT `LICENSE` file. All code in this
repository is MIT licensed, copyright Srinivasarao Polagani.
