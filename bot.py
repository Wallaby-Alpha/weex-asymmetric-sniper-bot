"""
weex-asymmetric-sniper-bot / bot.py
-----------------------------------
Production 4-Hour Asymmetric Sniper Trading Bot for WEEX Perpetuals.

Core Strategy (Style A):
1. Macro Regime Filter (Zero-Lookahead):
   - Fetches closed daily BTC klines.
   - Longs eligible ONLY when BTC Daily Close >= 50-day EMA.
   - Shorts eligible ONLY when BTC Daily Close < 50-day EMA.
2. 4-Hour Impulse Displacement:
   - Expansion >= 3.0x ATR(14) within 3 to 6 bars.
3. Fibonacci Retracement Entry Zone:
   - 50.0% to 70.5% discount/premium zone.
4. Style A Invalidation & Targets:
   - Stop Loss: Placed at exact 78.6% Fibonacci retracement level.
   - Take Profit: Placed at exact 1.272 Fibonacci extension level.
   - Typical Design R:R: 3.7:1 to 4.5:1.
5. Strict Risk & Portfolio Controls:
   - Max Concurrent Positions: 4 simultaneous positions portfolio-wide.
   - Single Position Guard: NEVER enter a second position on the same coin.
   - Priority Displacement Allocation: When contested, top setups ranked by Delta / ATR.
   - Native Exchange-Level Brackets: Submits preset SL & TP directly to WEEX matching engine.
   - Telegram alerts on entries, exits, errors, and 4h cadence heartbeats.
"""

import os
import sys
import json
import time
import hmac
import hashlib
import base64
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Set
import requests
import pandas as pd
import numpy as np

# Ensure UTF-8 output on all systems
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("weex_sniper.log", mode="a", encoding="utf-8")
    ]
)
logger = logging.getLogger("weex_sniper")

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
EXAMPLE_CONFIG_PATH = SCRIPT_DIR / "config.example.json"
STATE_FILE = SCRIPT_DIR / "sniper_state.json"


def load_config() -> Dict[str, Any]:
    default_cfg = {
        "api_key": os.environ.get("WEEX_API_KEY", ""),
        "secret_key": os.environ.get("WEEX_SECRET_KEY", ""),
        "passphrase": os.environ.get("WEEX_PASSPHRASE", ""),
        "dry_run": True,
        "max_concurrent_positions": 4,
        "risk_per_trade_usd": 100.0,
        "top_universe_count": 200,
        "min_atr_expansion": 3.0,
        "min_impulse_bars": 3,
        "max_impulse_bars": 6,
        "max_wait_bars": 24,
        "btc_symbol": "BTCUSDT",
        "btc_ema_span": 50,
        "poll_interval_seconds": 60,
        "telegram_bot_token": os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        "telegram_chat_id": os.environ.get("TELEGRAM_CHAT_ID", "")
    }

    candidates = [CONFIG_PATH, EXAMPLE_CONFIG_PATH]
    for c in candidates:
        if c.exists():
            try:
                with open(c, "r", encoding="utf-8") as f:
                    file_cfg = json.load(f)
                    default_cfg.update(file_cfg)
                    logger.info(f"Loaded configuration from {c}")
                    break
            except Exception as e:
                logger.warning(f"Error reading {c}: {e}")

    return default_cfg


def send_telegram(token: str, chat_id: str, text: str) -> bool:
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        logger.error(f"Telegram alert delivery failed: {e}")
        return False


def compute_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    tr = np.zeros(len(close))
    tr[0] = high[0] - low[0]
    for i in range(1, len(close)):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    atr = pd.Series(tr).rolling(period).mean().values
    return atr


