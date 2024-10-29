import os
from flask import Flask, jsonify
from google.cloud import secretmanager
from dotenv import load_dotenv
import alpaca_trade_api as tradeapi
import yfinance as yf
import requests
import json
import time


app = Flask(__name__)

monthly_invest = 350

#Strategy would be to allocate 50% to the SPXL SMA 200 Strategy, 15% to the TQQQ SMA 200 Strategy and 30% to HFEA
hfea_investment_amount = monthly_invest * 0.4
spxl_investment_amount = monthly_invest * 0.5
tqqq_investment_amount = monthly_invest * 0.1

upro_allocation = 0.45
tmf_allocation = 0.25
kmlm_allocation = 0.3

alpaca_environment = 'live'

def is_running_in_cloud():
    return (
        os.getenv('GAE_ENV', '').startswith('standard') or
        os.getenv('FUNCTION_NAME') is not None or
        os.getenv('K_SERVICE') is not None or
        os.getenv('GAE_INSTANCE') is not None or
        os.getenv('GOOGLE_CLOUD_PROJECT') is not None
    )
    
# Function to get secrets from Google Secret Manager
def get_secret(secret_name):
    # We're on Google Cloud
    print(os.getenv('GOOGLE_CLOUD_PROJECT'))
    client = secretmanager.SecretManagerServiceClient()
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    name = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8")

# Function to dynamically set environment (live or paper)
def set_alpaca_environment(env, use_secret_manager=True):
    if use_secret_manager and is_running_in_cloud():
        print('cloud')
        # On Google Cloud, use Secret Manager
        if env == 'live':
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
        if env == 'live':
            API_KEY = os.getenv("ALPACA_API_KEY_LIVE")
            SECRET_KEY = os.getenv("ALPACA_SECRET_KEY_LIVE")
            BASE_URL = "https://api.alpaca.markets"
        else:
            API_KEY = os.getenv("ALPACA_API_KEY_PAPER")
            SECRET_KEY = os.getenv("ALPACA_SECRET_KEY_PAPER")
            BASE_URL = "https://paper-api.alpaca.markets"
            
    # Initialize Alpaca API
    return tradeapi.REST(API_KEY, SECRET_KEY, BASE_URL, api_version='v2')

def get_telegram_secrets():
    if is_running_in_cloud():
        telegram_key = get_secret("TELEGRAM_KEY")
        chat_id = get_secret("TELEGRAM_CHAT_ID")
    else:
        load_dotenv()
        telegram_key = os.getenv("TELEGRAM_KEY")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        
    return telegram_key, chat_id
        
        
        
def make_monthly_buys(api):
    investment_amount = hfea_investment_amount

    # Get current portfolio allocations and values from get_hfea_allocations
    upro_diff, tmf_diff, kmlm_diff, upro_value, tmf_value, kmlm_value, total_value, target_upro_value, target_tmf_value, target_kmlm_value, current_upro_percent, current_tmf_percent, current_kmlm_percent = get_hfea_allocations(api)

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
    for symbol, qty in [("UPRO", upro_shares_to_buy), ("TMF", tmf_shares_to_buy), ("KMLM", kmlm_shares_to_buy)]:
        if qty > 0:
            api.submit_order(symbol=symbol, qty=qty, side='buy', type='market', time_in_force='day')
            print(f"Bought {qty:.6f} shares of {symbol}.")
            send_telegram_message(f"Bought {qty:.6f} shares of {symbol}.")
        else:
            print(f"No shares of {symbol} bought due to small amount.")
            send_telegram_message(f"No shares of {symbol} bought due to small amount.")

    # Report updated allocations
    send_telegram_message(f"Current HFEA allocation: UPRO: {current_upro_percent:.0%} - TMF: {current_tmf_percent:.0%} - KMLM: {current_kmlm_percent:.0%}")
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
    
    return (upro_diff, tmf_diff, kmlm_diff, upro_value, tmf_value, kmlm_value, total_value,
            target_upro_value, target_tmf_value, target_kmlm_value, current_upro_percent,
            current_tmf_percent, current_kmlm_percent)

