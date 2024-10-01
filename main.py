import os
from flask import Flask, jsonify
from google.cloud import secretmanager
from dotenv import load_dotenv
import alpaca_trade_api as tradeapi
import yfinance as yf
import requests

app = Flask(__name__)

hfea_investment_amount = 33
spxl_investment_amount = 75

upro_allocation = 0.55
tmf_allocation = 0.45

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
    upro_diff, tmf_diff, upro_value, tmf_value, total_value, target_upro_value, target_tmf_value = get_hfea_allocations(api)

    # Calculate how much each ETF is underweight (use the diffs returned by get_hfea_allocations)
    upro_underweight = max(0, target_upro_value - upro_value)
    tmf_underweight = max(0, target_tmf_value - tmf_value)
    # Calculate the total underweight between UPRO and TMF
    total_underweight = upro_underweight + tmf_underweight

    # If the portfolio is perfectly balanced, buy using the standard 55/45 split
    if total_underweight == 0:
        upro_amount = investment_amount * upro_allocation
        tmf_amount = investment_amount * tmf_allocation
    else:
        # Allocate proportionally based on underweight amounts
        upro_amount = (upro_underweight / total_underweight) * investment_amount
        tmf_amount = (tmf_underweight / total_underweight) * investment_amount
    # Get current prices for UPRO and TMF
    upro_price = float(api.get_latest_trade("UPRO").price)
    tmf_price = float(api.get_latest_trade("TMF").price)
    

    # Calculate number of shares to buy, allowing for fractional shares
    upro_shares_to_buy = upro_amount / upro_price
    tmf_shares_to_buy = tmf_amount / tmf_price

    # Execute market orders
    if upro_shares_to_buy > 0:
        api.submit_order(
            symbol="UPRO",
            qty=upro_shares_to_buy,
            side='buy',
            type='market',
            time_in_force='day'
        )
        print(f"Bought {upro_shares_to_buy:.6f} shares of UPRO.")
        send_telegram_message(f"Bought {upro_shares_to_buy:.6f} shares of UPRO.")
    else:
        print("No UPRO shares bought due to small amount.")
        send_telegram_message("No UPRO shares bought due to small amount.")

    if tmf_shares_to_buy > 0:
        api.submit_order(
            symbol="TMF",
            qty=tmf_shares_to_buy,
            side='buy',
            type='market',
            time_in_force='day'
        )
        print(f"Bought {tmf_shares_to_buy:.6f} shares of TMF.")
        send_telegram_message(f"Bought {tmf_shares_to_buy:.6f} shares of TMF.")
    else:
        print("No TMF shares bought due to small amount.")
        send_telegram_message("No TMF shares bought due to small amount.")

    return "Monthly investment executed."

def get_hfea_allocations(api):
    # Get current portfolio positions
    positions = {p.symbol: float(p.market_value) for p in api.list_positions()}
    
    # Calculate total portfolio value from UPRO and TMF only
    upro_value = positions.get("UPRO", 0)
    tmf_value = positions.get("TMF", 0)
    total_value = upro_value + tmf_value

    # Target values based on 55% UPRO and 45% TMF
    target_upro_value = total_value * upro_allocation
    target_tmf_value = total_value * tmf_allocation
    
    # Determine the current deviation from the target
    upro_diff = upro_value - target_upro_value
    tmf_diff = tmf_value - target_tmf_value
    
    return upro_diff, tmf_diff, upro_value, tmf_value, total_value, target_upro_value, target_tmf_value

