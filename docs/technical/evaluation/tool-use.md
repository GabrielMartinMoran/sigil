# Tool-use evaluation

Use `scripts.evaluate_tool_use` to smoke-test the tool pipeline offline or run
an explicit provider/model against a fixed local workflow. Passing software
tests does not establish that a model uses tools more accurately or efficiently.

## Offline smoke test

The default mode uses scripted model responses with real file tools. It checks
that the harness measures task success, requests, tool rounds, tool calls,
validation retries, duration, and estimated history tokens.

```sh
uv run python -m scripts.evaluate_tool_use
```

The report sets `model_quality_measured` to `false`. This is a deterministic
integration check, not a comparison of model quality.

## Live comparison

An explicit model enables live provider requests. Use the provider's normal
runtime credential configuration; the script does not load credential files.
These requests can incur charges.

```sh
uv run python -m scripts.evaluate_tool_use \
  --model 'provider:model' --repetitions 3 > /tmp/tool-use-report.json
```

For each repetition, the harness runs the same file-summing task with direct
file tools and then with `execute_code` also available. Each run has fresh
fixtures in a temporary directory, a request/tool-call budget, and a wall-clock
deadline. The report separates provider usage from local token estimates. It
records error types without storing credentials, provider error payloads, or the
complete model transcript.

Success requires the expected output file and unchanged input files. It does not
prove that the model performed the requested final verification read. The
comparison counts outer tool calls, not nested broker requests. It uses one
scenario and does not recreate the removed file-tool schemas. Therefore, it
cannot establish a general improvement or a legacy-versus-new-tool result.

Compare success rates before comparing rounds, calls, tokens, or duration.
Report failures and variation across repetitions, and retain the exact provider
model identifier. Run configurations consecutively to reduce resource
contention. No live-model result has been recorded as part of this change.

---

[Documentation index](../../README.md) · [Technical documentation](../README.md)
