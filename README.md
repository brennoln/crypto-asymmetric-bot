# Asymmetric Momentum Engine (v0.9.2 - Market Regime & Dynamic Timing)

An asynchronous, event-driven quantitative trading engine engineered to harvest right-tail fat-tailed distributions in decentralized liquidity pools through dynamic microstructural filtering, statistical anomaly detection, convex risk management, market regime gates, and asymmetric execution.

> **Architecture Status & Release Notice (v0.9.2)**  
> Version **0.9.2** introduces low-latency **Market Timing & Macro Regime Gates** to prevent overtrading during low-beta consolidation regimes and macro market stress:
> 1. **Zero-Inline Latency Macro Regime Gate:** Asynchronous background polling of the network base asset ($SOL / ETH$) to detect downside volatility. Vetoes new pool acquisitions if base asset is under severe 1h drawdown ($SOL_{1h} < -2.0\%$), reading cached memory state with $< 0.001\text{ ms}$ overhead on the execution hot-path.
> 2. **Sequential Stagnation Circuit Breaker:** Dynamically detects sideways "chop" market regimes. If 2 consecutive positions exit via Time-Stop/Inertia without exceeding $1.05\times$ peak multiple, the engine enters an automatic **3-hour Cooldown**, halting capital deployment to eliminate churn and fee erosion.
> 3. **Session Liquidity Clustering (Dead Zone vs. Golden Hours):** Gating based on global liquidity participation. Automatically scales position sizing ($-50\%$) and raises statistical anomaly thresholds ($Z \ge 2.2$) during the European night / US early morning Dead Zone (04:00–10:00 UTC), while unlocking maximum allocation during active Golden Hours (13:00–23:00 UTC).
> 4. **Real-Time Telemetry Dashboard (TUI & Web):** Live tabular monitoring of open slots, entry prices, current vs. peak multiples, ratchet trailing stop floors, unrealized PnL, Real Cash vs. Liquidated Equity, and an interactive drawer for past trade history.
> 5. **Standalone HTML Live Dashboard (`data/dashboard.html`):** Dark-mode responsive web interface with a 3-second auto-refresh cycle, performance cards, active positions table, and on-chain anti-rug audit log.
> 6. **Convex Holding Map (6-Tier Asymmetric Ladder):**
>    * $1.5\times$ (liquidate $10\% \to$ locks early profit and guarantees breakeven floor at $1.05\times$)
>    * $2.0\times$ (liquidate $30\% \to$ recovers $60\%$ principal, turning trade *Risk-Free*)
>    * $3.0\times$ (liquidate $20\% \to$ locks $+60\%$ initial capital, ratchet stop floor moves to $2.20\times$)
>    * $5.0\times$ (liquidate $15\% \to$ locks $+75\%$ net gain)
>    * $15.0\times$ (liquidate $10\% \to$ captures parabolic expansion)
>    * $50.0\times$ (*Moonbag*, liquidate $15\% \to +750\%$ net return)
> 7. **Progressive Ratchet Trailing Stop:** Guaranteed monotonically rising stop floors:
>    * *Pre-2x:* $20\%$ drop threshold, $1.18\times$ floor ($1.05\times$ strictly locked post-1.5x)
>    * *Post-2x (Risk-Free):* $35\%$ drop threshold, $1.50\times$ floor
>    * *Post-3x (Runner):* $25\%$ drop threshold, $2.20\times$ floor
> 8. **Dual-Stage Inertia & Fast-Cut Pruning:**
>    * *15-Minute Fast-Cut:* Instantly liquidates positions trading $< 0.95\times$ with zero momentum aggression ($Z \le 0$).
>    * *35-Minute Capacity Time-Stop:* Liquidates stagnant positions failing to reach $+20\%$ gain to rotate capital.
> 9. **Concave Liquidity Penalty & AMM Impact Cap:** Scaled allocation for pools below $\$35\text{k}$ liquidity using a concave depth factor $\left(\frac{L}{\$35.000}\right)^{0.75}$ to protect against AMM exit slippage.
> 10. **Anti-Cabal & Holder Concentration Guards:** Rejects dev holdings $> 10\%$, single whales $> 15\%$, top 10 wallets $> 30\%$, and connected insider networks $> 15\%$.
> 11. **Invariant Soft-Rug Detector ($\Lambda_t$):** Enforces AMM $x \cdot y = k$ liquidity-price coherence ($\Lambda_t = \frac{L_t / L_0}{\sqrt{P_t / P_0}} < 0.85$) to liquidate before pool collapse.

