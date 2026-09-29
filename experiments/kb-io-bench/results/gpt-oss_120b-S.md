# kb-io-bench results: gpt-oss:120b, tier S

## Correct rate by question kind

| kind | A | B | C | Cp | CB |
|---|---:|---:|---:|---:|---:|
| aggregate (n=5) | 0.80 | 1.00 | 1.00 | 1.00 | 0.20 |
| personal (n=15) | 1.00 | 1.00 | 1.00 | 0.93 | 0.47 |
| perturbed (n=15) | 1.00 | 0.60 | 0.53 | 1.00 | 0.00 |
| perturbed-multihop (n=10) | 0.60 | 0.40 | 0.40 | 0.90 | 0.00 |
| unanswerable (n=5) | 0.80 | 0.60 | 0.60 | 0.20 | 0.60 |
| unperturbed (n=10) | 0.80 | 0.70 | 0.70 | 0.80 | 0.10 |
| update-followup (n=15) | 0.93 | 0.73 | 0.47 | 0.60 | – |
| write-followup (n=55) | 0.91 | 0.73 | 0.78 | 0.36 | – |
| **perturbed (headline)** | 0.84 | 0.52 | 0.48 | 0.96 | 0.00 |

## Cost and reliability (all agent runs)

| metric | A | B | C | Cp | CB |
|---|---:|---:|---:|---:|---:|
| runs | 160 | 160 | 160 | 160 | 60 |
| failed_rate | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| tokens_mean | 20569.93 | 65082.57 | 64495.71 | 40717.36 | 511.82 |
| tokens_median | 3824.00 | 21384.00 | 20352.00 | 16224.50 | 402.50 |
| tool_calls_mean | 4.97 | 6.36 | 6.34 | 7.74 | 0.00 |
| step_cap_rate | 0.09 | 0.13 | 0.13 | 0.31 | 0.00 |
| empty_final_rate | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| tool_error_rate | 0.04 | 0.01 | 0.02 | 0.11 | – |
| tool_schema_tokens | 337 | 2254 | 2254 | 253 | 0 |
| tokens_net_mean | 17971.93 | 43867.40 | 43463.59 | 38042.51 | 511.82 |
| unexposed_tool_calls | 7 | 13 | 14 | 2 | 0 |

## Pilot gate (fail > 20% of tasks = stop)

| metric | A | B | C | Cp | CB |
|---|---:|---:|---:|---:|---:|
| tasks | 90 | 90 | 90 | 90 | 60 |
| failing | 0 | 0 | 0 | 9 | 0 |
| failing_rate | 0.00 | 0.00 | 0.00 | 0.10 | 0.00 |
| writers_zero_write_calls | 0 | 0 | 0 | 9 | 0 |
| claimed_done_wrote_nothing | 0 | 0 | 0 | 9 | 0 |
| writes_no_effect | 0 | 0 | 1 | 2 | 0 |
| malformed_calls | 2 | 0 | 0 | 4 | 0 |
| pass | True | True | True | True | True |

## WRITE

| metric | A | B | C | Cp |
|---|---:|---:|---:|---:|
| tasks | 15 | 15 | 15 | 15 |
| landed | 0.88 | 0.89 | 0.84 | 0.37 |
| landed_relaxed | 0.92 | 0.95 | 0.93 | 0.37 |
| linked | 0.87 | 0.84 | 0.81 | 0.37 |
| duplicates_per_task | 0.00 | 0.20 | 0.27 | 0.00 |
| violations_after | 0.00 | 0.00 | – | – |
| reviews_after | 0.00 | 0.00 | – | – |
| followup_correct | 0.91 | 0.73 | 0.78 | 0.36 |
| tokens_per_task | 157239.13 | 429565.80 | 429645.07 | 319899.80 |

## UPDATE

| metric | A | B | C | Cp |
|---|---:|---:|---:|---:|
| tasks | 15 | 15 | 15 | 15 |
| copies_updated_rate | 1.00 | 1.00 | 0.61 | 0.72 |
| all_copies_updated | 1.00 | 1.00 | 0.13 | 0.60 |
| copies_stale_mean | 0.00 | 0.00 | 1.53 | 1.13 |
| copies_total_mean | 3.87 | 3.87 | 3.87 | 3.87 |
| followup_correct | 0.93 | 0.73 | 0.47 | 0.60 |
| stale_read_rate | 0.00 | 0.00 | 0.20 | 0.27 |
| violations_after | 0.00 | 0.00 | – | – |
| tokens_per_task | 13311.07 | 103967.67 | 99515.93 | 67745.80 |

## Misses: was the answer value ever shown to the agent?