def rebalance_portfolio(api):

    #Get Upro and Tmf values and their deviation from perfect allocation
    upro_diff, tmf_diff, upro_value, tmf_value, total_value, target_upro_value, target_tmf_value = get_hfea_allocations(api)
    
    # Apply a margin for fees (e.g., 0.005%)
    fee_margin = 0.99

    # If the total value is 0, nothing to rebalance
    if total_value == 0:
        print("No holdings to rebalance.")
        send_telegram_message("No holdings to rebalance for HFEA Strategy.")
        return "No holdings to rebalance for HFEA Strategy."
    
    # If UPRO is over-allocated and TMF is under-allocated, sell UPRO to buy TMF
    if upro_diff > 0 and tmf_diff < 0:
        # Determine how much UPRO to sell to buy TMF and bring it to the target
        upro_shares_to_sell = (min(upro_diff, abs(tmf_diff)) / float(api.get_latest_trade("UPRO").price)) 
        tmf_shares_to_buy = upro_shares_to_sell * float(api.get_latest_trade("UPRO").price) / float(api.get_latest_trade("TMF").price) * fee_margin
        
        if upro_shares_to_sell > 0:
            api.submit_order(
                symbol="UPRO",
                qty=upro_shares_to_sell,
                side='sell',
                type='market',
                time_in_force='day'
            )
            print(f"Sold {upro_shares_to_sell:.6f} shares of UPRO to rebalance.")
            send_telegram_message(f"Sold {upro_shares_to_sell:.6f} shares of UPRO to rebalance.")


        if tmf_shares_to_buy > 0:
            api.submit_order(
                symbol="TMF",
                qty=tmf_shares_to_buy,
                side='buy',
                type='market',
                time_in_force='day'
            )
            print(f"Bought {tmf_shares_to_buy:.6f} shares of TMF to rebalance.")
            send_telegram_message(f"Bought {tmf_shares_to_buy:.6f} shares of TMF to rebalance.")


    # If TMF is over-allocated and UPRO is under-allocated, sell TMF to buy UPRO
    elif tmf_diff > 0 and upro_value < target_upro_value:
        # Determine how much TMF to sell to buy UPRO and bring it to the target
        tmf_shares_to_sell = (min(tmf_diff, abs(upro_diff)) / float(api.get_latest_trade("TMF").price)) 
        upro_shares_to_buy = tmf_shares_to_sell * float(api.get_latest_trade("TMF").price) / float(api.get_latest_trade("UPRO").price) * fee_margin
        
        if tmf_shares_to_sell > 0:
            api.submit_order(
                symbol="TMF",
                qty=tmf_shares_to_sell,
                side='sell',
                type='market',
                time_in_force='day'
            )
            print(f"Sold {tmf_shares_to_sell:.6f} shares of TMF to rebalance.")
            send_telegram_message(f"Sold {tmf_shares_to_sell:.6f} shares of TMF to rebalance.")


        if upro_shares_to_buy > 0:
            api.submit_order(
                symbol="UPRO",
                qty=upro_shares_to_buy,
                side='buy',
                type='market',
                time_in_force='day'
            )
            print(f"Bought {upro_shares_to_buy:.6f} shares of UPRO to rebalance.")
            send_telegram_message(f"Bought {upro_shares_to_buy:.6f} shares of UPRO to rebalance.")


    else:
        print(f"No rebalancing performed. Portfolio is already balanced or no significant deviation. ")
        send_telegram_message(f"No rebalancing performed. Portfolio is already balanced or no significant deviation. Only {upro_shares_to_buy:.6f} shares of UPRO would have been bought. Only {tmf_shares_to_sell:.6f} shares of TMF would have been bought")


    print("Rebalance check completed.")
    return "Rebalance executed."

# Function to calculate 200-SMA using yfinance
def calculate_200sma(symbol):
    data = yf.download(symbol, period="1y", interval="1d")  # Download 1 year of daily data
    sma_200 = data['Close'].rolling(window=200).mean().iloc[-1]
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
        send_telegram_message("S&P 500 is below 200-SMA. No SPXL shares bought.")
        print("S&P 500 is below 200-SMA. No SPXL shares bought.")
        return "S&P 500 is below 200-SMA. No SPXL shares bought."