def rebalance_portfolio(api):
    # Get UPRO, TMF, and KMLM values and deviations from target allocation
    upro_diff, tmf_diff, kmlm_diff, upro_value, tmf_value, kmlm_value, total_value, target_upro_value, target_tmf_value, target_kmlm_value, current_upro_percent, current_tmf_percent, current_kmlm_percent = get_hfea_allocations(api)
    
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
            upro_shares_to_sell = min(upro_diff, abs(tmf_diff)) / float(api.get_latest_trade("UPRO").price)
            tmf_shares_to_buy = (upro_shares_to_sell * float(api.get_latest_trade("UPRO").price) / float(api.get_latest_trade("TMF").price)) * fee_margin
            rebalance_actions.append(("UPRO", upro_shares_to_sell, 'sell'))
            rebalance_actions.append(("TMF", tmf_shares_to_buy, 'buy'))
        
        if kmlm_diff < 0:
            upro_shares_to_sell = min(upro_diff, abs(kmlm_diff)) / float(api.get_latest_trade("UPRO").price)
            kmlm_shares_to_buy = (upro_shares_to_sell * float(api.get_latest_trade("UPRO").price) / float(api.get_latest_trade("KMLM").price)) * fee_margin
            rebalance_actions.append(("UPRO", upro_shares_to_sell, 'sell'))
            rebalance_actions.append(("KMLM", kmlm_shares_to_buy, 'buy'))

    # If TMF is over-allocated, adjust UPRO or KMLM if under-allocated
    if tmf_diff > 0:
        if upro_diff < 0:
            tmf_shares_to_sell = min(tmf_diff, abs(upro_diff)) / float(api.get_latest_trade("TMF").price)
            upro_shares_to_buy = (tmf_shares_to_sell * float(api.get_latest_trade("TMF").price) / float(api.get_latest_trade("UPRO").price)) * fee_margin
            rebalance_actions.append(("TMF", tmf_shares_to_sell, 'sell'))
            rebalance_actions.append(("UPRO", upro_shares_to_buy, 'buy'))
        
        if kmlm_diff < 0:
            tmf_shares_to_sell = min(tmf_diff, abs(kmlm_diff)) / float(api.get_latest_trade("TMF").price)
            kmlm_shares_to_buy = (tmf_shares_to_sell * float(api.get_latest_trade("TMF").price) / float(api.get_latest_trade("KMLM").price)) * fee_margin
            rebalance_actions.append(("TMF", tmf_shares_to_sell, 'sell'))
            rebalance_actions.append(("KMLM", kmlm_shares_to_buy, 'buy'))

    # If KMLM is over-allocated, adjust UPRO or TMF if under-allocated
    if kmlm_diff > 0:
        if upro_diff < 0:
            kmlm_shares_to_sell = min(kmlm_diff, abs(upro_diff)) / float(api.get_latest_trade("KMLM").price)
            upro_shares_to_buy = (kmlm_shares_to_sell * float(api.get_latest_trade("KMLM").price) / float(api.get_latest_trade("UPRO").price)) * fee_margin
            rebalance_actions.append(("KMLM", kmlm_shares_to_sell, 'sell'))
            rebalance_actions.append(("UPRO", upro_shares_to_buy, 'buy'))
        
        if tmf_diff < 0:
            kmlm_shares_to_sell = min(kmlm_diff, abs(tmf_diff)) / float(api.get_latest_trade("KMLM").price)
            tmf_shares_to_buy = (kmlm_shares_to_sell * float(api.get_latest_trade("KMLM").price) / float(api.get_latest_trade("TMF").price)) * fee_margin
            rebalance_actions.append(("KMLM", kmlm_shares_to_sell, 'sell'))
            rebalance_actions.append(("TMF", tmf_shares_to_buy, 'buy'))

    # Execute rebalancing actions
    for symbol, qty, action in rebalance_actions:
        if qty > 0:
            order = api.submit_order(symbol=symbol, qty=qty, side=action, type='market', time_in_force='day')
            action_verb = "Bought" if action == 'buy' else "Sold"
            wait_for_order_fill(api, order.id)
            print(f"{action_verb} {qty:.6f} shares of {symbol} to rebalance.")
            send_telegram_message(f"{action_verb} {qty:.6f} shares of {symbol} to rebalance.")
    
    # Report completion of rebalancing check
    print("Rebalance check completed.")
    return "Rebalance executed."


