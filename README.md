# Investment Strategy with Alpaca and Google Cloud Functions

This project contains a set of Python Cloud Functions for managing a multi-strategy portfolio using Alpaca's trading API. The portfolio consists of three distinct investment strategies: **High-Frequency Equity Allocation (HFEA)**, **S&P 500 with 200-SMA**, and **9-Sig Strategy (Jason Kelly Methodology)**.

## Portfolio Allocation

The current portfolio is allocated across three strategies:
- **HFEA Strategy**: 47.5%
- **SPXL SMA Strategy**: 47.5%
- **9-Sig Strategy**: 5%

## Overview of the Strategies

The project is based on three distinct investment strategies, each designed to maximize returns by leveraging specific market behaviors and signals.

### 1. High-Frequency Equity Allocation (HFEA) Strategy

#### **Strategy Overview:**
The HFEA strategy is an aggressive investment approach that involves leveraging a portfolio composed of three leveraged ETFs: 
- **UPRO** (3x leveraged S&P 500) - 45% allocation
- **TMF** (3x leveraged long-term U.S. Treasury bonds) - 25% allocation  
- **KMLM** (KFA Mount Lucas Managed Futures Index Strategy ETF) - 30% allocation

This three-asset approach was selected based on research from the r/LETFs community's 2024 best portfolio competition. The strategy capitalizes on the diversification benefits of combining equities, bonds, and managed futures. KMLM provides additional diversification through exposure to commodity trends and can perform well in different market conditions than traditional stocks and bonds.

#### **Approach in the Script:**
- **Monthly Buys**: The script uses a sophisticated underweight-based allocation system. Instead of fixed percentages, it calculates which assets are underweight relative to their target allocations (45% UPRO, 25% TMF, 30% KMLM) and allocates the monthly investment proportionally to bring the portfolio back towards target. This approach automatically rebalances during monthly contributions.
  
- **Quarterly Rebalancing**: The script includes a quarterly rebalancing function that ensures the portfolio remains aligned with the 45/25/30 target allocation. Rebalancing involves selling portions of over-performing ETFs and buying under-performing ones through a series of paired trades, ensuring the portfolio stays on track with the strategy's risk and return profile.

