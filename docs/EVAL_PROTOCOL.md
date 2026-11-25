# Evaluation protocol

How models answer the tasks and how answers are scored. The same prompts, JSON schema and scorer serve every model; only the model ID and its reasoning setting change. Results on frozen v1 are in [reports/v1/findings.md](reports/v1/findings.md). Claims there speak only about the Claude models we tested: Haiku 5.5, Sonnet 5.5 and Opus 5.5 at effort high, and Opus 5.5 at effort xhigh.

## What the model sees

- **One user turn:** the task image (PNG at native resolution, 1024 x 768, base64), then the question text. Nothing else: no scene file, no object list, no examples.
- **One system prompt per answer type** (int, object, set, bool, number), from `packages/vtf/src/vtf/eval/prompt.py`. Each says what kind of answer is wanted and asks for only `{"answer": ...}`. Object and set prompts give the naming rule (a short everyday noun, plus colour and the size word small or large only when needed to tell objects apart, and the colour list) with no example answer, so no answer is anchored. Number prompts ask for centimetres.
- **Structured output:** the reply must match a JSON schema `{"answer": <typed value>}`. If a provider rejects the schema, the adapter retries once without it and the scorer extracts the JSON from the text.
- **Prompt version 0.2.0** is part of the cache key; a test hashes every prompt and schema and fails until the version is bumped, so a cached reply is never reused for a changed prompt.
- **Blind mode** (G5 baseline, `--blind`): the same model and settings with no image and a best-guess prompt; filed under `<model>__blind` with its own prompt version (0.2.0+blind.1), never mixed with image runs.
- **Scale stated** (`vtf.eval.scale`, `scripts/ask_sweep.py --condition scale`): tasks made before programs 0.3.0 get the sentence that states the scale in front of their distance or free-space question (the 1 cm grid, or the reference object's exact size); everything else is the standard request. Cached under prompt version 0.2.0+scale.1 and filed under `<run>/sweep_scale/`, never mixed with the original answers. From programs 0.3.0 the sentence is part of the question itself. Result: [reports/v1/scale_stated.md](reports/v1/scale_stated.md).

## Scoring

The scorer (`packages/vtf/src/vtf/eval/scorer.py`) never asks a model. It pulls `{"answer": ...}` from the reply, normalises it with the same vocabulary the question programs use (`vtf.core.vocab`), and compares it with the program's answer:

| Answer type | Families | Correct when |
| --- | --- | --- |
| int | count, count by colour | Exact |
| bool | free space | Exact |
| number (cm) | distance | Within max(2 cm, 10% of the truth); an explicit mm or m converts |
| object | nearest to gripper, front to back, relation, support, occlusion, gripper view | The colour, size and noun words pick out exactly one object visible in the image (20 or more pixels), and it is the true one. "the red mug" is right when the truth is "mug" and only one mug is visible; "table" answers support |
| set | containment, reachability | Every item resolves to one visible object and the resolved set equals the truth; "none" or [] is empty |

A reply with no readable answer, or one naming nothing in the image, counts as wrong and is tallied as a parse failure. The `fake-oracle` model returns the true answer from task.json and must score 100%; `vtf quickstart` runs it on every task as a check of the scorer.

## Settings per model

From [configs/models.yaml](../configs/models.yaml). Model IDs were confirmed with one smoke call each (ID resolves, image accepted, schema output parses) before the sweep.

| Key | Model ID | Thinking | Effort | max_tokens | Timeout | Price in / out per MTok |
| --- | --- | --- | --- | --- | --- | --- |
| claude-haiku-5-5 | claude-haiku-5-5 | adaptive | high | 16,000 | 120 s | $0.10 / $0.50 |
| claude-sonnet-5-5 | claude-sonnet-5-5 | adaptive | high | 16,000 | 120 s | $2 / $10 |
| claude-opus-5-5 | claude-opus-5-5 | adaptive | high | 16,000 | 120 s | $4 / $20 |
| claude-opus-5-5-xhigh | claude-opus-5-5 | adaptive | xhigh | 64,000 | 1,200 s | $4 / $20 |

No temperature, top_p or top_k is sent: API version 2026-10-21 rejects them on every model. Effort is sent on every call (Opus 5.5 and Haiku 5.5 default to medium). Each result records the model ID, API and SDK version, effort, max_tokens, thinking tokens and whether it came by a standard call or a batch. The Anthropic adapter refuses any model ID containing fable or mythos; those models are never called.

## Which tasks

- **v1 eval sample:** 1,500 tasks, a seeded stratified sample with an equal share per family x camera rig (`vtf eval out/v1 --sample 1500`, seed 0); its task list is `eval/claude-sonnet-5-5/selection.json` in the release.
- **Sweep set:** those 1,500 plus the 150 audit tasks (67 not already in the sample): 1,567 tasks, answered by all four settings (`scripts/model_sweep.py`, task list in `sweep/task_set.json`).
- **Intervals:** 95% bootstrap intervals. The findings resample whole scenes (2,000 resamples, the same draws for every model, a G6 twin counted with its source scene), because tasks from one scene are correlated.

## Batching and caching

- **Cache:** every reply is stored in SQLite (`out/cache/eval_cache.sqlite`), keyed by task ID, model key and prompt version. Task IDs are content hashes, so a cached reply serves the same task in any run folder; reruns make no calls and cost nothing. Failed calls are never cached, so a rerun retries them.
- **Message Batches:** the sweep sends each request through the Message Batches API at half price. Batch request bodies are byte-identical to the standard call's (asserted per request and tested), so a batched and a standard reply to one task share one cache entry. Batches hold at most 100 requests and 150 MB; every batch ID is saved to `sweep/state.json` before the next is sent, so a resumed driver collects a batch instead of re-sending it.
- **Gate per model:** each model's first 100 tasks (a seeded shuffle) go as one batch; error or truncation above 5% pauses that model, and the mean cost of those 100 projects its total. Result on v1: 4 x 1,567 replies, 0 errors, 0 truncations.
- **Standard calls:** the runner (`vtf eval`) uses a thread pool (4 requests at a time) and the SDK's retries (408, 409, 429, 5xx with backoff, 6 retries).

## Costs

| Model | Tasks | Input tokens | Output tokens (thinking) | Cost |
| --- | --- | --- | --- | --- |
| Sonnet 5.5 high | 1,567 (1,500 standard, 67 batched) | 2,176,380 | 275,613 (11,335) | $6.96 |
| Haiku 5.5 high | 1,567 batched | 2,176,380 | 470,390 (451,216) | $0.23 |
| Opus 5.5 high | 1,567 batched | 2,176,380 | 284,535 (266,035) | $7.20 |
| Opus 5.5 xhigh | 1,567 batched | 2,176,380 | 716,437 (697,885) | $11.52 |

The sweep came to $25.90 (out/v1/sweep/costs.json); with development runs, the blind baseline and smoke calls, model spend so far is about $28.75, plus $0.09 for the 20-task Sonnet run in `vtf quickstart`. A task costs about 1,390 input tokens, nearly all of it the image.

## Adding a provider

1. Write an adapter in `packages/vtf/src/vtf/eval/adapters/`: a subclass of `Adapter` whose `answer(image_path, question, answer_type)` sends the image and question with `system_prompt(answer_type)` and `response_schema(answer_type)` from `vtf.eval.prompt`, and returns an `AdapterResponse` with the raw text, tokens, latency and stop reason. Adapters never parse or score. Raise `FatalAdapterError` for errors every call would hit (bad key, unknown model).
2. Register it in `make_adapter` (`adapters/__init__.py`) under a provider name, with the settings it accepts.
3. Add an entry to `configs/models.yaml`: key, provider, model_id, price, settings, and `verified: true` once a smoke call has confirmed the ID. The Google, OpenAI and open-model entries there are unverified placeholders; no adapter for them is built yet.
4. Run a small gate first (`uv run vtf eval out/v1 --model <key> --sample 100`), check errors and the projected cost, then the full set. Cache, scorer, summaries, the report and the Task Explorer need no change.