# Function to calculate 200-SMA using yfinance
def calculate_200sma(symbol):
    data = yf.download(symbol, period="1y", interval="1d")  # Download 1 year of daily data
    sma_200 = data['Close'].rolling(window=200).mean().iloc[-1].item()
    return sma_200

# Function to get latest s&p price using yfinance
def get_latest_price(symbol):
    # Fetch the real-time data for SPY
    ticker = yf.Ticker(symbol)
    data = ticker.history(period="1d")

    # Get the current price
    price = data['Close'].iloc[-1]
    return price

def make_monthly_buy_spxl(api):
    
    sp_sma_200 = calculate_200sma("^GSPC")
    sp_latest_price = get_latest_price("^GSPC")

    investment_amount = spxl_investment_amount

    if sp_latest_price > sp_sma_200:
        spxl_price = api.get_latest_trade("SPXL").price
        shares_to_buy = investment_amount / spxl_price
        
        if shares_to_buy > 0:
            api.submit_order(
                symbol="SPXL",
                qty=shares_to_buy,
                side='buy',
                type='market',
                time_in_force='day'
            )
            send_telegram_message(f"Bought {shares_to_buy:.6f} shares of SPXL.")
            return f"Bought {shares_to_buy:.6f} shares of SPXL."
        else:
            send_telegram_message("Amount too small to buy SPXL shares.")
            print("Amount too small to buy SPXL shares.")
            return "Amount too small to buy SPXL shares."
    else:
        shv_price = api.get_latest_trade("SHV").price
        shv_shares_to_buy = investment_amount / shv_price
        
        if shv_shares_to_buy > 0:
            api.submit_order(
                symbol="SHV",
                qty=shv_shares_to_buy,
                side='buy',
                type='market',
                time_in_force='day'
            )
        send_telegram_message("S&P 500 is below 200-SMA. No SPXL shares bought. Bought {shares_to_buy:.6f} shares of SHV")
        print("S&P 500 is below 200-SMA. No SPXL shares bought. Bought {shares_to_buy:.6f} shares of SHV")
        return "S&P 500 is below 200-SMA. No SPXL shares bought."

# Function to sell SPXL and buy SHV if S&P 500 is significantly below its 200-SMA
def sell_spxl_if_below_200sma(api, margin=0.01):
    sp_sma_200 = calculate_200sma("^GSPC")
    sp_latest_price = get_latest_price("^GSPC")

    if sp_latest_price < sp_sma_200 * (1 - margin):
        positions = api.list_positions()
        spxl_position = next((p for p in positions if p.symbol == "SPXL"), None)
        
        if spxl_position:
            shares_to_sell = float(spxl_position.qty)
            # Sell all SPXL shares
            sell_order = api.submit_order(
                symbol="SPXL",
                qty=shares_to_sell,
                side='sell',
                type='market',
                time_in_force='day'
            )
            send_telegram_message(f"Sold all {shares_to_sell:.6f} shares of SPXL because S&P 500 is significantly below 200-SMA.")
            
            # Wait for the sell order to be filled
            wait_for_order_fill(api, sell_order.id)
            
            # Buy SHV with all available cash
            account = api.get_account()
            available_cash = float(account.cash)
            shv_price = api.get_latest_trade("SHV").price
            shv_shares_to_buy = available_cash / shv_price
            
            if shv_shares_to_buy > 0 and available_cash > 400:
                buy_order = api.submit_order(
                    symbol="SHV",
                    qty=shv_shares_to_buy,
                    side='buy',
                    type='market',
                    time_in_force='day'
                )
                send_telegram_message(f"Bought {shv_shares_to_buy:.6f} shares of SHV with available cash after selling SPXL.")
                return f"Sold {shares_to_sell:.6f} shares of SPXL and bought {shv_shares_to_buy:.6f} shares of SHV."
            else:
                send_telegram_message("Not enough cash to buy SHV shares.")
                return "Not enough cash to buy SHV shares."
        else:
            send_telegram_message("No SPXL position to sell.")
            return "No SPXL position to sell."
    else:
        send_telegram_message("S&P 500 is not significantly below 200-SMA. No SPXL shares sold.")
        return "S&P 500 is not significantly below 200-SMA. No SPXL shares sold."

