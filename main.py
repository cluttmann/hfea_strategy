import os
from flask import Flask, jsonify
from google.cloud import secretmanager
from dotenv import load_dotenv
import alpaca_trade_api as tradeapi
import yfinance as yf
import requests
import json
import time
import pandas_market_calendars as mcal
import datetime
from google.cloud import firestore


app = Flask(__name__)

monthly_invest = 400

# Strategy would be to allocate 40% to the SPXL, 10% to the EET, 10% to the EFO SMA 200 Strategy and 40% to HFEA
strategy_allocations = {
    "hfea_allo": 0.4,
    "spxl_allo": 0.4,
    "eet_allo": 0.1,
    "efo_allo": 0.1,
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

# Initialize Firestore client
project_id = os.getenv("GOOGLE_CLOUD_PROJECT_ID")
db = firestore.Client(project=project_id)

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

    # Initialize Alpaca API
    return tradeapi.REST(API_KEY, SECRET_KEY, BASE_URL, api_version="v2")


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
    upro_price = float(api.get_latest_trade("UPRO").price)
    tmf_price = float(api.get_latest_trade("TMF").price)
    kmlm_price = float(api.get_latest_trade("KMLM").price)

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
            api.submit_order(
                symbol=symbol, qty=qty, side="buy", type="market", time_in_force="day"
            )
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
    positions = {p.symbol: float(p.market_value) for p in api.list_positions()}
    upro_value = positions.get("UPRO", 0)
    tmf_value = positions.get("TMF", 0)
    kmlm_value = positions.get("KMLM", 0)
    total_value = upro_value + tmf_value + kmlm_value

    # Calculate current and target allocations
    current_upro_percent = upro_value / total_value
    current_tmf_percent = tmf_value / total_value
    current_kmlm_percent = kmlm_value / total_value
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
            upro_shares_to_sell = min(upro_diff, abs(tmf_diff)) / float(
                api.get_latest_trade("UPRO").price
            )
            tmf_shares_to_buy = (
                upro_shares_to_sell
                * float(api.get_latest_trade("UPRO").price)
                / float(api.get_latest_trade("TMF").price)
            ) * fee_margin
            rebalance_actions.append(("UPRO", upro_shares_to_sell, "sell"))
            rebalance_actions.append(("TMF", tmf_shares_to_buy, "buy"))

        if kmlm_diff < 0:
            upro_shares_to_sell = min(upro_diff, abs(kmlm_diff)) / float(
                api.get_latest_trade("UPRO").price
            )
            kmlm_shares_to_buy = (
                upro_shares_to_sell
                * float(api.get_latest_trade("UPRO").price)
                / float(api.get_latest_trade("KMLM").price)
            ) * fee_margin
            rebalance_actions.append(("UPRO", upro_shares_to_sell, "sell"))
            rebalance_actions.append(("KMLM", kmlm_shares_to_buy, "buy"))

    # If TMF is over-allocated, adjust UPRO or KMLM if under-allocated
    if tmf_diff > 0:
        if upro_diff < 0:
            tmf_shares_to_sell = min(tmf_diff, abs(upro_diff)) / float(
                api.get_latest_trade("TMF").price
            )
            upro_shares_to_buy = (
                tmf_shares_to_sell
                * float(api.get_latest_trade("TMF").price)
                / float(api.get_latest_trade("UPRO").price)
            ) * fee_margin
            rebalance_actions.append(("TMF", tmf_shares_to_sell, "sell"))
            rebalance_actions.append(("UPRO", upro_shares_to_buy, "buy"))

        if kmlm_diff < 0:
            tmf_shares_to_sell = min(tmf_diff, abs(kmlm_diff)) / float(
                api.get_latest_trade("TMF").price
            )
            kmlm_shares_to_buy = (
                tmf_shares_to_sell
                * float(api.get_latest_trade("TMF").price)
                / float(api.get_latest_trade("KMLM").price)
            ) * fee_margin
            rebalance_actions.append(("TMF", tmf_shares_to_sell, "sell"))
            rebalance_actions.append(("KMLM", kmlm_shares_to_buy, "buy"))

    # If KMLM is over-allocated, adjust UPRO or TMF if under-allocated
    if kmlm_diff > 0:
        if upro_diff < 0:
            kmlm_shares_to_sell = min(kmlm_diff, abs(upro_diff)) / float(
                api.get_latest_trade("KMLM").price
            )
            upro_shares_to_buy = (
                kmlm_shares_to_sell
                * float(api.get_latest_trade("KMLM").price)
                / float(api.get_latest_trade("UPRO").price)
            ) * fee_margin
            rebalance_actions.append(("KMLM", kmlm_shares_to_sell, "sell"))
            rebalance_actions.append(("UPRO", upro_shares_to_buy, "buy"))

        if tmf_diff < 0:
            kmlm_shares_to_sell = min(kmlm_diff, abs(tmf_diff)) / float(
                api.get_latest_trade("KMLM").price
            )
            tmf_shares_to_buy = (
                kmlm_shares_to_sell
                * float(api.get_latest_trade("KMLM").price)
                / float(api.get_latest_trade("TMF").price)
            ) * fee_margin
            rebalance_actions.append(("KMLM", kmlm_shares_to_sell, "sell"))
            rebalance_actions.append(("TMF", tmf_shares_to_buy, "buy"))

    # Execute rebalancing actions
    for symbol, qty, action in rebalance_actions:
        if qty > 0:
            order = api.submit_order(
                symbol=symbol, qty=qty, side=action, type="market", time_in_force="day"
            )
            action_verb = "Bought" if action == "buy" else "Sold"
            wait_for_order_fill(api, order.id)
            print(f"{action_verb} {qty:.6f} shares of {symbol} to rebalance.")
            send_telegram_message(
                f"{action_verb} {qty:.6f} shares of {symbol} to rebalance."
            )

    # Report completion of rebalancing check
    print("Rebalance check completed.")
    return "Rebalance executed."


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
        price = api.get_latest_trade(symbol).price
        print(price)
        shares_to_buy = investment_amount / price

        if shares_to_buy > 0:
            order = api.submit_order(
                symbol=symbol,
                qty=shares_to_buy,
                side="buy",
                type="market",
                time_in_force="day",
            )
            wait_for_order_fill(api, order.id)
            positions = api.list_positions()
            position = next((p for p in positions if p.symbol == symbol), None)
            invested = float(position.market_value)
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
        positions = api.list_positions()
        position = next((p for p in positions if p.symbol == symbol), None)

        if position:
            shares_to_sell = float(position.qty)
            invested = float(position.market_value)
            # Sell all SPXL shares
            sell_order = api.submit_order(
                symbol=symbol,
                qty=shares_to_sell,
                side="sell",
                type="market",
                time_in_force="day",
            )
            send_telegram_message(
                f"Sold all {shares_to_sell:.6f} shares of {symbol} because Index is significantly below 200-SMA."
            )

            # Wait for the sell order to be filled
            wait_for_order_fill(api, sell_order.id)
            save_balance(symbol + "_SMA", invested)
        else:
            send_telegram_message(
                f"Index is significantly below 200-SMA and no {symbol} position to sell."
            )
            return f"Index is significantly below 200-SMA and no {symbol} position to sell."
    elif latest_price > sma_200 * (1 + margin):
        # adjustment to read balance needed here
        account = api.get_account()
        available_cash = float(account.cash)
        invested_amount = load_balances().get(f"{symbol}_SMA", {}).get("invested", None)
        positions = api.list_positions()
        position = next((p for p in positions if p.symbol == symbol), None)
        if not position and available_cash > invested_amount:
            price = api.get_latest_trade(symbol).price
            shares_to_buy = invested_amount / price
            buy_order = api.submit_order(
                symbol=symbol,
                qty=shares_to_buy,
                side="buy",
                type="market",
                time_in_force="day",
            )
            wait_for_order_fill(api, buy_order.id)
            positions = api.list_positions()
            position = next((p for p in positions if p.symbol == symbol), None)
            invested = float(position.market_value)
            save_balance(symbol + "_SMA", invested)
            send_telegram_message(
                f"Bought {shares_to_buy:.6f} shares of {symbol} with available cash"
            )
            return f"Bought {shares_to_buy:.6f} shares of {symbol} with available cash."
        else:
            invested = float(position.market_value)
            save_balance(symbol + "_SMA", invested)
            send_telegram_message(
                f"Index is above 200-SMA. No {symbol} shares bought because of no cash but {invested} is already invested"
            )
            return f"Index is above 200-SMA. No {symbol} shares bought because of no cash but {invested} is already invested"
    else:
        positions = api.list_positions()
        position = next((p for p in positions if p.symbol == symbol), None)
        if position:
            invested = float(position.market_value)
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
        order = api.get_order(order_id)
        if order.status == "filled":
            print(f"Order {order_id} filled.")
            return float(order.filled_avg_price) * float(order.filled_qty)
        elif order.status == "canceled":
            print(f"Order {order_id} was canceled.")
            send_telegram_message(f"Order {order_id} was canceled.")
            return
        else:
            print(f"Waiting for order {order_id} to fill... (status: {order.status})")
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


def run_local(action, env="paper", request="test"):
    api = set_alpaca_environment(env=env, use_secret_manager=False)
    if action == "monthly_buy_hfea":
        return make_monthly_buys(api)
    elif action == "rebalance_hfea":
        return rebalance_portfolio(api)
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
    # elif action == 'monthly_buy_tqqq':
    #     return make_monthly_buy_tqqq(api)
    # elif action == 'sell_tqqq_below_200sma':
    #     return sell_tqqq_if_below_200sma(api)
    # elif action == 'buy_tqqq_above_200sma':
    #     return buy_tqqq_if_above_200sma(api)
    elif action == "index_alert":
        return check_index_drop(request)
    else:
        return "No valid action provided. Use 'buy' or 'rebalance'."


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--action",
        choices=[
            "monthly_buy_hfea",
            "rebalance_hfea",
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
        help="Action to perform: 'monthly_buy_hfea', 'rebalance_hfea', 'monthly_buy_spxl','sell_spxl_below_200sma','buy_spxl_above_200sma','sell_tqqq_below_200sma', 'buy_tqqq_above_200sma', 'monthly_buy_tqqq','index_alert'",
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
    args = parser.parse_args()

    # Run the function locally
    result = run_local(action=args.action, env=args.env)
    # save_balance("SPXL_SMA", 100)
    # save_balance("EET_SMA", 100)
    # save_balance("EFO_SMA", 100)

# local execution:
# python3 main.py --action monthly_buy_hfea --env paper
# python3 main.py --action rebalance_hfea --env paper
# python3 main.py --action monthly_buy_spxl --env paper
# python3 main.py --action sell_spxl_below_200sma --env paper
# python3 main.py --action buy_spxl_above_200sma --env paper
# python3 main.py --action monthly_buy_tqqq --env paper
# python3 main.py --action sell_tqqq_below_200sma --env paper
# python3 main.py --action buy_tqqq_above_200sma --env paper
# python3 main.py --action buy_eet_above_200sma --env paper
# python3 main.py --action sell_eet_below_200sma --env paper


# consider shifting to short term bonds when 200sma is below https://app.alpaca.markets/trade/BIL?asset_class=stocks
