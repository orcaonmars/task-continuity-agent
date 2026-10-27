# Reproducible task and recovery checks

Use a separate task folder with recoverable test data. Declare inputs, expected outcomes, and stop conditions before running. Do not publish personal paths, prompts, credentials, browser profiles, or private task contents.

## Record the environment

Include the commit, Windows/Python versions, model/backend versions, model quantization, context size, output-token budget, reasoning settings, hardware, enabled capabilities, and relevant timeout overrides. Report the actual elapsed duration and interventions. Keep simulated provider tests separate from real model trials.

## Small baseline

| Case | Expected outcome |
| --- | --- |
| File task | Write exact text, read it back, and finish with the same nonempty file/hash. |
| Small coding task | Make a bounded change and run a deterministic test that checks the requested behavior. |
| Actor interruption | Interrupt reasoning during a tracked job. The same job identity and saved objective survive; inspect uncertain actions before retrying. |
| Supervisor interruption | Verify watchdog recovery, unchanged task identity/checkpoint, and no duplicate launch. |
| Provider outage | Make a test endpoint unavailable, then restore it. Waiting does not consume actor crash retries; the same task continues. |
| Optional MCP failure | Configure a missing server. A core file task completes without starting it. Selecting extra tools records the error without losing core tools. |
| Explicit cancellation | Cancellation is durable before termination. Owned children exit and a late success cannot replace the stopped result. |
| Text-only model | File/DOM tasks work; no image blocks are sent to inference and no unsupported visual review is claimed. |

The automated suite covers deterministic variants of these cases. Run `scripts/validate.ps1`; Node.js 18+ also runs the included interface checks. A recorded live trial should additionally show the final files, read-back results, job identities, and any model correction attempts.

## Longer trials

Increase duration only after the baseline passes. Use the same declared tasks and checks across releases. Measure memory/handle growth, disk use, worker restarts, duplicate actions, time to recovery, and final task success. Stop for uncontrolled resource growth or unintended actions. A task that finishes early is a success; do not keep it running merely to consume a budget.

Publish attempts and failures alongside successes. Count verified task completions over all attempted tasks and show the sample size. A few short recovery tests are not a measured overnight success rate.

## Reporting

Open a bug report for a reproducible failure or propose a small documentation update with your result. Separate observed facts from your explanation of the cause. Include a minimal sanitized reproduction and the check that distinguishes failure from success.
