import os
from flask import Flask, jsonify
from google.cloud import secretmanager
from dotenv import load_dotenv
import yfinance as yf
import requests
import json
import time
import pandas_market_calendars as mcal
import datetime
from google.cloud import firestore


app = Flask(__name__)

monthly_invest = 165

# Strategy allocations: 47.5% HFEA, 47.5% SPXL SMA, 5% 9-Sig Strategy
strategy_allocations = {
    "hfea_allo": 0.475,      # Reduced from 0.5 to make room for 9-sig
    "spxl_allo": 0.475,      # Reduced from 0.5 to make room for 9-sig
    "nine_sig_allo": 0.05,   # New 5% allocation to 9-sig strategy
}

# Calculate investment amounts dynamically
investment_amounts = {
    key: monthly_invest * allocation for key, allocation in strategy_allocations.items()
}

# Strategy would be to allocate 50% to the SPXL SMA 200 Strategy and 50% to HFEA

# tqqq_investment_amount = monthly_invest * 0.1

upro_allocation = 0.45
tmf_allocation = 0.25
kmlm_allocation = 0.3
# Based on this https://www.reddit.com/r/LETFs/comments/1dyl49a/2024_rletfs_best_portfolio_competition_results/
# and this: https://testfol.io/?d=eJyNT9tKw0AQ%2FZUyzxGStBUaEEGkL1otog8iJYzJJF072a2TtbWE%2FLsTQy8igss%2B7M45cy4NlOxekecoWNWQNFB7FJ%2Fm6AkSiCaT0VkY6YUAyOb7eRzGx3m%2FsUGGJAr1BID5W2psweiNs5AUyDUFkGG9LNhtIQmPn7QQelfFZ0LhnaqJYza2TLfG5h33PGwDWDvxhWPjNOJLAxarLsUV2WxZoax0zdgN1f7abEyuOZXm5UM9hbQc2oymvc2ds6Rsb7IVSS%2FWvxWr1zsvCq5JMrL%2Bu027CCAXLDVzGxyMn%2BYP94Ob2e1s8Dib%2Ft%2F80PFv%2B0u%2BGJ5GGI072wNnVXH1eYoPwx%2B4Z%2F9bIx6ftli0X39%2BpPY%3D

alpaca_environment = "live"
margin = 0.01  # band around the 200sma to avoid too many trades

# 9-sig strategy configuration following Jason Kelly's methodology
nine_sig_config = {
    "target_allocation": {"tqqq": 0.8, "agg": 0.2},  # 80/20 target allocation
    "quarterly_growth_rate": 0.09,  # 9% quarterly growth target
    "bond_rebalance_threshold": 0.30,  # Rebalance when AGG > 30%
    "tolerance_amount": 25,  # Minimum trade amount to avoid tiny trades
}

# Initialize Firestore client
project_id = os.getenv("GOOGLE_CLOUD_PROJECT_ID")
db = firestore.Client(project=project_id)

def get_auth_headers(api):
    return {
        "APCA-API-KEY-ID": api["API_KEY"],
        "APCA-API-SECRET-KEY": api["SECRET_KEY"],
    }

def get_latest_trade(api, symbol):
    symbol = symbol.upper()
    market_data_base_url = "https://data.alpaca.markets"
    url = f"{market_data_base_url}/v2/stocks/{symbol}/trades/latest"
    try:
        response = requests.get(url, headers=get_auth_headers(api))
        response.raise_for_status()
        return response.json()["trade"]["p"]
    except requests.exceptions.HTTPError as e:
        if response.status_code == 404:
            print(f"Alpaca data not available for {symbol}, falling back to yfinance.")
            price = yf.Ticker(symbol).history(period="1d")["Close"].iloc[-1]
            return round(price, 2)
        else:
            raise

def get_account_cash(api):
    url = f"{api['BASE_URL']}/v2/account"
    response = requests.get(url, headers=get_auth_headers(api))
    response.raise_for_status()
    return float(response.json()["cash"])

def list_positions(api):
    url = f"{api['BASE_URL']}/v2/positions"
    response = requests.get(url, headers=get_auth_headers(api))
    response.raise_for_status()
    return response.json()

def get_order(api, order_id):
    url = f"{api['BASE_URL']}/v2/orders/{order_id}"
    response = requests.get(url, headers=get_auth_headers(api))
    response.raise_for_status()
    return response.json()

def submit_order(api, symbol, qty, side):
    url = f"{api['BASE_URL']}/v2/orders"
    data = {
        "symbol": symbol,
        "qty": round(qty, 6),
        "side": side,
        "type": "market",
        "time_in_force": "day",
    }
    response = requests.post(url, headers=get_auth_headers(api), json=data)
    response.raise_for_status()
    return response.json()

def is_running_in_cloud():
    return (
        os.getenv("GAE_ENV", "").startswith("standard")
        or os.getenv("FUNCTION_NAME") is not None
        or os.getenv("K_SERVICE") is not None
        or os.getenv("GAE_INSTANCE") is not None
        or os.getenv("GOOGLE_CLOUD_PROJECT") is not None
    )


# Function to get secrets from Google Secret Manager
def get_secret(secret_name):
    # We're on Google Cloud
    print(os.getenv("GOOGLE_CLOUD_PROJECT"))
    client = secretmanager.SecretManagerServiceClient()
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    name = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8")


