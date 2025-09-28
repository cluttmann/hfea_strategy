# HFEA Strategy with Alpaca and Google Cloud Functions

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
The HFEA strategy is an aggressive investment approach that involves leveraging a portfolio primarily composed of two leveraged ETFs: UPRO (3x leveraged S&P 500) and TMF (3x leveraged long-term U.S. Treasury bonds). The idea behind this strategy is to capitalize on the inverse correlation between equities and bonds, particularly during periods of market stress or downturns. 

By allocating a significant portion of the portfolio to UPRO and a smaller portion to TMF, the HFEA strategy seeks to amplify the returns of a traditional 60/40 equity/bond portfolio. The strategy's use of leveraged ETFs increases both potential returns and risks, aiming to provide substantial growth over time.

#### **Approach in the Script:**
- **Monthly Buys**: The script allocates a set investment amount into UPRO and TMF monthly, with 55% going into UPRO and 45% into TMF. This allocation is consistent with the HFEA strategy’s goal of balancing growth (UPRO) and risk mitigation (TMF).
  
- **Rebalancing**: The script includes a quarterly rebalancing function that ensures the portfolio remains aligned with the 55/45 target allocation. Rebalancing involves selling portions of the over-performing ETF and buying the under-performing one, ensuring the portfolio stays on track with the strategy’s risk and return profile.

#### **Expected Returns (CAGR):**
- The HFEA strategy, due to its leveraged nature, has historically delivered a Compound Annual Growth Rate (CAGR) of approximately 16%. This return is higher than traditional unleveraged equity/bond portfolios but comes with increased volatility and risk, including the potential for significant drawdowns.

### 2. S&P 500 with 200-SMA Strategy

#### **Strategy Overview:**
The S&P 500 with 200-SMA strategy is a trend-following investment approach that uses the 200-day Simple Moving Average (SMA) as a signal for entering or exiting the market. The 200-SMA is a widely-used technical indicator that smooths out daily price fluctuations and highlights the underlying trend of the market.

The basic premise of this strategy is that when the S&P 500 index is above its 200-SMA, the market is in an uptrend, and it is generally safer to be invested in equities. Conversely, when the S&P 500 is below its 200-SMA, the market is likely in a downtrend, and it may be prudent to reduce equity exposure or exit the market altogether.

#### **Approach in the Script:**
- **Buying SPXL**: The script monitors the S&P 500's position relative to its 200-SMA. If the S&P 500 is above the 200-SMA, indicating a bullish trend, the script will use all available cash to buy SPXL, a 3x leveraged ETF that tracks the S&P 500. This leverage allows for higher returns during uptrends.
  
- **Selling SPXL**: Conversely, if the S&P 500 falls significantly below its 200-SMA, the script will sell all holdings in SPXL. The condition for selling is more stringent, requiring the index to be significantly below the 200-SMA to avoid whipsaws—situations where the market briefly dips below the SMA only to quickly recover.

#### **Expected Returns (CAGR):**
- The S&P 500 with 200-SMA strategy has historically delivered a higher CAGR of around 20%. This is due to the strategy's ability to avoid major market drawdowns by exiting the market during downtrends. By staying invested during uptrends and moving to cash during downtrends, the strategy seeks to capture the majority of the market's upside while avoiding large losses during bear markets.

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
- **HFEA Strategy**: The HFEA strategy's use of leveraged ETFs means that both gains and losses are magnified. While the strategy can achieve higher returns, it also exposes the investor to greater volatility and the risk of significant drawdowns. This strategy requires a strong risk tolerance and is generally suitable for investors with a long-term horizon who can withstand short-term losses.
  
- **S&P 500 with 200-SMA Strategy**: The 200-SMA strategy, while still involving a leveraged ETF (SPXL), mitigates risk by using a market-timing mechanism. By exiting the market during downtrends, the strategy avoids significant drawdowns, making it less volatile than the HFEA strategy. However, it still carries the risks associated with leveraged ETFs, including the potential for loss during sharp market reversals.

- **9-Sig Strategy**: The 9-Sig strategy balances growth and risk management through systematic rebalancing and crash protection. While it uses leveraged ETFs (TQQQ), the monthly contributions to bonds and the "30 Down, Stick Around" rule provide significant downside protection. The strategy's systematic approach removes emotional decision-making and provides built-in risk management during market crashes.

### **Investment Horizon:**
- **HFEA Strategy**: Best suited for long-term investors who can afford to leave their investments untouched for several years, allowing the compounding effect to play out.
  
