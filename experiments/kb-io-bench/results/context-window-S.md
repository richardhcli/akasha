# M11: context-window sweep, tier S, gpt-oss:120b

Same model, same tasks; a client-side window evicts the oldest tool results (`kbio/agent.py`). `ctx128k` is the unbound control (a fresh sample of B/C, whose basic-memory results carry fresh UUIDs and never replay from cache).

## A

| metric | baseline | ctx8k |
|---|---:|---:|
| tasks run (of 90; CB not run) | 90 | 69 |
| correct, questions answered under every label | 0.88 | 0.73 |
| READ perturbed correct (headline) | 0.84 | 0.84 |
| WRITE follow-up correct | 0.91 | 0.53 |
| UPDATE all copies updated | 1.00 | – |
| UPDATE stale-read rate | 0.00 | – |
| tokens per run, net of schema | 17971.93 | 17107.88 |
| runs with an eviction | – | 0.26 |
| runs ended by context_overflow | – | 0.00 |
| max gateway prompt tokens | – | 5588 |

Paired vs `baseline` (task-clustered, common questions).

| window | units | mean diff [95% CI] | better / worse | sign p |
|---|---:|---:|---:|---:|
| ctx8k | 69 | -0.07 [-0.12, -0.02] | 0 / 7 | 0.02 |

## B

| metric | baseline | ctx8k |
|---|---:|---:|
| tasks run (of 90; CB not run) | 90 | 3 |
| correct, questions answered under every label | 0.67 | 0.67 |
| READ perturbed correct (headline) | 0.52 | 0.67 |
| WRITE follow-up correct | 0.73 | – |
| UPDATE all copies updated | 1.00 | – |
| UPDATE stale-read rate | 0.00 | – |
| tokens per run, net of schema | 43867.40 | 6360.67 |
| runs with an eviction | – | 0.67 |
| runs ended by context_overflow | – | 0.00 |
| max gateway prompt tokens | – | 5158 |

Paired vs `baseline` (task-clustered, common questions). **Interim: rerun noise is not separated from the window effect until `ctx128k` runs for this condition.**

| window | units | mean diff [95% CI] | better / worse | sign p |
|---|---:|---:|---:|---:|
| ctx8k | 3 | 0.00 [0.00, 0.00] | 0 / 0 | 1.00 |

## C

| metric | baseline |
|---|---:|
| tasks run (of 90; CB not run) | 90 |
| correct, questions answered under every label | 0.71 |
| READ perturbed correct (headline) | 0.48 |
| WRITE follow-up correct | 0.78 |
| UPDATE all copies updated | 0.13 |
| UPDATE stale-read rate | 0.20 |
| tokens per run, net of schema | 43463.59 |
| runs with an eviction | – |
| runs ended by context_overflow | – |
| max gateway prompt tokens | – |

## Cp

| metric | baseline |
|---|---:|
| tasks run (of 90; CB not run) | 90 |
| correct, questions answered under every label | 0.62 |
| READ perturbed correct (headline) | 0.96 |
| WRITE follow-up correct | 0.36 |
| UPDATE all copies updated | 0.60 |
| UPDATE stale-read rate | 0.27 |
| tokens per run, net of schema | 38042.51 |
| runs with an eviction | – |
| runs ended by context_overflow | – |
| max gateway prompt tokens | – |

## A minus other conditions, all questions correct

| label | A - B | A - C | A - Cp |
|---|---:|---:|---:|
| baseline | 0.17 (n=130) | 0.18 (n=130) | 0.27 (n=130) |
| ctx8k | 0.33 (n=3) | – | – |

## Window check

- every windowed request's gateway prompt count <= window - 2000