# Function to dynamically set environment (live or paper)
def set_alpaca_environment(env, use_secret_manager=True):
    if use_secret_manager and is_running_in_cloud():
        print("cloud")
        # On Google Cloud, use Secret Manager
        if env == "live":
            API_KEY = get_secret("ALPACA_API_KEY_LIVE")
            SECRET_KEY = get_secret("ALPACA_SECRET_KEY_LIVE")
            BASE_URL = "https://api.alpaca.markets"
        else:
            API_KEY = get_secret("ALPACA_API_KEY_PAPER")
            SECRET_KEY = get_secret("ALPACA_SECRET_KEY_PAPER")
            BASE_URL = "https://paper-api.alpaca.markets"
    else:
        # Running locally, use .env file
        load_dotenv()
        if env == "live":
            API_KEY = os.getenv("ALPACA_API_KEY_LIVE")
            SECRET_KEY = os.getenv("ALPACA_SECRET_KEY_LIVE")
            BASE_URL = "https://api.alpaca.markets"
        else:
            API_KEY = os.getenv("ALPACA_API_KEY_PAPER")
            SECRET_KEY = os.getenv("ALPACA_SECRET_KEY_PAPER")
            BASE_URL = "https://paper-api.alpaca.markets"

    # Return credentials dictionary instead of Alpaca API object
    return {"API_KEY": API_KEY, "SECRET_KEY": SECRET_KEY, "BASE_URL": BASE_URL}


def get_telegram_secrets():
    if is_running_in_cloud():
        telegram_key = get_secret("TELEGRAM_KEY")
        chat_id = get_secret("TELEGRAM_CHAT_ID")
    else:
        load_dotenv()
        telegram_key = os.getenv("TELEGRAM_KEY")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")

    return telegram_key, chat_id


def save_balance(strategy, invested):
    doc_ref = db.collection("strategy-balances").document(strategy)
    doc_ref.set(
        {
            "invested": invested,
        }
    )


def load_balances():
    balances = {}
    docs = db.collection("strategy-balances").stream()
    for doc in docs:
        balances[doc.id] = doc.to_dict()
    return balances


def update_balance_field(strategy, value):
    doc_ref = db.collection("strategy-balances").document(strategy)
    doc_ref.update({"invested": value})


# 9-Sig Strategy Data Management Functions
def save_nine_sig_quarterly_data(quarter_id, tqqq_balance, agg_balance, signal_line, action, quarterly_contributions):
    """Save quarterly data following 3Sig methodology for next quarter's calculations"""
    doc_ref = db.collection("nine-sig-quarters").document(quarter_id)
    doc_ref.set({
        "quarter_id": quarter_id,
        "quarter_end_date": datetime.datetime.now().isoformat(),
        "previous_tqqq_balance": tqqq_balance,
        "agg_balance": agg_balance,
        "signal_line": signal_line,
        "action_taken": action,
        "quarterly_contributions": quarterly_contributions,
        "total_portfolio": tqqq_balance + agg_balance,
        "timestamp": datetime.datetime.utcnow()
    })


def get_previous_quarter_tqqq_balance():
    """Get previous quarter's TQQQ ending balance for signal line calculation"""
    docs = db.collection("nine-sig-quarters").order_by("timestamp", direction=firestore.Query.DESCENDING).limit(1).stream()
    for doc in docs:
        data = doc.to_dict()
        return data.get("previous_tqqq_balance", 0)
    return 0


def check_spy_30_down_rule():
    """Check if SPY has dropped 30% from quarterly high in last 2 years"""
    try:
        # Get SPY data for last 2 years with quarterly intervals
        spy = yf.download("SPY", period="2y", interval="3mo")
        
        if len(spy) < 8:  # Need at least 2 years of quarterly data
            return False
            
        # Get highest quarterly close in last 2 years
        highest_close = spy["Close"].max()
        current_close = spy["Close"].iloc[-1]
        
        # Check if current is 30% below the high
        drop_percentage = (highest_close - current_close) / highest_close
        
        return drop_percentage >= 0.30
    except Exception as e:
        print(f"Error checking SPY 30 down rule: {e}")
        return False


def count_ignored_sell_signals():
    """Count how many sell signals have been ignored in the current crash protection period"""
    try:
        # Get recent quarters with ignored sell signals
        docs = db.collection("nine-sig-quarters").where("action_taken", "==", "SELL_IGNORED").order_by("timestamp", direction=firestore.Query.DESCENDING).limit(4).stream()
        return len(list(docs))
    except Exception as e:
        print(f"Error counting ignored sell signals: {e}")
        return 0


def make_monthly_nine_sig_contributions(api, force_execute=False):
    """Monthly contributions go ONLY to AGG (bonds) - Following 3Sig Rule"""
    investment_amount = investment_amounts["nine_sig_allo"]
    
    if not force_execute and not check_trading_day(mode="monthly"):
        print("Not first trading day of the month")
        return "Not first trading day of the month"
    
    if force_execute:
        print("9-Sig: Force execution enabled - bypassing trading day check")
        send_telegram_message("9-Sig: Force execution enabled for testing - bypassing trading day check")
    
    # ALL monthly contributions go to AGG only (core 3Sig rule)
    try:
        agg_price = float(get_latest_trade(api, "AGG"))
        agg_shares_to_buy = investment_amount / agg_price
        
        if agg_shares_to_buy > 0:
            order = submit_order(api, "AGG", agg_shares_to_buy, "buy")
            wait_for_order_fill(api, order["id"])
            print(f"9-Sig: Bought {agg_shares_to_buy:.6f} shares of AGG (monthly contribution)")
            send_telegram_message(f"9-Sig Monthly: Added ${investment_amount:.2f} to AGG bonds (following 3Sig methodology)")
        
        return f"9-Sig monthly contribution: ${investment_amount:.2f} invested in AGG"
    
    except Exception as e:
        error_msg = f"9-Sig monthly contribution failed: {str(e)}"
        print(error_msg)
        send_telegram_message(error_msg)
        return error_msg


