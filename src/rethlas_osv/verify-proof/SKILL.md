---
name: verify-proof
description: Prepare a full Rethlas proof blueprint for submission to the external OSVerify orchestrator at the end of this generation turn; do not submit partial progress as a complete solution.
---

Read the complete original `problem.md` and the current `results/blueprint.md`.
Check that the draft attempts every required conclusion under the original assumptions.
Include the necessary supporting results before their uses and preserve the full target
statement. Partial branches and isolated lemmas must not be submitted as full solutions.

Before submitting, test the draft computationally wherever it can be tested: run
`tools/python` (see AGENTS.md) on the claims it relies on that admit a finite, symbolic
or numerical check, such as small cases of a statement, explicit constructions and their
properties, identities and calculations. A review round costs far more than a check, and
a failed check is cheaper to repair now. Record each check and its outcome in memory.
Skip this only when no claim in the draft admits such a check, and say so in `progress`.

When a full candidate is ready, save it to `results/blueprint.md` and end the turn with
the required JSON response, setting `candidate_ready=true`. The Python orchestrator
will invoke OSVerify and return only independently approved feedback on continuation.
Do not call an HTTP/MCP verification tool or write a purportedly verified output file.

If only partial progress is available, save the complete current draft and remaining obligations in
`results/blueprint.md` and experiment-local memory, and return `candidate_ready=false`.
The orchestrator reviews every turn: false selects partial review, true selects full
review. Partial PASS permits continuation only; it must never trigger final handoff. On revision, consult
`feedback.json`, preserve it exactly in memory, and use its actual evidence to repair
or reconsider the proof route. A repair request is not a proof of the missing claim.
