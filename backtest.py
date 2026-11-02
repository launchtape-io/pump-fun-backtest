#!/usr/bin/env python3
"""Offline, conservative reserve-based replay of a fixed public preview. No RPC."""
import argparse
from dataclasses import asdict, dataclass
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Rules:
    entry_seconds: float = 0
    exit_seconds: float = 180
    take_profit_pct: float = 50
    stop_loss_pct: float = 20
    size_sol: float = 0.1
    fee_bps: float = 125
    slippage_bps: float = 100
    network_sol: float = 0.00001
    rent_sol: float = 0.00204
    latency_seconds: float = 0.4
    max_wait_seconds: float = 30

    def validate(self):
        for k, v in asdict(self).items():
            if not math.isfinite(v) or v < 0:
                raise ValueError(f'{k} must be finite and nonnegative')
        if not 0 < self.size_sol or not self.entry_seconds < self.exit_seconds <= 1800:
            raise ValueError('size must be positive; require entry < exit <= 1800 seconds')
        if self.fee_bps >= 10000 or self.slippage_bps >= 10000 or self.stop_loss_pct > 100:
            raise ValueError('fees/slippage must be below 10000 bps; stop <= 100%')


def number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def load_preview():
    meta = json.loads((ROOT / 'data/provenance.json').read_text())
    raw = (ROOT / 'data/preview.jsonl.gz').read_bytes()
    if hashlib.sha256(raw).hexdigest() != meta['files']['preview.jsonl.gz']['sha256']:
        raise ValueError('preview checksum mismatch')
    rows = [json.loads(line) for line in gzip.decompress(raw).splitlines() if line.strip()]
    if len(rows) != meta['files']['preview.jsonl.gz']['rows']:
        raise ValueError('preview row count mismatch')
    return rows, meta


def prepare(rows):
    coins, gaps, down, previous, identities = {}, [], None, -math.inf, set()
    for r in rows:
        if not isinstance(r, dict) or not number(r.get('t')) or r['t'] < previous:
            raise ValueError('rows need finite, nondecreasing arrival timestamps')
        previous = r['t']
        kind, mint = r.get('type'), r.get('mint')
        if kind == 'down':
            if down is None: down = r['t']
        elif kind == 'up':
            # An unmatched up implies the preview began during a gap.
            gaps.append((-math.inf if down is None else down, r['t']))
            down = None
        elif kind == 'grad':
            if not isinstance(mint, str) or not mint or mint in coins:
                raise ValueError('missing or duplicate graduation mint')
            coins[mint] = {'grad': r, 'trades': [], 'events': []}
        elif kind in ('trade', 'end', 'resume'):
            if mint not in coins:
                raise ValueError('coin row without a preceding graduation')
            if kind == 'trade':
                if 'sig' in r and 'i' in r:
                    identity = (mint, r['sig'], r['i'])
                    if identity in identities: raise ValueError('duplicate trade identity')
                    identities.add(identity)
                coins[mint]['trades'].append(r)
            else:
                coins[mint]['events'].append(r)
        else:
            raise ValueError(f'unsupported row type: {kind!r}')
    if down is not None: gaps.append((down, math.inf))
    return coins, gaps


def slot_marks(coin):
    grad = coin['grad']
    virtual = grad.get('virtualQuote')
    marks = []
    for r in coin['trades']:
        if r.get('src') == 'backfill':
            return [], 'backfill_present'
        if 'virtualQuote' in r: virtual = r['virtualQuote']
        b, q, slot = r.get('poolBase'), r.get('poolQuote'), r.get('slot')
        if (not all(number(x) for x in (b, q, virtual, slot)) or
                b <= 0 or q < 0 or virtual < 0 or q + virtual <= 0 or
                slot != int(slot) or slot < 0):
            return [], 'missing_or_invalid_reserves'
        if marks and slot < marks[-1]['slot']:
            return [], 'out_of_order_slots'
        mark = {'t': r['t'], 'slot': slot, 'base': b, 'quote': q,
                'virtual': virtual, 'price': (q + virtual) / b}
        # Last received trade in each slot, not a claim of exact chain order.
        if marks and slot == marks[-1]['slot']: marks[-1] = mark
        else: marks.append(mark)
    return marks, None


def buy(mark, rules):
    spend = rules.size_sol * (1 - rules.fee_bps / 10000)
    tokens = mark['base'] * spend / (mark['quote'] + mark['virtual'] + spend)
    return tokens * (1 - rules.slippage_bps / 10000)


def sell(mark, tokens, rules):
    gross = (mark['quote'] + mark['virtual']) * tokens / (mark['base'] + tokens)
    if gross > mark['quote']:
        return None  # Real SOL reserve cannot pay this hypothetical sale.
    return gross * (1 - rules.fee_bps / 10000) * (1 - rules.slippage_bps / 10000) - rules.network_sol