# Function to buy SPXL with all available cash if S&P 500 is above its 200-SMA
def buy_spxl_if_above_200sma(api):
    sp_sma_200 = calculate_200sma("^GSPC")
    sp_latest_price = get_latest_price("^GSPC")
    
    positions = {p.symbol: float(p.qty) for p in api.list_positions()}
    shv_position = positions.get("SHV", 0)

    if sp_latest_price > sp_sma_200:
        account = api.get_account()
        available_cash = float(account.cash)        
        # If there's an SHV position, sell it first
        if shv_position > 0:
            sell_order = api.submit_order(
                symbol="SHV",
                qty=shv_position,
                side='sell',
                type='market',
                time_in_force='day'
            )
            send_telegram_message(f"Sold all {shv_position:.6f} shares of SHV to buy SPXL.")
            # Wait for the sell order to be filled
            wait_for_order_fill(api, sell_order.id)

            # Update the available cash after selling SHV
            account = api.get_account()
            available_cash = float(account.cash)
        
        spxl_price = api.get_latest_trade("SPXL").price
        shares_to_buy = available_cash / spxl_price
        
        if shares_to_buy > 0 and available_cash > 400: #make sure enough cash from actual 200SMA sells is available vs monthly budget
            buy_order = api.submit_order(
                symbol="SPXL",
                qty=shares_to_buy,
                side='buy',
                type='market',
                time_in_force='day'
            )
            send_telegram_message(f"Bought {shares_to_buy:.6f} shares of SPXL with available cash after selling SHV.")
            return f"Bought {shares_to_buy:.6f} shares of SPXL with available cash."
        else:
            spxl_value = positions.get("SPXL", 0)
            send_telegram_message(f"S&P 500 is above 200-SMA. No SPXL shares bought because of no cash but {spxl_value} is already invested")
            return f"S&P 500 is above 200-SMA. No SPXL shares bought because of no cash but {spxl_value} is already invested"
    else:
        shv_value = positions.get("SHV", 0)
        send_telegram_message(f"S&P 500 is below 200-SMA. No SPXL shares bought but {shv_value} is invested in BIL")
        return "S&P 500 is below 200-SMA. No SPXL shares bought."


def make_monthly_buy_tqqq(api):
    sp_sma_200 = calculate_200sma("^GSPC")
    sp_latest_price = get_latest_price("^GSPC")

    if sp_latest_price > sp_sma_200:
        tqqq_price = api.get_latest_trade("TQQQ").price
        shares_to_buy = tqqq_investment_amount / tqqq_price
        
        if shares_to_buy > 0:
            api.submit_order(
                symbol="TQQQ",
                qty=shares_to_buy,
                side='buy',
                type='market',
                time_in_force='day'
            )
            send_telegram_message(f"Bought {shares_to_buy:.6f} shares of TQQQ.")
            return f"Bought {shares_to_buy:.6f} shares of TQQQ."
        else:
            send_telegram_message("Amount too small to buy TQQQ shares.")
            return "Amount too small to buy TQQQ shares."
    else:
        bil_price = api.get_latest_trade("BIL").price
        bil_shares_to_buy = tqqq_investment_amount / bil_price

        if bil_shares_to_buy > 0:
            api.submit_order(
                symbol="BIL",
                qty=bil_shares_to_buy,
                side='buy',
                type='market',
                time_in_force='day'
            )
            send_telegram_message(f"S&P 500 is below 200-SMA. Bought {bil_shares_to_buy:.6f} shares of BIL instead of TQQQ.")
            return f"S&P 500 is below 200-SMA. Bought {bil_shares_to_buy:.6f} shares of BIL."
        else:
            send_telegram_message("Amount too small to buy BIL shares.")
            return "Amount too small to buy BIL shares."