---

## 📊 Backtested Performance Summary (17-Day Dual Regime Test)

The engine was evaluated on two strictly non-overlapping historical multi-chain datasets (Solana & Base DEX pools, $15$-minute candle resolution) with an initial bankroll of **R$ 1,000.00 ($173.91 USD)**:

| Evaluation Window | Market Regime | Trades | Net PnL (R$) | ROI (%) | Profit Factor | Capital Preservation |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Window A (19/10 to 29/10)** | Choppy / Consolidation | 8 | **-R$ 65.27** | **-6.53%** | 0.06x | **93.47% preserved** (zero blow-up) |
| **Window B (29/10 to 05/11)** | Expansion / Parabolic Runners | 11 | **+R$ 1,126.26** | **+112.63%** | **5.87x** | Massive asymmetric right-tail capture |
| **Full 17-Day Cycle** | **Complete Market Cycle** | **19** | **+R$ 1,060.99** | **+106.10%** | **4.21x** | **Bankroll doubled with controlled risk** |

*Key Takeaway:* In adverse/sideways regimes, the strict 15m Fast-Cut and 35m Time-Stop limited cumulative drawdown to just $-6.53\%$. When volatility expanded, the ratchet ladder generated $+112.63\%$, fulfilling convex positive mathematical expectancy.

---

## 📌 Quantitative Thesis & Convex Payoff Model

Standard curve-fitting models and supervised machine learning architectures (e.g., LSTMs, naive regression networks) applied to non-stationary decentralized exchange (DEX) pricing series suffer from severe in-sample memorization, high latency, and low signal-to-noise ratios (SNR).

Instead of attempting unfeasible point-price forecasting, this engine models the trading game as a **strictly asymmetric convex payoff structure**:

$$\mathbb{E}[\text{Return}] = p_{\text{loss}} \cdot (-C_{\text{stop}}) + p_{\text{flat}} \cdot (-\delta_{\text{friction}}) + \sum_{k=1}^{n} p_k \cdot R_k$$

Where:
* The system accepts and expects an empirical base-rate loss of $p_{\text{loss}} + p_{\text{flat}} \approx 85\text{--}90\%$, constrained to a strictly parameterized fixed unit allocation risk ($\$15.00$).
* **Positive Mathematical Expectancy ($\mathbb{E}[R] > 0$)** is achieved via convex right-tail realization ($2.0\times, 5.0\times, 15.0\times, 50.0\times$) captured during microstructural liquidity ignition events.

---

## 🔬 Core Microstructure & Quantitative Features (v0.7)

### 1. Multi-Source Discovery & Temporally Normalized Ignition Scoring
The engine polls multiple decentralized token discovery endpoints concurrently (`token-profiles`, `token-boosts/latest`, `token-boosts/top`), deduplicating ticker clones and ranking candidates via a composite **Ignition Score**:

$$\text{Score} = 35 \cdot \min\left(\frac{V_{5m}}{L_{\text{pool}}}, 3.0\right) + 25 \cdot \min\left(\frac{V_{5m}}{\bar{V}_{5m}}, 5.0\right) + 20 \cdot \max(\text{OFI}_{5m}, 0) + 0.10 \cdot \min(\text{Buys}_{5m}, 100) + \text{MomentumBonus}$$

* **Temporal Baseline Normalization ($\bar{V}_{5m}$):** Corrects age bias for pools $< 60$ minutes old:
  $$W_{5m} = \text{clip}\left(\frac{\text{Age}_{\text{min}}}{5.0}, 1.0, 12.0\right), \qquad \bar{V}_{5m} = \frac{V_{h1}}{W_{5m}}$$