def replay(rows, rules, window_end_ms):
    rules.validate()
    if not number(window_end_ms): raise ValueError('finite preview boundary required')
    coins, gaps = prepare(rows)
    results = []
    for mint, coin in coins.items():
        g = coin['grad']; t0 = g['t']
        result = {'mint': mint, 'status': None, 'net_pnl_sol': None, 'net_return_pct': None}
        results.append(result)
        if g.get('follow') is False:
            result['status'] = 'not_followed'; continue
        if g.get('late') or g.get('followFrom', t0) > t0:
            result['status'] = 'late_pickup'; continue
        if not number(g.get('slot')):
            result['status'] = 'missing_graduation_slot'; continue
        marks, error = slot_marks(coin)
        if error:
            result['status'] = error; continue
        end = min([window_end_ms] + [e['t'] for e in coin['events'] if e['type'] == 'end'])
        marks = [m for m in marks if t0 <= m['t'] <= end]
        target = t0 + (rules.entry_seconds + rules.latency_seconds) * 1000
        entry = next((m for m in marks if m['t'] > target and m['slot'] > g['slot']), None)
        if not entry or entry['t'] > target + rules.max_wait_seconds * 1000:
            result['status'] = 'no_timely_entry'; continue
        deadline = t0 + rules.exit_seconds * 1000
        if entry['t'] >= deadline:
            result['status'] = 'entry_after_deadline'; continue
        tokens = buy(entry, rules)
        cost = rules.size_sol + rules.network_sol + rules.rent_sol
        result.update(entry_s=(entry['t']-t0)/1000, entry_slot=entry['slot'], tokens=tokens, cost_sol=cost)
        signal_t, signal_slot, reason = deadline, entry['slot'], 'time'
        for m in marks:
            if m['slot'] <= entry['slot'] or m['t'] <= entry['t']: continue
            if m['t'] > deadline: break
            signal_slot = m['slot']  # Timer cannot fill at an already observed slot.
            change = (m['price'] / entry['price'] - 1) * 100
            if change <= -rules.stop_loss_pct:
                signal_t, reason = m['t'], 'stop_loss'; break
            if change >= rules.take_profit_pct:
                signal_t, reason = m['t'], 'take_profit'; break
        fill_target = signal_t + rules.latency_seconds * 1000
        exit_mark = next((m for m in marks if m['t'] > fill_target and m['slot'] > signal_slot), None)
        result.update(exit_reason=reason, signal_s=(signal_t-t0)/1000)
        if not exit_mark or exit_mark['t'] > fill_target + rules.max_wait_seconds * 1000:
            result['status'] = 'no_timely_exit'; continue
        result.update(exit_s=(exit_mark['t']-t0)/1000, exit_slot=exit_mark['slot'])
        if (any(a <= exit_mark['t'] and b >= t0 for a,b in gaps) or
                any(e['type']=='resume' and t0 <= e['t'] <= exit_mark['t'] for e in coin['events'])):
            result['status'] = 'coverage_gap'; continue
        proceeds = sell(exit_mark, tokens, rules)
        if proceeds is None:
            result['status'] = 'insufficient_exit_liquidity'; continue
        result.update(status='closed', proceeds_sol=proceeds,
                      net_pnl_sol=proceeds-cost, net_return_pct=100*(proceeds/cost-1))
    closed = [r for r in results if r['status']=='closed']
    return {'model':'hypothetical_constant_product_next_observed_slot', 'rules':asdict(rules),
            'summary':{'graduates':len(results), 'closed':len(closed),
                       'excluded':len(results)-len(closed),
                       'entered_but_unresolved':sum(r['status']!='closed' and 'entry_s' in r for r in results),
                       'closed_only_net_pnl_sol':sum(r['net_pnl_sol'] for r in closed) if closed else None},
            'results':results}


def report(result):
    r = result['rules']; s=result['summary']
    lines = ['pump fun backtest | OFFLINE PUBLIC PREVIEW',
             'Hypothetical fills from recorded reserves; no orders sent.',
             f"Rule: entry +{r['entry_seconds']:g}s; TP +{r['take_profit_pct']:g}%; SL -{r['stop_loss_pct']:g}%; exit +{r['exit_seconds']:g}s from graduation.",
             f"Costs per side: fee {r['fee_bps']:g} bps + adverse slippage {r['slippage_bps']:g} bps + network {r['network_sol']:g} SOL.",
             f"Size {r['size_sol']:g} SOL; unrecovered account rent {r['rent_sol']:g} SOL; delay {r['latency_seconds']:g}s plus next observed slot.",
             f"Coverage: {s['graduates']} graduates, {s['closed']} closed simulations, {s['excluded']} excluded, {s['entered_but_unresolved']} entered but unresolved.", '']
    for row in result['results']:
        pnl = 'NA' if row['net_pnl_sol'] is None else f"{row['net_pnl_sol']:+.6f} SOL ({row['net_return_pct']:+.2f}%)"
        lines.append(f"{row['mint']}  {row['status']}  {row.get('exit_reason','-')}  {pnl}")
    pnl=s['closed_only_net_pnl_sol']
    lines += ['', 'Closed-only P&L: '+ ('NA' if pnl is None else f'{pnl:+.6f} SOL'),
              'Excluded/unresolved positions are NOT zero-return trades; this is not a portfolio return.',
              'Five chronological graduates from one old preview cannot establish an edge. See README for limitations.']
    return '\n'.join(lines)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for key, value in asdict(Rules()).items():
        parser.add_argument('--'+key.replace('_','-'), type=float, default=value)
    parser.add_argument('--json', action='store_true', help='machine-readable results')
    args=vars(parser.parse_args()); json_output=args.pop('json')
    try:
        rows, meta=load_preview()
        result=replay(rows,Rules(**args),meta['source_window_end_ms'])
    except (ValueError, KeyError, OSError, TypeError) as e:
        parser.exit(2, f'error: {e}\n')
    print(json.dumps(result,indent=2,allow_nan=False) if json_output else report(result))


if __name__=='__main__': main()