def sell_tqqq_if_below_200sma(api, margin=0.01):
    sp_sma_200 = calculate_200sma("^GSPC")
    sp_latest_price = get_latest_price("^GSPC")

    if sp_latest_price < sp_sma_200 * (1 - margin):
        positions = api.list_positions()
        tqqq_position = next((p for p in positions if p.symbol == "TQQQ"), None)

        if tqqq_position:
            shares_to_sell = float(tqqq_position.qty)
            sell_order = api.submit_order(
                symbol="TQQQ",
                qty=shares_to_sell,
                side='sell',
                type='market',
                time_in_force='day'
            )
            send_telegram_message(f"Sold all {shares_to_sell:.6f} shares of TQQQ because S&P 500 is significantly below 200-SMA.")
            
            # Wait for the sell order to be filled
            investable_funds = wait_for_order_fill(api, sell_order.id)

            # Buy BIL with all available cash
            account = api.get_account()
            available_cash = float(account.cash)
            bil_price = api.get_latest_trade("BIL").price
            bil_shares_to_buy = available_cash / bil_price

            if bil_shares_to_buy > 0:
                buy_order = api.submit_order(
                    symbol="BIL",
                    qty=bil_shares_to_buy,
                    side='buy',
                    type='market',
                    time_in_force='day'
                )
                send_telegram_message(f"Bought {bil_shares_to_buy:.6f} shares of BIL with available cash after selling TQQQ.")
                return f"Sold {shares_to_sell:.6f} shares of TQQQ and bought {bil_shares_to_buy:.6f} shares of BIL."
            else:
                send_telegram_message("Not enough cash to buy BIL shares.")
                return "Not enough cash to buy BIL shares."
        else:
            send_telegram_message("No TQQQ position to sell.")
            return "No TQQQ position to sell."
    else:
        send_telegram_message("S&P 500 is not significantly below 200-SMA. No TQQQ shares sold.")
        return "S&P 500 is not significantly below 200-SMA. No TQQQ shares sold."


def buy_tqqq_if_above_200sma(api):
    sp_sma_200 = calculate_200sma("^GSPC")
    sp_latest_price = get_latest_price("^GSPC")

    positions = {p.symbol: float(p.qty) for p in api.list_positions()}
    bil_position = positions.get("BIL", 0)

    if sp_latest_price > sp_sma_200:
        account = api.get_account()
        available_cash = float(account.cash)
        tqqq_price = api.get_latest_trade("TQQQ").price

        # If there's a BIL position, sell it first
        if bil_position > 0:
            sell_order = api.submit_order(
                symbol="BIL",
                qty=bil_position,
                side='sell',
                type='market',
                time_in_force='day'
            )
            send_telegram_message(f"Sold all {bil_position:.6f} shares of BIL to buy TQQQ.")
            
            # Wait for the sell order to be filled
            investable_funds = wait_for_order_fill(api, sell_order.id)

            # Update the available cash after selling BIL
            account = api.get_account()
            available_cash = float(account.cash)

        shares_to_buy = available_cash / tqqq_price
        
        if shares_to_buy > 0 and available_cash > 400:  # Make sure enough cash is available from sales or budget
            buy_order = api.submit_order(
                symbol="TQQQ",
                qty=shares_to_buy,
                side='buy',
                type='market',
                time_in_force='day'
            )
            send_telegram_message(f"Bought {shares_to_buy:.6f} shares of TQQQ with available cash.")
            return f"Bought {shares_to_buy:.6f} shares of TQQQ with available cash."
        else:
            tqqq_value = positions.get("TQQQ", 0)
            send_telegram_message("Not enough cash to buy TQQQ shares.")
            send_telegram_message(f"S&P 500 is above 200-SMA. No TQQQ shares bought because of no cash but {tqqq_value} is already invested.")
            return f"S&P 500 is above 200-SMA. No TQQQ shares bought because of no cash but {tqqq_value} is already invested."
    else:
        bil_value = positions.get("BIL", 0)
        send_telegram_message(f"S&P 500 is below 200-SMA. No TQQQ shares bought but {bil_value} is invested in BIL")
        return "S&P 500 is below 200-SMA. No TQQQ shares bought."


