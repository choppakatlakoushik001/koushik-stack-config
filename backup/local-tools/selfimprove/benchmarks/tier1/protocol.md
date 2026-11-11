# Frozen synthetic evaluation protocol v1

This suite contains 30 original tasks: ten arithmetic, ten string, and ten list
functions. All inputs, instructions and expected outputs are synthetic. They
contain no personal data, host files, network resources or external dependencies.
Each case asks for one Python function named solve with the supplied signature.
The prompt is the instruction followed by the signature and the checks, in their
stored order. Keep those prompts and all original case IDs fixed for comparison.

A trusted evaluation runner supplies each generated solve callable to score_case.
An original case passes only if every check succeeds with the expected Python
value and exact result type. Exceptions and missing/noncallable implementations
fail. Do not treat child tasks or retry attempts as extra successful samples.
Measure elapsed time with a monotonic clock from the beginning of the original
case, including every retry, child task, and scoring check; record minutes even
for failures. Use seed 0 and a fresh generation context for each original case.
Use the same model, model file hash, generation/runtime configuration, hardware
and runner version before and after the setting change. Record those identities
in the evaluation run evidence. Do not compare runs with different identities.
There is no benchmark runner or sandbox in this suite; generated code requires a
separately reviewed isolated evaluator before execution.

Settings in settings/qwen.json are local proposals only. No installed driver or
router consumes them. Changing a proposal alone cannot improve model behavior;
these synthetic cases establish a fixed scoring contract for future evaluation,
not evidence of Qwen improvement. Do not fabricate baseline or trial outcomes.
Collect actual measurements using si.py record --benchmark NAME --case CASE_ID
only when an approved isolated evaluator really applies that proposal. Baseline
and trial each require all 30 original cases. If routing is later evaluated,
aggregate split children into their original case before scoring and timing.

The manifest sha256 field fingerprints sorted input paths and bytes using
8-byte unsigned big-endian length framing for each UTF-8 path and byte string.
The controller verifies that input fingerprint, then separately hashes the full
manifest bytes and those inputs for the trial identity. This avoids a digest that
would have to contain its own hash. The scorer, protocol and full case definitions
are frozen inputs. Any change invalidates the current pinned input fingerprint;
revising it is an explicit evaluation-policy change and also changes trial identity.