- **S&P 500 with 200-SMA Strategy**: This strategy can also be used for long-term growth, but with a focus on preserving capital during market downturns. It's more suitable for investors who are cautious about market cycles and prefer to reduce exposure during bear markets.

- **9-Sig Strategy**: Designed for long-term systematic growth with quarterly rebalancing. The strategy's systematic approach and crash protection make it suitable for investors who want exposure to leveraged growth but with built-in risk management. The monthly contributions to bonds provide a steady foundation while the quarterly rebalancing optimizes growth.

### **Key Assumptions:**
- **HFEA Strategy**: Assumes that the inverse correlation between equities and bonds will persist, and that over time, the leveraged returns will outweigh the increased volatility.
  
- **S&P 500 with 200-SMA Strategy**: Assumes that the 200-SMA is a reliable indicator of market trends and that the market's behavior will continue to follow historical patterns where it tends to trend above or below the 200-SMA for extended periods.

- **9-Sig Strategy**: Assumes that the systematic rebalancing approach will capture market growth while the crash protection rule will prevent significant losses during major market downturns. The strategy assumes that the 9% quarterly growth target is achievable over long-term market cycles and that the monthly contributions to bonds provide sufficient stability for the leveraged growth component.

## Conclusion

All three strategies offer unique ways to potentially enhance returns, but they come with their own sets of risks and assumptions. The HFEA strategy seeks to maximize growth through a balanced but leveraged approach, while the S&P 500 with 200-SMA strategy aims to capture market gains while avoiding major downturns. The 9-Sig strategy provides systematic growth with built-in crash protection and systematic rebalancing.

Together, these strategies provide a comprehensive blend of aggressive growth and risk management:
- **HFEA (47.5%)**: Leveraged equity/bond balance targeting ~16% CAGR
- **SPXL SMA (47.5%)**: Trend-following with market timing targeting ~20% CAGR  
- **9-Sig (5%)**: Systematic growth with crash protection targeting ~36% CAGR

Each strategy has a proven track record of delivering strong long-term returns for disciplined investors who can adhere to the strategies during both good and bad times in the market. The diversification across three different approaches helps reduce overall portfolio risk while maintaining strong growth potential.

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
- **S&P 500 200-day SMA**: Monitors S&P 500 Index crossing above/below 200-day SMA
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

📉 S&P 500 Index Alert: Crossed BELOW its 200-day SMA! 
Current: $4,320.15 (SMA: $4,380.50, -1.38%)

Alert: S&P 500 has dropped 32.15% from its ATH! 
Consider a loan with a duration of 6 to 8 years (50k to 100k) at around 4.5% interest max
```

## Project Structure

- `main.py`: The main Python script containing all strategy logic:
  - HFEA strategy (monthly buys and quarterly rebalancing)
  - SPXL SMA strategy (monthly buys and daily SMA monitoring)
  - 9-Sig strategy (monthly contributions and quarterly signals)
  - Unified index alert system
- `requirements.txt`: Python dependencies for the project.
- `cloudbuild.yaml`: Google Cloud Build configuration for deploying Cloud Functions and Cloud Scheduler jobs.
- `.gitignore`: Specifies files and directories to be ignored by Git.
- `README.md`: Comprehensive documentation of all strategies and setup instructions.

### **Cloud Functions Deployed:**
- `monthly_buy_hfea`: HFEA monthly investment function
- `rebalance_hfea`: HFEA quarterly rebalancing function
- `monthly_buy_spxl`: SPXL SMA monthly investment function
- `daily_trade_spxl_200sma`: SPXL SMA daily trading function
- `monthly_nine_sig_contributions`: 9-Sig monthly contributions function
- `quarterly_nine_sig_signal`: 9-Sig quarterly signal function
- `index_alert`: Unified index alert system

### **Cloud Scheduler Jobs:**
- Monthly functions: First trading day of each month
- Quarterly functions: First trading day of each quarter
- Index alerts: Hourly during trading hours
- Daily SMA functions: Daily during trading hours

## Setup

### Prerequisites

- [Google Cloud SDK](https://cloud.google.com/sdk/docs/install)
- [Python 3.7+](https://www.python.org/downloads/)

### Installing Dependencies

First, clone the repository and navigate into the project directory:

```bash
git clone https://github.com/yourusername/hfea-alpaca-strategy.git
cd hfea-alpaca-strategy