def make_monthly_buys(api):
    investment_amount = investment_amounts["hfea_allo"]

    if not check_trading_day(mode="monthly"):
        print("Not first trading day of the month")
        return "Not first trading day of the month"
    # Get current portfolio allocations and values from get_hfea_allocations
    (
        upro_diff,
        tmf_diff,
        kmlm_diff,
        upro_value,
        tmf_value,
        kmlm_value,
        total_value,
        target_upro_value,
        target_tmf_value,
        target_kmlm_value,
        current_upro_percent,
        current_tmf_percent,
        current_kmlm_percent,
    ) = get_hfea_allocations(api)

    # Calculate underweight amounts
    upro_underweight = max(0, target_upro_value - upro_value)
    tmf_underweight = max(0, target_tmf_value - tmf_value)
    kmlm_underweight = max(0, target_kmlm_value - kmlm_value)
    total_underweight = upro_underweight + tmf_underweight + kmlm_underweight

    # If perfectly balanced, use standard split
    if total_underweight == 0:
        upro_amount = investment_amount * upro_allocation
        tmf_amount = investment_amount * tmf_allocation
        kmlm_amount = investment_amount * kmlm_allocation
    else:
        # Allocate proportionally based on underweight amounts
        upro_amount = (upro_underweight / total_underweight) * investment_amount
        tmf_amount = (tmf_underweight / total_underweight) * investment_amount
        kmlm_amount = (kmlm_underweight / total_underweight) * investment_amount

    # Get current prices for UPRO, TMF, and KMLM
    upro_price = float(get_latest_trade(api, "UPRO"))
    tmf_price = float(get_latest_trade(api, "TMF"))
    kmlm_price = float(get_latest_trade(api, "KMLM"))

    # Calculate number of shares to buy
    upro_shares_to_buy = upro_amount / upro_price
    tmf_shares_to_buy = tmf_amount / tmf_price
    kmlm_shares_to_buy = kmlm_amount / kmlm_price

    # Execute market orders
    for symbol, qty in [
        ("UPRO", upro_shares_to_buy),
        ("TMF", tmf_shares_to_buy),
        ("KMLM", kmlm_shares_to_buy),
    ]:
        if qty > 0:
            submit_order(api, symbol, qty, "buy")
            print(f"Bought {qty:.6f} shares of {symbol}.")
            send_telegram_message(f"Bought {qty:.6f} shares of {symbol}.")
        else:
            print(f"No shares of {symbol} bought due to small amount.")
            send_telegram_message(f"No shares of {symbol} bought due to small amount.")

    # Report updated allocations
    send_telegram_message(
        f"Current HFEA allocation: UPRO: {current_upro_percent:.0%} - TMF: {current_tmf_percent:.0%} - KMLM: {current_kmlm_percent:.0%}"
    )
    return "Monthly investment executed."


def get_hfea_allocations(api):
    positions = {p["symbol"]: float(p["market_value"]) for p in list_positions(api)}
    upro_value = positions.get("UPRO", 0)
    tmf_value = positions.get("TMF", 0)
    kmlm_value = positions.get("KMLM", 0)
    total_value = upro_value + tmf_value + kmlm_value
    # Calculate current and target allocations
    current_upro_percent = upro_value / total_value if total_value else 0
    current_tmf_percent = tmf_value / total_value if total_value else 0
    current_kmlm_percent = kmlm_value / total_value if total_value else 0
    target_upro_value = total_value * upro_allocation
    target_tmf_value = total_value * tmf_allocation
    target_kmlm_value = total_value * kmlm_allocation
    # Calculate deviations
    upro_diff = upro_value - target_upro_value
    tmf_diff = tmf_value - target_tmf_value
    kmlm_diff = kmlm_value - target_kmlm_value
    return (
        upro_diff,
        tmf_diff,
        kmlm_diff,
        upro_value,
        tmf_value,
        kmlm_value,
        total_value,
        target_upro_value,
        target_tmf_value,
        target_kmlm_value,
        current_upro_percent,
        current_tmf_percent,
        current_kmlm_percent,
    )