class WeexClient:
    """Production client for WEEX V3 Contract API."""
    def __init__(self, api_key: str, secret_key: str, passphrase: str, dry_run: bool = True):
        self.base_url = "https://api-contract.weex.com"
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.dry_run = dry_run
        self.simulated_positions: Dict[str, Dict[str, Any]] = {}
        self.exchange_info: Dict[str, Dict[str, Any]] = {}
        self.refresh_exchange_info()

    def _generate_signature(self, timestamp: str, method: str, path: str, query: str = "", body: str = "") -> str:
        message = f"{timestamp}{method.upper()}{path}{query}{body}"
        mac = hmac.new(self.secret_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256)
        return base64.b64encode(mac.digest()).decode("utf-8")

    def _get_headers(self, method: str, path: str, query: str = "", body: str = "") -> Dict[str, str]:
        ts = str(int(time.time() * 1000))
        sign = self._generate_signature(ts, method, path, query, body)
        return {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": sign,
            "ACCESS-TIMESTAMP": ts,
            "ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json"
        }

    def refresh_exchange_info(self):
        url = f"{self.base_url}/capi/v3/market/exchangeInfo"
        try:
            r = requests.get(url, timeout=12)
            if r.status_code == 200:
                symbols = r.json().get("symbols", [])
                meta = {}
                for s in symbols:
                    sym = s.get("symbol", "")
                    meta[sym] = {
                        "pricePrecision": int(s.get("pricePrecision", 4)),
                        "quantityPrecision": int(s.get("quantityPrecision", 2)),
                        "contractVal": float(s.get("contractVal", 1.0)),
                        "minOrderSize": float(s.get("minOrderSize", 1.0)),
                        "maxOrderSize": float(s.get("maxOrderSize", 100000000.0)),
                    }
                self.exchange_info = meta
                logger.info(f"Loaded exchange trading specifications for {len(self.exchange_info)} symbols.")
        except Exception as e:
            logger.warning(f"Failed to fetch exchangeInfo: {e}")

    def format_order_values(
        self, symbol: str, raw_qty: float, entry_p: float, sl_p: float, tp_p: float
    ) -> Tuple[str, str, str, str]:
        info = self.exchange_info.get(symbol, {
            "pricePrecision": 4,
            "quantityPrecision": 2,
            "contractVal": 1.0,
            "minOrderSize": 0.0001
        })

        c_val = info.get("contractVal", 1.0)
        min_size = info.get("minOrderSize", 0.0001)
        step = c_val if c_val > 0 else min_size

        if step > 0:
            lots = int(raw_qty / step)
            qty = lots * step
        else:
            qty = raw_qty

        if qty < min_size:
            formatted_qty = "0"
        elif info.get("quantityPrecision", 2) > 0:
            formatted_qty = f"{qty:.{info['quantityPrecision']}f}"
        else:
            formatted_qty = str(int(qty))

        p_prec = info.get("pricePrecision", 4)
        formatted_entry = f"{entry_p:.{p_prec}f}"
        formatted_sl = f"{sl_p:.{p_prec}f}"
        formatted_tp = f"{tp_p:.{p_prec}f}"

        return formatted_qty, formatted_entry, formatted_sl, formatted_tp

    def get_top_contracts_by_volume(self, top_n: int = 200) -> List[str]:
        url = f"{self.base_url}/capi/v3/market/ticker/24hr"
        try:
            r = requests.get(url, timeout=12)
            if r.status_code != 200:
                logger.error(f"Failed to fetch tickers: {r.status_code} {r.text}")
                return []
            tickers = r.json()
            if not isinstance(tickers, list):
                return []
            usdt_tickers = []
            bad_tokens = ["STOCK", "OIL", "XAUT", "GOLD", "ETF", "NVDA", "AAPL", "TSLA", "MSFT", "AMZN"]
            for t in tickers:
                sym = t.get("symbol", "")
                if sym.endswith("USDT") or sym.endswith("_USDT"):
                    if any(b in sym for b in bad_tokens):
                        continue
                    vol = float(t.get("quoteVolume", 0.0) or t.get("volume", 0.0) or 0.0)
                    usdt_tickers.append((sym, vol))

            usdt_tickers.sort(key=lambda x: x[1], reverse=True)
            top_syms = [x[0] for x in usdt_tickers[:top_n]]
            return top_syms
        except Exception as e:
            logger.error(f"Error fetching top contracts: {e}")
            return []

    def get_klines(self, symbol: str, granularity: str = "4h", limit: int = 100) -> Optional[pd.DataFrame]:
        url = f"{self.base_url}/capi/v3/market/klines"
        gran_map = {"1h": "1h", "4h": "4h", "1d": "1d"}
        params = {
            "symbol": symbol,
            "interval": gran_map.get(granularity, "4h"),
            "limit": limit
        }
        try:
            r = requests.get(url, params=params, timeout=10)
            if r.status_code != 200:
                return None
            data = r.json()
            if not isinstance(data, list) or len(data) < 20:
                return None
            rows = []
            for d in data:
                rows.append([d[0], float(d[1]), float(d[2]), float(d[3]), float(d[4]), float(d[5])])
            df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume"])
            df["open_time"] = pd.to_datetime(df["open_time"].astype(int), unit="ms", utc=True)
            df = df.sort_values("open_time").reset_index(drop=True)
            return df
        except Exception as e:
            logger.error(f"Error fetching klines for {symbol}: {e}")
            return None

    def get_open_positions(self) -> List[Dict[str, Any]]:
        if self.dry_run:
            return list(self.simulated_positions.values())
        if not self.api_key:
            return []
        path = "/capi/v3/account/position/allPosition"
        try:
            headers = self._get_headers("GET", path)
            r = requests.get(f"{self.base_url}{path}", headers=headers, timeout=10)
            if r.status_code == 200:
                res_data = r.json()
                pos_list = res_data.get("data", []) if isinstance(res_data, dict) else res_data
                active = [p for p in pos_list if float(p.get("total", 0.0) or p.get("size", 0.0) or 0.0) > 0]
                return active
            return []
        except Exception as e:
            logger.warning(f"Could not fetch positions: {e}")
            return []

    def get_open_position_symbols(self) -> Set[str]:
        positions = self.get_open_positions()
        return {p.get("symbol", "") for p in positions if p.get("symbol")}

    def place_native_bracket_order(
        self,
        symbol: str,
        side: str,                 # "BUY" or "SELL"
        position_side: str,        # "LONG" or "SHORT"
        raw_quantity: float,
        entry_price: float,
        sl_price: float,
        tp_price: float
    ) -> Dict[str, Any]:
        """Submits order with native exchange-level stop loss and take profit attached."""
        qty_str, entry_str, sl_str, tp_str = self.format_order_values(
            symbol, raw_quantity, entry_price, sl_price, tp_price
        )

        if qty_str == "0":
            err_msg = f"Quantity {raw_quantity:.4f} is below minimum allowed size for {symbol}"
            logger.error(f"❌ Order Rejected: {err_msg}")
            return {"status": "error", "error": {"msg": err_msg, "code": "MIN_SIZE_VIOLATION"}}

        if self.dry_run:
            sim_pos = {
                "symbol": symbol,
                "side": side,
                "positionSide": position_side,
                "quantity": qty_str,
                "entryPrice": entry_str,
                "slPrice": sl_str,
                "tpPrice": tp_str,
                "timestamp": int(time.time())
            }
            self.simulated_positions[symbol] = sim_pos
            logger.info(f"[DRY-RUN SIMULATION] {side} {position_side} {qty_str} {symbol} | Entry: {entry_str} | SL: {sl_str} | TP: {tp_str}")
            return {
                "status": "simulated",
                "orderId": f"sim_{int(time.time()*1000)}",
                "formatted_qty": qty_str,
                "formatted_entry": entry_str,
                "formatted_sl": sl_str,
                "formatted_tp": tp_str
            }

        path = "/capi/v3/order"
        payload = {
            "symbol": symbol,
            "side": side.upper(),
            "positionSide": position_side.upper(),
            "type": "MARKET",
            "quantity": qty_str,
            "slTriggerPrice": sl_str,
            "tpTriggerPrice": tp_str,
            "SlWorkingType": "MARK_PRICE",
            "TpWorkingType": "MARK_PRICE",
            "newClientOrderId": f"snp_{int(time.time()*1000)}"
        }
        body_str = json.dumps(payload)
        headers = self._get_headers("POST", path, body=body_str)

        logger.info(f"Submitting WEEX V3 Contract Order: {payload}")
        try:
            r = requests.post(f"{self.base_url}{path}", headers=headers, data=body_str, timeout=12)
            res = r.json()
            if r.status_code == 200 and res.get("code") == "00000":
                logger.info(f"✅ Live WEEX Order Placed Successfully: {symbol} {side} | Response: {res}")
                return {
                    "status": "success",
                    "orderId": res.get("data", {}).get("orderId", "N/A"),
                    "data": res.get("data"),
                    "formatted_qty": qty_str,
                    "formatted_entry": entry_str,
                    "formatted_sl": sl_str,
                    "formatted_tp": tp_str
                }
            else:
                logger.error(f"❌ WEEX Order Failed: {r.status_code} | {res}")
                return {"status": "error", "error": res}
        except Exception as e:
            logger.error(f"Exception during order placement: {e}")
            return {"status": "error", "error": str(e)}


