import unittest
from datetime import date
from research.crypto_pilot.models import Candle, ValidationError
from research.crypto_pilot.engine import BacktestEngine
class ReviewRegression(unittest.TestCase):
    def test_nonfinite_candles_rejected(self):
        for field in ('open','high','low','close','volume'):
            for v in (float('nan'),float('inf'),float('-inf')):
                kw=dict(time=date(2026,1,1),open=1.0,high=2.0,low=0.5,close=1.5,volume=1.0); kw[field]=v
                with self.subTest(field=field,value=v),self.assertRaises(ValidationError): Candle(**kw)
    def test_invalid_rates_and_cash_rejected(self):
        for field in ('fee_rate','slippage_rate'):
            for v in (float('nan'),float('inf'),-0.1,1,1.1):
                with self.subTest(field=field,value=v),self.assertRaises(ValueError): BacktestEngine(**{field:v})
        for v in (float('nan'),float('inf'),0,-1):
            with self.subTest(initial_cash=v),self.assertRaises(ValueError):BacktestEngine(initial_cash=v)
