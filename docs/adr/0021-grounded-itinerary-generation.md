# 21. Grounded itinerary generation

Date: 2026-11-06 · Status: Accepted

## Context
`POST /api/ai/itineraries` turns a sentence, dates, a party size and a budget into a day-by-day plan. A tour that isn't in the
catalogue, a departure with no seats or a plan over budget is a defect, and a language model will produce all three sooner or
later. The stack also has to run, and CI has to pass, with no API key and no spending.

## Decision
**The model proposes and code disposes.** Parse, retrieve, plan, validate, repair at most twice, then fall back:

1. **Grounding.** Hybrid retrieval with the trip's dates and party size as filters gives at most 30 tours. Their departures
   and the road distances between them (PostGIS) are the whole catalogue for this request.
2. **Structured output, with no free text.** The schema holds only ids, numbers and timestamps: nothing a model writes reaches
   a traveller as words. Names, prices and times in the response come from the catalogue, and travel times from PostGIS.
3. **A validator that trusts nothing,** with one rule per way a plan can be wrong: unknown tour or departure, a departure of
   another tour, outside the trip, ending after it, the wrong day, not enough seats, a misreported price, start or duration,
   over budget, overlap, an infeasible road journey, a repeated tour, and an empty plan when something fits. Road time is
   straight-line distance at 60 km/h, deliberately below the 100 km/h gravel limit, because roads aren't straight.
4. **Repair,** at most twice, feeding back the violations. Those messages are built only from rule names, ids and numbers.
5. **A deterministic planner,** greedy and constraint-respecting, for when repair fails, a provider fails, the quota is spent
   or Redis is down, and for everything when no provider is configured. Its output goes through the same validator. The
   response always says which path produced it (`path`, `fallbackReason`), so a fallback is never mistaken for model output.

**Providers sit behind one small interface:** Anthropic through its official SDK, any OpenAI-compatible endpoint (which covers
a local Ollama), and a Fake that needs no network. Config chooses, and the default is none.

- Anthropic uses `output_config.format`, not forced tool use, which current models reject with a 400. Default
  `claude-opus-5-5` at `medium` effort, configurable. Server-side refusal fallback (`fallbacks: "default"`) is on by default
  and `ANTHROPIC_FALLBACKS=` turns it off.
- OpenAI-compatible endpoints use plain HTTP and `response_format: json_schema`. No second SDK for one POST.

**Retries are ours, not the SDK's:** at most two, on 5xx, timeouts and connection failures, inside an overall deadline. No 4xx
is retried, 429 included: the deterministic planner can answer now, which beats making a traveller wait out a rate limit.

**Cost control is a reservation, not a check afterwards.** Before each call, a Lua script in Redis reserves the most the call
could cost against the user's daily budget, atomically, so concurrent requests can't overshoot. The difference is returned
afterwards. Over budget, or with Redis down, no model is called. The day is Windhoek's.

**Prompt injection is assumed to succeed sometimes.** Descriptions and the traveller's words travel as JSON in delimiters, with
angle brackets escaped so no value can close one, and the system prompt calls them data. But the defence that matters is that
a fooled model can do no harm beyond a skewed choice among real, valid departures. See [the threat model](../threat-model.md).

**Streaming** is Server-Sent Events on the same endpoint when the client sends `Accept: text/event-stream`, otherwise JSON.
No database connection is held during a model call.

## Consequences
- **A plan is always valid and can be dull.** The fallback is greedy by retrieval rank, with no sense of a pleasant trip. It
  covers days and stays within budget, which is the promise it makes.
- **Prose is gone.** A model could write a nice paragraph about the trip, and the schema forbids it. That is the price of having
  no channel for injected text to reach a traveller. If wanted later: generate it only from validated catalogue fields, and
  treat it as untrusted output.
- **Real providers are unmeasured here.** No key was used, so the code is tested against a local stub through the real SDK and
  the [itinerary benchmark](../benchmarks/itineraries.md) reports only the deterministic planner and the Fake. The Fake's
  numbers describe the pipeline's paths, not any model's ability. Setting `LLM_PROVIDER` and rerunning fills in the rest.
- **Skewed choices aren't detected.** An operator's description could still persuade a model to prefer their tour: the plan
  would be valid. Accepted while operators are trusted; review their text before opening sign-up.
- **Thinking counts against `max_tokens`,** so the 16,000 default is reserved per attempt. Against the 200,000 daily default, a
  user can be refused for quota earlier than their real usage implies, because each in-flight reservation holds the maximum.
- **GenAI metrics follow conventions that are still in development** (`gen_ai.client.inference.*`, moved to their own
  repository in 2026), so names may change again.
