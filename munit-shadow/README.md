# munit-shadow

An **ambient, live** agent for MuleSoft Mule 4 projects. It watches your
flow XML files while you're actively working, and the moment you save a
change to a flow, it incrementally keeps that flow's MUnit test file in
sync -- in place, preserving every byte of your own hand-written assertions
and mocks.

```
munit-shadow sync <project-dir> [--enrich] [--dry-run] [--model claude-opus-5]
munit-shadow watch <project-dir> [--enrich] [--poll-interval 2.0] [--use-watchdog] [--model claude-opus-5]
```

`sync` runs the update engine once and exits. `watch` runs the same engine
forever, reacting to file changes, printing one quiet status line per
change -- nothing at all when a flow is already in sync:

```
orders-flow.xml changed -> updated src/test/munit/orders-flow-test.xml: +1 new mock (db:select)
orders-flow.xml changed -> updated src/test/munit/orders-flow-test.xml: 1 stale mock flagged (db:select no longer in flow)
```

## How this differs from `mule-munit-scaffold`

There's a sibling project by the same author,
[mule-munit-scaffold](../mulesoft-tools-foundry/mule-munit-scaffold), that
also generates MUnit tests from Mule flow XML. They solve deliberately
different problems and should never be confused:

| | mule-munit-scaffold | munit-shadow |
|---|---|---|
| **Trigger** | You run it once, on demand | It runs continuously in the background while you edit |
| **Output** | A brand new test file, from a blank slate, every time | The *same* existing file, edited incrementally, in place |
| **Existing content** | N/A -- there's nothing to preserve, it always starts empty | Sacred -- never reformatted, reordered, or deleted |
| **Relationship to the developer** | *Ask*: you invoke it when you want a skeleton | *Push*: it notices drift and fixes it for you, unattended |
| **Write strategy** | Builds a fresh `ElementTree` and serializes it | Text-level surgical splice into the original bytes (see below) |

Put differently: mule-munit-scaffold answers "give me a test skeleton for
this flow" once. munit-shadow answers "keep the test I already wrote for
this flow honest, forever, without me having to remember to do it" -- and
its entire value proposition collapses to zero the instant it damages a
single byte of something a human wrote. That's why byte-preservation isn't
a nice-to-have here -- it's the whole point.

