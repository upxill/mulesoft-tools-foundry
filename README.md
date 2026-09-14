# mulesoft-tools-foundary

Seven independent, working CLI tools for MuleSoft development productivity —
architecture review, DataWeave authoring, RAML/OAS-to-MCP generation, API
governance, testing, dependency hygiene, and production debugging. Each
subdirectory is a complete, standalone project with its own README, tests,
license, and quickstart; this repo just brings them together in one place.

Three of the seven use Claude directly to generate/explain/review real
Mule/DataWeave artifacts and need a live `ANTHROPIC_API_KEY` for their core
function. The other four are deterministic-first — parsing, diffing, static
analysis, and live third-party API calls (Maven Central) that need **no**
Anthropic API key or network access to Claude at all; where one of those four
also has an *optional* LLM-powered enhancement, it's opt-in and clearly
labeled. Every project's own README states plainly what was verified with a
live run versus what's a disclosed dry-run (this environment didn't always
have Anthropic API credentials available during development) — check the
"Verification" / "Honesty note" / "Limitations" section in a project's own
README before relying on its claims.

## Tools

### AI-powered (need a live `ANTHROPIC_API_KEY`)

| Project | What it does |
|---|---|
| [`mule-flow-doctor/`](mule-flow-doctor/) | Architectural reviewer for Mule 4 apps — reads the real `<flow-ref>` call graph, connector configs, and DataWeave transforms, and reports what actually breaks in production: missing error handling, missing reconnection strategies, hardcoded secrets. |
| [`mule-to-mcp/`](mule-to-mcp/) | Turns a RAML 1.0 or OpenAPI 3.x spec into a working [MCP](https://modelcontextprotocol.io) server in one command, understanding real Mule/Anypoint auth patterns (Client ID enforcement, OAuth2) along the way. |
| [`dataweave-copilot/`](dataweave-copilot/) | Describe a DataWeave 2.0 transformation in plain English and get a real, execution-verified `.dwl` script back; also explains existing scripts and drafts MUnit-style edge-case tests. |

### Deterministic-first (no API key needed for the core function)

| Project | What it does | Optional LLM enhancement |
|---|---|---|
| [`raml-guard/`](raml-guard/) | CI-gating CLI that diffs two RAML/OAS spec versions and classifies every change as breaking, non-breaking, or informational — catches accidental breaking changes before they reach Anypoint Exchange consumers. | `--summarize` release-note writer |
| [`mule-munit-scaffold/`](mule-munit-scaffold/) | Reads real Mule 4 flow XML and generates MUnit test skeletons with a mock already wired up for every outbound connector call in the flow — automates the most tedious, most-skipped part of Mule testing. | `--enrich` mock/assertion writer |
| [`mule-connector-audit/`](mule-connector-audit/) | Parses a project's `pom.xml`, queries the live Maven Central API for each connector's actual latest version and release date, and flags stale or possibly-abandoned connectors. | `--explain` summary |
| [`mule-log-tracer/`](mule-log-tracer/) | Groups interleaved Mule/CloudHub log lines by correlation ID and reconstructs a readable, ordered per-request waterfall trace, flagging slow steps, errors, and out-of-order events. | `--explain` summary |

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

Then follow that project's own README for its specific CLI usage, quickstart,
and examples (the three AI-powered tools need `ANTHROPIC_API_KEY` set; the
four deterministic-first tools work out of the box).

## License

Each project carries its own MIT `LICENSE` file. All code in this
repository is MIT licensed, copyright Srinivasarao Polagani.