class AsymmetricSniperEngine:
    """Core Strategy Engine enforcing Regime, Impulse, Retracement, and Invalidation rules."""
    def __init__(self, config: Dict[str, Any], client: WeexClient):
        self.cfg = config
        self.client = client
        self.max_slots = int(self.cfg.get("max_concurrent_positions", 4))
        self.risk_usd = float(self.cfg.get("risk_per_trade_usd", 100.0))
        self.min_atr_expansion = float(self.cfg.get("min_atr_expansion", 3.0))
        self.min_impulse_bars = int(self.cfg.get("min_impulse_bars", 3))
        self.max_impulse_bars = int(self.cfg.get("max_impulse_bars", 6))
        self.max_wait_bars = int(self.cfg.get("max_wait_bars", 24))
        self.state: Dict[str, Any] = self._load_state()

    def _load_state(self) -> Dict[str, Any]:
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Error loading state: {e}")
        return {"active_trades": {}, "last_heartbeat_time": 0}

    def _save_state(self):
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to persist state: {e}")

    def evaluate_btc_regime(self) -> Tuple[str, float, float]:
        """
        Fetches BTC 1D klines and computes 50-day EMA without lookahead.
        Returns: (regime_str: "BULL" | "BEAR", btc_close, btc_50_ema)
        """
        btc_symbol = self.cfg.get("btc_symbol", "BTCUSDT")
        df_1d = self.client.get_klines(btc_symbol, granularity="1d", limit=80)
        if df_1d is None or len(df_1d) < 55:
            logger.warning("Could not calculate BTC Daily Regime! Defaulting to NEUTRAL.")
            return "NEUTRAL", 0.0, 0.0

        # Discard the last unclosed candle to guarantee zero lookahead
        closed_df = df_1d.iloc[:-1].copy().reset_index(drop=True)
        closes = closed_df["close"].astype(float)
        ema50 = closes.ewm(span=self.cfg.get("btc_ema_span", 50), adjust=False).mean()

        latest_close = float(closes.iloc[-1])
        latest_ema = float(ema50.iloc[-1])

        regime = "BULL" if latest_close >= latest_ema else "BEAR"
        return regime, latest_close, latest_ema

    def detect_sniper_setup(self, df_4h: pd.DataFrame, symbol: str, regime: str) -> Optional[Dict[str, Any]]:
        """
        Scans closed 4h candles for a confirmed impulse wave and checks if current price
        is in the 50% - 70.5% retracement zone without breaching the 78.6% SL.
        """
        if len(df_4h) < 30:
            return None

        # Exclude currently forming candle to strictly avoid lookahead
        closed_df = df_4h.iloc[:-1].copy().reset_index(drop=True)
        highs = closed_df["high"].values.astype(float)
        lows = closed_df["low"].values.astype(float)
        closes = closed_df["close"].values.astype(float)
        atrs = compute_atr(highs, lows, closes, 14)

        current_candle = df_4h.iloc[-1]
        current_price = float(current_candle["close"])
        n = len(closed_df)

        side = "LONG" if regime == "BULL" else "SHORT"

        # Look back up to max_wait_bars to find confirmed impulse
        for i in range(n - 1, max(14, n - 1 - self.max_wait_bars), -1):
            cur_atr = atrs[i]
            if np.isnan(cur_atr) or cur_atr <= 0:
                continue

            is_impulse = False
            origin_price = 0.0
            extreme_price = 0.0

            if side == "LONG":
                # Upward impulse (rise >= 3.0x ATR in 3 to 6 bars)
                for w in range(self.min_impulse_bars, self.max_impulse_bars + 1):
                    if i - w < 0:
                        continue
                    sub_l = np.min(lows[i - w:i + 1])
                    sub_h = np.max(highs[i - w:i + 1])
                    if highs[i] >= sub_h * 0.999 and (sub_h - sub_l) >= self.min_atr_expansion * cur_atr:
                        is_impulse = True
                        origin_price = sub_l
                        extreme_price = sub_h
                        break
            else:
                # Downward impulse (drop >= 3.0x ATR in 3 to 6 bars)
                for w in range(self.min_impulse_bars, self.max_impulse_bars + 1):
                    if i - w < 0:
                        continue
                    sub_h = np.max(highs[i - w:i + 1])
                    sub_l = np.min(lows[i - w:i + 1])
                    if lows[i] <= sub_l * 1.001 and (sub_h - sub_l) >= self.min_atr_expansion * cur_atr:
                        is_impulse = True
                        origin_price = sub_h
                        extreme_price = sub_l
                        break

            if not is_impulse or origin_price == extreme_price:
                continue

            delta = abs(origin_price - extreme_price)
            impulse_strength = delta / cur_atr

            # Style A Fibonacci Calculation
            if side == "LONG":
                p50 = extreme_price - 0.50 * delta
                p70 = extreme_price - 0.705 * delta
                sl_price = extreme_price - 0.786 * delta
                tp_price = extreme_price + 0.272 * delta
                inval_beyond = extreme_price * 1.002

                # Subsequent price activity check between impulse completion and now
                subsequent_highs = highs[i + 1:] if i + 1 < n else []
                subsequent_lows = lows[i + 1:] if i + 1 < n else []
                if len(subsequent_highs) > 0 and np.max(subsequent_highs) > inval_beyond:
                    continue # Impulse broke higher, setup superseded
                if len(subsequent_lows) > 0 and np.min(subsequent_lows) <= sl_price:
                    continue # Already breached 78.6% SL

                # Current price condition: inside the 50% to 70.5% retracement zone
                if p70 <= current_price <= p50:
                    risk = current_price - sl_price
                    reward = tp_price - current_price
                    if risk <= 0 or reward <= 0:
                        continue
                    rr = reward / risk
                    return {
                        "symbol": symbol,
                        "side": "BUY",
                        "positionSide": "LONG",
                        "current_price": current_price,
                        "sl_price": sl_price,
                        "tp_price": tp_price,
                        "risk_per_unit": risk,
                        "reward_per_unit": reward,
                        "rr_ratio": rr,
                        "impulse_strength": impulse_strength,
                        "origin_price": origin_price,
                        "extreme_price": extreme_price
                    }

            else: # SHORT
                p50 = extreme_price + 0.50 * delta
                p70 = extreme_price + 0.705 * delta
                sl_price = extreme_price + 0.786 * delta
                tp_price = extreme_price - 0.272 * delta
                inval_beyond = extreme_price * 0.998

                subsequent_lows = lows[i + 1:] if i + 1 < n else []
                subsequent_highs = highs[i + 1:] if i + 1 < n else []
                if len(subsequent_lows) > 0 and np.min(subsequent_lows) < inval_beyond:
                    continue # Impulse dumped lower, setup superseded
                if len(subsequent_highs) > 0 and np.max(subsequent_highs) >= sl_price:
                    continue # Already breached 78.6% SL

                # Current price condition: inside the 50% to 70.5% retracement zone
                if p50 <= current_price <= p70:
                    risk = sl_price - current_price
                    reward = current_price - tp_price
                    if risk <= 0 or reward <= 0:
                        continue
                    rr = reward / risk
                    return {
                        "symbol": symbol,
                        "side": "SELL",
                        "positionSide": "SHORT",
                        "current_price": current_price,
                        "sl_price": sl_price,
                        "tp_price": tp_price,
                        "risk_per_unit": risk,
                        "reward_per_unit": reward,
                        "rr_ratio": rr,
                        "impulse_strength": impulse_strength,
                        "origin_price": origin_price,
                        "extreme_price": extreme_price
                    }

        return None

    def execute_scan_cycle(self):
        """Runs a complete scanning and execution cycle across the WEEX contract universe."""
        logger.info("----------------------------------------------------------------------")
        logger.info("Starting WEEX 4h Asymmetric Sniper Scan Cycle...")

        # 1. Macro Regime Check
        regime, btc_close, btc_ema = self.evaluate_btc_regime()
        logger.info(f"🌐 [BTC REGIME] Price: ${btc_close:,.1f} | 50 EMA: ${btc_ema:,.1f} | Regime: {regime}")
        if regime not in ("BULL", "BEAR"):
            logger.warning("Unconfirmed regime. Skipping scan cycle.")
            return

        # 2. Open Positions & Portfolio Capacity Check
        open_symbols = self.client.get_open_position_symbols()
        current_open_count = len(open_symbols)
        available_slots = self.max_slots - current_open_count

        logger.info(f"📊 [PORTFOLIO SLOTS] Open: {current_open_count}/{self.max_slots} | Available Slots: {available_slots} | Open Coins: {list(open_symbols)}")

        if available_slots <= 0:
            logger.info(f"🔒 Portfolio slot capacity reached ({self.max_slots}/{self.max_slots}). No new entries permitted.")
            return

        # 3. Fetch Universe & Scan for Setups
        universe_symbols = self.client.get_top_contracts_by_volume(self.cfg.get("top_universe_count", 200))
        logger.info(f"🔍 Scanning Top {len(universe_symbols)} liquid contracts for {regime} impulse retracements...")

        candidate_setups: List[Dict[str, Any]] = []

        for sym in universe_symbols:
            # Rule: NEVER enter a second position on the same coin!
            if sym in open_symbols:
                continue

            df_4h = self.client.get_klines(sym, granularity="4h", limit=50)
            if df_4h is None or len(df_4h) < 30:
                continue

            setup = self.detect_sniper_setup(df_4h, sym, regime)
            if setup is not None:
                logger.info(f"🎯 [SETUP DETECTED] {sym} {setup['positionSide']} | Price: {setup['current_price']} | SL (78.6%): {setup['sl_price']:.4f} | TP (1.272): {setup['tp_price']:.4f} | R:R: {setup['rr_ratio']:.2f} | Impulse/ATR: {setup['impulse_strength']:.2f}x")
                candidate_setups.append(setup)

        logger.info(f"Found {len(candidate_setups)} valid setups across the market.")

        if not candidate_setups:
            return

        # 4. Displacement Priority Ranking: Allocate contested slots to highest Impulse/ATR
        candidate_setups.sort(key=lambda x: x["impulse_strength"], reverse=True)
        selected_setups = candidate_setups[:available_slots]

        logger.info(f"Allocating {len(selected_setups)} available slots to top-ranked displacement setups.")

        # 5. Order Placement with Native Brackets & Telegram Alerting
        tg_token = self.cfg.get("telegram_bot_token", "")
        tg_chat = self.cfg.get("telegram_chat_id", "")

        for s in selected_setups:
            sym = s["symbol"]
            side = s["side"]
            pos_side = s["positionSide"]
            entry_p = s["current_price"]
            sl_p = s["sl_price"]
            tp_p = s["tp_price"]
            risk_unit = s["risk_per_unit"]

            # Position sizing based on fixed USD risk
            raw_qty = self.risk_usd / risk_unit if risk_unit > 0 else 0.0

            logger.info(f"Executing Bracket Order: {sym} {side} {pos_side} | Qty: {raw_qty:.4f} | Entry: {entry_p} | SL: {sl_p:.4f} | TP: {tp_p:.4f}")

            order_res = self.client.place_native_bracket_order(
                symbol=sym,
                side=side,
                position_side=pos_side,
                raw_quantity=raw_qty,
                entry_price=entry_p,
                sl_price=sl_p,
                tp_price=tp_p
            )

            if order_res.get("status") in ("success", "simulated"):
                self.state["active_trades"][sym] = {
                    "side": pos_side,
                    "entry_price": order_res.get("formatted_entry", str(entry_p)),
                    "sl_price": order_res.get("formatted_sl", str(sl_p)),
                    "tp_price": order_res.get("formatted_tp", str(tp_p)),
                    "qty": order_res.get("formatted_qty", str(raw_qty)),
                    "order_id": order_res.get("orderId"),
                    "entry_time": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                    "impulse_strength": s["impulse_strength"],
                    "rr_ratio": s["rr_ratio"]
                }
                self._save_state()

                # Dispatch Telegram Notification
                msg = (
                    f"🎯 <b>WEEX ASYMMETRIC SNIPER ENTRY</b>\n\n"
                    f"<b>Symbol:</b> {sym}\n"
                    f"<b>Direction:</b> {pos_side} ({side})\n"
                    f"<b>Entry Price:</b> {order_res.get('formatted_entry', entry_p)}\n"
                    f"<b>Native Stop Loss (78.6% Fib):</b> {order_res.get('formatted_sl', sl_p)}\n"
                    f"<b>Native Take Profit (1.272 Ext):</b> {order_res.get('formatted_tp', tp_p)}\n"
                    f"<b>Design R:R:</b> {s['rr_ratio']:.2f}:1\n"
                    f"<b>Impulse Strength:</b> {s['impulse_strength']:.2f}x ATR\n"
                    f"<b>Risk Allocated:</b> ${self.risk_usd:.2f} USDT\n"
                    f"<b>Execution Mode:</b> {'DRY-RUN' if self.cfg.get('dry_run') else 'LIVE WEEX BRACKET'}\n"
                    f"<b>Active Portfolio Slots:</b> {len(self.state['active_trades'])}/{self.max_slots}"
                )
                send_telegram(tg_token, tg_chat, msg)

    def send_cadence_heartbeat(self):
        """Sends periodic 4h telegram heartbeat with regime and open positions status."""
        now = time.time()
        last_hb = self.state.get("last_heartbeat_time", 0)
        # Send heartbeat roughly every 4 hours (14400s)
        if now - last_hb < 14400:
            return

        regime, btc_close, btc_ema = self.evaluate_btc_regime()
        open_symbols = self.client.get_open_position_symbols()

        tg_token = self.cfg.get("telegram_bot_token", "")
        tg_chat = self.cfg.get("telegram_chat_id", "")

        msg = (
            f"⚡ <b>WEEX SNIPER HEARTBEAT</b>\n\n"
            f"<b>BTC Regime:</b> {regime} (BTC ${btc_close:,.1f} vs 50 EMA ${btc_ema:,.1f})\n"
            f"<b>Active Positions:</b> {len(open_symbols)}/{self.max_slots}\n"
            f"<b>Open Coins:</b> {', '.join(open_symbols) if open_symbols else 'None (100% Cash)'}\n"
            f"<b>Mode:</b> {'DRY-RUN' if self.cfg.get('dry_run') else 'LIVE TRADING'}\n"
            f"<b>System Status:</b> Healthy & Monitoring"
        )
        send_telegram(tg_token, tg_chat, msg)
        self.state["last_heartbeat_time"] = now
        self._save_state()

    def run_loop(self):
        logger.info("WEEX 4h Asymmetric Sniper Bot daemon initialized.")
        tg_token = self.cfg.get("telegram_bot_token", "")
        tg_chat = self.cfg.get("telegram_chat_id", "")
        send_telegram(tg_token, tg_chat, "🚀 <b>WEEX Asymmetric Sniper Bot Started</b>\nMonitoring 4h candles with 4-slot portfolio capacity.")

        while True:
            try:
                self.execute_scan_cycle()
                self.send_cadence_heartbeat()
            except Exception as e:
                logger.error(f"Error in scan loop: {e}", exc_info=True)
                send_telegram(tg_token, tg_chat, f"⚠️ <b>WEEX Sniper Bot Error:</b> {e}")

            poll_sec = int(self.cfg.get("poll_interval_seconds", 60))
            time.sleep(poll_sec)


def main():
    cfg = load_config()
    client = WeexClient(
        api_key=cfg.get("api_key", ""),
        secret_key=cfg.get("secret_key", ""),
        passphrase=cfg.get("passphrase", ""),
        dry_run=cfg.get("dry_run", True)
    )
    engine = AsymmetricSniperEngine(cfg, client)

    if "--scan-once" in sys.argv:
        engine.execute_scan_cycle()
    else:
        engine.run_loop()


if __name__ == "__main__":
    main()
