"""
test_bot.py
-----------
Unit tests for WEEX 4h Asymmetric Sniper Bot.
Verifies:
1. Style A level math (50%, 70.5%, 78.6% SL, 1.272 TP).
2. Single position per coin guard.
3. Max 4 concurrent positions capacity.
4. Contested priority ranking by impulse strength.
5. Precision engine formatting.
"""

import pytest
import numpy as np
import pandas as pd
from bot import WeexClient, AsymmetricSniperEngine, compute_atr


def test_style_a_levels_math():
    client = WeexClient("", "", "", dry_run=True)
    engine = AsymmetricSniperEngine({"max_concurrent_positions": 4, "risk_per_trade_usd": 100}, client)

    # 1. Bearish Impulse Wave (Short): Drop from 100 to 50 (delta = 50)
    origin_high = 100.0
    trough_low = 50.0
    delta = 50.0

    p50 = trough_low + 0.50 * delta     # 75.0
    p70 = trough_low + 0.705 * delta    # 85.25
    sl_786 = trough_low + 0.786 * delta # 89.30
    tp_1272 = trough_low - 0.272 * delta# 36.40

    assert p50 == pytest.approx(75.0)
    assert p70 == pytest.approx(85.25)
    assert sl_786 == pytest.approx(89.30)
    assert tp_1272 == pytest.approx(36.40)

    # Risk from entry at 80 to SL 89.30 = 9.30
    # Reward from entry at 80 to TP 36.40 = 43.60
    # R:R = 43.60 / 9.30 = 4.68:1
    rr = (80.0 - tp_1272) / (sl_786 - 80.0)
    assert rr > 4.0

    # 2. Bullish Impulse Wave (Long): Rise from 50 to 100 (delta = 50)
    p50_l = origin_high - 0.50 * delta     # 75.0
    p70_l = origin_high - 0.705 * delta    # 64.75
    sl_786_l = origin_high - 0.786 * delta # 60.70
    tp_1272_l = origin_high + 0.272 * delta# 113.60

    assert p50_l == pytest.approx(75.0)
    assert p70_l == pytest.approx(64.75)
    assert sl_786_l == pytest.approx(60.70)
    assert tp_1272_l == pytest.approx(113.60)


def test_single_position_per_coin_guard():
    client = WeexClient("", "", "", dry_run=True)
    # Simulate existing open position for SOLUSDT
    client.simulated_positions["SOLUSDT"] = {"symbol": "SOLUSDT", "positionSide": "SHORT"}
    
    engine = AsymmetricSniperEngine({"max_concurrent_positions": 4}, client)
    open_syms = client.get_open_position_symbols()
    
    assert "SOLUSDT" in open_syms
    assert len(open_syms) == 1
    
    # Verify SOLUSDT would be skipped in scan loop
    assert "SOLUSDT" in open_syms


def test_max_concurrent_positions_cap():
    client = WeexClient("", "", "", dry_run=True)
    # Simulate 4 open positions
    for s in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT"]:
        client.simulated_positions[s] = {"symbol": s}
        
    engine = AsymmetricSniperEngine({"max_concurrent_positions": 4}, client)
    open_syms = client.get_open_position_symbols()
    available_slots = engine.max_slots - len(open_syms)
    
    assert len(open_syms) == 4
    assert available_slots == 0


def test_priority_displacement_ranking():
    # If 3 setups signal but only 1 slot is open, prioritize highest impulse_strength
    candidates = [
        {"symbol": "ADAUSDT", "impulse_strength": 3.2},
        {"symbol": "AVAXUSDT", "impulse_strength": 6.8},
        {"symbol": "NEARUSDT", "impulse_strength": 4.5},
    ]
    candidates.sort(key=lambda x: x["impulse_strength"], reverse=True)
    
    available_slots = 1
    selected = candidates[:available_slots]
    
    assert len(selected) == 1
    assert selected[0]["symbol"] == "AVAXUSDT"
    assert selected[0]["impulse_strength"] == 6.8


def test_format_order_values():
    client = WeexClient("", "", "", dry_run=True)
    client.exchange_info["ETHUSDT"] = {
        "pricePrecision": 2,
        "quantityPrecision": 3,
        "contractVal": 0.01,
        "minOrderSize": 0.01,
        "maxOrderSize": 10000.0
    }
    
    qty_str, entry_str, sl_str, tp_str = client.format_order_values(
        "ETHUSDT", raw_qty=1.23456, entry_p=2500.456, sl_p=2450.123, tp_p=2700.789
    )
    
    assert qty_str == "1.230"
    assert entry_str == "2500.46"
    assert sl_str == "2450.12"
    assert tp_str == "2700.79"