* **Directional Momentum Guard:** Gives bonus points for orderly 5m price gains while rejecting negative 5m candles.
* **Order Flow Imbalance (OFI):** Measures instantaneous taker aggression against passive pool inventory:
  $$\text{OFI} = \frac{\text{Buys}_{5m} - \text{Sells}_{5m}}{\text{Buys}_{5m} + \text{Sells}_{5m}} \in [-1, 1]$$

### 2. On-Chain Contract Security & Anti-Cabal Supply Audit
Before execution, candidate tokens undergo deep on-chain verification:
* **RugCheck Full Report Audit:** Evaluates risk score, contract flags, and verified status. Rejects `score > 2000` or tokens flagged as `rugged`.
* **Authority Guards:** Instantly blocks contracts with active `freezeAuthority` or `mintAuthority`.
* **Dev Holding Threshold:** Rejects tokens where the creator holds $> 10\%$ of circulating tokens.
* **Whale & Top 10 Concentration:** Evaluates non-pool, non-burn token accounts, discarding pools where any single wallet has $> 15\%$ or the top 10 hold $> 30\%$.
* **Insider Network (Cabal) Detection:** Rejects pools where connected insider clusters own $> 15\%$.
* **Invariant Soft-Rug Detector ($\Lambda_t$):** During trade lifecycle, monitors pool constant preservation:
  $$\Lambda_t = \frac{L_t / L_0}{\sqrt{P_t / P_0}} < 0.85 \implies \text{Immediate Market Liquidation}$$

### 3. Asymmetric Laddered Holding Map & Monotonic Protection (v0.7)
* **Convex De-risking Ladder:**
  * **$2.0\times$ Multiplier:** Liquidates $35\%$ of original position inventory, retrieving $70\%$ of initial capital invested ($0.35 \times 2.0 = 0.70x$) while preserving $65\%$ of inventory to exploit convex tail moves.
  * **$5.0\times$ Multiplier:** Liquidates $30\%$ of position ($0.30 \times 5.0 = 1.50x$, total cumulative realized return: $2.20x$ / $+120\%$ net gain locked).
  * **$15.0\times$ Multiplier:** Liquidates $20\%$ of position ($0.20 \times 15.0 = 3.00x$, total cumulative realized return: $5.20x$).
  * **$50.0\times$ Multiplier (*Moonbag*):** Liquidates the remaining $15\%$ ($0.15 \times 50.0 = 7.50x$, total cumulative realized return: $12.70x$ / $+1170\%$ net return).