| scope | A | B | C | Cp | CB |
|---|---:|---:|---:|---:|---:|
| misses_perturbed (shown / never / shown+abstained) | 0 / 4 / 0 | 0 / 12 / 0 | 0 / 13 / 0 | 0 / 1 / 0 | 0 / 25 / 0 |
| misses_all (shown / never / shown+abstained) | 1 / 11 / 0 | 7 / 27 / 1 | 9 / 27 / 0 | 5 / 39 / 2 | 0 / 34 / 0 |
| update follow-ups wrong (stale / other definition) | 0 / 1 | 0 / 4 | 3 / 5 | 4 / 2 | – |

## Paired tests per family (task-clustered; the headline tests)

| pair | family | units | mean diff a-b [95% CI] | a better / b better | sign p |
|---|---|---:|---:|---:|---:|
| A-vs-B | read-aggregate | 5 | -0.20 [-0.60, 0.00] | 0 / 1 | 1.0 |
| A-vs-B | read-personal | 15 | 0.00 [0.00, 0.00] | 0 / 0 | 1.0 |
| A-vs-B | read-perturbed | 25 | 0.32 [0.12, 0.52] | 9 / 1 | 0.0215 |
| A-vs-B | read-unanswerable | 5 | 0.20 [0.00, 0.60] | 1 / 0 | 1.0 |
| A-vs-B | read-unperturbed | 10 | 0.10 [0.00, 0.30] | 1 / 0 | 1.0 |
| A-vs-B | update | 15 | 0.20 [-0.07, 0.47] | 4 / 1 | 0.375 |
| A-vs-B | write | 15 | 0.17 [0.02, 0.32] | 7 / 1 | 0.0703 |
| B-vs-C | read-aggregate | 5 | 0.00 [0.00, 0.00] | 0 / 0 | 1.0 |
| B-vs-C | read-personal | 15 | 0.00 [0.00, 0.00] | 0 / 0 | 1.0 |
| B-vs-C | read-perturbed | 25 | 0.04 [0.00, 0.12] | 1 / 0 | 1.0 |
| B-vs-C | read-unanswerable | 5 | 0.00 [-0.60, 0.60] | 1 / 1 | 1.0 |
| B-vs-C | read-unperturbed | 10 | 0.00 [0.00, 0.00] | 0 / 0 | 1.0 |
| B-vs-C | update | 15 | 0.27 [0.07, 0.47] | 4 / 0 | 0.125 |
| B-vs-C | write | 15 | -0.04 [-0.23, 0.14] | 4 / 5 | 1.0 |
| A-vs-C | read-aggregate | 5 | -0.20 [-0.60, 0.00] | 0 / 1 | 1.0 |
| A-vs-C | read-personal | 15 | 0.00 [0.00, 0.00] | 0 / 0 | 1.0 |
| A-vs-C | read-perturbed | 25 | 0.36 [0.16, 0.60] | 10 / 1 | 0.0117 |
| A-vs-C | read-unanswerable | 5 | 0.20 [0.00, 0.60] | 1 / 0 | 1.0 |
| A-vs-C | read-unperturbed | 10 | 0.10 [0.00, 0.30] | 1 / 0 | 1.0 |
| A-vs-C | update | 15 | 0.47 [0.20, 0.73] | 7 / 0 | 0.0156 |
| A-vs-C | write | 15 | 0.13 [-0.06, 0.34] | 6 / 2 | 0.2891 |
| A-vs-Cp | read-aggregate | 5 | -0.20 [-0.60, 0.00] | 0 / 1 | 1.0 |
| A-vs-Cp | read-personal | 15 | 0.07 [0.00, 0.20] | 1 / 0 | 1.0 |
| A-vs-Cp | read-perturbed | 25 | -0.12 [-0.24, 0.00] | 0 / 3 | 0.25 |
| A-vs-Cp | read-unanswerable | 5 | 0.60 [0.20, 1.00] | 3 / 0 | 0.25 |
| A-vs-Cp | read-unperturbed | 10 | 0.00 [0.00, 0.00] | 0 / 0 | 1.0 |
| A-vs-Cp | update | 15 | 0.33 [0.13, 0.60] | 5 / 0 | 0.0625 |
| A-vs-Cp | write | 15 | 0.54 [0.27, 0.78] | 10 / 1 | 0.0117 |

## Paired tests (all questions pooled; clustered, so p is overstated)

| pair | n | correct a / b | McNemar a-only / b-only | p | judge wins / losses | sign p |
|---|---:|---:|---:|---:|---:|---:|
| A-vs-B | 130 | 116 / 94 | 28 / 6 | 0.0002 | 17 / 7 | 0.0639 |
| B-vs-C | 130 | 94 / 92 | 12 / 10 | 0.8318 | 13 / 9 | 0.5235 |
| A-vs-C | 130 | 116 / 92 | 30 / 6 | 0.0001 | 22 / 9 | 0.0294 |
| A-vs-Cp | 130 | 116 / 81 | 41 / 6 | 0.0 | 36 / 7 | 0.0 |