def rebalance_portfolio(api):
    if not check_trading_day(mode="quarterly"):
        print("Not first trading day of the month in this Quarter")
        return "Not first trading day of the month in this Quarter"
    # Get UPRO, TMF, and KMLM values and deviations from target allocation
    (
        upro_diff,
        tmf_diff,
        kmlm_diff,
        upro_value,
        tmf_value,
        kmlm_value,
        total_value,
        target_upro_value,
        target_tmf_value,
        target_kmlm_value,
        current_upro_percent,
        current_tmf_percent,
        current_kmlm_percent,
    ) = get_hfea_allocations(api)

    # Apply a margin for fees (e.g., 0.5%)
    fee_margin = 0.995

    # If the total value is 0, nothing to rebalance
    if total_value == 0:
        print("No holdings to rebalance.")
        send_telegram_message("No holdings to rebalance for HFEA Strategy.")
        return "No holdings to rebalance for HFEA Strategy."

    # Define trade parameters for each ETF
    rebalance_actions = []

    # If UPRO is over-allocated, adjust TMF or KMLM if under-allocated
    if upro_diff > 0:
        if tmf_diff < 0:
            upro_shares_to_sell = min(upro_diff, abs(tmf_diff)) / float(get_latest_trade(api, "UPRO"))
            tmf_shares_to_buy = (
                upro_shares_to_sell
                * float(get_latest_trade(api, "UPRO"))
                / float(get_latest_trade(api, "TMF"))
            ) * fee_margin
            rebalance_actions.append(("UPRO", upro_shares_to_sell, "sell"))
            rebalance_actions.append(("TMF", tmf_shares_to_buy, "buy"))

        if kmlm_diff < 0:
            upro_shares_to_sell = min(upro_diff, abs(kmlm_diff)) / float(get_latest_trade(api, "UPRO"))
            kmlm_shares_to_buy = (
                upro_shares_to_sell
                * float(get_latest_trade(api, "UPRO"))
                / float(get_latest_trade(api, "KMLM"))
            ) * fee_margin
            rebalance_actions.append(("UPRO", upro_shares_to_sell, "sell"))
            rebalance_actions.append(("KMLM", kmlm_shares_to_buy, "buy"))

    # If TMF is over-allocated, adjust UPRO or KMLM if under-allocated
    if tmf_diff > 0:
        if upro_diff < 0:
            tmf_shares_to_sell = min(tmf_diff, abs(upro_diff)) / float(get_latest_trade(api, "TMF"))
            upro_shares_to_buy = (
                tmf_shares_to_sell
                * float(get_latest_trade(api, "TMF"))
                / float(get_latest_trade(api, "UPRO"))
            ) * fee_margin
            rebalance_actions.append(("TMF", tmf_shares_to_sell, "sell"))
            rebalance_actions.append(("UPRO", upro_shares_to_buy, "buy"))

        if kmlm_diff < 0:
            tmf_shares_to_sell = min(tmf_diff, abs(kmlm_diff)) / float(get_latest_trade(api, "TMF"))
            kmlm_shares_to_buy = (
                tmf_shares_to_sell
                * float(get_latest_trade(api, "TMF"))
                / float(get_latest_trade(api, "KMLM"))
            ) * fee_margin
            rebalance_actions.append(("TMF", tmf_shares_to_sell, "sell"))
            rebalance_actions.append(("KMLM", kmlm_shares_to_buy, "buy"))

    # If KMLM is over-allocated, adjust UPRO or TMF if under-allocated
    if kmlm_diff > 0:
        if upro_diff < 0:
            kmlm_shares_to_sell = min(kmlm_diff, abs(upro_diff)) / float(get_latest_trade(api, "KMLM"))
            upro_shares_to_buy = (
                kmlm_shares_to_sell
                * float(get_latest_trade(api, "KMLM"))
                / float(get_latest_trade(api, "UPRO"))
            ) * fee_margin
            rebalance_actions.append(("KMLM", kmlm_shares_to_sell, "sell"))
            rebalance_actions.append(("UPRO", upro_shares_to_buy, "buy"))

        if tmf_diff < 0:
            kmlm_shares_to_sell = min(kmlm_diff, abs(tmf_diff)) / float(get_latest_trade(api, "KMLM"))
            tmf_shares_to_buy = (
                kmlm_shares_to_sell
                * float(get_latest_trade(api, "KMLM"))
                / float(get_latest_trade(api, "TMF"))
            ) * fee_margin
            rebalance_actions.append(("KMLM", kmlm_shares_to_sell, "sell"))
            rebalance_actions.append(("TMF", tmf_shares_to_buy, "buy"))

    # Execute rebalancing actions
    for symbol, qty, action in rebalance_actions:
        if qty > 0:
            order = submit_order(api, symbol, qty, action)
            action_verb = "Bought" if action == "buy" else "Sold"
            wait_for_order_fill(api, order["id"])
            print(f"{action_verb} {qty:.6f} shares of {symbol} to rebalance.")
            send_telegram_message(
                f"{action_verb} {qty:.6f} shares of {symbol} to rebalance."
            )

    # Report completion of rebalancing check
    print("Rebalance check completed.")
    return "Rebalance executed."


