**English** | [Русский](SCORING.ru.md)

# Node rating

[`nodes_tester`](README.md) keeps the rating in SQLite and updates it after each measurement of a node.
The instant score is computed from the latest result of each test of the node: tests have their own
lifetimes, so the results may be from different times.
The computation has three parts: the GATE, the instant score and aggregation over time.

## Component sources

Each component is in the range from 0 to 1.

| Component | Source metrics |
|---|---|
| `latency` | TTFB from `latency` |
| `jitter` | standard deviation of the latency from `jitter` |
| `consistency` | ratio of jitter to the mean latency |
| `throughput` | `speed_mbps` from `download` |
| `throttle` | `throttle_ratio` from `download` |
| `reliability` | request loss, `hold_ratio` and the share of reachable targets |

“Lower is better” metrics use linear normalisation between the good and the bad
threshold:

```text
lower_better(x) = clamp((bad - x) / (bad - good), 0, 1)
```

“Higher is better” metrics:

```text
higher_better(x) = clamp((x - low) / (high - low), 0, 1)
```

The thresholds are in `rating.thresholds`, the weights in `rating.weights` of `config.json`. A missing test does not
get a zero score: its component is excluded, and the remaining weights are normalised again.

## Reliability

Reliability combines the available parts:

| Part | Base weight |
|---|---:|
| successful requests `1 - loss_pct / 100` | 0.5 |
| download hold `hold_ratio` | 0.3 |
| reachable targets `reached / total` | 0.2 |

If a part is missing, its weight is also excluded and the sum is recomputed from the remaining data.

A `429`, `5xx` or another clearly recognised HTTP refusal of the test server counts as
neutral for throughput: the transport answered, but the service did not give a measurement.
A real network failure gives zero transport components.

## GATE

The instant GATE fails if at least one condition holds:

- `connectivity` failed;
- the `jitter` loss is above `rating.thresholds.loss_gate_percent`;
- `reachability` checked the targets but reached none;
- all tests with an `ok` field failed.

On a GATE failure the resulting rating of the current state is zero right away. EWMA keeps
updating inside the state, but does not hide the current unavailability.

## Instant score

For a node that passed the GATE:

```text
S_run = 100 * sum(weight_i * component_i) / sum(available_weight_i)
```

If there are no scoring components, `S_run` is zero. `connectivity` alone confirms access
but gives no quality for the rating.

## Aggregation over time

After each measurement of a node these are stored:

```text
score_ewma = alpha * S_run + (1 - alpha) * previous_score_ewma
flap       = alpha * abs(S_run - previous_score_ewma) + (1 - alpha) * previous_flap
avail      = alpha * gate + (1 - alpha) * previous_avail
```

Availability turns into a factor from 0.5 to 1 between `rating.availability.floor` and `rating.availability.full`.
The result for a node that passed the GATE:

```text
score = clamp(score_ewma * avail_factor - flap_lambda * flap, 0, 100)
```

So a single good measurement does not fully cover an unstable history, and frequent jumps
get a separate penalty.

## Heavy download

The `heavy` check (heavy download) is not part of the formula. The long download is run by the reserve process for
reserve candidates of a group (see “Group reserve” in the [README](README.md)): a node gets into the
reserve only after passing it. The result lasts `checks.heavy.ttl` (`6h` by default), then for
reserve members the download is repeated.

A failure is stored as `heavy_ok = 0` and excludes the node from switching candidates for the same
period. If no candidate is left without a veto, the switcher falls back to the full
list so that the group is not left without a choice.

## Where the data is stored

- `scores`: the current snapshot, components and EWMA state;
- `score_history`: a point of `score`, `S_run` and the GATE for each measurement of a node;
- `results`: physical metrics of individual tests;
- `heavy_ok` and `heavy_ts`: the state of the heavy veto in `scores`;
- `reserve`: the groups whose reserve has the node.

Database details: [`STORAGE.md`](STORAGE.md).

## Tuning

Change one class of parameters at a time and judge by the history of several measurements:

1. Thresholds define how a physical metric becomes a component.
2. Weights define the relative importance of the available components.
3. `alpha` sets how fast the rating reacts to a new measurement.
4. `flap_lambda` strengthens or weakens the flapping penalty.
5. `rating.availability.floor` and `rating.availability.full` set the effect of long-term availability.

Changing the formula does not recompute old history rows. The new values gradually
update the current snapshot through the next measurements.
