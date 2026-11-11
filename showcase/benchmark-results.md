# Benchmark results

These are the measured results I retained in local summaries. They are snapshots from small, fixed comparisons on this laptop, not universal model rankings.

## Production Qwen runtime profile

The selected `gpu24_ngram` profile was compared head-to-head with the previous `gpu14_ngram` profile on the same local model and five-case workload.

| Measurement | 24 GPU layers | 14 GPU layers |
| --- | ---: | ---: |
| Generation speed | 9.72 tokens/s | 7.27 tokens/s |
| Time to first token | 8.42 s | 8.35 s |
| Five-case wall time | 25.0 s | 33.2 s |

The quality result was unchanged in that comparison. I kept 24 layers as the default because it reduced total wall time and raised generation speed without changing the measured answers. Startup still falls back to 14 layers and then CPU if the preferred profile fails.

## Coder-model comparisons

The model checks used the same deterministic 20-case local quality suite. Generation speed and time to first token were measured from the local server run.

| Model | Quality | Generation | First token | Decision |
| --- | ---: | ---: | ---: | --- |
| Qwen2.5-Coder-3B, 24 GPU layers | 13/20 | 9.72 tokens/s | 8.42 s | Production baseline |
| Qwen3-4B, 24 GPU layers | 18/20 | 3.89 tokens/s | 35.14 s | Fallback only |
| DeepSeek-Coder-1.3B, 28 GPU layers | 14/20 | 6.50 tokens/s | 10.20 s | Fallback only |

Qwen3-4B was more accurate on the fixed suite but much slower on both speed measurements. DeepSeek gained one quality pass but was also slower. My rule is that a replacement must not make any measured item slower, so neither replaced the production model.

## Optional Python type checking

The driver comparison used five tasks, two seeds, and two modes: 20 invocations and 22 model generations. Task fixtures, model settings, output limits, and server profile were held constant. Complete driver subprocess time was measured; model startup and final shutdown were outside the per-run totals.

| Measurement | Baseline | Type checking enabled |
| --- | ---: | ---: |
| Accepted runs | 8/10 | 6/10 |
| Driver time | 164.4 s | 141.8 s |
| Prompt tokens | 2,230 | 2,032 |
| Raw output lines | 125 | 101 |
| Bugs caught only by the checker | 0 | 0 |

The lower typed-mode time came from fast refusals and two avoided extraction retries, not from cheaper initial prompts. Type checking did not establish a reliability gain and reduced the accepted-run rate from 80% to 60%, so it remains opt-in rather than the default.

## Limits

These samples are deliberately small. Two seeds do not remove thermal, cache, scheduling, or sampling effects. A passing driver verdict checks the known tests and gates; it does not prove hidden-test correctness. The results are useful for conservative local choices, not broad claims about every workload.