def execute_quarterly_nine_sig_signal(api, force_execute=False):
    """Execute quarterly 9-sig signal following Jason Kelly's exact 5-step process"""
    if not force_execute and not check_trading_day(mode="quarterly"):
        print("Not first trading day of the quarter")
        return "Not first trading day of the quarter"
    
    if force_execute:
        print("9-Sig: Force execution enabled - bypassing trading day check")
        send_telegram_message("9-Sig: Force execution enabled for testing - bypassing trading day check")
    
    try:
        # Step 1: Get current positions
        positions = {p["symbol"]: float(p["market_value"]) for p in list_positions(api)}
        current_tqqq_balance = positions.get("TQQQ", 0)
        current_agg_balance = positions.get("AGG", 0)
        total_portfolio = current_tqqq_balance + current_agg_balance
        
        # Step 1: Determine the Quarter's Signal Line
        previous_tqqq_balance = get_previous_quarter_tqqq_balance()
        quarterly_contributions = investment_amounts["nine_sig_allo"] * 3  # 3 months
        half_quarterly_contributions = quarterly_contributions * 0.5
        
        # Signal Line = Previous TQQQ Balance × 1.09 + (Half of Quarterly Contributions)
        if previous_tqqq_balance == 0 and total_portfolio > 0:
            # First quarter: Set signal line as 80% of total portfolio
            signal_line = total_portfolio * nine_sig_config["target_allocation"]["tqqq"]
            send_telegram_message("9-Sig: First quarter initialization - setting 80/20 target allocation")
        else:
            signal_line = (previous_tqqq_balance * (1 + nine_sig_config["quarterly_growth_rate"])) + half_quarterly_contributions
        
        # Step 2: Determine Action (Buy, Sell, or Hold)
        difference = current_tqqq_balance - signal_line
        tolerance = nine_sig_config["tolerance_amount"]
        
        # Step 3: Execute the Trade
        if abs(difference) < tolerance:
            action = "HOLD"
            send_telegram_message(f"9-Sig: HOLD - TQQQ ${current_tqqq_balance:.2f} within tolerance of signal line ${signal_line:.2f}")
            
        elif difference < 0:
            # BUY Signal: Need more TQQQ
            amount_to_buy = abs(difference)
            action = "BUY"
            
            # Step 4: Check for bond rebalancing on buy signals
            agg_percentage = current_agg_balance / total_portfolio if total_portfolio > 0 else 0
            if agg_percentage > nine_sig_config["bond_rebalance_threshold"]:
                # Add excess bonds to the buy amount
                target_agg_balance = total_portfolio * nine_sig_config["target_allocation"]["agg"]
                excess_agg = current_agg_balance - target_agg_balance
                amount_to_buy += excess_agg
                send_telegram_message(f"9-Sig: Rebalancing excess AGG (${excess_agg:.2f}) during buy signal")
            
            if current_agg_balance >= amount_to_buy:
                # Execute buy trade
                tqqq_price = float(get_latest_trade(api, "TQQQ"))
                agg_price = float(get_latest_trade(api, "AGG"))
                
                agg_shares_to_sell = amount_to_buy / agg_price
                tqqq_shares_to_buy = amount_to_buy / tqqq_price
                
                # Sell AGG first, then buy TQQQ
                sell_order = submit_order(api, "AGG", agg_shares_to_sell, "sell")
                wait_for_order_fill(api, sell_order["id"])
                
                buy_order = submit_order(api, "TQQQ", tqqq_shares_to_buy, "buy")
                wait_for_order_fill(api, buy_order["id"])
                
                send_telegram_message(f"9-Sig: BUY signal executed - Bought ${amount_to_buy:.2f} TQQQ (sold AGG)")
            else:
                # Insufficient AGG funds
                send_telegram_message(f"9-Sig: BUY signal but insufficient AGG (${current_agg_balance:.2f} < ${amount_to_buy:.2f}) - HOLDING existing positions")
                action = "HOLD_INSUFFICIENT_FUNDS"
                
        else:
            # SELL Signal: Too much TQQQ
            amount_to_sell = difference
            action = "SELL"
            
            # Step 5: Check for "30 Down, Stick Around" rule
            if check_spy_30_down_rule():
                ignored_count = count_ignored_sell_signals()
                
                if ignored_count < 4:
                    action = "SELL_IGNORED"
                    send_telegram_message(f"9-Sig: SELL signal IGNORED due to '30 Down, Stick Around' rule (SPY down >30%). Ignored {ignored_count + 1}/4 signals.")
                else:
                    send_telegram_message("9-Sig: Resuming normal operation after ignoring 4 sell signals")
            
            if action == "SELL":
                # Execute sell trade
                tqqq_price = float(get_latest_trade(api, "TQQQ"))
                agg_price = float(get_latest_trade(api, "AGG"))
                
                tqqq_shares_to_sell = amount_to_sell / tqqq_price
                agg_shares_to_buy = amount_to_sell / agg_price
                
                # Sell TQQQ first, then buy AGG
                sell_order = submit_order(api, "TQQQ", tqqq_shares_to_sell, "sell")
                wait_for_order_fill(api, sell_order["id"])
                
                buy_order = submit_order(api, "AGG", agg_shares_to_buy, "buy")
                wait_for_order_fill(api, buy_order["id"])
                
                send_telegram_message(f"9-Sig: SELL signal executed - Sold ${amount_to_sell:.2f} TQQQ (bought AGG)")
        
        # Save quarterly data for next calculation
        current_quarter = f"{datetime.datetime.now().year}-Q{((datetime.datetime.now().month-1)//3+1)}"
        save_nine_sig_quarterly_data(
            current_quarter,
            current_tqqq_balance,
            current_agg_balance, 
            signal_line,
            action,
            quarterly_contributions
        )
        
        # Report final allocations
        updated_positions = {p["symbol"]: float(p["market_value"]) for p in list_positions(api)}
        updated_total = updated_positions.get("TQQQ", 0) + updated_positions.get("AGG", 0)
        if updated_total > 0:
            tqqq_pct = updated_positions.get("TQQQ", 0) / updated_total
            agg_pct = updated_positions.get("AGG", 0) / updated_total
            send_telegram_message(f"9-Sig allocation: TQQQ {tqqq_pct:.1%}, AGG {agg_pct:.1%} (Target: 80/20)")
        
        return f"9-Sig quarterly signal: {action}"
    
    except Exception as e:
        error_msg = f"9-Sig quarterly signal failed: {str(e)}"
        print(error_msg)
        send_telegram_message(error_msg)
        return error_msg


# Function to calculate 200-SMA using yfinance
def calculate_200sma(symbol):
    data = yf.download(
        symbol, period="1y", interval="1d"
    )  # Download 1 year of daily data
    sma_200 = data["Close"].rolling(window=200).mean().iloc[-1].item()
    return sma_200


# Function to get latest s&p price using yfinance
def get_latest_price(symbol):
    # Fetch the real-time data for SPY
    ticker = yf.Ticker(symbol)
    data = ticker.history(period="1d")

    # Get the current price
    price = data["Close"].iloc[-1]
    return price


