# pump fun backtest

**Test a pump fun sniper rule offline on real, bundled PumpSwap trade data—with fees, slippage, order-size impact and delayed simulated fills.**

For Solana trading-bot builders who want to inspect a rule before connecting a wallet. Python 3.10+, standard library only. No installation step, API key, RPC or wallet. The preview runs offline.

## Clone and run

With Git and Python already installed, these are the only commands:

```sh
git clone https://github.com/launchtape-io/pump-fun-backtest.git
cd pump-fun-backtest
python3 backtest.py
```

On Windows, use `py -3 backtest.py`. Clone size is about 2.3 MB of bundled data; execution should take seconds, with no network calls. The two-minute setup target assumes Git and Python are available.

The default rule buys after graduation, then exits on a +50% pool-price move, a -20% move, or at +180 seconds from graduation. Those are **example inputs, not recommended settings**. Output lists every coin, why it exited or was excluded, and hypothetical net P&L. An unfollowed coin stays `NA`; a missing exit never becomes a zero or an invented sale.

```sh
python3 backtest.py --take-profit-pct 30 --stop-loss-pct 15 --exit-seconds 120
python3 backtest.py --entry-seconds 15 --latency-seconds 1 --slippage-bps 200
python3 backtest.py --json > result.json
python3 -m unittest discover -s tests -v
```

## What is bundled

The **first five graduation records by timestamp** from Launch Tape's already-public October 10, 2026, 14:00–16:00 UTC GitHub preview, with their original rows and all feed down/up markers. Selection uses no price outcome, follow flag or strategy result. One of the five was not followed; it remains visible in the output. The full source is larger than this demo.

[Source file and hashes](data/provenance.json) · [Data terms](DATA_LICENSE.txt) · [Rebuild the subset](tools/extract_preview.py)

This is a tiny chronological subset of one old recording window, **not a representative sample of pump fun launches**. Coverage flags are evidence of known gaps, not proof all trades were captured. The repo does not bundle purchased files, contact lists or private delivery links. Results are calculated locally; no performance statistic is used as a sales claim.

## How fills work

These are **hypothetical fills against recorded pool reserves**, not orders that actually executed. The original trade rows are real observations. We cannot know where your transaction would have landed.

1. Keep the last received trade in each slot. Use its real token reserve `B`, real SOL reserve `Q`, and the explicit virtual SOL reserve `V` carried from graduation or an update. Missing reserves disqualify the coin; there is no price-only fallback. Arrival order within a slot is not guaranteed chain transaction order.
2. Entry uses the first observed slot strictly after the graduation slot and after the configured entry time plus latency. It is a delayed graduation-entry proxy. No fill occurs at the graduation's displayed price.
3. Take-profit and stop-loss triggers compare the subsequent slot's marginal pool price `(Q+V)/B` to the entry slot's marginal price. They are gross price thresholds, not net returns. Exit uses a later observed slot after the configured latency. A -20% stop can execute much lower after a dump. A timer exit follows the same later-observation rule.
4. With size `S`, fee fraction `f`, and slippage fraction `s`, buy tokens are `B*S*(1-f)/(Q+V+S*(1-f))*(1-s)`. Selling `T` tokens yields `(Q+V)*T/(B+T)*(1-f)*(1-s)`, less exit network cost. A sale requiring more than the real SOL reserve is unfillable, not capped into an invented successful exit.
5. Cost basis is `S + entry network cost + unrecovered account rent`. P&L is net exit proceeds minus that basis. Position size affects both modeled swap prices. Historical future reserves are unchanged by our hypothetical trade; this is a small-order approximation, not a counterfactual chain simulation.

Default **assumptions**, not a verified fee schedule: 125 bps swap fee and 100 bps adverse slippage per side, 0.00001 SOL network/tip cost per side, 0.00204 SOL unrecovered account rent, 0.1 SOL size and 0.4-second delay. All are CLI parameters; `--help` lists them. Historical fee fields, failed transactions, priority bidding, MEV, account creation/recovery and dynamic fee changes are not fully reconstructed. Raising costs does not guarantee realism. A zero-cost run is intentionally optimistic.

An observation arriving more than 30 seconds after a scheduled fill is rejected by default (`--max-wait-seconds`). No interpolation or stale-price fill is used. Positions crossing a known connection gap or resume marker are excluded. Late pickups, backfill-containing tapes, invalid reserves and regressing slots are conservatively excluded. A recording end or the source window can leave a position unresolved. Those positions might have lost the entire stake: closed-only P&L must not be interpreted as a portfolio return. Capital limits and overlapping positions are not modeled.

## Move beyond the preview

Freeze a rule before testing unseen days. Report exclusions and missing data alongside results. Changing parameters repeatedly on these same five coins teaches overfitting, not whether a rule works.

For purchased full-day files, the separate [Launch Tape Python package](https://github.com/launchtape-io/launchtape#test-your-bots-rules) has a day-file replay interface. Its model and defaults differ; outputs are not directly comparable to this small demo. This script deliberately accepts only its checksum-verified bundled preview, so an unrelated file cannot silently be treated as the same sample.

Code: [MIT](LICENSE). Bundled data: [separate research-use terms](DATA_LICENSE.txt); the code license does not relicense the data. Market data and simulated outcomes are not investment advice or a promise of profit.

run it on every graduate: [launchtape.io/?src=gh](https://launchtape.io/?src=gh)
