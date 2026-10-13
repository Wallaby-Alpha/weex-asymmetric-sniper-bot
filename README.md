# WEEX 4-Hour Asymmetric Sniper Bot

A quantitative, regime-gated trading bot operating on WEEX USDT Perpetuals.

Based on empirical cross-sectional research across 136 crypto assets, the bot exploits deep Fibonacci retracements inside strong 4-hour institutional impulses with native exchange-level stop losses and profit targets.

---

## 🏛️ Strategy Specifications (Style A)

1. **Directional Macro Regime Filter (Zero-Lookahead):**
   - Continuously evaluates closed daily BTC candles against the **50-day EMA**.
   - **Bull Market (BTC $\ge$ 50 EMA):** Scans exclusively for **LONG** setups.
   - **Bear Market (BTC $<$ 50 EMA):** Scans exclusively for **SHORT** setups.
2. **4-Hour Impulse Displacement:**
   - Detects violent institutional moves where price expands $\ge 3.0 \times \text{ATR}_{14}$ within 3 to 6 candles.
3. **Fibonacci Entry Zone:**
   - Enters when price pulls back into the **50.0% to 70.5% retracement zone**.
4. **Style A Risk Management (Native Exchange Brackets):**
   - **Stop Loss:** Exact **78.6% Fibonacci retracement level**.
   - **Take Profit:** Exact **1.272 Fibonacci extension level**.
   - **Design R:R:** ~3.7:1 to 4.5:1.
   - Brackets are submitted directly via `/capi/v3/order` with `slTriggerPrice` and `tpTriggerPrice` using `MARK_PRICE`.
5. **Portfolio Exposure & Sizing Rules:**
   - **Max Concurrent Positions:** Strictly capped at **4 simultaneous open positions** portfolio-wide (empirically derived sweet spot for Calmar & drawdown control).
   - **Single Position Per Coin Guard:** Strictly enforces 1 position maximum per coin. Never pyramids or takes a second position on an active coin.
   - **Displacement Priority Allocation:** If more candidate setups trigger than available open slots, priority is awarded to the highest **Impulse Strength ($\Delta / \text{ATR}_{14}$)**.
   - **Telegram Alerts:** Instant notifications on entries, SL/TP brackets, 4h cadence heartbeats, and order errors.

---

## 🚀 Droplet Deployment Instructions

### 1. SSH into your DigitalOcean Droplet
```bash
ssh root@<YOUR_DROPLET_IP>
```

### 2. Clone the Repository
```bash
git clone https://github.com/Wallaby-Alpha/weex-asymmetric-sniper-bot.git
cd weex-asymmetric-sniper-bot
```

### 3. Run Automated Setup
```bash
chmod +x setup_droplet.sh
bash setup_droplet.sh
```

### 4. Configure API Credentials
Edit `config.json` with your WEEX API keys and Telegram credentials:
```bash
nano config.json
```

```json
{
  "api_key": "YOUR_WEEX_API_KEY",
  "secret_key": "YOUR_WEEX_SECRET_KEY",
  "passphrase": "YOUR_WEEX_PASSPHRASE",
  "dry_run": false,
  "max_concurrent_positions": 4,
  "risk_per_trade_usd": 100.0,
  "telegram_bot_token": "YOUR_BOT_TOKEN",
  "telegram_chat_id": "YOUR_CHAT_ID"
}
```

### 5. Start the Systemd Service
```bash
systemctl start weex-sniper
systemctl status weex-sniper
```

### 6. View Live Logs
```bash
journalctl -u weex-sniper -f
# OR
tail -f weex_sniper.log
```

---

## 🛠️ Management Commands

| Action | Command |
| :--- | :--- |
| **Start Bot** | `systemctl start weex-sniper` |
| **Stop Bot** | `systemctl stop weex-sniper` |
| **Restart Bot** | `systemctl restart weex-sniper` |
| **Check Status** | `systemctl status weex-sniper` |
| **Single Manual Scan** | `venv/bin/python3 bot.py --scan-once` |
| **Run Unit Tests** | `venv/bin/pytest test_bot.py` |