def check_trading_day(mode="daily"):
    """
    Check if today is a trading day, the first trading day of the month, or the first trading day of the quarter.

    :param mode: "daily" for a regular trading day, "monthly" for the first trading day of the month,
                 "quarterly" for the first trading day of the quarter.
    :return: True if the condition is met, False otherwise.
    """
    # Get current date
    today = datetime.datetime.now()

    # Load the NYSE market calendar
    nyse = mcal.get_calendar("NYSE")

    # Check if the market is open today
    schedule = nyse.schedule(start_date=today.date(), end_date=today.date())
    if schedule.empty:
        return False  # Market is closed today (e.g., weekend or holiday)

    if mode == "daily":
        return True  # It's a trading day

    # Check if it's the first trading day of the month
    if mode == "monthly":
        first_day_of_month = today.replace(day=1)
        schedule = nyse.schedule(
            start_date=first_day_of_month,
            end_date=first_day_of_month + datetime.timedelta(days=6),
        )
        first_trading_day = schedule.index[0].date()
        return today.date() == first_trading_day

    # Check if it's the first trading day of the quarter
    if mode == "quarterly":
        first_day_of_quarter = today.replace(day=1)
        if today.month not in [1, 4, 7, 10]:
            return False  # Not the first month of a quarter
        schedule = nyse.schedule(
            start_date=first_day_of_quarter,
            end_date=first_day_of_quarter + datetime.timedelta(days=6),
        )
        first_trading_day = schedule.index[0].date()
        return today.date() == first_trading_day

    raise ValueError("Invalid mode. Use 'daily', 'monthly', or 'quarterly'.")


def monthly_buying_sma(api, symbol):
    if not check_trading_day(mode="monthly"):
        return "Not first trading day of the month"

    if symbol == "SPXL":
        sma_200 = calculate_200sma("^GSPC")
        latest_price = get_latest_price("^GSPC")
        investment_amount = investment_amounts["spxl_allo"]
    elif symbol == "EET":
        sma_200 = calculate_200sma("EEM")
        latest_price = get_latest_price("EEM")
        investment_amount = investment_amounts["eet_allo"]
    elif symbol == "EFO":
        sma_200 = calculate_200sma("EFA")
        latest_price = get_latest_price("EFA")
        investment_amount = investment_amounts["efo_allo"]

    print(investment_amount, latest_price, sma_200)
    if latest_price > sma_200 * (1 + margin):
        price = get_latest_trade(api, symbol)
        print(price)
        shares_to_buy = investment_amount / price

        if shares_to_buy > 0:
            order = submit_order(api, symbol, shares_to_buy, "buy")
            wait_for_order_fill(api, order["id"])
            positions = list_positions(api)
            position = next((p for p in positions if p["symbol"] == symbol), None)
            invested = float(position["market_value"])
            save_balance(symbol + "_SMA", invested)
            send_telegram_message(f"Bought {shares_to_buy:.6f} shares of {symbol}.")
            return f"Bought {shares_to_buy:.6f} shares of {symbol}."
        else:
            send_telegram_message(f"Amount too small to buy {symbol} shares.")
            return f"Amount too small to buy {symbol} shares."
    else:
        invested_amount = load_balances().get(f"{symbol}_SMA", {}).get("invested", None)
        updated_balance = investment_amount + invested_amount
        save_balance(symbol + "_SMA", updated_balance)
        send_telegram_message(
            f"Index is significantly below 200-SMA and no monthly invest was done into {symbol} but {updated_balance} of the cash is allocated to to this strategy"
        )
        return f"Index is significantly below 200-SMA and no monthly invest was done into {symbol} but {updated_balance} of the cash is allocated to to this strategy"


def daily_trade_sma(api, symbol):
    if not check_trading_day(mode="daily"):
        send_telegram_message(f"Market closed today. Skipping 200SMA. for {symbol}")
        return "Market closed today."

    if symbol == "SPXL":
        sma_200 = calculate_200sma("^GSPC")
        latest_price = get_latest_price("^GSPC")
    elif symbol == "EET":
        sma_200 = calculate_200sma("EEM")
        latest_price = get_latest_price("EEM")
    elif symbol == "EFO":
        sma_200 = calculate_200sma("EFA")
        latest_price = get_latest_price("EFA")

    if latest_price < sma_200 * (1 - margin):
        positions = list_positions(api)
        position = next((p for p in positions if p["symbol"] == symbol), None)

        if position:
            shares_to_sell = float(position["qty"])
            invested = float(position["market_value"])
            # Sell all SPXL shares
            sell_order = submit_order(api, symbol, shares_to_sell, "sell")
            send_telegram_message(
                f"Sold all {shares_to_sell:.6f} shares of {symbol} because Index is significantly below 200-SMA."
            )
            # Wait for the sell order to be filled
            wait_for_order_fill(api, sell_order["id"])
            save_balance(symbol + "_SMA", invested)
        else:
            send_telegram_message(
                f"Index is significantly below 200-SMA and no {symbol} position to sell."
            )
            return f"Index is significantly below 200-SMA and no {symbol} position to sell."
    elif latest_price > sma_200 * (1 + margin):
        # adjustment to read balance needed here
        available_cash = get_account_cash(api)
        invested_amount = load_balances().get(f"{symbol}_SMA", {}).get("invested", None)
        positions = list_positions(api)
        position = next((p for p in positions if p["symbol"] == symbol), None)
        if not position and available_cash > invested_amount:
            price = get_latest_trade(api, symbol)
            shares_to_buy = invested_amount / price
            buy_order = submit_order(api, symbol, shares_to_buy, "buy")
            wait_for_order_fill(api, buy_order["id"])
            positions = list_positions(api)
            position = next((p for p in positions if p["symbol"] == symbol), None)
            invested = float(position["market_value"])
            save_balance(symbol + "_SMA", invested)
            send_telegram_message(
                f"Bought {shares_to_buy:.6f} shares of {symbol} with available cash"
            )
            return f"Bought {shares_to_buy:.6f} shares of {symbol} with available cash."
        else:
            invested = float(position["market_value"]) if position else 0
            save_balance(symbol + "_SMA", invested)
            send_telegram_message(
                f"Index is above 200-SMA. No {symbol} shares bought because of no cash but {invested} is already invested"
            )
            return f"Index is above 200-SMA. No {symbol} shares bought because of no cash but {invested} is already invested"
    else:
        positions = list_positions(api)
        position = next((p for p in positions if p["symbol"] == symbol), None)
        if position:
            invested = float(position["market_value"])
            save_balance(symbol + "_SMA", invested)
        send_telegram_message(
            f"Index is not significantly below or above 200-SMA. No {symbol} shares sold or bought"
        )
        return f"Index is not significantly below or above 200-SMA. No {symbol} shares sold or bought"

