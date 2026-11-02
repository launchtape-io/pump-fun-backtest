import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backtest import Rules, buy, sell, replay, load_preview, slot_marks


def grad(**kw):
    return dict(type='grad',t=0,slot=1,mint='coin',virtualQuote=0,**kw)


def trade(t,slot,q=100,b=1000,**kw):
    return dict(type='trade',t=t,slot=slot,mint='coin',poolBase=b,poolQuote=q,**kw)


def run(rows,rules=None,end=100000):
    return replay(rows,rules or Rules(exit_seconds=3,latency_seconds=0,fee_bps=0,slippage_bps=0,network_sol=0,rent_sol=0),end)


class ReplayTests(unittest.TestCase):
    def test_constant_product_and_costs(self):
        m={'base':1000,'quote':100,'virtual':0}
        r=Rules(size_sol=10,fee_bps=100,slippage_bps=200,network_sol=.01)
        tokens=1000*9.9/109.9*.98
        self.assertAlmostEqual(buy(m,r),tokens)
        self.assertAlmostEqual(sell(m,tokens,r),100*tokens/(1000+tokens)*.99*.98-.01)

    def test_position_size_has_impact(self):
        m={'base':1000,'quote':100,'virtual':0}; r=Rules()
        self.assertLess(buy(m,replace(r,size_sol=10))/10,buy(m,r)/.1)

    def test_real_liquidity_cannot_be_virtual(self):
        self.assertIsNone(sell({'base':1000,'quote':.001,'virtual':100},100,Rules()))

    def test_stop_fills_after_crash_not_at_threshold(self):
        r=run([grad(),trade(100,2),trade(1000,3,79),trade(1100,4,20)])['results'][0]
        self.assertEqual(r['status'],'closed'); self.assertEqual(r['exit_slot'],4)
        self.assertEqual(r['exit_reason'],'stop_loss'); self.assertLess(r['net_return_pct'],-79)

    def test_take_profit_uses_next_slot(self):
        r=run([grad(),trade(100,2),trade(1000,3,160),trade(1100,4,130)])['results'][0]
        self.assertEqual(r['exit_reason'],'take_profit'); self.assertEqual(r['exit_slot'],4)
        self.assertLess(r['net_return_pct'],30)

    def test_no_mid_slot_trigger(self):
        r=run([grad(),trade(100,2),trade(1000,3,20),trade(1100,3,100),trade(3100,4),trade(3200,5)])['results'][0]
        self.assertEqual(r['exit_reason'],'time'); self.assertEqual(r['exit_slot'],4)

    def test_deadline_between_slots(self):
        r=run([grad(),trade(100,2),trade(1000,3),trade(3100,4)])['results'][0]
        self.assertEqual(r['signal_s'],3); self.assertEqual(r['exit_s'],3.1)

    def test_deadline_at_slot_requires_later_slot(self):
        r=run([grad(),trade(100,2),trade(3000,3),trade(3100,4)])['results'][0]
        self.assertEqual(r['exit_slot'],4)

    def test_no_exit_no_invented_return(self):
        r=run([grad(),trade(100,2),trade(1000,3)])
        self.assertEqual(r['results'][0]['status'],'no_timely_exit')
        self.assertIsNone(r['results'][0]['net_return_pct'])
        self.assertIsNone(r['summary']['closed_only_net_pnl_sol'])
        self.assertEqual(r['summary']['entered_but_unresolved'],1)

    def test_late_fill_rejected(self):
        r=run([grad(),trade(100,2),trade(34000,3)])['results'][0]
        self.assertEqual(r['status'],'no_timely_exit')

    def test_delay_and_entry_not_same_slot(self):
        rules=Rules(exit_seconds=3,latency_seconds=.4)
        r=run([grad(),trade(200,1),trade(300,2),trade(600,3),trade(3100,4),trade(3500,5)],rules)['results'][0]
        self.assertEqual(r['entry_slot'],3); self.assertEqual(r['exit_slot'],5)

    def test_end_and_preview_boundary(self):
        rows=[grad(),trade(100,2),{'type':'end','t':2000,'mint':'coin','why':'budget'},trade(3100,3)]
        self.assertEqual(run(rows)['results'][0]['status'],'no_timely_exit')
        self.assertEqual(run([grad(),trade(100,2),trade(3100,3)],end=2000)['results'][0]['status'],'no_timely_exit')

    def test_gap_and_resume_exclude_return(self):
        for events in ([{'type':'down','t':200},{'type':'up','t':300}],
                       [{'type':'resume','t':200,'mint':'coin'}],
                       [{'type':'down','t':200}]):
            r=run([grad(),trade(100,2),*events,trade(3100,3)])['results'][0]
            self.assertEqual(r['status'],'coverage_gap'); self.assertIsNone(r['net_pnl_sol'])

    def test_gap_before_graduation_not_disqualifying(self):
        r=run([{'type':'down','t':-200},{'type':'up','t':-100},grad(),trade(100,2),trade(3100,3)])['results'][0]
        self.assertEqual(r['status'],'closed')

    def test_unfollowed_and_late_preserved(self):
        for kw,status in [({'follow':False},'not_followed'),({'late':True},'late_pickup'),({'followFrom':10},'late_pickup')]:
            r=run([grad(**kw)])
            self.assertEqual(r['results'][0]['status'],status); self.assertEqual(r['summary']['graduates'],1)

    def test_backfill_not_used_as_live_fill(self):
        r=run([grad(),trade(100,2,src='backfill')])['results'][0]
        self.assertEqual(r['status'],'backfill_present')

    def test_invalid_reserves_not_zero(self):
        for q in (None,float('nan'),-1):
            self.assertEqual(run([grad(),trade(100,2,q)])['results'][0]['status'],'missing_or_invalid_reserves')
        g=grad(); del g['virtualQuote']
        self.assertEqual(run([g,trade(100,2)])['results'][0]['status'],'missing_or_invalid_reserves')

    def test_virtual_reserve_updates(self):
        marks,error=slot_marks({'grad':grad(),'trades':[trade(100,2,virtualQuote=20),trade(200,3)]})
        self.assertIsNone(error); self.assertAlmostEqual(marks[-1]['price'],.12)

    def test_order_and_duplicates(self):
        with self.assertRaises(ValueError): run([grad(),trade(200,2),trade(100,3)])
        with self.assertRaises(ValueError): run([grad(),grad()])
        with self.assertRaises(ValueError): run([grad(),trade(100,2,sig='x',i=0),trade(200,2,sig='x',i=0)])
        self.assertEqual(run([grad(),trade(100,3),trade(200,2)])['results'][0]['status'],'out_of_order_slots')

    def test_bad_rules_rejected(self):
        for kw in ({'size_sol':0},{'fee_bps':10000},{'slippage_bps':float('nan')},{'latency_seconds':-1},{'exit_seconds':0},{'exit_seconds':1801}):
            with self.assertRaises(ValueError): replace(Rules(),**kw).validate()

    def test_costs_reduce_result(self):
        rows=[grad(),trade(100,2),trade(3100,3)]
        free=run(rows)['results'][0]['net_pnl_sol']
        costly=run(rows,Rules(exit_seconds=3,latency_seconds=0))['results'][0]['net_pnl_sol']
        self.assertLess(costly,free)

    def test_public_preview_reproducible_and_exclusions_preserved(self):
        rows,meta=load_preview(); result=replay(rows,Rules(),meta['source_window_end_ms'])
        self.assertEqual(result['summary']['graduates'],5)
        self.assertEqual(result['summary']['closed'],4)
        self.assertEqual([r['mint'] for r in result['results']],meta['mints'])
        self.assertEqual(sum(r['status']=='not_followed' for r in result['results']),1)
        self.assertTrue(all(r['exit_slot']>r['entry_slot'] for r in result['results'] if r['status']=='closed'))

    def test_cli_json_and_bad_parameter(self):
        root=Path(__file__).resolve().parents[1]
        p=subprocess.run([sys.executable,str(root/'backtest.py'),'--json'],capture_output=True,text=True)
        self.assertEqual(p.returncode,0); self.assertEqual(json.loads(p.stdout)['summary']['graduates'],5)
        p=subprocess.run([sys.executable,str(root/'backtest.py'),'--size-sol','nan'],capture_output=True,text=True)
        self.assertEqual(p.returncode,2); self.assertIn('finite',p.stderr)


if __name__=='__main__': unittest.main()