# Function to send a message via Telegram
def send_telegram_message(message):
    telegram_key, chat_id = get_telegram_secrets()
    url = f"https://api.telegram.org/bot{telegram_key}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": message
    }
    response = requests.post(url, data=data)
    return response.status_code

# Function to get the chat title
def get_chat_title():
    telegram_key, chat_id = get_telegram_secrets()
    url = f"https://api.telegram.org/bot{telegram_key}/getChat?chat_id={chat_id}"
    response = requests.get(url)
    chat_info = response.json()
    
    if chat_info['ok']:
        return chat_info['result'].get('title', '')
    else:
        return None
    

def get_index_data(index_symbol):
    """Fetch the all-time high and current price for an index."""
    # Download historical data for the index
    data = yf.download(index_symbol, period='max')
    
    # Get the all-time high
    all_time_high = data['High'].max().item()

    # Get the current price (latest close price)
    current_price = data['Close'].iloc[-1].item()
    
    return current_price, all_time_high

def check_index_drop(request):
    """Cloud Function that checks if an index has dropped 30% below its all-time high."""
    
    # Handle case where Content-Type is not set to application/json (e.g., application/octet-stream)
    if request.content_type == 'application/json':
        request_json = request.get_json(silent=True)
    else:
        # If the Content-Type is octet-stream or undefined, attempt to decode the body manually
        try:
            request_json = json.loads(request.data.decode('utf-8'))
        except Exception:
            return jsonify({"error": "Failed to parse request body"}), 400

    # Check if the required parameters are present
    if request_json and 'index_symbol' in request_json and 'index_name' in request_json:
        index_symbol = request_json['index_symbol']
        index_name = request_json['index_name']
    else:
        return jsonify({"error": "Missing index_symbol or index_name in the request body"}), 400

    
    current_price, all_time_high = get_index_data(index_symbol)
    
    # Calculate the percentage drop
    drop_percentage = ((all_time_high - current_price) / all_time_high) * 100

    # Send alert if the index has dropped 30% or more
    if drop_percentage >= 30:
        message = f"Alert: {index_name} has dropped {drop_percentage:.2f}% from its ATH! Consider a loan with a duration of 6 to 8 years (50k to 100k) at around 4.5% interest max"
        send_telegram_message(message)
        return jsonify({"message": message}), 200
    else:
        #message = f"Alert: {index_name} is within safe range ({drop_percentage:.2f}% below ATH)."
        #send_telegram_message(message)
        return jsonify({"message": f"{index_name} is within safe range ({drop_percentage:.2f}% below ATH)."}), 200

# Helper function to wait for an order to be filled
def wait_for_order_fill(api, order_id, timeout=300, poll_interval=5):
    elapsed_time = 0
    while elapsed_time < timeout:
        order = api.get_order(order_id)
        if order.status == 'filled':
            print(f"Order {order_id} filled.")
            return float(order.filled_avg_price) * float(order.filled_qty)
        elif order.status == 'canceled':
            print(f"Order {order_id} was canceled.")
            send_telegram_message(f"Order {order_id} was canceled.")
            return
        else:
            print(f"Waiting for order {order_id} to fill... (status: {order.status})")
            time.sleep(poll_interval)
            elapsed_time += poll_interval
    print(f"Timeout: Order {order_id} did not fill within {timeout} seconds.")
    send_telegram_message(f"Timeout: Order {order_id} did not fill within {timeout} seconds.")

@app.route('/monthly_buy_hfea', methods=['POST'])
def monthly_buy_hfea(request):
    api = set_alpaca_environment(env=alpaca_environment)  # or 'paper' based on your needs
    return make_monthly_buys(api)

@app.route('/rebalance_hfea', methods=['POST'])
def rebalance_hfea(request):
    api = set_alpaca_environment(env=alpaca_environment)  # or 'paper' based on your needs
    return rebalance_portfolio(api)

@app.route('/monthly_buy_spxl', methods=['POST'])
def monthly_buy_spxl(request):
    api = set_alpaca_environment(env=alpaca_environment)  # or 'paper' based on your needs
    return make_monthly_buy_spxl(api)