# Function to send a message via Telegram
def send_telegram_message(message):
    telegram_key, chat_id = get_telegram_secrets()
    url = f"https://api.telegram.org/bot{telegram_key}/sendMessage"
    data = {"chat_id": chat_id, "text": message}
    response = requests.post(url, data=data)
    return response.status_code


# Function to get the chat title
def get_chat_title():
    telegram_key, chat_id = get_telegram_secrets()
    url = f"https://api.telegram.org/bot{telegram_key}/getChat?chat_id={chat_id}"
    response = requests.get(url)
    chat_info = response.json()

    if chat_info["ok"]:
        return chat_info["result"].get("title", "")
    else:
        return None


def get_index_data(index_symbol):
    """Fetch the all-time high and current price for an index."""
    # Download historical data for the index
    data = yf.download(index_symbol, period="max")

    # Get the all-time high
    all_time_high = data["High"].max().item()

    # Get the current price (latest close price)
    current_price = data["Close"].iloc[-1].item()

    return current_price, all_time_high


def check_index_drop(request):
    """Cloud Function that checks if an index has dropped 30% below its all-time high."""

    # Handle case where Content-Type is not set to application/json (e.g., application/octet-stream)
    if request.content_type == "application/json":
        request_json = request.get_json(silent=True)
    else:
        # If the Content-Type is octet-stream or undefined, attempt to decode the body manually
        try:
            request_json = json.loads(request.data.decode("utf-8"))
        except Exception:
            return jsonify({"error": "Failed to parse request body"}), 400

    # Check if the required parameters are present
    if request_json and "index_symbol" in request_json and "index_name" in request_json:
        index_symbol = request_json["index_symbol"]
        index_name = request_json["index_name"]
    else:
        return jsonify(
            {"error": "Missing index_symbol or index_name in the request body"}
        ), 400

    current_price, all_time_high = get_index_data(index_symbol)

    # Calculate the percentage drop
    drop_percentage = ((all_time_high - current_price) / all_time_high) * 100

    # Send alert if the index has dropped 30% or more
    if drop_percentage >= 30:
        message = f"Alert: {index_name} has dropped {drop_percentage:.2f}% from its ATH! Consider a loan with a duration of 6 to 8 years (50k to 100k) at around 4.5% interest max"
        send_telegram_message(message)
        return jsonify({"message": message}), 200
    else:
        # message = f"Alert: {index_name} is within safe range ({drop_percentage:.2f}% below ATH)."
        # send_telegram_message(message)
        return jsonify(
            {
                "message": f"{index_name} is within safe range ({drop_percentage:.2f}% below ATH)."
            }
        ), 200


# Helper function to wait for an order to be filled
def wait_for_order_fill(api, order_id, timeout=300, poll_interval=5):
    elapsed_time = 0
    while elapsed_time < timeout:
        order = get_order(api, order_id)
        if order["status"] == "filled":
            print(f"Order {order_id} filled.")
            return float(order["filled_avg_price"]) * float(order["filled_qty"])
        elif order["status"] == "canceled":
            print(f"Order {order_id} was canceled.")
            send_telegram_message(f"Order {order_id} was canceled.")
            return
        else:
            print(f"Waiting for order {order_id} to fill... (status: {order['status']})")
            time.sleep(poll_interval)
            elapsed_time += poll_interval
    print(f"Timeout: Order {order_id} did not fill within {timeout} seconds.")
    send_telegram_message(
        f"Timeout: Order {order_id} did not fill within {timeout} seconds."
    )


@app.route("/monthly_buy_hfea", methods=["POST"])
def monthly_buy_hfea(request):
    api = set_alpaca_environment(
        env=alpaca_environment
    )  # or 'paper' based on your needs
    return make_monthly_buys(api)


@app.route("/rebalance_hfea", methods=["POST"])
def rebalance_hfea(request):
    api = set_alpaca_environment(
        env=alpaca_environment
    )  # or 'paper' based on your needs
    return rebalance_portfolio(api)


@app.route("/monthly_nine_sig_contributions", methods=["POST"])
def monthly_nine_sig_contributions(request):
    api = set_alpaca_environment(env=alpaca_environment)
    return make_monthly_nine_sig_contributions(api)


@app.route("/quarterly_nine_sig_signal", methods=["POST"])
def quarterly_nine_sig_signal(request):
    api = set_alpaca_environment(env=alpaca_environment)
    return execute_quarterly_nine_sig_signal(api)


@app.route("/monthly_buy_spxl", methods=["POST"])
def monthly_buy_spxl(request):
    api = set_alpaca_environment(
        env=alpaca_environment
    )  # or 'paper' based on your needs
    result = monthly_buying_sma(api, "SPXL")
    print(result)
    return result, 200


@app.route("/monthly_buy_eet", methods=["POST"])
def monthly_buy_eet(request):
    api = set_alpaca_environment(
        env=alpaca_environment
    )  # or 'paper' based on your needs
    result = monthly_buying_sma(api, "EET")
    print(result)
    return result, 200


@app.route("/monthly_buy_efo", methods=["POST"])
def monthly_buy_efo(request):
    api = set_alpaca_environment(
        env=alpaca_environment
    )  # or 'paper' based on your needs
    result = monthly_buying_sma(api, "EFO")
    print(result)
    return result, 200


