# Baseten notes: Muse Spark Open (`meta-models/muse-spark-open-1026`)

Probed 2026-10-27 via `scripts/probe_baseten.py` against
`https://inference.baseten.co/v1` (OpenAI-compatible, Bearer auth).
22 probe calls, 39,061 tokens (27,687 in, 11,374 out, 9,995 reasoning).
No API key is stored anywhere in this repo; set `BASETEN_API_KEY` in `.env`.

## WARNING: price unknown

Baseten bills separately from the Meta credit and the per-token price was
not established during probing. Spend entries for Baseten-hosted calls
record `cost_usd: 0.0` with full token counts, stay out of the Meta daily
spend guard, and the CLIs print a separate `Baseten tokens:` line. Never
treat the 0 as free; check the Baseten invoice for real dollars.

## What works

- Plain chat (HTTP 200). Final answer in `message.content`,
  thinking in `message.reasoning_content`, reasoning count in
  `usage.completion_tokens_details.reasoning_tokens`.
- Tool calling with OpenAI-style `tools`. `arguments` arrives as a JSON
  string, `id` values are present (`chatcmpl-tool-<uuid>`), several
  calls can come back at once (2 observed for a two-host isolate ask).
- `tool_choice` with a function object is accepted and forces the call.
- `reasoning_effort` (`low`, `medium`) is ACCEPTED, not rejected, and it
  collapses thinking: omitted spent 325 reasoning tokens on the blue
  turn while `low` spent 10 and `medium` spent 9 (single samples).
  The client keeps sending it; the default (`low`) is the cheap path.
- `temperature` 0.5 and 0.0 are accepted (HTTP 200 throughout).
- Blue-style turn with the real `SYSTEM_PROMPT` + `TOOL_SCHEMAS` and a
  made-up successful-sign-in alert returns a sensible tool call
  (`read_logs` on app, investigating before isolating).

## Quirks (`crucible/llm.py` handles all of these)

1. The model always reasons, and thinking counts against `max_tokens`.
   Measured 8-1323 reasoning tokens per blue turn. Small budgets cut the
   reply: `max_tokens=256` gave 2/3 tool calls, `1024` gave 1/3,
   `1536` gave 3/3 but one sample filled 92% of the budget, `3000`
   gave 3/3. The client floors Baseten open-model calls at 3000.
2. `finish_reason=length` arrives with `content: null` (not `""`),
   reasoning cut to a stub, and no tool calls. The client normalizes
   null to `""`; length replies are never cached; agents fall back to
   the scripted baseline with a note.
3. Tool-call replies also carry `content: null`. Blue's reasoning text
   falls back to `Model chose <tool>.` (existing behavior, no crash).
4. `reasoning_content` is ABSENT (not empty) when reasoning is 0, seen
   with forced `tool_choice`. The client defaults it to `""`.
5. Tool replies are never cached (`_cacheable` needs text), so every
   blue turn is a fresh call on either host. Same as the Meta path.

## Recommended settings

- `--blue-model meta-models/muse-spark-open-1026`
  `--blue-base-url https://inference.baseten.co/v1`
  `--blue-api-key-env BASETEN_API_KEY`
  works on `run_exercise`, `batch`, `campaign`, and `bench`.
- Leave reasoning effort at the default `low`; it cut thinking from
  hundreds of tokens to ~10 per turn in probing.
- Expect ~1.6k prompt tokens per blue turn plus 10-1400 completion
  tokens depending on effort; budget context accordingly.
- Watch the `Baseten tokens:` summary line; totals also live in
  `runs/spend.jsonl` entries with `"host": "baseten"`.

## Unsure / single-sample items

- `medium` behaving exactly like `low` (9 vs 10 tokens) is one sample
  each; the effort scale past `low` is unmapped.
- Forced `tool_choice` skipping reasoning (0 tokens) is one sample.
- `max_tokens=256` beating `1024` (2/3 vs 1/3) is sampling noise from
  3 repeats, not a real ranking; reasoning length varies wildly.
- Temperature 0.0 was accepted but that one sample rambled 509
  reasoning tokens about saying "ok" and hit `length` at 512.
- The blue turn investigated (`read_logs`) instead of isolating on a
  bare alert with no log evidence; whether it isolates with stronger
  evidence on this host is untested (no exercise was run).