# Function to sell SPXL if S&P 500 is significantly below its 200-SMA
def sell_spxl_if_below_200sma(api, margin=0.01):
    sp_sma_200 = calculate_200sma("^GSPC")
    sp_latest_price = get_latest_price("^GSPC")

    if sp_latest_price < sp_sma_200 * (1 - margin):
        positions = api.list_positions()
        spxl_position = next((p for p in positions if p.symbol == "SPXL"), None)
        
        if spxl_position:
            shares_to_sell = float(spxl_position.qty)
            api.submit_order(
                symbol="SPXL",
                qty=shares_to_sell,
                side='sell',
                type='market',
                time_in_force='day'
            )
            send_telegram_message(f"Sold all {shares_to_sell} shares of SPXL because S&P 500 is significantly below 200-SMA.")
            return f"Sold all {shares_to_sell} shares of SPXL because S&P 500 is significantly below 200-SMA."
        else:
            send_telegram_message("No SPXL position to sell.")
            return "No SPXL position to sell."
    else:
        send_telegram_message("S&P 500 is not significantly below 200-SMA. No SPXL shares sold.")
        return "S&P 500 is not significantly below 200-SMA. No SPXL shares sold."

# Function to buy SPXL with all available cash if S&P 500 is above its 200-SMA
def buy_spxl_if_above_200sma(api):
    sp_sma_200 = calculate_200sma("^GSPC")
    ticker = yf.Ticker("^GSPC")
    data = ticker.history(period="1d")
    sp_latest_price = data['Close'].iloc[-1]
    fee_margin = 0.995

    if sp_latest_price > sp_sma_200:
        account = api.get_account()
        available_cash = float(account.cash)
        spxl_price = api.get_latest_trade("SPXL").price
        shares_to_buy = available_cash / spxl_price * fee_margin 
        
        if shares_to_buy > 0 and available_cash > 10:
            api.submit_order(
                symbol="SPXL",
                qty=shares_to_buy,
                side='buy',
                type='market',
                time_in_force='day'
            )
            send_telegram_message(f"Bought {shares_to_buy:.6f} shares of SPXL with available cash.")
            return f"Bought {shares_to_buy:.6f} shares of SPXL with available cash."
        else:
            send_telegram_message("Not enough cash to buy SPXL shares.")
            return "Not enough cash to buy SPXL shares."
    else:
        send_telegram_message("S&P 500 is below 200-SMA. No SPXL shares bought.")
        return "S&P 500 is below 200-SMA. No SPXL shares bought."
    

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
    all_time_high = data['High'].max()

    # Get the current price (latest close price)
    current_price = data['Close'].iloc[-1]
    
    return current_price, all_time_high

def check_index_drop(request):
    """Cloud Function that checks if an index has dropped 35% below its all-time high."""
    
    # Get the data from the request body (JSON format)
    request_json = request.get_json(silent=True)
    if request_json and 'index_symbol' in request_json and 'index_name' in request_json:
        index_symbol = request_json['index_symbol']
        index_name = request_json['index_name']
    else:
        return jsonify({"error": "Missing index_symbol or index_name in the request body"}), 400


    current_price, all_time_high = get_index_data(index_symbol)
    
    # Calculate the percentage drop
    drop_percentage = ((all_time_high - current_price) / all_time_high) * 100

    # Send alert if the index has dropped 35% or more
    if drop_percentage >= 35:
        message = f"Alert: {index_name} has dropped {drop_percentage:.2f}% from its all-time high!"
        send_telegram_message(message)
        return jsonify({"message": message}), 200
    else:
        return jsonify({"message": f"{index_name} is within safe range ({drop_percentage:.2f}% below ATH)."}), 200


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

def run_local(action, env='paper'):
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
    elif action == 'index_alert':
        return check_index_drop(request)
    else:
        return "No valid action provided. Use 'buy' or 'rebalance'."

if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser()
    parser.add_argument('--action', choices=['monthly_buy_hfea', 'rebalance_hfea', 'monthly_buy_spxl','sell_spxl_below_200sma','buy_spxl_above_200sma'], required=True, help="Action to perform: 'monthly_buy_hfea', 'rebalance_hfea', 'monthly_buy_spxl','sell_spxl_below_200sma','buy_spxl_above_200sma'")
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

#consider shifting to short term bonds when 200sma is below https://app.alpaca.markets/trade/BIL?asset_class=stocks