#### **Expected Returns (CAGR):**
- The HFEA strategy with this three-asset allocation has been optimized for improved risk-adjusted returns compared to traditional two-asset HFEA portfolios. 
- **Historical Performance**: Based on [backtesting from 1994 to present](https://testfol.io/?d=eJyNT9tKw0AQ%2FZUyzxGStBUaEEGkL1otog8iJYzJJF072a2TtbWE%2FLsTQy8igss%2B7M45cy4NlOxekecoWNWQNFB7FJ%2Fm6AkSiCaT0VkY6YUAyOb7eRzGx3m%2FsUGGJAr1BID5W2psweiNs5AUyDUFkGG9LNhtIQmPn7QQelfFZ0LhnaqJYza2TLfG5h33PGwDWDvxhWPjNOJLAxarLsUV2WxZoax0zdgN1f7abEyuOZXm5UM9hbQc2oymvc2ds6Rsb7IVSS%2FWvxWr1zsvCq5JMrL%2Bu027CCAXLDVzGxyMn%2BYP94Ob2e1s8Dib%2Ft%2F80PFv%2B0u%2BGJ5GGI072wNnVXH1eYoPwx%2B4Z%2F9bIx6ftli0X39%2BpPY%3D), this portfolio achieved approximately **15% CAGR (pre-tax)** or roughly **13% CAGR (post-tax)**.
- The addition of KMLM provides trend-following and crisis alpha characteristics that can enhance returns during certain market conditions while reducing overall portfolio volatility compared to traditional UPRO/TMF-only portfolios.

#### **Research Sources:**
This implementation is based on extensive backtesting and research from:
- [r/LETFs 2024 Best Portfolio Competition Results](https://www.reddit.com/r/LETFs/comments/1dyl49a/2024_rletfs_best_portfolio_competition_results/)

### 2. S&P 500 with 200-SMA Strategy

#### **Strategy Overview:**
The S&P 500 with 200-SMA strategy is a trend-following investment approach that uses the 200-day Simple Moving Average (SMA) as a signal for entering or exiting the market. The 200-SMA is a widely-used technical indicator that smooths out daily price fluctuations and highlights the underlying trend of the market.

The basic premise of this strategy is that when the S&P 500 index is above its 200-SMA, the market is in an uptrend, and it is generally safer to be invested in equities. Conversely, when the S&P 500 is below its 200-SMA, the market is likely in a downtrend, and it may be prudent to reduce equity exposure or exit the market altogether.

#### **Approach in the Script:**
- **Buying SPXL**: The script monitors the S&P 500's position relative to its 200-SMA with a 1% margin band. If the S&P 500 is more than 1% above the 200-SMA, indicating a confirmed bullish trend, the script will use allocated cash to buy SPXL, a 3x leveraged ETF that tracks the S&P 500. This leverage allows for higher returns during uptrends.
  
- **Selling SPXL**: If the S&P 500 falls more than 1% below its 200-SMA, the script will sell all holdings in SPXL. The 1% margin band helps avoid whipsaws—situations where the market briefly crosses the SMA only to quickly reverse—reducing unnecessary trading and transaction costs.

- **Monthly Contributions**: On the first trading day of each month, if the market is above the 200-SMA (plus margin), the monthly allocation is invested in SPXL. If the market is below the 200-SMA, the cash is held and tracked in Firestore for future deployment when conditions improve.

#### **Expected Returns:**
- The S&P 500 with 200-SMA strategy aims to enhance returns through trend-following and risk management. By avoiding major market drawdowns through strategic exits during downtrends, the strategy seeks to capture the majority of market upside while protecting capital during bear markets. The use of 3x leverage (SPXL) amplifies returns during bullish periods while the 200-SMA timing mechanism provides downside protection. Historical backtests of similar strategies have shown improved risk-adjusted returns compared to buy-and-hold approaches.

### 3. 9-Sig Strategy (Jason Kelly Methodology)

#### **Strategy Overview:**
The 9-Sig strategy is based on Jason Kelly's methodology from his book "The 3% Signal". It's a systematic approach to managing a TQQQ (3x leveraged NASDAQ-100) and AGG (iShares Core U.S. Aggregate Bond ETF) portfolio with built-in crash protection. The strategy aims for 9% quarterly growth while maintaining an 80/20 allocation between TQQQ and AGG.

#### **Key Components:**

**Target Allocation:**
- **80% TQQQ**: 3x leveraged NASDAQ-100 ETF for growth
- **20% AGG**: Bond ETF for stability and crash protection

**Monthly Contributions (First Trading Day of Month):**
- **ALL** monthly contributions go to AGG bonds only
- Amount: $10.25 per month (5% of total $205 monthly investment)
- **Rationale**: This follows the core 3Sig rule - monthly contributions always go to the safer asset

**Quarterly Rebalancing (First Trading Day of Quarter):**
The strategy uses a sophisticated signal line calculation to determine when to rebalance:

```
Signal Line = Previous TQQQ Balance × 1.09 + (Half of Quarterly Contributions)
```

**Rebalancing Logic:**
- **BUY Signal**: When Current TQQQ < Signal Line → Sell AGG, Buy TQQQ
- **SELL Signal**: When Current TQQQ > Signal Line → Sell TQQQ, Buy AGG  
- **HOLD Signal**: When within $25 tolerance of signal line → No action
- **First Quarter**: Signal line set to 80% of total portfolio value

**Crash Protection - "30 Down, Stick Around" Rule:**
- When SPY drops >30% from all-time high, the strategy ignores the first 4 SELL signals
- This prevents selling during major market crashes
- After 4 ignored signals, normal operation resumes

#### **Example Scenarios:**

**First Quarter:**
```
Starting: $0 TQQQ, $30.75 AGG (from 3 months of contributions)
Signal Line: $24.60 (80% of total portfolio)
Action: BUY $24.60 worth of TQQQ
Result: $24.60 TQQQ, $6.15 AGG (80/20 allocation)
```

**Normal BUY Signal:**
```
Signal Line: $1,105
Current TQQQ: $1,000 (need $105 more)
Action: Sell $105 worth of AGG → Buy $105 worth of TQQQ
Result: Rebalanced to signal line
```

**Crash Protection Example:**
```
Normal SELL Signal: Current TQQQ > Signal Line
BUT: SPY down 35% from ATH
Action: SELL_IGNORED (signal ignored due to crash protection)
Result: Hold TQQQ position during market crash
```

#### **Expected Returns:**
- **Target**: 9% quarterly growth (approximately 36% annually compounded)
- **Historical Performance**: Based on Jason Kelly's methodology, this strategy has shown strong risk-adjusted returns with built-in crash protection
- **Risk Management**: The monthly contributions to bonds and crash protection rule help mitigate downside risk

#### **Data Management:**
- All quarterly data is stored in Firestore (`nine-sig-quarters` collection)
- Tracks: balances, signal lines, actions taken, and performance metrics
- Enables accurate calculation of subsequent quarters' signal lines

## Detailed Analysis of All Strategies

### **Risk and Volatility:**
- **HFEA Strategy**: The HFEA strategy's use of leveraged ETFs means that both gains and losses are magnified. The three-asset allocation (UPRO/TMF/KMLM at 45/25/30) provides better diversification than traditional two-asset HFEA portfolios. KMLM's managed futures component can provide uncorrelated returns during certain market conditions, potentially reducing overall portfolio volatility. However, this strategy still requires a strong risk tolerance and is generally suitable for investors with a long-term horizon who can withstand short-term losses.
  
- **S&P 500 with 200-SMA Strategy**: The 200-SMA strategy, while still involving a leveraged ETF (SPXL), mitigates risk by using a market-timing mechanism. By exiting the market during downtrends, the strategy avoids significant drawdowns, making it less volatile than the HFEA strategy. However, it still carries the risks associated with leveraged ETFs, including the potential for loss during sharp market reversals.

- **9-Sig Strategy**: The 9-Sig strategy balances growth and risk management through systematic rebalancing and crash protection. While it uses leveraged ETFs (TQQQ), the monthly contributions to bonds and the "30 Down, Stick Around" rule provide significant downside protection. The strategy's systematic approach removes emotional decision-making and provides built-in risk management during market crashes.

### **Investment Horizon:**
- **HFEA Strategy**: Best suited for long-term investors who can afford to leave their investments untouched for several years, allowing the compounding effect to play out.
  
- **S&P 500 with 200-SMA Strategy**: This strategy can also be used for long-term growth, but with a focus on preserving capital during market downturns. It's more suitable for investors who are cautious about market cycles and prefer to reduce exposure during bear markets.

- **9-Sig Strategy**: Designed for long-term systematic growth with quarterly rebalancing. The strategy's systematic approach and crash protection make it suitable for investors who want exposure to leveraged growth but with built-in risk management. The monthly contributions to bonds provide a steady foundation while the quarterly rebalancing optimizes growth.

### **Key Assumptions:**
- **HFEA Strategy**: Assumes that the diversification benefits of combining equities, bonds, and managed futures will persist, and that over time, the leveraged returns will outweigh the increased volatility. The strategy also assumes that KMLM's trend-following approach will provide crisis alpha and reduce drawdowns during major market dislocations.
  
- **S&P 500 with 200-SMA Strategy**: Assumes that the 200-SMA is a reliable indicator of market trends and that the market's behavior will continue to follow historical patterns where it tends to trend above or below the 200-SMA for extended periods.

- **9-Sig Strategy**: Assumes that the systematic rebalancing approach will capture market growth while the crash protection rule will prevent significant losses during major market downturns. The strategy assumes that the 9% quarterly growth target is achievable over long-term market cycles and that the monthly contributions to bonds provide sufficient stability for the leveraged growth component.

## Conclusion

All three strategies offer unique ways to potentially enhance returns, but they come with their own sets of risks and assumptions. The HFEA strategy seeks to maximize growth through a balanced but leveraged approach, while the S&P 500 with 200-SMA strategy aims to capture market gains while avoiding major downturns. The 9-Sig strategy provides systematic growth with built-in crash protection and systematic rebalancing.

Together, these strategies provide a comprehensive blend of aggressive growth and risk management:
- **HFEA (47.5%)**: Three-asset leveraged portfolio (UPRO 45%, TMF 25%, KMLM 30%) with enhanced diversification through managed futures exposure
- **SPXL SMA (47.5%)**: Trend-following with market timing using 200-day SMA signals  
- **9-Sig (5%)**: Systematic TQQQ/AGG growth with crash protection following Jason Kelly's methodology

Each strategy has been carefully selected and optimized based on historical backtests and current market research. The diversification across three different approaches—equity/bond/futures leverage, trend-following, and systematic rebalancing—helps reduce overall portfolio risk while maintaining strong growth potential.

## Index Alert System

The project includes a unified index alert system that monitors multiple indices and provides automated notifications via Telegram when specific conditions are met.

### **Alert Types:**

#### **1. All-Time High (ATH) Drop Alerts**
- **S&P 500**: Monitors for 30% drop from all-time high
- **MSCI World (URTH)**: Monitors for 30% drop from all-time high
- **Schedule**: Every hour during trading hours (9:30 AM - 3:30 PM)
- **Purpose**: Alert when major indices have significant drawdowns for potential investment opportunities

#### **2. SMA Crossing Alerts**
- **URTH 255-day SMA**: Monitors iShares MSCI World ETF crossing above/below 255-day SMA
- **SPY 200-day SMA**: Monitors SPY (S&P 500 ETF) crossing above/below 200-day SMA
- **Schedule**: Every hour during trading hours (9:15 AM - 3:15 PM)
- **Purpose**: Track trend changes and potential market direction shifts

### **Alert Configuration:**
- **Noise Threshold**: 1% minimum deviation to avoid excessive notifications
- **Emoji Indicators**: 🚀 for above SMA, 📉 for below SMA
- **Telegram Integration**: All alerts sent to configured Telegram chat
- **Unified System**: Single Cloud Function handles all alert types with different parameters

### **Example Alert Messages:**
```
🚀 URTH Alert: iShares MSCI World ETF crossed ABOVE its 255-day SMA! 
Current: $180.50 (SMA: $178.20, +1.29%)

📉 SPY Alert: Crossed BELOW its 200-day SMA! 
Current: $432.15 (SMA: $438.50, -1.38%)

Alert: S&P 500 has dropped 32.15% from its ATH! 
Consider a loan with a duration of 6 to 8 years (50k to 100k) at around 4.5% interest max
```

## Project Structure

- `main.py`: The main Python script containing all strategy logic:
  - **HFEA strategy**: Three-asset portfolio (UPRO/TMF/KMLM at 45/25/30) with monthly underweight-based buys and quarterly rebalancing
  - **SPXL SMA strategy**: Trend-following with 200-day SMA (monthly buys and daily trading)
  - **9-Sig strategy**: Jason Kelly methodology with monthly AGG contributions and quarterly TQQQ/AGG signals with crash protection
  - **Unified index alert system**: Monitors multiple indices for ATH drops and SMA crossings
  - **Firestore integration**: Persistent storage for 9-Sig quarterly data, strategy balances, and unified market data cache
  - **Alpaca integration**: All market data fetched from Alpaca IEX feed (no yfinance dependency)
- `requirements.txt`: Python dependencies including pandas, Google Cloud libraries, and Flask.
- `cloudbuild.yaml`: Google Cloud Build configuration for deploying Cloud Functions and Cloud Scheduler jobs.
- `README.md`: Comprehensive documentation of all strategies and setup instructions.

### **Cloud Functions Deployed:**
- `monthly_invest_all`: **Orchestrator function (RECOMMENDED)** - Runs all three monthly strategies with coordinated budget calculations
- `monthly_buy_hfea`: HFEA monthly investment function (individual execution)
- `rebalance_hfea`: HFEA quarterly rebalancing function
- `monthly_buy_spxl`: SPXL SMA monthly investment function (individual execution)
- `daily_trade_spxl_200sma`: SPXL SMA daily trading function
- `monthly_nine_sig_contributions`: 9-Sig monthly contributions function (individual execution)
- `quarterly_nine_sig_signal`: 9-Sig quarterly signal function
- `index_alert`: Unified index alert system

### **Cloud Scheduler Jobs:**
- **Monthly orchestrator**: First trading day of each month at 12:00 PM ET (`monthly_invest_all` - runs all three monthly strategies with coordinated budgets)
- **Quarterly functions**: First trading day of each quarter at specified times (`rebalance_hfea` at 2:00 PM ET, `quarterly_nine_sig_signal` at 1:00 PM ET)
- **Index alerts**: Hourly during trading hours (9:15 AM - 3:15 PM for SMA alerts, 9:30 AM - 3:30 PM for ATH drop alerts)
- **Daily SMA functions**: 3:56 PM ET on weekdays (`daily_trade_spxl_200sma`)

**Note**: Individual monthly functions (`monthly_buy_hfea`, `monthly_buy_spxl`, `monthly_nine_sig_contributions`) are deployed but not scheduled. They remain available for manual execution and debugging purposes. The `monthly_invest_all` orchestrator is used for production to ensure coordinated budget allocation and prevent over-spending.

## Monthly Investment Orchestrator

The `monthly_invest_all` orchestrator is a coordinated execution system that manages all three monthly investment strategies (HFEA, SPXL SMA, and 9-Sig) in a single unified process.

### **Why Use an Orchestrator?**

Without the orchestrator, each strategy would independently:
1. Check margin conditions
2. Calculate available cash and margin
3. Determine its investment amount
4. Execute trades

This approach creates a critical problem: **each function would try to use the full available buying power**, leading to over-spending and failed trades.

### **How the Orchestrator Solves This**

The orchestrator (`monthly_invest_all_strategies()` function):

1. **Calculates budgets once**: Checks margin conditions and calculates total available buying power a single time
2. **Distributes precisely**: Splits the total amount according to strategy allocations:
   - HFEA: 47.5%
   - SPXL SMA: 47.5%
   - 9-Sig: 5%
3. **Passes pre-calculated amounts**: Each strategy receives its exact budget and margin conditions as parameters
4. **Prevents over-spending**: Since budgets are pre-calculated, there's no risk of multiple strategies competing for the same funds

### **Key Features**

- **Coordinated execution**: All three strategies run in sequence with shared context
- **Exact splits**: Portfolio allocation percentages are maintained precisely
- **Single margin check**: Margin conditions evaluated once and shared across all strategies
- **Unified reporting**: Consolidated Telegram notifications show the complete picture
- **Fail-safe design**: If one strategy fails, others can still execute

### **Production Recommendation**

For production deployments, **always use the orchestrator** (`monthly_invest_all`) instead of scheduling individual monthly functions. This ensures:
- Consistent portfolio allocation
- No race conditions between functions
- Accurate budget management
- Simplified monitoring and debugging

The individual functions remain deployed for manual testing and debugging but should not be scheduled in production environments.

## Margin-Aware Investment Logic

The system includes intelligent margin control for all monthly investment functions (HFEA, SPXL SMA, and 9-Sig). This feature enables controlled use of leverage (up to +10%) only when market conditions are favorable and borrowing costs are reasonable.

### **Core Principles**

1. **Conservative Leverage**: Maximum +10% exposure (1.10× leverage) - enhances returns without excessive risk
2. **Rule-Based Activation**: Margin only enabled when ALL safety gates pass
3. **Automatic Deactivation**: Switches to cash-only mode when conditions deteriorate
4. **Full Transparency**: Every monthly cycle generates a consolidated Telegram report with decision rationale

### **Four Safety Gates**

Margin is enabled ONLY when all four conditions are met:

#### Gate 1: Market Trend
- **Requirement**: SPY > 200-day SMA
- **Rationale**: Only use leverage in confirmed bull markets
- **Note**: Uses SPY (S&P 500 ETF) as S&P 500 Index proxy

#### Gate 2: Margin Rate
- **Requirement**: Borrowing cost ≤ 8.0%
- **Calculation**: FRED Federal Funds Rate + spread
  - Accounts < $35k: FRED rate + 2.5%
  - Accounts ≥ $35k: FRED rate + 1.0%
- **Data Source**: Federal Reserve Economic Data (FRED) API - DFEDTARU series
- **Rationale**: Avoid expensive borrowing that erodes returns

#### Gate 3: Buffer
- **Requirement**: Buffer ≥ 5%
- **Formula**: `(Equity / Portfolio Value) - (Maintenance Margin / Portfolio Value)`
- **Rationale**: Maintain safety cushion above maintenance margin

#### Gate 4: Leverage
- **Requirement**: Current leverage < 1.14×
- **Formula**: `Portfolio Value / Equity`
- **Rationale**: Prevent over-leveraging

### **Investment Behavior**

#### When Margin is Enabled (All gates pass)
- **Buying Power**: `Cash + (Equity × 10%)`
- **Approach**: All-or-Nothing - invest full monthly amount or skip entirely
- **Firestore**: Not applicable (actively investing)
- **Reporting**: Shows green decision with all gate details

#### When Margin is Disabled (Any gate fails)
- **Buying Power**: Cash only (no margin borrowing)
- **If Still Leveraged** (Leverage > 1.0×):
  - Skip all investments to prioritize deleveraging
  - No Firestore additions (money stays in account)
- **If Equity-Only** (Leverage ≤ 1.0×):
  - Use available cash for investments if sufficient
  - **SPXL SMA Only**: Add skipped amount to Firestore when SMA trend is bearish
  - **HFEA/9-Sig**: Skip without Firestore addition
- **Reporting**: Shows red decision with failed gate(s) highlighted

### **Firestore Logic**

The system tracks skipped investments differently based on strategy and reason:

- **Add to Firestore**: Only for SPXL SMA strategy when:
  1. Index is below 200-SMA (bearish trend), AND
  2. Account is fully equity-only (leverage ≤ 1.0×)
  
- **Skip Firestore**: In all other cases:
  - Margin gates fail (not SMA-related)
  - Account is still leveraged (deleveraging priority)
  - HFEA or 9-Sig strategies (no Firestore tracking)

### **Telegram Reporting**

Each monthly investment cycle generates ONE consolidated message per strategy:

```
📊 [Strategy Name] Monthly Update

Market Trend: ✅ SPY $585.00 (200-SMA: $550.00)
Margin Rate: ✅ 6.5% (FRED 4.0% + 2.5%)
Buffer: ✅ 8.2%
Leverage: ✅ 1.05x

Decision: 🟢 Margin ENABLED (+10%) / 🔴 Cash-Only Mode

Account: Equity $15,000.00 | Portfolio $15,750.00 | Cash $500.00

Action: [Invested $97.50 / Skipped - reason]
```

### **Configuration**

All margin control parameters are defined in `margin_control_config`:

```python
margin_control_config = {
    "target_margin_pct": 0.10,      # Maximum +10% leverage
    "max_margin_rate": 0.08,        # 8% rate threshold
    "min_buffer_pct": 0.05,         # 5% minimum buffer
    "max_leverage": 1.14,           # Maximum 1.14x leverage
    "spread_below_35k": 0.025,      # +2.5% for accounts <$35k
    "spread_above_35k": 0.01,       # +1.0% for accounts ≥$35k
    "portfolio_threshold": 35000,   # Threshold for spread calculation
}
```

### **Fail-Safe Mechanisms**

- **Data Unavailable**: If FRED API, yfinance, or Alpaca fails → default to cash-only mode
- **API Errors**: All errors logged and reported via Telegram
- **Deleveraging Priority**: When gates fail while leveraged, skip all investments to reduce exposure

## Technical Configuration

### **Key Parameters:**

**Dynamic Monthly Investment:**
- Investment amounts are calculated dynamically each month based on available cash and margin conditions
- Total available = Account cash - Reserved amounts (for bearish strategies) + Approved margin (up to +10% of equity)
- Split across strategies: HFEA 47.5%, SPXL SMA 47.5%, 9-Sig 5%
- All-or-Nothing approach: Invest full calculated amount or skip entirely

**HFEA Strategy:**
- Portfolio allocation: 47.5% of total monthly investment
- Asset allocation: UPRO 45%, TMF 25%, KMLM 30%
- Rebalancing: Quarterly with 0.5% fee margin
- Investment approach: Underweight-based proportional allocation

**SPXL SMA Strategy:**
- Portfolio allocation: 47.5% of total monthly investment
- SMA period: 200 days
- Margin band: 1% (to avoid whipsaws)
- Tracked index: S&P 500 (SPY ETF as proxy)

**9-Sig Strategy:**
- Portfolio allocation: 5% of total monthly investment
- Target allocation: TQQQ 80%, AGG 20%
- Quarterly growth target: 9%
- Monthly contributions: 100% to AGG (bonds)
- Signal tolerance: $25 (minimum trade amount)
- Crash protection: "30 Down, Stick Around" rule (ignores first 4 sell signals when SPY down >30% from ATH)
- Bond rebalancing threshold: 30% (triggers rebalancing when AGG exceeds this)

**Alert System:**
- ATH drop threshold: 30% for S&P 500 and MSCI World
- SMA noise threshold: 1% (minimum deviation to trigger alert)
- URTH SMA period: 255 days
- SPY SMA period: 200 days

### **Data Storage:**
- **Firestore Collections:**
  - `strategy-balances`: Tracks invested amounts for each strategy
  - `nine-sig-quarters`: Historical quarterly data for 9-Sig signal calculations
  - `nine-sig-monthly-contributions`: Tracks actual monthly 9-Sig contributions for accurate quarterly signal calculation
  - `market-data`: Unified collection caching market prices, SMA values (200-day, 255-day), crossing states, and alert timestamps (5-minute cache expiry) - single source of truth for all market data

### **Trading Platform:**
- **Alpaca API**: Live and paper trading environments supported
- **Order execution**: Market orders with fill-wait logic (5-minute polling, 300-second timeout)
- **Market Data**: Uses SPY (S&P 500 ETF) as proxy for S&P 500 Index - tracks with <0.1% difference
- **Data Source**: Alpaca IEX feed (included with Basic subscription) - no rate limiting, 5 years of historical data
- **Caching**: 5-minute Firestore cache for all price and SMA data to minimize API calls

## Setup

### Prerequisites

- [Google Cloud SDK](https://cloud.google.com/sdk/docs/install)
- [Python 3.10+](https://www.python.org/downloads/)
- Alpaca Trading Account (live or paper)
- Google Cloud Project with Firestore enabled
- Telegram Bot (for notifications)

### Installing Dependencies

First, clone the repository and navigate into the project directory:

```bash
git clone https://github.com/yourusername/hfea-alpaca-strategy.git
cd hfea-alpaca-strategy
pip install -r requirements.txt
```

### Local Development and Testing

The script supports local execution for testing strategies before deploying to Google Cloud:

```bash
# RECOMMENDED - Monthly Orchestrator (runs all three monthly strategies with coordinated budgets)
python3 main.py --action monthly_invest_all --env paper --force

# Individual Strategy Testing (for debugging specific strategies)
# HFEA Strategy
python3 main.py --action monthly_buy_hfea --env paper --force
python3 main.py --action rebalance_hfea --env paper

# SPXL SMA Strategy
python3 main.py --action monthly_buy_spxl --env paper --force
python3 main.py --action sell_spxl_below_200sma --env paper
python3 main.py --action buy_spxl_above_200sma --env paper

# 9-Sig Strategy (with force execution for testing outside trading days)
python3 main.py --action monthly_nine_sig_contributions --env paper --force
python3 main.py --action quarterly_nine_sig_signal --env paper --force
```

**Why use the orchestrator (`monthly_invest_all`)?**
- Calculates budgets once and distributes them to all strategies
- Ensures exact percentage splits (47.5% HFEA, 47.5% SPXL SMA, 5% 9-Sig)
- Prevents over-spending by coordinating margin and cash allocation
- Recommended for production use to maintain portfolio balance

**Environment Variables:**
Create a `.env` file in the project root with the following variables:
```
ALPACA_API_KEY_LIVE=your_live_key
ALPACA_SECRET_KEY_LIVE=your_live_secret
ALPACA_API_KEY_PAPER=your_paper_key
ALPACA_SECRET_KEY_PAPER=your_paper_secret
TELEGRAM_KEY=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
FREDKEY=your_fred_api_key
GOOGLE_CLOUD_PROJECT_ID=your_project_id
```

**Note**: Get a free FRED API key from https://fred.stlouisfed.org/docs/api/api_key.html

### Deployment to Google Cloud

The project uses Google Cloud Build for automated deployment:

```bash
# Authenticate with Google Cloud
gcloud auth login

# Set your project
gcloud config set project YOUR_PROJECT_ID

# Deploy all functions and schedulers
gcloud builds submit --config cloudbuild.yaml
```

**Required Google Cloud Setup:**
1. Enable Cloud Functions API
2. Enable Cloud Scheduler API
3. Enable Firestore API
4. Enable Secret Manager API
5. Store API keys in Secret Manager:
   - `ALPACA_API_KEY_LIVE`
   - `ALPACA_SECRET_KEY_LIVE`
   - `ALPACA_API_KEY_PAPER`
   - `ALPACA_SECRET_KEY_PAPER`
   - `TELEGRAM_KEY`
   - `TELEGRAM_CHAT_ID`
   - `FREDKEY` (for margin rate calculations)

The `cloudbuild.yaml` file defines all Cloud Functions and their corresponding Cloud Scheduler jobs. Deployment is parallelized for faster updates.

## Additional Features

### **Trading Day Detection**

The system uses `pandas_market_calendars` to accurately detect:
- Regular trading days
- First trading day of the month
- First trading day of the quarter

This ensures all functions execute only on appropriate market days, avoiding failed trades on holidays and weekends.

### **Telegram Notifications**

All trading actions, rebalancing operations, and alerts are sent via Telegram for real-time monitoring. This includes:
- Trade confirmations with quantities and prices
- Portfolio allocation updates
- Alert notifications (ATH drops, SMA crossings)
- Error messages and timeouts

### **Force Execution Mode**

The 9-Sig strategy functions support a `--force` flag for testing purposes, allowing execution outside of scheduled trading days. This is useful for:
- Testing strategy logic without waiting for month/quarter start
- Debugging signal calculations
- Validating Firestore data storage

**Note:** Force execution should only be used in paper trading environment.

## Contributing

This is a personal trading bot implementation. Feel free to fork and adapt for your own use, but please note:
- This is not financial advice
- Leveraged ETFs carry significant risk
- Past performance does not guarantee future results
- Always test thoroughly in paper trading before using live funds

## License

This project is for educational and personal use only.