@app.route('/sell_spxl_below_200sma', methods=['POST'])
def sell_spxl_below_200sma(request):
    api = set_alpaca_environment(env=alpaca_environment)  # or 'paper' based on your needs
    return sell_spxl_if_below_200sma(api)

@app.route('/buy_spxl_above_200sma', methods=['POST'])
def buy_spxl_above_200sma(request):
    api = set_alpaca_environment(env=alpaca_environment)  # or 'paper' based on your needs
    return buy_spxl_if_above_200sma(api)

@app.route('/index_alert', methods=['POST'])
def index_alert(request):
    return check_index_drop(request)

@app.route('/monthly_buy_tqqq', methods=['POST'])
def monthly_buy_tqqq(request):
    api = set_alpaca_environment(env=alpaca_environment)  # or 'paper' based on your needs
    return make_monthly_buy_tqqq(api)

@app.route('/sell_tqqq_below_200sma', methods=['POST'])
def sell_tqqq_below_200sma(request):
    api = set_alpaca_environment(env=alpaca_environment)  # or 'paper' based on your needs
    return sell_tqqq_if_below_200sma(api)

@app.route('/buy_tqqq_above_200sma', methods=['POST'])
def buy_tqqq_above_200sma(request):
    api = set_alpaca_environment(env=alpaca_environment)  # or 'paper' based on your needs
    return buy_tqqq_if_above_200sma(api)


def run_local(action, env='paper', request='test'):
    api = set_alpaca_environment(env=env, use_secret_manager=False)
    if action == 'monthly_buy_hfea':
        return make_monthly_buys(api)
    elif action == 'rebalance_hfea':
        return rebalance_portfolio(api)
    elif action == 'monthly_buy_spxl':
        return make_monthly_buy_spxl(api)
    elif action == 'sell_spxl_below_200sma':
        return sell_spxl_if_below_200sma(api)
    elif action == 'buy_spxl_above_200sma':
        return buy_spxl_if_above_200sma(api)
    elif action == 'monthly_buy_tqqq':
        return make_monthly_buy_tqqq(api)
    elif action == 'sell_tqqq_below_200sma':
        return sell_tqqq_if_below_200sma(api)
    elif action == 'buy_tqqq_above_200sma':
        return buy_tqqq_if_above_200sma(api)
    elif action == 'index_alert':
        return check_index_drop(request)
    else:
        return "No valid action provided. Use 'buy' or 'rebalance'."



if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser()
    parser.add_argument('--action', choices=['monthly_buy_hfea', 'rebalance_hfea', 'monthly_buy_spxl','sell_spxl_below_200sma','buy_spxl_above_200sma','index_alert', 'sell_tqqq_below_200sma', 'buy_tqqq_above_200sma', 'monthly_buy_tqqq'], required=True, help="Action to perform: 'monthly_buy_hfea', 'rebalance_hfea', 'monthly_buy_spxl','sell_spxl_below_200sma','buy_spxl_above_200sma','sell_tqqq_below_200sma', 'buy_tqqq_above_200sma', 'monthly_buy_tqqq','index_alert'")
    parser.add_argument('--env', choices=['live', 'paper'], default='paper', help="Alpaca environment: 'live' or 'paper'")
    parser.add_argument('--use_secret_manager', action='store_true', help="Use Google Secret Manager for API keys")
    args = parser.parse_args()

    # Run the function locally
    result = run_local(action=args.action, env=args.env)

#local execution:
    #python3 main.py --action monthly_buy_hfea --env paper
    #python3 main.py --action rebalance_hfea --env paper
    #python3 main.py --action monthly_buy_spxl --env paper
    #python3 main.py --action sell_spxl_below_200sma --env paper
    #python3 main.py --action buy_spxl_above_200sma --env paper
    #python3 main.py --action monthly_buy_tqqq --env paper
    #python3 main.py --action sell_tqqq_below_200sma --env paper
    #python3 main.py --action buy_tqqq_above_200sma --env paper


#consider shifting to short term bonds when 200sma is below https://app.alpaca.markets/trade/BIL?asset_class=stocks