import os
from flask import Flask
from google.cloud import secretmanager
from dotenv import load_dotenv
import alpaca_trade_api as tradeapi
import yfinance as yf

app = Flask(__name__)

hfea_investment_amount = 30
spxl_investment_amount = 70

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


def make_monthly_buys(api):

    investment_amount = hfea_investment_amount

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

# Function to calculate 200-SMA using yfinance
def calculate_200sma(symbol):
    data = yf.download(symbol, period="1y", interval="1d")  # Download 1 year of daily data
    sma_200 = data['Close'].rolling(window=200).mean().iloc[-1]
    return sma_200

# Function to get latest s&p price using yfinance
def get_latest_price(symbol):
    # Fetch the real-time data for SPY
    ticker = yf.Ticker(symbol)
    data = ticker.history(period="1m")

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
            return f"Bought {shares_to_buy:.6f} shares of SPXL."
        else:
            return "Amount too small to buy SPXL shares."
    else:
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
            return f"Sold all {shares_to_sell} shares of SPXL because S&P 500 is significantly below 200-SMA."
        else:
            return "No SPXL position to sell."
    else:
        return "S&P 500 is not significantly below 200-SMA. No SPXL shares sold."

# Function to buy SPXL with all available cash if S&P 500 is above its 200-SMA
def buy_spxl_if_above_200sma(api):
    sp_sma_200 = calculate_200sma("^GSPC")
    ticker = yf.Ticker("^GSPC")
    data = ticker.history(period="1m")
    sp_latest_price = data['Close'].iloc[-1]
    fee_margin = 0.995

    if sp_latest_price > sp_sma_200:
        account = api.get_account()
        available_cash = float(account.cash)
        spxl_price = api.get_latest_trade("SPXL").price
        shares_to_buy = available_cash / spxl_price * fee_margin 
        
        if shares_to_buy > 0:
            api.submit_order(
                symbol="SPXL",
                qty=shares_to_buy,
                side='buy',
                type='market',
                time_in_force='day'
            )
            return f"Bought {shares_to_buy:.6f} shares of SPXL with available cash."
        else:
            return "Not enough cash to buy SPXL shares."
    else:
        return "S&P 500 is below 200-SMA. No SPXL shares bought."

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
    # print(result)

    # import os
    # port = int(os.environ.get('PORT', 8080))  # Get the port from the environment or default to 8080
    # app.run(host='0.0.0.0', port=port)

#python3 main.py --action buy --env paper
#python3 main.py --action rebalance --env paper



#python3 main.py --action monthly_buy_hfea --env paper
#python3 main.py --action rebalance_hfea --env paper
#python3 main.py --action monthly_buy_spxl --env paper
#python3 main.py --action sell_spxl_below_200sma --env paper
#python3 main.py --action buy_spxl_above_200sma --env paper


#to dos
#check if market is open if not no action
#adjust for daylight savings vs standard time