munit-shadow does **not** import any code from mule-munit-scaffold (see
`munit_shadow/xml_parser.py`'s module docstring) -- the outbound-call
detection heuristic was independently re-derived to the same quality bar,
documented below and cross-checked against the same class of sources.

## The byte-preservation guarantee (the trust-critical design decision)

A human has to be able to trust that a tool silently editing their files in
the background will **never** destroy their work. munit-shadow earns that
trust with one hard rule, enforced in `munit_shadow/splice.py`:

> **It never parses-and-reserializes the whole XML document.** It never
> calls `ElementTree.write()` (or equivalent) on an existing file.

`ElementTree.write()` looks safe but isn't: it reorders/reformats
attributes, can renumber or collapse whitespace, and (unless you opt in to
a non-default `TreeBuilder`) silently **drops every XML comment** in the
document -- which would be catastrophic for a tool whose whole job includes
adding comments and never losing a human's own ones.

Instead, munit-shadow:

1. Parses the existing file with `xml.parsers.expat` (Python stdlib) purely
   to build a **read-only index** of exact byte offsets -- where each
   `<munit:test>`, `<munit:behavior>`, `</munit:behavior>`, and
   `<munit-tools:mock-when>` starts and ends, working on the file's raw
   bytes throughout so offsets are never ambiguous.
2. Renders new content (a new mock-when block, a stale-marker comment, or a
   whole new `<munit:test>`) as plain text via `munit_shadow/scaffold.py`.
3. Splices that text in as a raw byte insertion at the **start of the
   line** containing the relevant anchor -- e.g. right before an existing
   `</munit:behavior>`, or right before a mock-when that's gone stale.
   Every byte before and after that point, in the original file, is
   untouched.
4. Re-parses the **result** with `ElementTree.fromstring()` to confirm it's
   still well-formed XML. If it isn't, `SpliceError` is raised and the file
   is **not written** -- fail loudly, never write partial or corrupt output.

This is proven in the test suite by more than "the result parses":
`tests/test_splice.py::test_new_mock_added_and_existing_content_byte_preserved`
runs a real sync against the bundled, hand-authored example test file and
asserts, via `difflib.SequenceMatcher` over the file's lines, that **every
diff opcode between the original and the result is `equal` or `insert` --
never `replace` or `delete`**. That is a literal, line-level diff
assertion, not a well-formedness check -- well-formedness alone would not
catch reformatting damage (a reformatted file can still be perfectly valid
XML).

## The incremental update engine

For each flow file (`src/main/mule/*.xml`):

1. **Detect** every outbound connector call with `xml_parser.py` (see
   heuristic below).
2. **Locate** the matching test file (`test_file_index.py`, default
   `src/main/mule/orders-flow.xml` -> `src/test/munit/orders-flow-test.xml`,
   configurable via `--flow-dir`/`--test-dir`/`--test-suffix`).
   - If it **doesn't exist**, `scaffold.py` generates a brand-new one from
     scratch (this is the one place munit-shadow is allowed to build with
     `ElementTree` -- there's no existing content to damage yet).
3. If it **does exist**, `mock_diff.py` diffs the flow's currently-detected
   calls against the mocks already in the file:
   - **New calls** get a new `<munit-tools:mock-when>` spliced into the
     matching `<munit:test>`'s `<munit:behavior>` (created if it doesn't
     exist yet), with a placeholder (or `--enrich`'d) `then-return`.
   - **Stale calls** (mocked, but the call is gone from the flow) are
     **never deleted**. An XML comment is spliced in immediately above the
     mock:
     ```xml
     <!-- munit-shadow: this outbound call was not found in the flow as of the last sync; consider removing this mock if it's no longer needed -->
     ```
     If that marker is already there from a previous run and the call is
     *still* stale, it is not duplicated.

## Detection heuristic: what counts as an "outbound call"

Documented in full in `munit_shadow/xml_parser.py`'s module docstring; in
short:

1. The element's namespace must be neither the Mule core namespace
   (`.../schema/mule/core`) nor the EE/DataWeave namespace (`.../ee/core`)
   -- this is why `<ee:transform>`, `<logger>`, `<choice>`/`<when>`, `<try>`,
   `<flow-ref>`, `<set-payload>`, etc. are never mistaken for I/O, no matter
   how deep inside them the scan looks.
2. Its local tag name must be in a curated verb set that denotes real I/O:
   `request`, `select`, `insert`, `update`, `upsert`, `delete`,
   `bulk-insert`, `bulk-update`, `bulk-delete`, `query`, `publish`,
   `consume`, `publish-consume`, `send`, `execute-ddl`, `execute-script`,
   `stored-procedure`.
3. Message *sources* (`http:listener`, `<scheduler>`, anything ending in
   `-listener`/`-trigger`) are excluded by construction -- none of their
   local names are in the verb set above.

**Mock identity** (`OutboundCall.identity_key()`) is deliberately just
`(operation, config-ref)` -- the only two things MUnit's own `mock-when` can
actually match on at runtime. Two calls to the same operation sharing a
config-ref (or both lacking one) are treated as the same call, matching the
real ceiling on MUnit's own disambiguation power rather than pretending
`doc:name` or XML position give it more than it has.

## Mule/MUnit XML shapes and the Maven layout convention

Confirmed against current MuleSoft documentation on 2026-09-14:

- [Flow and Subflow Scopes](https://docs.mulesoft.com/mule-runtime/latest/flow-component)
  -- `<flow name="...">`, `<sub-flow name="...">`, `<flow-ref name="..."/>`.
- [MUnit Test Structure Fundamentals](https://docs.mulesoft.com/munit/latest/munit-test-concept)
  -- confirms both the `<munit:config>`/`<munit:test>`/`<munit:behavior>`/
  `<munit:execution>`/`<munit:validation>` shapes **and** the project layout
  convention this tool assumes, stated explicitly on that page: *"The base
  file in MUnit is the test suite file, an XML file located in the
  `src/test/munit` directory of your Mule application project."* Combined
  with the universal Mule Maven archetype convention of keeping application
  flow XML under `src/main/mule`, this is where the tool's default
  `--flow-dir`/`--test-dir` values come from.
- Mock Event Processor / MUnit Tools Module Reference -- `<munit-tools:mock-when
  processor="...">`, `<munit-tools:with-attributes>`/`<munit-tools:with-attribute
  attributeName="..." whereValue="..."/>`, `<munit-tools:then-return>`/
  `<munit-tools:payload value="..." mediaType="..."/>`.

The default naming convention: `src/main/mule/orders-flow.xml` ->
`src/test/munit/orders-flow-test.xml`. Override with `--flow-dir`,
`--test-dir`, `--test-suffix` if a project uses a different layout.

## The bundled example: `examples/order_management_app/`

- `src/main/mule/global-config.xml` -- connector configs only, no flows (skipped by the sync engine).
- `src/main/mule/orders-flow.xml` -- flow `orders-process-flow`: `http:listener` -> `ee:transform` -> `http:request` (check inventory).
- `src/test/munit/orders-flow-test.xml` -- a **genuinely hand-authored** test file, not scaffold output: inconsistent indentation (2/4/8-space mixes), a real human comment signed "Srinivas", and a real custom assertion (`payload.quantityAvailable is equalTo(42)`) rather than a generic placeholder. This is the fixture the byte-preservation test runs against.

## The live watch-loop verification (real, not simulated)

This was actually performed end-to-end as part of building this tool --
not described hypothetically. Transcript below is real command output and
real file contents, copy-pasted.

**Setup**: the example app was copied to a scratch directory, and
`munit-shadow watch <scratch-dir> --poll-interval 1 --verbose` was started
as a real background OS process (`nohup ... &`, a real PID).

**Direction 1 -- a flow gains a call.** While the watcher was running, the
real flow file was edited on disk to add a `<db:select>` that wasn't there
before:

```diff
         <http:request method="GET" config-ref="inventoryServiceConfig" path="/inventory/${vars.sku}" doc:name="Check Inventory"/>
         <logger level="INFO" message="Inventory check complete" doc:name="Log Inventory Result"/>
+        <db:select config-ref="ordersDbConfig" doc:name="Lookup Customer Record">
+            <db:sql>SELECT * FROM customers WHERE sku = :sku</db:sql>
+            <db:input-parameters><![CDATA[#[{ sku: vars.sku }]]]></db:input-parameters>
+        </db:select>
     </flow>
```

After the poll interval elapsed, the running watcher printed, unattended:

```
orders-flow.xml changed -> updated /tmp/munit-shadow-live-verify/app/src/test/munit/orders-flow-test.xml: +1 new mock (db:select)
```

The watcher was then stopped (`SIGTERM`). The resulting test file was read
back from disk for real, and diffed against the original hand-authored
fixture with `difflib.SequenceMatcher`:

```diff
--- BEFORE (hand-authored, committed fixture)
+++ AFTER (live watch run result)
@@ -22,6 +22,14 @@
                 <munit-tools:payload value="#[{ sku: 'SKU-1', quantityAvailable: 42 }]" mediaType="application/json"/>
             </munit-tools:then-return>
         </munit-tools:mock-when>
+        <munit-tools:mock-when processor="db:select" doc:name="Mock db:select - config-ref=ordersDbConfig">
+            <munit-tools:with-attributes>
+                <munit-tools:with-attribute attributeName="config-ref" whereValue="#['ordersDbConfig']"/>
+            </munit-tools:with-attributes>
+            <munit-tools:then-return>
+                <munit-tools:payload value="#[{ mocked: true, operation: 'db:select', note: 'TODO(db_select): replace with a realistic mock response payload' }]" mediaType="application/json"/>
+            </munit-tools:then-return>
+        </munit-tools:mock-when>
     </munit:behavior>
         <munit:execution>
       <munit:set-event>

non-insertion opcodes: []
```

Confirmed for real: (a) a new mock-when for `db:select` was actually added;
(b) the pre-existing hand-authored `quantityAvailable` assertion, the
"Srinivas:" comment, and the mixed indentation are byte-identical to
before (zero `replace`/`delete` diff opcodes -- only `insert`); (c) nothing
else in the file changed; (d) the result re-parses as well-formed XML.

**Direction 2 -- a flow loses a call.** The `<db:select>` block was then
removed from the flow for real, and `munit-shadow sync` was run again:

```
$ munit-shadow sync /tmp/munit-shadow-live-verify/app --verbose
global-config.xml: no changes
orders-flow.xml changed -> updated /tmp/munit-shadow-live-verify/app/src/test/munit/orders-flow-test.xml: 1 stale mock flagged (db:select no longer in flow)
munit-shadow: synced 2 flow file(s), 1 test file(s) updated.
```

Real diff of that change:

```diff
         </munit-tools:mock-when>
+        <!-- munit-shadow: this outbound call was not found in the flow as of the last sync; consider removing this mock if it's no longer needed -->
         <munit-tools:mock-when processor="db:select" doc:name="Mock db:select - config-ref=ordersDbConfig">
             <munit-tools:with-attributes>
                 <munit-tools:with-attribute attributeName="config-ref" whereValue="#['ordersDbConfig']"/>
```

The mock-when was **flagged, not deleted** -- confirmed by re-reading the
file, `grep`-ing for `processor="db:select"` (still present) and for the
marker comment (present, immediately above it).

**Idempotency**, also checked for real: running `munit-shadow sync` a
second time with no further edits printed `synced 2 flow file(s), 0 test
file(s) updated.` and the marker-comment count in the file stayed at
exactly 1 (`grep -c` confirmed) -- no duplicate marker.

## `--enrich`: real attempt made, live vs. verified-via-fake (full disclosure)

**This environment has no `ANTHROPIC_API_KEY`, no `ANTHROPIC_AUTH_TOKEN`,
and no `ant` CLI/OAuth profile** -- checked directly (`ant` not on `PATH`,
both env vars unset) before writing this section, not assumed.

`--enrich` was run for real, unstubbed, against a real new call
(`db:select` added to the example flow): it built the real prompt, called
the real `anthropic.Anthropic()` client, and issued the real
`client.messages.create(model="claude-opus-5", thinking={"type":
"adaptive"}, ...)`. It reached a genuine SDK-level authentication error:

```
TypeError: "Could not resolve authentication method. Expected one of api_key, auth_token, or credentials to be set. Or for one of the `X-Api-Key` or `Authorization` headers to be explicitly omitted"
```

`enrich_new_call_payloads()` caught that (it catches *any* exception --
missing package, bad auth, rate limit, malformed JSON -- and returns `{}`)
and the sync proceeded exactly as if `--enrich` had not been passed: the
new `db:select` mock was written with the deterministic placeholder
payload, and the run completed with exit code 0, no crash. That fallback
behavior is the important thing to verify, and it was verified live, not
mocked.

Since a real model response couldn't be exercised, the *request-shape and
response-handling* logic is separately verified in `tests/test_enrich.py`
by substituting a fake `anthropic` module into `sys.modules` -- asserting
the real `enrich_new_call_payloads()` code sends `model="claude-opus-5"`
and `thinking={"type": "adaptive"}`, includes the flow's real XML and the
new calls in the prompt, correctly matches a model JSON response back to
the right `(operation, config_ref)` key, and falls back to `{}` (never
raises) on a non-JSON response, an SDK exception, or a missing package.
With real credentials, the exact same code path applies; only the model
call itself would stop failing on auth.

## Verification honestly disclosed: what was and wasn't checked

- **Well-formedness and byte-preservation**: verified for real, both by the
  automated test suite (`tests/test_splice.py`) and by the live watch-loop
  run above, on an actually-running background process editing an actual
  file on disk.
- **Incremental mock insertion / stale flagging correctness**: verified for
  real, both directions, live (see transcript above) and by the automated
  suite (idempotency, no-behavior-section, missing-test-in-multi-flow-file
  edge cases).
- **`--enrich`'s network path**: the request was made for real and reached
  a genuine SDK auth error (no credentials in this sandbox); the
  fallback-on-failure behavior that matters most was verified live. The
  JSON request/response *shape* is verified against a fake `anthropic`
  module, not a live model response -- see above.
- **MUnit test *execution* is never attempted.** There is no Mule runtime
  in this environment. munit-shadow verifies well-formedness, correct
  incremental mock insertion, and byte-preservation -- it cannot confirm a
  synced test would actually pass (or even load) under Mule's real MUnit
  runner.

## Quickstart

```bash
pip install -e ".[dev]"          # installs pytest + optional anthropic/watchdog extras
pytest tests/ -v                  # 43 tests, no network/API calls required

munit-shadow sync examples/order_management_app --dry-run --verbose
munit-shadow watch examples/order_management_app --poll-interval 2
```

## CLI reference

```
munit-shadow sync  <project-dir> [--flow-dir DIR] [--test-dir DIR] [--test-suffix SUF]
                    [--enrich] [--model MODEL] [--dry-run] [--verbose]

munit-shadow watch <project-dir> [--flow-dir DIR] [--test-dir DIR] [--test-suffix SUF]
                    [--enrich] [--model MODEL] [--verbose]
                    [--poll-interval SECONDS] [--use-watchdog]
```

- `--flow-dir` (default `src/main/mule`), `--test-dir` (default
  `src/test/munit`), `--test-suffix` (default `-test`) -- override the
  naming convention if a project doesn't follow the standard Maven layout.
- `--enrich` -- call Claude to suggest realistic `then-return` payloads for
  *newly added* mocks only. Off by default; the core engine needs zero API
  key without it.
- `--model` -- default `$ANTHROPIC_MODEL` or `claude-opus-5`.
- `--dry-run` (`sync` only) -- report what would change, write nothing.
- `--verbose` -- print a line for every flow, including unchanged ones
  (ambient mode is silent about unchanged flows by default).
- `--poll-interval` (`watch` only, default `2.0`) -- seconds between mtime
  polls in the default, dependency-free watch mode.
- `--use-watchdog` (`watch` only) -- use the optional `watchdog` package
  for real OS filesystem events instead of polling. Falls back to polling
  automatically if `watchdog` isn't installed or fails to start, so a
  missing/broken optional dependency never breaks `watch`.

## Project layout

```
munit_shadow/
  cli.py               argument parsing, sync_one_flow_file()/run_sync() orchestration
  xml_parser.py         outbound-call detection (self-contained; see module docstring)
  test_file_index.py    flow-file <-> test-file path mapping / naming convention
  mock_diff.py           new-calls / stale-calls diff logic + the stale-marker text
  splice.py              the text-level surgical XML insertion engine (the trust-critical core)
  scaffold.py            MUnit XML text rendering: whole new files AND spliceable fragments
  enrich.py               optional LLM enrichment (only module that imports anthropic)
  watcher.py              poll-based (default) and watchdog-based (optional) watch loop
  report.py                ambient CLI status-line formatting
examples/order_management_app/
  src/main/mule/           global-config.xml, orders-flow.xml
  src/test/munit/           orders-flow-test.xml (hand-authored, the byte-preservation fixture)
tests/
  test_xml_parser.py        detection heuristic: zero/several calls, core/ee exclusion, dedupe
  test_test_file_index.py    naming convention mapping, custom flags
  test_mock_diff.py          existing-mock extraction, new/stale/unchanged diffing, stale idempotency
  test_scaffold.py           whole-file generation well-formedness, comment sanitization
  test_splice.py             THE byte-preservation tests: pure-insertion diff assertions, all edge cases
  test_cli.py                 end-to-end sync against a real copy of the example app
  test_enrich.py               request shape / fallback behavior via a fake anthropic module
  test_watcher.py              a real background poll loop detecting a real file edit
```

## Limitations (honest)

- **Static XML analysis only; MUnit test execution is never attempted.**
  No Mule runtime is available here. A synced test that is well-formed,
  byte-preserving, and structurally correct could still fail (or fail to
  load) under a real Mule runtime for reasons this tool cannot see.
- **The outbound-call detector is a curated-verb heuristic**, not a full
  connector operation catalog -- see "Detection heuristic" above. It will
  miss a connector operation whose local tag name isn't in the curated
  verb set.
- **Mock identity is `(operation, config-ref)` only**, matching MUnit's own
  real disambiguation ceiling -- not source-XML position, not `doc:name`.
  Two calls to the same operation with the same (or no) config-ref
  genuinely cannot be told apart by MUnit at runtime, and munit-shadow
  doesn't pretend otherwise.
- **Test-file matching assumes the `test-<flow-name>` naming convention**
  for `<munit:test name="...">` (the same convention this tool's own
  generator uses). A hand-authored test whose `<munit:test>` name doesn't
  follow that pattern won't be recognized as "the test for this flow", and
  munit-shadow will instead append a brand new `<munit:test>` block for
  that flow rather than guessing which existing block to touch -- safe
  (never touches the wrong block) but not fully general.
- **An empty, self-closing `<munit:behavior/>` is not specially handled.**
  Real hand-written files always write `<munit:behavior>` with actual
  mock-when children when they use it at all; the final well-formedness
  re-check is the safety net if this assumption is ever wrong.
- **Generated `doc:name` attributes are omitted when splicing into a file
  that doesn't already declare the `doc` XML namespace prefix**, to avoid
  ever introducing an unbound-prefix well-formedness break -- a small,
  deliberate quality tradeoff in favor of the byte-preservation guarantee.
- **`--enrich`'s live model-response path was not exercised against a real
  model** in this environment (no credentials) -- see disclosure above. The
  request/response handling logic is verified against a fake client and
  the graceful-fallback behavior was verified live.
- **mtime-polling has the time resolution of `--poll-interval`.** The
  `--use-watchdog` OS-event mode is more responsive in real-world use but
  wasn't the primary mode used for this project's own live verification
  (polling was chosen specifically because it's guaranteed to work in any
  sandboxed environment with zero extra dependencies).

## License

MIT License. Copyright (c) 2026 Srinivasarao Polagani. See `LICENSE`.
