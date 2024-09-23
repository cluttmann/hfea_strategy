import os
from flask import escape, request
from google.cloud import secretmanager
from dotenv import load_dotenv
import alpaca_trade_api as tradeapi

import os
from flask import Flask, request
from google.cloud import secretmanager
from dotenv import load_dotenv
import alpaca_trade_api as tradeapi

app = Flask(__name__)

# Function to get secrets from Google Secret Manager
def get_secret(secret_name):
    if os.getenv('GAE_ENV', '').startswith('standard'):
        # We're on Google Cloud
        client = secretmanager.SecretManagerServiceClient()
        project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
        name = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("UTF-8")
    else:
        # Running locally, use .env file
        load_dotenv()
        return os.getenv(secret_name)

# Function to dynamically set environment (live or paper)
def set_alpaca_environment(env='paper', use_secret_manager=True):
    if use_secret_manager and os.getenv('GAE_ENV', '').startswith('standard'):
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


# Trading symbols
UPRO = "UPRO"
TMF = "TMF"

def make_monthly_buys(api):

    # Get account information
    account = api.get_account()
    available_cash = float(account.cash)

    # Only invest 30% of available cash
    investment_amount = available_cash * 0.30

    # Determine how much to invest in each ETF
    upro_amount = investment_amount * 0.55
    tmf_amount = investment_amount * 0.45

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
    else:
        print("No UPRO shares bought due to small amount.")

    if tmf_shares_to_buy > 0:
        api.submit_order(
            symbol="TMF",
            qty=tmf_shares_to_buy,
            side='buy',
            type='market',
            time_in_force='day'
        )
        print(f"Bought {tmf_shares_to_buy:.6f} shares of TMF.")
    else:
        print("No TMF shares bought due to small amount.")

    print("Monthly investment executed.")
    return "Monthly investment executed."


def rebalance_portfolio(api):

    # Get current portfolio positions
    positions = {p.symbol: float(p.market_value) for p in api.list_positions()}
    
    # Calculate total portfolio value from UPRO and TMF only
    upro_value = positions.get("UPRO", 0)
    tmf_value = positions.get("TMF", 0)
    total_value = upro_value + tmf_value

    # If the total value is 0, nothing to rebalance
    if total_value == 0:
        print("No holdings to rebalance.")
        return "No holdings to rebalance."

    # Target values based on 55% UPRO and 45% TMF
    target_upro_value = total_value * 0.55
    target_tmf_value = total_value * 0.45
    
    # Determine the current deviation from the target
    upro_diff = upro_value - target_upro_value
    tmf_diff = tmf_value - target_tmf_value

    # Apply a margin for fees (e.g., 0.005%)
    fee_margin = 0.995

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

        if tmf_shares_to_buy > 0:
            api.submit_order(
                symbol="TMF",
                qty=tmf_shares_to_buy,
                side='buy',
                type='market',
                time_in_force='day'
            )
            print(f"Bought {tmf_shares_to_buy:.6f} shares of TMF to rebalance.")

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

        if upro_shares_to_buy > 0:
            api.submit_order(
                symbol="UPRO",
                qty=upro_shares_to_buy,
                side='buy',
                type='market',
                time_in_force='day'
            )
            print(f"Bought {upro_shares_to_buy:.6f} shares of UPRO to rebalance.")

    else:
        print("No rebalancing performed. Portfolio is already balanced or no significant deviation.")

    print("Rebalance check completed.")
    return "Rebalance executed."




def execute_hfea_strategy(request):
    # Get the action and environment parameters from the request
    request_json = request.get_json(silent=True)
    request_args = request.args

    action = None
    env = 'paper'  # Default environment is paper
    use_secret_manager = True  # Default to Google Cloud Secret Manager

    if request_json:
        action = request_json.get('action')
        env = request_json.get('env', 'paper')
        use_secret_manager = request_json.get('use_secret_manager', True)
    elif request_args:
        action = request_args.get('action')
        env = request_args.get('env', 'paper')
        use_secret_manager = request_args.get('use_secret_manager', 'true').lower() == 'true'

    # Set Alpaca environment (live or paper) and choose secret manager usage
    api = set_alpaca_environment(env, use_secret_manager)

    # Execute the requested action
    if action == 'buy':
        return make_monthly_buys(api)
    elif action == 'rebalance':
        return rebalance_portfolio(api)
    else:
        return "No valid action provided. Use 'buy' or 'rebalance'.", 400

@app.route('/monthly_buy', methods=['POST'])
def monthly_buy():
    api = set_alpaca_environment(env='live')  # or 'paper' based on your needs
    return make_monthly_buys(api)

@app.route('/rebalance', methods=['POST'])
def rebalance():
    api = set_alpaca_environment(env='live')  # or 'paper' based on your needs
    return rebalance_portfolio(api)

def run_local(action, env='paper'):
    api = set_alpaca_environment(env=env, use_secret_manager=False)
    if action == 'buy':
        return make_monthly_buys(api)
    elif action == 'rebalance':
        return rebalance_portfolio(api)
    else:
        return "No valid action provided. Use 'buy' or 'rebalance'."

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--action', choices=['buy', 'rebalance'], required=True, help="Action to perform: 'buy' or 'rebalance'")
    parser.add_argument('--env', choices=['live', 'paper'], default='paper', help="Alpaca environment: 'live' or 'paper'")
    parser.add_argument('--use_secret_manager', action='store_true', help="Use Google Secret Manager for API keys")
    args = parser.parse_args()

    # Run the function locally
    result = run_local(action=args.action, env=args.env)
    print(result)


#python3 main.py --action buy --env paper
#python3 main.py --action rebalance --env paper
