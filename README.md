# HFEA Strategy with Alpaca and Google Cloud Functions

This project contains a set of Python Cloud Functions for managing a HFEA & S&P 500 200 SMA strategy using Alpaca's trading API.

## Overview of the Strategies

The project is based on two distinct investment strategies: the **High-Frequency Equity Allocation (HFEA)** strategy and the **S&P 500 with 200-SMA** strategy. Each of these strategies is designed to maximize returns by leveraging specific market behaviors and signals.

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
- The S&P 500 with 200-SMA strategy has historically delivered a higher CAGR of around 20%. This is due to the strategy’s ability to avoid major market drawdowns by exiting the market during downtrends. By staying invested during uptrends and moving to cash during downtrends, the strategy seeks to capture the majority of the market's upside while avoiding large losses during bear markets.

## Detailed Analysis of Both Strategies

### **Risk and Volatility:**
- **HFEA Strategy**: The HFEA strategy’s use of leveraged ETFs means that both gains and losses are magnified. While the strategy can achieve higher returns, it also exposes the investor to greater volatility and the risk of significant drawdowns. This strategy requires a strong risk tolerance and is generally suitable for investors with a long-term horizon who can withstand short-term losses.
  
- **S&P 500 with 200-SMA Strategy**: The 200-SMA strategy, while still involving a leveraged ETF (SPXL), mitigates risk by using a market-timing mechanism. By exiting the market during downtrends, the strategy avoids significant drawdowns, making it less volatile than the HFEA strategy. However, it still carries the risks associated with leveraged ETFs, including the potential for loss during sharp market reversals.

### **Investment Horizon:**
- **HFEA Strategy**: Best suited for long-term investors who can afford to leave their investments untouched for several years, allowing the compounding effect to play out.
  
- **S&P 500 with 200-SMA Strategy**: This strategy can also be used for long-term growth, but with a focus on preserving capital during market downturns. It’s more suitable for investors who are cautious about market cycles and prefer to reduce exposure during bear markets.

### **Key Assumptions:**
- **HFEA Strategy**: Assumes that the inverse correlation between equities and bonds will persist, and that over time, the leveraged returns will outweigh the increased volatility.
  
- **S&P 500 with 200-SMA Strategy**: Assumes that the 200-SMA is a reliable indicator of market trends and that the market's behavior will continue to follow historical patterns where it tends to trend above or below the 200-SMA for extended periods.

## Conclusion

Both strategies offer unique ways to potentially enhance returns, but they come with their own sets of risks and assumptions. The HFEA strategy seeks to maximize growth through a balanced but leveraged approach, while the S&P 500 with 200-SMA strategy aims to capture market gains while avoiding major downturns. Together, these strategies provide a blend of aggressive growth and risk management, each with a proven track record of delivering strong long-term returns (16% for HFEA and 20% for S&P 500 with 200-SMA) for disciplined investors who can adhere to the strategies during both good and bad times in the market.



## Project Structure

- `main.py`: The main Python script containing the logic for monthly buys and rebalancing.
- `requirements.txt`: Python dependencies for the project.
- `cloudbuild.yaml`: Google Cloud Build configuration for deploying the Cloud Functions.
- `.gitignore`: Specifies files and directories to be ignored by Git.
- `README.md`: Overview and setup instructions for the project.

## Setup

### Prerequisites

- [Google Cloud SDK](https://cloud.google.com/sdk/docs/install)
- [Python 3.7+](https://www.python.org/downloads/)

### Installing Dependencies

First, clone the repository and navigate into the project directory:

```bash
git clone https://github.com/yourusername/hfea-alpaca-strategy.git
cd hfea-alpaca-strategy
