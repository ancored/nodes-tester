const e=`**English** | [Русский](rating.ru.md)

# Rating

The rating is one number from 0 to 100 that says how good a node is right now and how stable it has been. Groups
choose among their candidates by the rating, rotation prefers higher-rated nodes, and a rating of 0 means the node
is not used at all.

The rating is updated after every visit of the node. It is built from the latest result of each measurement, even
if the measurements were taken at different times (they have their own [lifetimes](checks.md#lifetimes)).

<a id="formula"></a>
## How the rating is computed

\`\`\`mermaid
flowchart TD
    res["Latest results of the measurements"] --> gate{"Gate passed?"}
    gate -->|no| zero["Rating 0 at once"]
    gate -->|yes| comp["Six components 0…1:<br>reliability, consistency, throttling,<br>jitter, latency, speed"]
    comp --> run["Instant score S_run =<br>weighted mean × 100"]
    run --> ewma["Smoothing over time (EWMA)"]
    ewma --> avail["× availability factor 0.5…1"]
    avail --> flap["− penalty for jumps (flapping)"]
    flap --> score["Rating 0…100"]
\`\`\`

<a id="gate"></a>
## Gate

The gate is the basic “the node is alive” condition. It fails if at least one of these holds:

- \`connectivity\` failed: no connection through the node;
- the loss in \`jitter\` is above \`rating.thresholds.loss_gate_percent\` (50 % by default);
- \`reachability\` checked sites but none answered;
- every test that has a success flag failed.

On a gate failure the rating becomes 0 immediately, without smoothing: the history does not hide a node that is
dead now. A gate failure also starts a [pause](restrictions.md#pause).

<a id="components"></a>
## Components

Every component is normalised to the range from 0 (bad) to 1 (good) between two thresholds of \`rating.thresholds\`:

| Component | Source | Thresholds (bad → good) | Weight |
|---|---|---|---:|
| reliability | successful requests, download hold, reachable sites | — | 0.35 |
| consistency | jitter divided by the mean latency | \`variation\` 0.8 → 0.1 | 0.20 |
| throttling | speed of the last download window to the first | \`throttle\` 0.3 → 0.9 | 0.15 |
| jitter | deviation of latency | \`jitter\` 250 ms → 15 ms | 0.10 |
| latency | time to the first byte | \`latency\` 1.5 s → 150 ms | 0.10 |
| speed | download speed | \`download_mbps\` 2 → 25 Mbit/s | 0.10 |

Between the thresholds the value changes linearly; beyond them it stays at 0 or 1. Reliability is itself a mix:
successful requests (\`1 − loss\`) with weight 0.5, the download hold (the share of the file received) 0.3 and the
share of reachable sites 0.2.

A missing measurement does not give a zero: its component is left out and the remaining weights are scaled up. A
refusal of a test server (HTTP 429 or 5xx) is neutral for speed: the transport answered, the service did not.

<a id="instant"></a>
## Instant score

\`\`\`text
S_run = 100 × Σ(weight × component) / Σ(weights of the available components)
\`\`\`

\`connectivity\` alone confirms access but gives no quality: a node with only a connection result has \`S_run\` 0.

<a id="smoothing"></a>
## Smoothing over time

After each measurement three values are updated with the factor \`alpha\` (\`rating.alpha\`, 0.3 by default):

\`\`\`text
score_ewma = alpha × S_run + (1 − alpha) × previous score_ewma
flap       = alpha × |S_run − previous score_ewma| + (1 − alpha) × previous flap
avail      = alpha × gate + (1 − alpha) × previous avail        (gate: 1 passed, 0 failed)
\`\`\`

The final rating:

\`\`\`text
rating = clamp(score_ewma × availability factor − flap_lambda × flap, 0, 100)
\`\`\`

- **Availability factor**: below \`availability.floor\` (0.7) availability gives 0.5, from \`availability.full\` (0.9)
  it gives 1, in between linearly. A node that often fails the gate loses up to half of its rating.
- **Flapping penalty**: \`flap_lambda\` (0.5) × the average jump of the score. A node that is excellent one hour and
  poor the next ranks below a node that is steadily good.

So one good measurement does not cover an unstable history, and one bad measurement (except a gate failure) does
not ruin a good node.

<a id="reading"></a>
## How to read the rating

| What you see | Meaning |
|---|---|
| 70 and above (green) | a good, stable node |
| 20 and below (red) | weak or unstable; groups take it last |
| 0 | the gate failed now, or there are no scoring measurements yet |
| “No measurements” | the node has not been measured yet; this is not a failure |

The colours are only a rough guide; switching rules use their own thresholds:

- \`selection.rotation.min_score\` (55): rotation does not choose lower-rated nodes;
- \`selection.tier_floor\` (75): below it a node goes to a lower [tier](groups.md#tiers) even if it passed the
  preferred checks;
- \`selection.comfort_floor\` (50): above it the active node is not replaced by quality without the margin;
- \`selection.quarantine.low_score\` (0, off): a live but weak node below it goes to [quarantine](restrictions.md#low-score).

The physical values — ms, Mbit/s, loss — are on the [“Results”](nodes.md#results) page; the normalised components
are on the [“Rating”](nodes.md#rating) page; the history of one node is in its [card](node-card.md#state).

<a id="tuning"></a>
## Tuning

Thresholds and weights are in the \`rating\` section ([reference](../CONFIG.md#rating)). Change one class of
parameters at a time and judge by several measurements:

1. thresholds decide how a physical value turns into a component;
2. weights decide which components matter more;
3. \`alpha\` sets how fast the rating reacts (more — faster, but jumpier);
4. \`flap_lambda\` strengthens or weakens the penalty for instability;
5. \`availability.floor\` and \`availability.full\` set how much frequent failures cost.

Old history is not recomputed: new values affect the next updates.
`;export{e as default};