@app.route("/daily_trade_spxl_200sma", methods=["POST"])
def daily_trade_spxl_200sma(request):
    api = set_alpaca_environment(
        env=alpaca_environment
    )  # or 'paper' based on your needs
    result = daily_trade_sma(api, "SPXL")
    print(result)
    return result, 200


@app.route("/daily_trade_eet_200sma", methods=["POST"])
def daily_trade_eet_200sma(request):
    api = set_alpaca_environment(
        env=alpaca_environment
    )  # or 'paper' based on your needs
    result = daily_trade_sma(api, "EET")
    print(result)
    return result, 200


@app.route("/daily_trade_efo_200sma", methods=["POST"])
def daily_trade_efo_200sma(request):
    api = set_alpaca_environment(
        env=alpaca_environment
    )  # or 'paper' based on your needs
    result = daily_trade_sma(api, "EFO")
    print(result)
    return result, 200


@app.route("/index_alert", methods=["POST"])
def index_alert(request):
    return check_index_drop(request)


# @app.route('/monthly_buy_tqqq', methods=['POST'])
# def monthly_buy_tqqq(request):
#     api = set_alpaca_environment(env=alpaca_environment)  # or 'paper' based on your needs
#     return make_monthly_buy_tqqq(api)

# @app.route('/sell_tqqq_below_200sma', methods=['POST'])
# def sell_tqqq_below_200sma(request):
#     api = set_alpaca_environment(env=alpaca_environment)  # or 'paper' based on your needs
#     return sell_tqqq_if_below_200sma(api)

# @app.route('/buy_tqqq_above_200sma', methods=['POST'])
# def buy_tqqq_above_200sma(request):
#     api = set_alpaca_environment(env=alpaca_environment)  # or 'paper' based on your needs
#     return buy_tqqq_if_above_200sma(api)


def run_local(action, env="paper", request="test", force_execute=False):
    api = set_alpaca_environment(env=env, use_secret_manager=False)
    if action == "monthly_buy_hfea":
        return make_monthly_buys(api)
    elif action == "rebalance_hfea":
        return rebalance_portfolio(api)
    elif action == "monthly_nine_sig_contributions":
        return make_monthly_nine_sig_contributions(api, force_execute=force_execute)
    elif action == "quarterly_nine_sig_signal":
        return execute_quarterly_nine_sig_signal(api, force_execute=force_execute)
    elif action == "monthly_buy_spxl":
        return monthly_buying_sma(api, "SPXL")
    elif action == "sell_spxl_below_200sma":
        return daily_trade_sma(api, "SPXL")
    elif action == "buy_spxl_above_200sma":
        return daily_trade_sma(api, "SPXL")
    elif action == "monthly_buy_eet":
        return monthly_buying_sma(api, "EET")
    elif action == "sell_eet_below_200sma":
        return daily_trade_sma(api, "EET")
    elif action == "buy_eet_above_200sma":
        return daily_trade_sma(api, "EET")
    elif action == "monthly_buy_efo":
        return monthly_buying_sma(api, "EFO")
    elif action == "sell_efo_below_200sma":
        return daily_trade_sma(api, "EFO")
    elif action == "buy_efo_above_200sma":
        return daily_trade_sma(api, "EFO")
    elif action == "index_alert":
        return check_index_drop(request)
    else:
        return "No valid action provided."


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--action",
        choices=[
            "monthly_buy_hfea",
            "rebalance_hfea",
            "monthly_nine_sig_contributions",
            "quarterly_nine_sig_signal",
            "monthly_buy_spxl",
            "sell_spxl_below_200sma",
            "buy_spxl_above_200sma",
            "index_alert",
            "sell_tqqq_below_200sma",
            "buy_tqqq_above_200sma",
            "monthly_buy_tqqq",
            "monthly_buy_eet",
            "sell_eet_below_200sma",
            "buy_eet_above_200sma",
            "monthly_buy_efo",
            "sell_efo_below_200sma",
            "buy_efo_above_200sma",
        ],
        required=True,
        help="Action to perform: including 9-sig strategy actions 'monthly_nine_sig_contributions', 'quarterly_nine_sig_signal'",
    )
    parser.add_argument(
        "--env",
        choices=["live", "paper"],
        default="paper",
        help="Alpaca environment: 'live' or 'paper'",
    )
    parser.add_argument(
        "--use_secret_manager",
        action="store_true",
        help="Use Google Secret Manager for API keys",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force execution even if not on the correct trading day (for testing)",
    )
    args = parser.parse_args()

    # Run the function locally
    result = run_local(action=args.action, env=args.env, force_execute=args.force)
    # save_balance("SPXL_SMA", 100)
    # save_balance("EET_SMA", 100)
    # save_balance("EFO_SMA", 100)

# local execution:
# python3 main.py --action monthly_buy_hfea --env paper
# python3 main.py --action rebalance_hfea --env paper
# python3 main.py --action monthly_nine_sig_contributions --env paper --force
# python3 main.py --action quarterly_nine_sig_signal --env paper --force
# python3 main.py --action monthly_buy_spxl --env paper
# python3 main.py --action sell_spxl_below_200sma --env paper
# python3 main.py --action buy_spxl_above_200sma --env paper
# python3 main.py --action monthly_buy_tqqq --env paper
# python3 main.py --action sell_tqqq_below_200sma --env paper
# python3 main.py --action buy_tqqq_above_200sma --env paper
# python3 main.py --action buy_eet_above_200sma --env paper
# python3 main.py --action sell_eet_below_200sma --env paper


# consider shifting to short term bonds when 200sma is below https://app.alpaca.markets/trade/BIL?asset_class=stocks