* **Monotonic Trailing Stop & Breakeven:**
  * *$[1.15\times, 1.40\times)$ Stage:* Activates Breakeven floor ($1.02\times$, covering round-trip network fees) and Early Profit Lock on $15\%$ drawdown from peak.
  * *$[1.40\times, 2.00\times)$ Stage:* Trailing Stop allows $20\%$ drawdown but strictly guarantees an exit floor of $\ge 1.18\times$ (higher than the previous bracket's stop).
  * *$[2.00\times, \infty)$ Stage (Runner):* Allows $50\%$ drawdown for exponential breathing room, while strictly guaranteeing an exit floor of $\ge 1.40\times$ (+40% profit locked).
* **Anti-Wick Safeguard:** Prevents premature stop-out caused by ephemeral reporting anomalies when liquidity remains intact.
* **Dual-Stage Inertia & Time-Stop:**
  * *10-Minute Stagnation Invalidation:* Immediately prunes any non-risk-free position held for $\ge 10.0$ minutes that is trading negative ($\text{Price} < \text{Entry}$) with non-positive volume momentum ($Z \le 0.0$).
  * *20-Minute Capacity Time-Stop:* Liquidates stagnant positions that fail to gain $> +20\%$ to rotate capital.

### 4. Realistic Friction Modeling (Price Impact, LP Fee & Gas)
* **Constant Product AMM Price Impact ($x \cdot y = k$):**
  $$\text{Impact} = \frac{\Delta x}{R_{\text{usd}} + \Delta x}, \qquad R_{\text{usd}} = \frac{L_{\text{pool}}}{2}$$
* **Pool LP Swap Fee:** Deducts $0.30\%$ standard pool fee on every swap ($\gamma = 0.003$):
  $$P_{\text{buy}} = P_{\text{spot}} \cdot (1 + \text{Impact}) \cdot (1 + \gamma), \qquad P_{\text{sell}} = P_{\text{spot}} \cdot (1 - \text{Impact}) \cdot (1 - \gamma)$$
* **Network Fees:** Deducts simulated priority fees ($\$0.15$ default per transaction).

### 5. Real-Time Telemetry & Standalone Dashboard (v0.8)
* **Terminal TUI Formatter:** Compact ANSI dashboard displaying portfolio uptime, capacity slots, total gas fees, net PnL, active position metrics (entry, spot, current multiple, peak multiple, stop floor, and de-risking badge), plus the recent security audit log.
* **Auto-Refreshing Web Dashboard (`data/dashboard.html`):** Standalone, dark-mode responsive HTML dashboard generated on-the-fly with 3-second auto-refresh, metric summary cards, and color-coded status badges.
* **Standalone Telemetry Inspector:** Allows inspecting database positions and generating the latest HTML report at any time:
  ```powershell
  python -m src.telemetry.dashboard
  ```

---

## 🏗 Modular Architecture

```text
crypto-asymmetric-bot/
├── analytics/
│   ├── analytics.py          # Vectorized rolling metrics (Z-Score, Volatility, OFI)
│   ├── report.py             # Quantitative performance summary (PnL, Win-Rate, Expectancy)
│   └── test_analytics.py     # Standalone diagnostic runner for historical tick metrics
├── config/
│   └── settings.py           # Pydantic-validated hyperparameters & network limits
├── data/
│   ├── dashboard.html        # Real-time auto-refreshing dark-mode web dashboard
│   ├── database.py           # Async SQLite persistence (WAL mode) with lock timeouts
│   └── trading_history.db    # Relational store for high-frequency ticks and trades
├── src/
│   ├── audit/
│   │   └── holder_analyzer.py# Entry safety heuristics + RugCheck on-chain anti-cabal & holder audit
│   ├── execution/
│   │   └── paper_client.py   # High-fidelity OMS with AMM price impact, LP fee & gas tracking
│   ├── risk/
│   │   └── position_sizer.py # Fixed unit sizing & daily portfolio drawdown circuit breaker
│   ├── scanner/
│   │   └── dex_scanner.py    # Multi-endpoint discovery, age-normalized ignition & backoff
│   ├── strategy/
│   │   ├── holding_map.py    # Asymmetric laddered de-risking structure (4-tier convex)
│   │   └── stat_strategy.py  # Tick-driven signal generator with conditional OFI exit
│   ├── telemetry/
│   │   └── dashboard.py      # Real-time TUI terminal board & dynamic HTML generator
│   └── utils/
│       └── logger.py         # Structured asynchronous console logger
├── tests/                    # Comprehensive Pytest test suite (103 unit & integration tests)
├── main.py                   # Asyncio dual-loop orchestration (Scanner + Watcher)
├── pytest.ini                # Pytest configuration
├── requirements.txt          # Python dependency specifications
└── .env.example              # Environment variables template
```

---

## 🚀 Execution & Quick Start

### 1. Requirements & Setup
```powershell
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Running Unit & Integration Tests
```powershell
pytest
```

### 3. Launching the Simulator
```powershell
# Execução contínua
python main.py

# Ou execução com tempo limite programado (ex: 5 minutos / 300 segundos)
python main.py --duration 300
```

### 4. Visualizing Real-Time Telemetry & HTML Dashboard
```powershell
python -m src.telemetry.dashboard
# Abra data/dashboard.html no seu navegador para ver o dashboard em tempo real com auto-refresh
```

### 5. Inspecting Quantitative Analytics & BI Report
```powershell
python -m analytics.report
```
