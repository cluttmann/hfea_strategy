import os
from flask import Flask, jsonify
from google.cloud import secretmanager
from dotenv import load_dotenv
import requests
import json
import time
import pandas as pd
import pandas_market_calendars as mcal
import datetime
from google.cloud import firestore


app = Flask(__name__)

# Strategy allocation percentages for dynamic monthly investment calculation
# Investment amounts are calculated dynamically each month based on available cash and margin
strategy_allocations = {
    "hfea_allo": 0.475,      # 47.5% to HFEA
    "spxl_allo": 0.475,      # 47.5% to SPXL SMA
    "nine_sig_allo": 0.05,   # 5% to 9-Sig strategy
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

# Margin control configuration for automated leverage management
# Enables up to +10% leverage only when market conditions are favorable
margin_control_config = {
    "target_margin_pct": 0.10,      # Maximum +10% leverage allowed
    "max_margin_rate": 0.08,        # 8% rate threshold (FRED + spread must be ≤ this)
    "min_buffer_pct": 0.05,         # 5% minimum buffer required
    "max_leverage": 1.14,           # Maximum 1.14x leverage allowed
    "spread_below_35k": 0.025,      # +2.5% spread for accounts <$35k
    "spread_above_35k": 0.01,       # +1.0% spread for accounts ≥$35k
    "portfolio_threshold": 35000,   # Threshold for spread calculation (in dollars)
    "min_investment": 1.00,         # Minimum investment amount (Alpaca requirement)
}

# Firestore client - initialized lazily to respect .env file
_db_client = None

def get_firestore_client():
    """
    Get or initialize Firestore client with correct project ID.
    Lazy loading ensures .env file is loaded first in local development.
    """
    global _db_client
    if _db_client is None:
        # Ensure .env is loaded for local development
        if not is_running_in_cloud():
            load_dotenv()
        
        project_id = os.getenv("GOOGLE_CLOUD_PROJECT_ID")
        if not project_id:
            # Fallback to GOOGLE_CLOUD_PROJECT (used in cloud environments)
            project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
        
        _db_client = firestore.Client(project=project_id)
    
    return _db_client


# Market data cache settings - Firestore-based for cross-function sharing
CACHE_DURATION_MINUTES = 5  # Cache freshness window


def get_cached_market_data(symbol, data_type):
    """
    Get cached market data from Firestore to avoid redundant Alpaca API calls.
    Cache expires after 5 minutes. Works across all Cloud Functions.
    
    Args:
        symbol: Market symbol (e.g., "SPY", "URTH", "EEM", "EFA")
        data_type: "price", "sma200", "sma255", or state fields
    
    Returns:
        Cached value or None if not cached/expired/unavailable
    """
    try:
        # Normalize symbol for Firestore document ID (remove special chars)
        doc_id = symbol.replace("^", "").replace(".", "_")
        
        doc_ref = get_firestore_client().collection("market-data").document(doc_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            return None
        
        data = doc.to_dict()
        
        # Check if cache is still fresh
        timestamp = data.get("timestamp")
        if timestamp:
            # Convert both to naive UTC for comparison (handles timezone-aware Firestore timestamps)
            if hasattr(timestamp, 'tzinfo') and timestamp.tzinfo is not None:
                timestamp = timestamp.replace(tzinfo=None)
            
            now_utc = datetime.datetime.utcnow()
            age_seconds = (now_utc - timestamp).total_seconds()
            
            if age_seconds > (CACHE_DURATION_MINUTES * 60):
                return None  # Expired
        
        # Return the requested data type
        return data.get(data_type)
        
    except Exception as e:
        print(f"Warning: Could not read market data cache for {symbol}.{data_type}: {e}")
        return None


def get_all_market_data(symbol):
    """
    Get ALL market data for a symbol efficiently.
    Use this when you need multiple metrics (price, sma200, sma255, states).
    If cache is stale, fetches fresh and calculates all metrics at once.
    
    Args:
        symbol: Stock symbol (e.g., "SPY", "URTH")
    
    Returns:
        dict with all market data: price, sma200, sma255, sma200_state, sma255_state, timestamp
        Or None if cache is stale (triggers update)
    
    Example:
        data = get_all_market_data("SPY")
        if data is None:
            data = update_market_data("SPY")
        spy_price = data["price"]
        spy_sma = data["sma200"]
    """
    try:
        # Normalize symbol for Firestore document ID
        doc_id = symbol.replace("^", "").replace(".", "_")
        
        doc_ref = get_firestore_client().collection("market-data").document(doc_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            return None
        
        data = doc.to_dict()
        
        # Check if cache is still fresh
        timestamp = data.get("timestamp")
        if timestamp:
            # Convert both to naive UTC for comparison (handles timezone-aware Firestore timestamps)
            if hasattr(timestamp, 'tzinfo') and timestamp.tzinfo is not None:
                timestamp = timestamp.replace(tzinfo=None)
            
            now_utc = datetime.datetime.utcnow()
            age_seconds = (now_utc - timestamp).total_seconds()
            
            if age_seconds > (CACHE_DURATION_MINUTES * 60):
                return None  # Expired - caller should update
        
        return data
        
    except Exception as e:
        print(f"Warning: Could not read market data for {symbol}: {e}")
        return None


def set_cached_market_data(symbol, data_type, value):
    """
    Cache market data to Firestore to avoid redundant Alpaca API calls.
    Accessible across all Cloud Functions. Automatically expires after 5 minutes.
    
    Args:
        symbol: Market symbol
        data_type: "price", "sma200", or "sma255"
        value: Data value to cache
    """
    try:
        # Normalize symbol for Firestore document ID (remove special chars)
        doc_id = symbol.replace("^", "").replace(".", "_")
        
        doc_ref = get_firestore_client().collection("market-data").document(doc_id)
        
        # Get existing data or create new
        doc = doc_ref.get()
        if doc.exists:
            data = doc.to_dict()
        else:
            data = {"symbol": symbol}  # Store original symbol for reference
        
        # Update the specific data type and timestamp
        data[data_type] = value
        data["timestamp"] = datetime.datetime.utcnow()
        
        doc_ref.set(data)
        
    except Exception as e:
        print(f"Warning: Could not cache market data for {symbol}.{data_type}: {e}")


def get_auth_headers(api):
    return {
        "APCA-API-KEY-ID": api["API_KEY"],
        "APCA-API-SECRET-KEY": api["SECRET_KEY"],
    }


def get_alpaca_historical_bars(api, symbol, days=400):
    """
    Fetch historical daily bars from Alpaca using IEX feed.
    Primary data source for all SMA calculations (no rate limiting).
    
    Args:
        api: Alpaca API credentials dict
        symbol: Stock symbol (e.g., "SPY", "URTH")
        days: Number of calendar days of history to fetch (default 400 for 200-day SMA)
    
    Returns:
        List of closing prices (most recent last), or None on error
    """
    try:
        from datetime import datetime, timedelta
        
        market_data_base_url = "https://data.alpaca.markets"
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        url = f"{market_data_base_url}/v2/stocks/{symbol}/bars"
        params = {
            "start": start_date.strftime("%Y-%m-%d"),
            "end": end_date.strftime("%Y-%m-%d"),
            "timeframe": "1Day",
            "limit": 10000,
            "adjustment": "split",
            "feed": "iex"  # Use IEX feed (included with Basic subscription)
        }
        
        response = requests.get(url, headers=get_auth_headers(api), params=params)
        response.raise_for_status()
        
        data = response.json()
        bars = data.get("bars", [])
        
        if not bars:
            print(f"No Alpaca bars returned for {symbol}")
            return None
        
        # Extract closing prices
        closes = [bar['c'] for bar in bars]
        print(f"Fetched {len(closes)} bars for {symbol} from Alpaca IEX feed")
        return closes
        
    except Exception as e:
        print(f"Alpaca historical fetch failed for {symbol}: {e}")
        return None


def get_latest_trade(api, symbol):
    """
    Get latest trade price from Alpaca.
    No fallback - raises error if Alpaca data unavailable.
    
    Args:
        api: Alpaca API credentials dict
        symbol: Stock symbol
    
    Returns:
        Latest trade price
    """
    symbol = symbol.upper()
    market_data_base_url = "https://data.alpaca.markets"
    url = f"{market_data_base_url}/v2/stocks/{symbol}/trades/latest"
    
    response = requests.get(url, headers=get_auth_headers(api))
    response.raise_for_status()
    return response.json()["trade"]["p"]

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
    
    # Enhanced error handling to show Alpaca's actual error message
    if not response.ok:
        try:
            error_detail = response.json()
            print(f"Alpaca order error for {symbol}: {error_detail}")
        except Exception:
            print(f"Alpaca order error for {symbol}: {response.text}")
    
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


def get_fred_rate():
    """
    Fetch the current Federal Funds Target Rate (Upper Limit) from FRED API.
    
    Returns:
        float: Current FRED rate as a decimal (e.g., 0.0525 for 5.25%), or None on error
    """
    try:
        # Get FRED API key from Secret Manager or env
        if is_running_in_cloud():
            fred_key = get_secret("FREDKEY")
        else:
            load_dotenv()
            fred_key = os.getenv("FREDKEY")
        
        if not fred_key:
            print("FRED API key not found")
            return None
        
        # Fetch DFEDTARU (Federal Funds Target Rate - Upper Limit)
        url = f"https://api.stlouisfed.org/fred/series/observations?series_id=DFEDTARU&api_key={fred_key}&file_type=json&sort_order=desc&limit=1"
        
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        
        if "observations" in data and len(data["observations"]) > 0:
            # Get the most recent observation value
            rate_value = data["observations"][0]["value"]
            
            # Handle '.' (missing data) or other non-numeric values
            if rate_value == "." or rate_value is None:
                print("FRED API returned missing data")
                return None
            
            # Convert to float and return as decimal (FRED returns percentage, e.g., 5.25)
            return float(rate_value) / 100.0
        else:
            print("No FRED data available")
            return None
            
    except Exception as e:
        print(f"Error fetching FRED rate: {e}")
        return None


def get_account_info(api):
    """
    Fetch full account information from Alpaca including equity, portfolio value, and margin data.
    
    Args:
        api: Alpaca API credentials dict
    
    Returns:
        dict: Account information with keys: equity, portfolio_value, maintenance_margin, cash
              Returns None on error
    """
    try:
        url = f"{api['BASE_URL']}/v2/account"
        response = requests.get(url, headers=get_auth_headers(api))
        response.raise_for_status()
        
        account_data = response.json()
        
        # Extract relevant fields for margin calculations
        return {
            "equity": float(account_data.get("equity", 0)),
            "portfolio_value": float(account_data.get("portfolio_value", 0)),
            "maintenance_margin": float(account_data.get("maintenance_margin", 0)),
            "cash": float(account_data.get("cash", 0)),
        }
    except Exception as e:
        print(f"Error fetching account info: {e}")
        return None


def check_margin_conditions(api):
    """
    Evaluate all margin control gates to determine if leverage is allowed.
    
    All 4 gates must pass for margin to be enabled:
    1. Market Trend: SPX > 200-SMA
    2. Margin Rate: FRED rate + spread ≤ 8.0%
    3. Buffer: (equity/portfolio_value) - (maintenance_margin/portfolio_value) ≥ 5%
    4. Leverage: portfolio_value / equity < 1.14×
    
    Args:
        api: Alpaca API credentials dict
    
    Returns:
        dict: {
            "allowed": bool - True if all gates pass
            "target_margin": float - 0.10 if allowed, else 0.0
            "gate_results": dict - individual gate pass/fail status
            "metrics": dict - all calculated metrics
            "errors": list - any errors encountered
        }
    """
    result = {
        "allowed": False,
        "target_margin": 0.0,
        "gate_results": {
            "market_trend": False,
            "margin_rate": False,
            "buffer": False,
            "leverage": False,
        },
        "metrics": {},
        "errors": [],
    }
    
    try:
        # Gate 1: Market Trend (SPY > 200-SMA as S&P 500 proxy)
        try:
            # Get all SPY data at once (efficient single fetch/read)
            spy_data = get_all_market_data("SPY")
            if spy_data is None:
                spy_data = update_market_data("SPY")
            
            spy_price = spy_data["price"]
            spy_sma = spy_data["sma200"]
            result["metrics"]["spx_price"] = spy_price  # Keep key name for compatibility
            result["metrics"]["spx_sma"] = spy_sma
            result["gate_results"]["market_trend"] = spy_price > spy_sma
        except Exception as e:
            result["errors"].append(f"Market trend check failed: {e}")
            return result
        
        # Get account information for remaining gates
        account_info = get_account_info(api)
        if not account_info:
            result["errors"].append("Failed to fetch account information")
            return result
        
        equity = account_info["equity"]
        portfolio_value = account_info["portfolio_value"]
        maintenance_margin = account_info["maintenance_margin"]
        cash = account_info["cash"]
        
        result["metrics"]["equity"] = equity
        result["metrics"]["portfolio_value"] = portfolio_value
        result["metrics"]["maintenance_margin"] = maintenance_margin
        result["metrics"]["cash"] = cash
        
        # Gate 2: Margin Rate (FRED + spread ≤ 8.0%)
        try:
            fred_rate = get_fred_rate()
            if fred_rate is None:
                result["errors"].append("Failed to fetch FRED rate")
                return result
            
            # Determine spread based on equity (actual account value)
            if equity <= margin_control_config["portfolio_threshold"]:
                spread = margin_control_config["spread_below_35k"]
            else:
                spread = margin_control_config["spread_above_35k"]
            
            margin_rate = fred_rate + spread
            result["metrics"]["fred_rate"] = fred_rate
            result["metrics"]["spread"] = spread
            result["metrics"]["margin_rate"] = margin_rate
            result["gate_results"]["margin_rate"] = margin_rate <= margin_control_config["max_margin_rate"]
        except Exception as e:
            result["errors"].append(f"Margin rate check failed: {e}")
            return result
        
        # Gate 3: Buffer (≥ 5%)
        try:
            if portfolio_value > 0:
                buffer = (equity / portfolio_value) - (maintenance_margin / portfolio_value)
            else:
                buffer = 0.0
            
            result["metrics"]["buffer"] = buffer
            result["gate_results"]["buffer"] = buffer >= margin_control_config["min_buffer_pct"]
        except Exception as e:
            result["errors"].append(f"Buffer check failed: {e}")
            return result
        
        # Gate 4: Leverage (< 1.14×)
        try:
            if equity > 0:
                leverage = portfolio_value / equity
            else:
                leverage = 0.0
            
            result["metrics"]["leverage"] = leverage
            result["gate_results"]["leverage"] = leverage < margin_control_config["max_leverage"]
        except Exception as e:
            result["errors"].append(f"Leverage check failed: {e}")
            return result
        
        # All gates must pass
        result["allowed"] = all(result["gate_results"].values())
        result["target_margin"] = margin_control_config["target_margin_pct"] if result["allowed"] else 0.0
        
    except Exception as e:
        result["errors"].append(f"Unexpected error in margin check: {e}")
    
    return result


def calculate_monthly_investments(api, margin_result):
    """
    Calculate dynamic monthly investment amounts based on available cash and margin.
    
    Steps:
    1. Get total cash from account
    2. Load Firestore balances for all SMA strategies  
    3. Check which strategies are currently below their SMA (bearish)
    4. Subtract reserved amounts only for bearish strategies
    5. Add margin if approved (equity × 10%)
    6. Apply Regulation T check (margin ≤ available_cash for 50/50 rule)
    7. Split total by strategy percentages
    
    Args:
        api: Alpaca API credentials
        margin_result: Result from check_margin_conditions()
    
    Returns:
        dict: {
            "total_cash": float,           # Total cash in account
            "total_reserved": float,       # Total reserved for bearish strategies
            "total_available": float,      # Total cash - reserved
            "margin_approved": float,      # Margin amount (0 if disabled)
            "total_investing": float,      # Total available + margin
            "strategy_amounts": dict,      # Amount per strategy
            "reserved_amounts": dict       # What was reserved per strategy
        }
    """
    # Step 1: Get total cash from account
    metrics = margin_result.get("metrics", {})
    total_cash = metrics.get("cash", 0)
    equity = metrics.get("equity", 0)
    
    # Step 2: Load Firestore reserved amounts
    balances = load_balances()
    reserved_amounts = {}
    
    # Step 3 & 4: Check SMA status and subtract if bearish
    for symbol, firestore_key in [("SPXL", "SPXL_SMA")]:
        # Determine which index to check (use SPY as S&P 500 proxy)
        if symbol == "SPXL":
            index_symbol = "SPY"
        else:
            continue
        
        # Check if currently bearish (below SMA)
        try:
            # Get all market data at once (efficient single fetch/read)
            market_data = get_all_market_data(index_symbol)
            if market_data is None:
                market_data = update_market_data(index_symbol)
            
            sma_200 = market_data["sma200"]
            latest_price = market_data["price"]
            is_bearish = latest_price < sma_200
            
            if is_bearish:
                # Subtract reserved amount
                reserved = balances.get(firestore_key, {}).get("invested", 0)
                if reserved and reserved > 0:
                    reserved_amounts[firestore_key] = reserved
        except Exception as e:
            print(f"Error checking {symbol} SMA for reserved calculation: {e}")
            # Conservative: don't subtract if we can't determine (avoids using reserved cash)
    
    # Step 5: Calculate available cash
    total_reserved = sum(reserved_amounts.values())
    available_cash = max(0, total_cash - total_reserved)  # Ensure non-negative
    
    # Step 6: Add margin if approved
    target_margin = margin_result.get("target_margin", 0)
    if target_margin > 0 and equity > 0:
        margin_available = equity * target_margin
        # Our +10% cap automatically satisfies Regulation T (50% rule)
        # No need to cap - margin is ADDITIONAL to available cash
        margin_approved = margin_available
    else:
        margin_approved = 0
    
    total_investing = available_cash + margin_approved
    
    # Step 8: Split by strategy percentages
    strategy_amounts = {
        key: total_investing * allocation 
        for key, allocation in strategy_allocations.items()
    }
    
    return {
        "total_cash": total_cash,
        "total_reserved": total_reserved,
        "total_available": available_cash,
        "margin_approved": margin_approved,
        "total_investing": total_investing,
        "strategy_amounts": strategy_amounts,
        "reserved_amounts": reserved_amounts
    }


def save_balance(strategy, invested):
    """
    Save strategy balance to Firestore.
    Handles Firestore unavailability gracefully for local testing.
    """
    try:
        doc_ref = get_firestore_client().collection("strategy-balances").document(strategy)
        doc_ref.set(
            {
                "invested": invested,
            }
        )
    except Exception as e:
        print(f"Warning: Could not save balance to Firestore for {strategy}: {e}")


def load_balances():
    """
    Load strategy balances from Firestore.
    Returns empty dict if Firestore is unavailable (local testing without proper config).
    """
    balances = {}
    try:
        docs = get_firestore_client().collection("strategy-balances").stream()
        for doc in docs:
            balances[doc.id] = doc.to_dict()
    except Exception as e:
        print(f"Warning: Could not load Firestore balances (local testing?): {e}")
        # Return empty dict for local testing without Firestore
    return balances


def update_balance_field(strategy, value):
    doc_ref = get_firestore_client().collection("strategy-balances").document(strategy)
    doc_ref.update({"invested": value})


# 9-Sig Strategy Data Management Functions
def save_nine_sig_quarterly_data(quarter_id, tqqq_balance, agg_balance, signal_line, action, quarterly_contributions):
    """Save quarterly data following 3Sig methodology for next quarter's calculations"""
    doc_ref = get_firestore_client().collection("nine-sig-quarters").document(quarter_id)
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
    docs = get_firestore_client().collection("nine-sig-quarters").order_by("timestamp", direction=firestore.Query.DESCENDING).limit(1).stream()
    for doc in docs:
        data = doc.to_dict()
        return data.get("previous_tqqq_balance", 0)
    return 0


def track_nine_sig_monthly_contribution(amount):
    """
    Track actual 9-Sig monthly contribution for quarterly signal calculation.
    Handles Firestore unavailability gracefully for local testing.
    """
    try:
        current_month = datetime.datetime.now().strftime("%Y-%m")
        doc_ref = get_firestore_client().collection("nine-sig-monthly-contributions").document(current_month)
        doc_ref.set({
            "month": current_month,
            "amount": amount,
            "timestamp": datetime.datetime.utcnow()
        })
    except Exception as e:
        print(f"Warning: Could not track 9-Sig contribution to Firestore: {e}")


def get_quarterly_nine_sig_contributions():
    """
    Get sum of actual 9-Sig contributions made in the current quarter.
    Returns 0 if Firestore is unavailable (local testing).
    """
    try:
        today = datetime.datetime.now()
        
        # Determine current quarter's start month
        quarter_start_month = ((today.month - 1) // 3) * 3 + 1
        quarter_start = datetime.datetime(today.year, quarter_start_month, 1)
        
        # Get all monthly contributions from this quarter
        docs = get_firestore_client().collection("nine-sig-monthly-contributions").where(
            "timestamp", ">=", quarter_start
        ).stream()
        
        total_contributions = sum(doc.to_dict().get("amount", 0) for doc in docs)
        return total_contributions
    except Exception as e:
        print(f"Warning: Could not load 9-Sig quarterly contributions from Firestore: {e}")
        return 0  # Return 0 for local testing without Firestore


def check_spy_30_down_rule():
    """
    Check if SPY has dropped 30% from all-time high using Alpaca data.
    Uses 2-year period to capture recent all-time highs and crashes.
    """
    try:
        # Get API credentials
        api = set_alpaca_environment(env=alpaca_environment)
        
        # Fetch 2 years of SPY data from Alpaca
        from datetime import datetime, timedelta
        
        market_data_base_url = "https://data.alpaca.markets"
        end_date = datetime.now()
        start_date = end_date - timedelta(days=730)  # 2 years
        
        url = f"{market_data_base_url}/v2/stocks/SPY/bars"
        params = {
            "start": start_date.strftime("%Y-%m-%d"),
            "end": end_date.strftime("%Y-%m-%d"),
            "timeframe": "1Day",
            "limit": 10000,
            "adjustment": "split",
            "feed": "iex"
        }
        
        response = requests.get(url, headers=get_auth_headers(api), params=params)
        response.raise_for_status()
        
        data = response.json()
        bars = data.get("bars", [])
        
        if len(bars) < 10:  # Need sufficient data
            print(f"Insufficient SPY data for 30-down rule: {len(bars)} bars")
            return False
        
        # Get all-time high and current close from bars
        all_time_high = max(bar['h'] for bar in bars)
        current_close = bars[-1]['c']
        
        # Check if current is 30% below the all-time high
        drop_percentage = (all_time_high - current_close) / all_time_high
        
        return drop_percentage >= 0.30
        
    except Exception as e:
        print(f"Error checking SPY 30 down rule: {e}")
        return False


def count_ignored_sell_signals():
    """Count how many sell signals have been ignored in the current crash protection period"""
    try:
        # Get recent quarters with ignored sell signals
        docs = get_firestore_client().collection("nine-sig-quarters").where("action_taken", "==", "SELL_IGNORED").order_by("timestamp", direction=firestore.Query.DESCENDING).limit(4).stream()
        return len(list(docs))
    except Exception as e:
        print(f"Error counting ignored sell signals: {e}")
        return 0


def make_monthly_nine_sig_contributions(api, force_execute=False, investment_calc=None, margin_result=None):
    """
    Monthly contributions go ONLY to AGG (bonds) - Following 3Sig Rule.
    Now includes margin-aware logic with dynamic investment amounts and All-or-Nothing approach.
    
    Args:
        api: Alpaca API credentials
        force_execute: Bypass trading day check for testing
        investment_calc: Pre-calculated investment amounts (from orchestrator) - optional
        margin_result: Pre-calculated margin conditions (from orchestrator) - optional
    """
    if not force_execute and not check_trading_day(mode="monthly"):
        print("Not first trading day of the month")
        return "Not first trading day of the month"
    
    if force_execute:
        print("9-Sig: Force execution enabled - bypassing trading day check")
        send_telegram_message("9-Sig: Force execution enabled for testing - bypassing trading day check")
    
    # If not provided by orchestrator, calculate independently
    if margin_result is None:
        margin_result = check_margin_conditions(api)
    
    if investment_calc is None:
        investment_calc = calculate_monthly_investments(api, margin_result)
    
    investment_amount = investment_calc["strategy_amounts"]["nine_sig_allo"]
    
    target_margin = margin_result["target_margin"]
    metrics = margin_result["metrics"]
    leverage = metrics.get("leverage", 1.0)
    
    # Determine available buying power (already calculated in investment_calc)
    buying_power = investment_calc["total_available"] + investment_calc["margin_approved"]
    
    # Check if we should skip investment
    if target_margin == 0:
        # Cash-only mode triggered
        if leverage > 1.0:
            # Still leveraged - must skip to deleverage
            action_taken = f"Skipped - Deleveraging required (leverage: {leverage:.2f}x)"
            send_margin_summary_message(margin_result, "9-Sig", action_taken, investment_calc)
            print(action_taken)
            return action_taken
        # Equity-only but gates failed - skip without Firestore addition
        action_taken = f"Skipped - Margin gates failed (cash-only mode, buying power: ${buying_power:.2f})"
        send_margin_summary_message(margin_result, "9-Sig", action_taken, investment_calc)
        print(action_taken)
        return action_taken
    
    # Check if we have sufficient buying power for full investment (All-or-Nothing)
    if buying_power < investment_amount:
        action_taken = f"Skipped - Insufficient buying power (${buying_power:.2f} < ${investment_amount:.2f})"
        send_margin_summary_message(margin_result, "9-Sig", action_taken, investment_calc)
        print(action_taken)
        return action_taken
    
    # Check minimum investment amount (Alpaca requirement)
    if investment_amount < margin_control_config["min_investment"]:
        action_taken = f"Skipped - Investment amount ${investment_amount:.2f} below Alpaca minimum ($1.00)"
        send_margin_summary_message(margin_result, "9-Sig", action_taken, investment_calc)
        print(action_taken)
        return action_taken
    
    # ALL monthly contributions go to AGG only (core 3Sig rule)
    try:
        agg_price = float(get_latest_trade(api, "AGG"))
        agg_shares_to_buy = investment_amount / agg_price
        
        if agg_shares_to_buy > 0:
            order = submit_order(api, "AGG", agg_shares_to_buy, "buy")
            wait_for_order_fill(api, order["id"])
            print(f"9-Sig: Bought {agg_shares_to_buy:.6f} shares of AGG (monthly contribution)")
            
            # Track the actual contribution amount for quarterly signal calculation
            track_nine_sig_monthly_contribution(investment_amount)
            
            # Create action summary
            action_taken = f"Invested ${investment_amount:.2f} in AGG - {agg_shares_to_buy:.4f} shares"
            send_margin_summary_message(margin_result, "9-Sig", action_taken, investment_calc)
        
        return f"9-Sig monthly contribution: ${investment_amount:.2f} invested in AGG"
    
    except Exception as e:
        error_msg = f"9-Sig monthly contribution failed: {str(e)}"
        print(error_msg)
        send_telegram_message(error_msg)
        return error_msg


def make_monthly_buys(api, force_execute=False, investment_calc=None, margin_result=None):
    """
    Make monthly HFEA purchases with margin-aware logic and dynamic investment amounts.
    Uses All-or-Nothing approach: invest full amount or skip entirely.
    
    Args:
        api: Alpaca API credentials
        force_execute: Bypass trading day check for testing
        investment_calc: Pre-calculated investment amounts (from orchestrator) - optional
        margin_result: Pre-calculated margin conditions (from orchestrator) - optional
    """
    if not force_execute and not check_trading_day(mode="monthly"):
        print("Not first trading day of the month")
        return "Not first trading day of the month"
    
    if force_execute:
        print("HFEA: Force execution enabled - bypassing trading day check")
        send_telegram_message("HFEA: Force execution enabled for testing - bypassing trading day check")
    
    # If not provided by orchestrator, calculate independently
    if margin_result is None:
        margin_result = check_margin_conditions(api)
    
    if investment_calc is None:
        investment_calc = calculate_monthly_investments(api, margin_result)
    
    investment_amount = investment_calc["strategy_amounts"]["hfea_allo"]
    
    target_margin = margin_result["target_margin"]
    metrics = margin_result["metrics"]
    leverage = metrics.get("leverage", 1.0)
    
    # Determine available buying power (already calculated in investment_calc)
    buying_power = investment_calc["total_available"] + investment_calc["margin_approved"]
    
    # Check if we should skip investment
    if target_margin == 0:
        # Cash-only mode triggered
        if leverage > 1.0:
            # Still leveraged - must skip to deleverage
            action_taken = f"Skipped - Deleveraging required (leverage: {leverage:.2f}x)"
            send_margin_summary_message(margin_result, "HFEA", action_taken, investment_calc)
            print(action_taken)
            return action_taken
        # Equity-only but gates failed - skip without Firestore addition
        action_taken = f"Skipped - Margin gates failed (cash-only mode, buying power: ${buying_power:.2f})"
        send_margin_summary_message(margin_result, "HFEA", action_taken, investment_calc)
        print(action_taken)
        return action_taken
    
    # Check if we have sufficient buying power for full investment (All-or-Nothing)
    if buying_power < investment_amount:
        action_taken = f"Skipped - Insufficient buying power (${buying_power:.2f} < ${investment_amount:.2f})"
        send_margin_summary_message(margin_result, "HFEA", action_taken, investment_calc)
        print(action_taken)
        return action_taken
    
    # Check minimum investment amount (Alpaca requirement)
    if investment_amount < margin_control_config["min_investment"]:
        action_taken = f"Skipped - Investment amount ${investment_amount:.2f} below Alpaca minimum ($1.00)"
        send_margin_summary_message(margin_result, "HFEA", action_taken, investment_calc)
        print(action_taken)
        return action_taken
    
    # Proceed with investment - we have sufficient funds
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
    shares_bought = []
    for symbol, qty in [
        ("UPRO", upro_shares_to_buy),
        ("TMF", tmf_shares_to_buy),
        ("KMLM", kmlm_shares_to_buy),
    ]:
        if qty > 0:
            submit_order(api, symbol, qty, "buy")
            print(f"Bought {qty:.6f} shares of {symbol}.")
            shares_bought.append(f"{symbol}: {qty:.4f} shares")
        else:
            print(f"No shares of {symbol} bought due to small amount.")
    
    # Create action summary
    action_taken = f"Invested ${investment_amount:.2f} - " + ", ".join(shares_bought)
    
    # Send consolidated margin summary message with investment calculation
    send_margin_summary_message(margin_result, "HFEA", action_taken, investment_calc)
    
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
        
        # Get actual contributions made during this quarter (dynamic amounts)
        quarterly_contributions = get_quarterly_nine_sig_contributions()
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


# Unified function to fetch all market data and calculate all SMAs at once
def update_market_data(symbol):
    """
    Fetch fresh market data from Alpaca and calculate ALL metrics in one operation.
    ALWAYS calculates and saves: price, sma200, sma255, sma200_state, sma255_state.
    This ensures complete consistency across all symbols and makes the system extensible.
    
    Args:
        symbol: Stock symbol (e.g., "SPY", "URTH")
    
    Returns:
        dict with keys: price, sma200, sma255, sma200_state, sma255_state, timestamp
    """
    print(f"Fetching fresh market data for {symbol} from Alpaca IEX feed")
    
    # Get API credentials
    api = set_alpaca_environment(env=alpaca_environment)
    
    # Fetch historical data (500 days covers both 200 and 255-day SMAs)
    closes = get_alpaca_historical_bars(api, symbol, days=500)
    
    if not closes or len(closes) < 255:
        raise ValueError(f"Insufficient Alpaca data for {symbol}. Got {len(closes) if closes else 0} bars, need at least 255.")
    
    # Get current price from latest trade
    current_price = get_latest_trade(api, symbol)
    
    # Calculate both SMAs from same dataset
    df = pd.DataFrame({'close': closes})
    sma_200 = df['close'].rolling(window=200).mean().iloc[-1]
    sma_255 = df['close'].rolling(window=255).mean().iloc[-1]
    
    # Calculate states for both SMA periods
    # Using 1% noise threshold (matches default in alert system)
    noise_threshold_pct = 1.0  # 1% threshold to avoid noise (as percentage)
    
    # 200-day state
    diff_200_pct = ((current_price - sma_200) / sma_200) * 100
    if diff_200_pct > noise_threshold_pct:
        sma200_state = "above"
    elif diff_200_pct < -noise_threshold_pct:
        sma200_state = "below"
    else:
        sma200_state = "neutral"
    
    # 255-day state
    diff_255_pct = ((current_price - sma_255) / sma_255) * 100
    if diff_255_pct > noise_threshold_pct:
        sma255_state = "above"
    elif diff_255_pct < -noise_threshold_pct:
        sma255_state = "below"
    else:
        sma255_state = "neutral"
    
    # Prepare complete market data
    market_data = {
        "symbol": symbol,
        "price": float(current_price),
        "sma200": float(sma_200),
        "sma255": float(sma_255),
        "sma200_state": sma200_state,
        "sma255_state": sma255_state,
        "timestamp": datetime.datetime.utcnow()
    }
    
    # Save everything to Firestore at once
    doc_id = symbol.replace("^", "").replace(".", "_")
    doc_ref = get_firestore_client().collection("market-data").document(doc_id)
    
    # Get existing data (to preserve alert tracking fields)
    doc = doc_ref.get()
    if doc.exists:
        existing_data = doc.to_dict()
        # Preserve alert date fields if they exist
        for field in ['sma200_last_hour_alert_date', 'sma255_last_hour_alert_date']:
            if field in existing_data:
                market_data[field] = existing_data[field]
    
    # Write complete data
    doc_ref.set(market_data)
    
    print(f"Updated {symbol}: Price=${market_data['price']:.2f}, SMA200=${market_data['sma200']:.2f} ({sma200_state}), SMA255=${market_data['sma255']:.2f} ({sma255_state})")
    
    return market_data


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


def monthly_buying_sma(api, symbol, force_execute=False, investment_calc=None, margin_result=None):
    """
    Monthly SMA-based investment with margin-aware logic and dynamic investment amounts.
    Uses All-or-Nothing approach: invest full amount or skip entirely.
    Only adds to Firestore when SMA trend is bearish AND account is equity-only.
    
    Args:
        api: Alpaca API credentials
        symbol: Symbol to trade (e.g., "SPXL")
        force_execute: Bypass trading day check for testing
        investment_calc: Pre-calculated investment amounts (from orchestrator) - optional
        margin_result: Pre-calculated margin conditions (from orchestrator) - optional
    """
    if not force_execute and not check_trading_day(mode="monthly"):
        return "Not first trading day of the month"
    
    if force_execute:
        print(f"{symbol} SMA: Force execution enabled - bypassing trading day check")
        send_telegram_message(f"{symbol} SMA: Force execution enabled for testing - bypassing trading day check")

    # Get symbol-specific parameters (use SPY as S&P 500 proxy for SPXL decisions)
    if symbol == "SPXL":
        # Get all SPY market data at once (efficient single fetch/read)
        spy_data = get_all_market_data("SPY")
        if spy_data is None:
            spy_data = update_market_data("SPY")
        
        sma_200 = spy_data["sma200"]
        latest_price = spy_data["price"]
    else:
        return f"Unknown symbol: {symbol}"

    # If not provided by orchestrator, calculate independently
    if margin_result is None:
        margin_result = check_margin_conditions(api)
    
    if investment_calc is None:
        investment_calc = calculate_monthly_investments(api, margin_result)
    
    investment_amount = investment_calc["strategy_amounts"]["spxl_allo"]
    
    target_margin = margin_result["target_margin"]
    metrics = margin_result["metrics"]
    leverage = metrics.get("leverage", 1.0)
    
    # Determine available buying power (already calculated in investment_calc)
    buying_power = investment_calc["total_available"] + investment_calc["margin_approved"]

    print(f"{symbol}: Investment=${investment_amount:.2f}, Price={latest_price:.2f}, SMA={sma_200:.2f}, Leverage={leverage:.2f}x")
    
    # Check SMA trend
    if latest_price > sma_200 * (1 + margin):
        # Bullish trend - attempt to buy
        
        # Check if we should skip investment
        if target_margin == 0:
            # Cash-only mode triggered
            if leverage > 1.0:
                # Still leveraged - must skip to deleverage
                action_taken = f"Skipped - Deleveraging required (leverage: {leverage:.2f}x)"
                send_margin_summary_message(margin_result, f"{symbol} SMA", action_taken, investment_calc)
                print(action_taken)
                return action_taken
            # Equity-only but gates failed - skip without Firestore addition
            action_taken = f"Skipped - Margin gates failed (cash-only mode, buying power: ${buying_power:.2f})"
            send_margin_summary_message(margin_result, f"{symbol} SMA", action_taken, investment_calc)
            print(action_taken)
            return action_taken
        
        # Check if we have sufficient buying power for full investment (All-or-Nothing)
        if buying_power < investment_amount:
            action_taken = f"Skipped - Insufficient buying power (${buying_power:.2f} < ${investment_amount:.2f})"
            send_margin_summary_message(margin_result, f"{symbol} SMA", action_taken, investment_calc)
            print(action_taken)
            return action_taken
        
        # Check minimum investment amount (Alpaca requirement)
        if investment_amount < margin_control_config["min_investment"]:
            action_taken = f"Skipped - Investment amount ${investment_amount:.2f} below Alpaca minimum ($1.00)"
            send_margin_summary_message(margin_result, f"{symbol} SMA", action_taken, investment_calc)
            print(action_taken)
            return action_taken
        
        # Execute purchase
        price = get_latest_trade(api, symbol)
        print(f"Executing buy: price={price}")
        shares_to_buy = investment_amount / price

        if shares_to_buy > 0:
            order = submit_order(api, symbol, shares_to_buy, "buy")
            wait_for_order_fill(api, order["id"])
            positions = list_positions(api)
            position = next((p for p in positions if p["symbol"] == symbol), None)
            invested = float(position["market_value"]) if position else 0
            save_balance(symbol + "_SMA", invested)
            
            action_taken = f"Bought {shares_to_buy:.4f} shares of {symbol} (${investment_amount:.2f})"
            send_margin_summary_message(margin_result, f"{symbol} SMA", action_taken, investment_calc)
            return f"Bought {shares_to_buy:.6f} shares of {symbol}."
        else:
            action_taken = f"Amount too small to buy {symbol} shares"
            send_margin_summary_message(margin_result, f"{symbol} SMA", action_taken, investment_calc)
            return f"Amount too small to buy {symbol} shares."
    else:
        # Bearish trend (below SMA) - skip buying
        # Only add to Firestore if account is equity-only (leverage <= 1.0)
        if leverage <= 1.0:
            # Equity-only account - can add skipped amount to Firestore
            invested_amount = load_balances().get(f"{symbol}_SMA", {}).get("invested", 0)
            if invested_amount is None:
                invested_amount = 0
            updated_balance = investment_amount + invested_amount
            save_balance(symbol + "_SMA", updated_balance)
            
            action_taken = f"Skipped (SMA bearish) - Added ${investment_amount:.2f} to Firestore. Total reserved: ${updated_balance:.2f}"
            send_margin_summary_message(margin_result, f"{symbol} SMA", action_taken, investment_calc)
            return f"Index is significantly below 200-SMA and no monthly invest was done into {symbol} but ${updated_balance:.2f} of the cash is allocated to this strategy"
        else:
            # Still leveraged - skip without Firestore addition (deleveraging priority)
            action_taken = "Skipped (SMA bearish + leveraged) - No Firestore addition during deleverage"
            send_margin_summary_message(margin_result, f"{symbol} SMA", action_taken, investment_calc)
            return f"Index is significantly below 200-SMA. Skipping {symbol} investment (account leveraged: {leverage:.2f}x)"


def daily_trade_sma(api, symbol):
    if not check_trading_day(mode="daily"):
        send_telegram_message(f"Market closed today. Skipping 200SMA. for {symbol}")
        return "Market closed today."

    # Use SPY as S&P 500 proxy for SPXL trading decisions
    if symbol == "SPXL":
        # Get all SPY market data at once (efficient single fetch/read)
        spy_data = get_all_market_data("SPY")
        if spy_data is None:
            spy_data = update_market_data("SPY")
        
        sma_200 = spy_data["sma200"]
        latest_price = spy_data["price"]
    else:
        return f"Unknown symbol: {symbol}"

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


def send_margin_summary_message(margin_result, strategy_name, action_taken, investment_calc=None):
    """
    Send consolidated monthly margin summary to Telegram.
    
    Args:
        margin_result: Dict from check_margin_conditions() with gate results and metrics
        strategy_name: Name of the strategy (e.g., "HFEA", "SPXL SMA", "9-Sig")
        action_taken: Description of action taken (e.g., "Bought X shares", "Skipped - insufficient funds")
        investment_calc: Optional dict from calculate_monthly_investments() with investment breakdown
    """
    metrics = margin_result.get("metrics", {})
    gate_results = margin_result.get("gate_results", {})
    errors = margin_result.get("errors", [])
    
    # Build the message
    message_parts = [f"📊 {strategy_name} Monthly Update\n"]
    
    # Check for errors first
    if errors:
        message_parts.append("⚠️ ERRORS DETECTED - Defaulting to Cash-Only Mode")
        for error in errors:
            message_parts.append(f"  • {error}")
        message_parts.append("")
    
    # Market Trend
    spx_price = metrics.get("spx_price", 0)
    spx_sma = metrics.get("spx_sma", 0)
    trend_emoji = "✅" if gate_results.get("market_trend", False) else "❌"
    message_parts.append(f"Market Trend: {trend_emoji} SPX ${spx_price:.2f} (200-SMA: ${spx_sma:.2f})")
    
    # Margin Rate
    margin_rate = metrics.get("margin_rate", 0)
    fred_rate = metrics.get("fred_rate", 0)
    spread = metrics.get("spread", 0)
    rate_emoji = "✅" if gate_results.get("margin_rate", False) else "❌"
    message_parts.append(f"Margin Rate: {rate_emoji} {margin_rate*100:.1f}% (FRED {fred_rate*100:.1f}% + {spread*100:.1f}%)")
    
    # Buffer
    buffer = metrics.get("buffer", 0)
    buffer_emoji = "✅" if gate_results.get("buffer", False) else "❌"
    message_parts.append(f"Buffer: {buffer_emoji} {buffer*100:.1f}%")
    
    # Leverage
    leverage = metrics.get("leverage", 0)
    leverage_emoji = "✅" if gate_results.get("leverage", False) else "❌"
    message_parts.append(f"Leverage: {leverage_emoji} {leverage:.2f}x")
    
    # Decision
    message_parts.append("")
    if margin_result.get("allowed", False):
        message_parts.append("Decision: 🟢 Margin ENABLED (+10%)")
    else:
        message_parts.append("Decision: 🔴 Cash-Only Mode")
    
    # Investment Calculation (if provided)
    if investment_calc:
        message_parts.append("\n💰 Monthly Investment Calculation:")
        message_parts.append(f"Total Cash: ${investment_calc['total_cash']:,.2f}")
        if investment_calc['total_reserved'] > 0:
            message_parts.append(f"Reserved (bearish): ${investment_calc['total_reserved']:,.2f}")
            # Show which strategies are reserved
            for key, value in investment_calc['reserved_amounts'].items():
                message_parts.append(f"  • {key}: ${value:,.2f}")
        message_parts.append(f"Available: ${investment_calc['total_available']:,.2f}")
        if investment_calc['margin_approved'] > 0:
            message_parts.append(f"Margin Approved: ${investment_calc['margin_approved']:,.2f}")
        message_parts.append("━━━━━━━━━━━━━━━━━━━━━━")
        message_parts.append(f"Total Investing: ${investment_calc['total_investing']:,.2f}")
        
        # Show this strategy's allocation
        strategy_key = None
        if "HFEA" in strategy_name:
            strategy_key = "hfea_allo"
            pct = "47.5%"
        elif "9-Sig" in strategy_name:
            strategy_key = "nine_sig_allo"
            pct = "5%"
        elif "SMA" in strategy_name:
            strategy_key = "spxl_allo"
            pct = "47.5%"
        
        if strategy_key and strategy_key in investment_calc['strategy_amounts']:
            message_parts.append(f"\nThis Strategy ({pct}): ${investment_calc['strategy_amounts'][strategy_key]:,.2f}")
    
    # Account Info
    equity = metrics.get("equity", 0)
    portfolio_value = metrics.get("portfolio_value", 0)
    message_parts.append(f"\nAccount: Equity ${equity:,.2f} | Portfolio ${portfolio_value:,.2f}")
    
    # Action Taken
    message_parts.append(f"\nAction: {action_taken}")
    
    # Send the consolidated message
    full_message = "\n".join(message_parts)
    send_telegram_message(full_message)


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
    """
    Fetch the all-time high and current price for an index using Alpaca.
    Uses 5 years of data (maximum available with Basic subscription).
    
    Args:
        index_symbol: Stock symbol (e.g., "SPY", "URTH")
    
    Returns:
        tuple: (current_price, all_time_high)
    """
    try:
        # Get API credentials
        api = set_alpaca_environment(env=alpaca_environment)
        
        # Fetch 5 years of data from Alpaca (max available with Basic plan)
        from datetime import datetime, timedelta
        
        market_data_base_url = "https://data.alpaca.markets"
        end_date = datetime.now()
        start_date = end_date - timedelta(days=1825)  # 5 years
        
        url = f"{market_data_base_url}/v2/stocks/{index_symbol}/bars"
        params = {
            "start": start_date.strftime("%Y-%m-%d"),
            "end": end_date.strftime("%Y-%m-%d"),
            "timeframe": "1Day",
            "limit": 10000,
            "adjustment": "split",
            "feed": "iex"
        }
        
        response = requests.get(url, headers=get_auth_headers(api), params=params)
        response.raise_for_status()
        
        data = response.json()
        bars = data.get("bars", [])
        
        if not bars:
            raise ValueError(f"No Alpaca data returned for {index_symbol}")
        
        # Get all-time high and current close from bars
        all_time_high = max(bar['h'] for bar in bars)
        current_price = bars[-1]['c']
        
        return current_price, all_time_high
        
    except Exception as e:
        print(f"Error fetching index data for {index_symbol}: {e}")
        raise


def get_index_sma_state(index_symbol, sma_period):
    """
    Load the previous SMA state for an index from Firestore.
    
    Args:
        index_symbol: Market symbol (e.g., "^GSPC")
        sma_period: SMA period (e.g., 200, 255)
    
    Returns:
        dict with keys: state, timestamp
        Returns None if no previous state exists
    """
    try:
        # Normalize symbol for Firestore document ID
        doc_id = index_symbol.replace("^", "").replace(".", "_")
        
        doc_ref = get_firestore_client().collection("market-data").document(doc_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            return None
        
        data = doc.to_dict()
        
        # Extract the state field for this SMA period
        state_field = f"sma{sma_period}_state"
        state = data.get(state_field)
        
        if state is None:
            return None
        
        return {
            "state": state,
            "timestamp": data.get("timestamp")
        }
        
    except Exception as e:
        print(f"Warning: Could not load SMA state for {index_symbol}: {e}")
        return None


def save_index_sma_state(index_symbol, sma_period, state, price, sma_value):
    """
    Save the current SMA state for an index to Firestore.
    Note: update_market_data() now handles price/SMA/state updates automatically.
    This function is kept for backward compatibility with alert system.
    
    Args:
        index_symbol: Market symbol
        sma_period: SMA period
        state: Current state ("above", "below", or "neutral")
        price: Current price (ignored - preserved from update_market_data)
        sma_value: Current SMA value (ignored - preserved from update_market_data)
    """
    try:
        # Normalize symbol for Firestore document ID
        doc_id = index_symbol.replace("^", "").replace(".", "_")
        
        doc_ref = get_firestore_client().collection("market-data").document(doc_id)
        
        # Get existing data
        doc = doc_ref.get()
        if not doc.exists:
            print(f"Warning: No market data exists for {index_symbol}. Call update_market_data() first.")
            return
        
        data = doc.to_dict()
        
        # Only update the specific state field (price and SMA already set by update_market_data)
        data[f"sma{sma_period}_state"] = state
        data["timestamp"] = datetime.datetime.utcnow()
        
        doc_ref.set(data)
        
    except Exception as e:
        print(f"Warning: Could not save SMA state for {index_symbol}: {e}")


def is_last_trading_hour():
    """
    Check if current time is within the last hour of the trading day.
    
    Returns:
        bool: True if within 1 hour of market close, False otherwise
    """
    try:
        # Get current time
        now = datetime.datetime.now()
        
        # Load NYSE calendar
        nyse = mcal.get_calendar("NYSE")
        
        # Get today's schedule
        schedule = nyse.schedule(start_date=now.date(), end_date=now.date())
        
        if schedule.empty:
            # Market is closed today
            return False
        
        # Get market close time for today
        market_close = schedule.iloc[0]['market_close']
        
        # Convert to naive datetime for comparison (both in local timezone)
        if hasattr(market_close, 'tz_localize'):
            market_close_naive = market_close.tz_localize(None)
        elif hasattr(market_close, 'tz_convert'):
            market_close_naive = market_close.tz_convert(None)
        else:
            market_close_naive = market_close.replace(tzinfo=None)
        
        # Calculate time until market close
        time_until_close = market_close_naive - now
        
        # Check if within last hour (3600 seconds)
        return 0 <= time_until_close.total_seconds() <= 3600
        
    except Exception as e:
        print(f"Warning: Could not determine if last trading hour: {e}")
        return False


def was_last_hour_alert_sent_today(index_symbol, sma_period):
    """
    Check if a last-hour confirmation alert was already sent today.
    
    Args:
        index_symbol: Market symbol
        sma_period: SMA period
    
    Returns:
        bool: True if alert was already sent today, False otherwise
    """
    try:
        # Normalize symbol for Firestore document ID
        doc_id = index_symbol.replace("^", "").replace(".", "_")
        
        doc_ref = get_firestore_client().collection("market-data").document(doc_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            return False
        
        data = doc.to_dict()
        
        # Get the last hour alert date field for this SMA period
        alert_date_field = f"sma{sma_period}_last_hour_alert_date"
        last_alert_date = data.get(alert_date_field)
        
        if not last_alert_date:
            return False
        
        # Check if alert was sent today
        today = datetime.datetime.now().date()
        
        # Handle both string and datetime formats
        if isinstance(last_alert_date, str):
            last_alert_date = datetime.datetime.fromisoformat(last_alert_date).date()
        elif hasattr(last_alert_date, 'date'):
            last_alert_date = last_alert_date.date()
        
        return last_alert_date == today
        
    except Exception as e:
        print(f"Warning: Could not check last hour alert status: {e}")
        return False


def mark_last_hour_alert_sent(index_symbol, sma_period):
    """
    Mark that a last-hour confirmation alert was sent today.
    Updates the unified market-data document with the alert date.
    
    Args:
        index_symbol: Market symbol
        sma_period: SMA period
    """
    try:
        # Normalize symbol for Firestore document ID
        doc_id = index_symbol.replace("^", "").replace(".", "_")
        
        doc_ref = get_firestore_client().collection("market-data").document(doc_id)
        
        # Get existing data or create new
        doc = doc_ref.get()
        if doc.exists:
            data = doc.to_dict()
        else:
            data = {"symbol": index_symbol}
        
        # Update the last hour alert date field for this SMA period
        alert_date_field = f"sma{sma_period}_last_hour_alert_date"
        data[alert_date_field] = datetime.datetime.now().date().isoformat()
        data["timestamp"] = datetime.datetime.utcnow()
        
        doc_ref.set(data)
        
    except Exception as e:
        print(f"Warning: Could not mark last hour alert as sent: {e}")




def check_unified_index_alert(request):
    """Unified index alert function that can handle multiple indices and alert types"""
    
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
    if not request_json:
        return jsonify({"error": "No request body provided"}), 400
    
    # Extract parameters with defaults
    index_symbol = request_json.get("index_symbol")
    index_name = request_json.get("index_name", index_symbol)
    alert_type = request_json.get("alert_type", "ath_drop")  # "ath_drop", "sma_crossing"
    sma_period = request_json.get("sma_period", 200)  # Default to 200-day SMA
    threshold_percent = request_json.get("threshold_percent", 30.0)  # For ATH drops
    noise_threshold = request_json.get("noise_threshold", 1.0)  # For SMA crossings
    
    if not index_symbol:
        return jsonify({"error": "Missing required parameter: index_symbol"}), 400

    try:
        if alert_type == "ath_drop":
            # Handle all-time high drop alerts
            current_price, all_time_high = get_index_data(index_symbol)
            drop_percentage = ((all_time_high - current_price) / all_time_high) * 100
            
            if drop_percentage >= threshold_percent:
                message = f"Alert: {index_name} has dropped {drop_percentage:.2f}% from its ATH! Consider a loan with a duration of 6 to 8 years (50k to 100k) at around 4.5% interest max"
                send_telegram_message(message)
                return jsonify({"message": message, "status": "ath_drop_alert", "drop_percentage": drop_percentage}), 200
            else:
                return jsonify({
                    "message": f"{index_name} is within safe range ({drop_percentage:.2f}% below ATH)",
                    "status": "within_range",
                    "drop_percentage": drop_percentage
                }), 200
                
        elif alert_type == "sma_crossing":
            # Handle SMA crossing alerts with crossover detection
            # Get all market data at once for efficiency
            market_data = get_all_market_data(index_symbol)
            if market_data is None:
                market_data = update_market_data(index_symbol)
            
            current_price = market_data["price"]
            
            # Get appropriate SMA based on period
            if sma_period == 255:
                sma_value = market_data["sma255"]
            elif sma_period == 200:
                sma_value = market_data["sma200"]
            else:
                # For any other period, calculate dynamically using Alpaca
                api = set_alpaca_environment(env=alpaca_environment)
                
                # Fetch enough data for custom SMA period (add 50% buffer)
                days_needed = int(sma_period * 1.5 * 1.4)  # trading days to calendar days with buffer
                closes = get_alpaca_historical_bars(api, index_symbol, days=days_needed)
                
                if closes and len(closes) >= sma_period:
                    df = pd.DataFrame({'close': closes})
                    sma_value = df['close'].rolling(window=sma_period).mean().iloc[-1]
                else:
                    raise ValueError(f"Insufficient Alpaca data for {index_symbol} {sma_period}-day SMA. Got {len(closes) if closes else 0} bars, need {sma_period}.")
            
            # Calculate percentage difference from SMA
            price_diff_percent = ((current_price - sma_value) / sma_value) * 100
            
            # Load previous state from Firestore
            previous_state_data = get_index_sma_state(index_symbol, sma_period)
            previous_state = previous_state_data.get("state") if previous_state_data else None
            
            # Determine current state based on noise threshold
            if price_diff_percent > noise_threshold:
                current_state = "above"
            elif price_diff_percent < -noise_threshold:
                current_state = "below"
            else:
                current_state = "neutral"
            
            # Check if we're in the last trading hour
            in_last_hour = is_last_trading_hour()
            already_sent_last_hour = was_last_hour_alert_sent_today(index_symbol, sma_period)
            
            # Initialize response variables
            message = None
            status = None
            alert_sent = False
            
            # Check for state change (crossover)
            if previous_state and previous_state != current_state:
                # State changed - send crossover alert
                if current_state == "above":
                    emoji = "🚀" if price_diff_percent > 2.0 else "📈"
                    urgency = " ⚡🔔 LAST HOUR" if in_last_hour else ""
                    message = f"{emoji} {index_name} Alert: Crossed ABOVE its {sma_period}-day SMA!{urgency}\nCurrent: ${current_price:.2f} (SMA: ${sma_value:.2f}, +{price_diff_percent:.2f}%)"
                    status = "crossover_above"
                    alert_sent = True
                    
                elif current_state == "below":
                    emoji = "📉" if price_diff_percent < -2.0 else "📊"
                    urgency = " ⚡🔔 LAST HOUR" if in_last_hour else ""
                    message = f"{emoji} {index_name} Alert: Crossed BELOW its {sma_period}-day SMA!{urgency}\nCurrent: ${current_price:.2f} (SMA: ${sma_value:.2f}, {price_diff_percent:.2f}%)"
                    status = "crossover_below"
                    alert_sent = True
                    
                elif current_state == "neutral":
                    # Moved into neutral zone from above or below
                    message = f"📊 {index_name}: Entered neutral zone (within {noise_threshold}% of {sma_period}-day SMA)\nCurrent: ${current_price:.2f} (SMA: ${sma_value:.2f}, {price_diff_percent:+.2f}%)"
                    status = "neutral_zone"
                    alert_sent = True
                
                # Send the crossover alert
                if message:
                    send_telegram_message(message)
                    # If sent during last hour, mark it
                    if in_last_hour:
                        mark_last_hour_alert_sent(index_symbol, sma_period)
            
            # Check for last hour confirmation (only if no crossover alert was sent)
            elif in_last_hour and not already_sent_last_hour and current_state != "neutral":
                # Send urgent confirmation alert during last trading hour
                if current_state == "above":
                    message = f"⚡🔔 {index_name} FINAL HOUR CONFIRMATION:\nStill ABOVE {sma_period}-day SMA\nCurrent: ${current_price:.2f} (SMA: ${sma_value:.2f}, +{price_diff_percent:.2f}%)\n\n✅ Signal: Buy/Hold position"
                    status = "last_hour_above"
                    alert_sent = True
                elif current_state == "below":
                    message = f"⚡🔔 {index_name} FINAL HOUR CONFIRMATION:\nStill BELOW {sma_period}-day SMA\nCurrent: ${current_price:.2f} (SMA: ${sma_value:.2f}, {price_diff_percent:.2f}%)\n\n❌ Signal: Avoid/Sell position"
                    status = "last_hour_below"
                    alert_sent = True
                
                # Send the last hour confirmation
                if message:
                    send_telegram_message(message)
                    mark_last_hour_alert_sent(index_symbol, sma_period)
            
            # Save current state to Firestore (always update)
            save_index_sma_state(index_symbol, sma_period, current_state, current_price, sma_value)
            
            # Return appropriate response
            if alert_sent:
                return jsonify({
                    "message": message,
                    "status": status,
                    "price_diff_percent": price_diff_percent,
                    "current_price": current_price,
                    "sma_value": sma_value,
                    "previous_state": previous_state,
                    "current_state": current_state
                }), 200
            else:
                # No alert sent - state unchanged
                return jsonify({
                    "message": f"{index_name} is {current_state} {sma_period}-day SMA (no state change, no alert sent)",
                    "status": f"{current_state}_no_change",
                    "price_diff_percent": price_diff_percent,
                    "current_price": current_price,
                    "sma_value": sma_value,
                    "previous_state": previous_state,
                    "current_state": current_state
                }), 200
        else:
            return jsonify({"error": f"Invalid alert_type: {alert_type}. Must be 'ath_drop' or 'sma_crossing'"}), 400
                
    except Exception as e:
        error_message = f"Error checking {index_name} alert: {str(e)}"
        print(error_message)
        send_telegram_message(error_message)
        return jsonify({"error": error_message}), 500


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


def monthly_invest_all_strategies(api, force_execute=False):
    """
    Orchestrator function that runs all three monthly investment strategies.
    Calculates budgets ONCE and distributes them to ensure exact percentage splits.
    
    This prevents the problem of each function independently calculating and over-spending.
    
    Args:
        api: Alpaca API credentials
        force_execute: Bypass trading day check for testing
    
    Returns:
        dict with results from all three strategies
    """
    if not force_execute and not check_trading_day(mode="monthly"):
        print("Not first trading day of the month")
        return {"error": "Not first trading day of the month"}
    
    # Calculate margin conditions and investment amounts ONCE
    print("=== Monthly Investment Orchestrator ===")
    print("Calculating budgets for all strategies...")
    
    margin_result = check_margin_conditions(api)
    investment_calc = calculate_monthly_investments(api, margin_result)
    
    print(f"Total investing power: ${investment_calc['total_investing']:.2f}")
    print(f"  HFEA (47.5%): ${investment_calc['strategy_amounts']['hfea_allo']:.2f}")
    print(f"  SPXL (47.5%): ${investment_calc['strategy_amounts']['spxl_allo']:.2f}")
    print(f"  9-Sig (5%): ${investment_calc['strategy_amounts']['nine_sig_allo']:.2f}")
    
    # Run all three strategies with pre-calculated budgets
    results = {}
    
    print("\n=== Executing HFEA ===")
    results["hfea"] = make_monthly_buys(api, force_execute, investment_calc, margin_result)
    
    print("\n=== Executing SPXL SMA ===")
    results["spxl"] = monthly_buying_sma(api, "SPXL", force_execute, investment_calc, margin_result)
    
    print("\n=== Executing 9-Sig ===")
    results["nine_sig"] = make_monthly_nine_sig_contributions(api, force_execute, investment_calc, margin_result)
    
    print("\n=== All Monthly Strategies Complete ===")
    
    return results


@app.route("/monthly_invest_all", methods=["POST"])
def monthly_invest_all(request):
    """
    Orchestrator endpoint that runs all three monthly strategies in one coordinated execution.
    Recommended for production use to ensure exact budget splits and avoid over-spending.
    """
    api = set_alpaca_environment(env=alpaca_environment)
    results = monthly_invest_all_strategies(api)
    return jsonify(results), 200


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


@app.route("/daily_trade_spxl_200sma", methods=["POST"])
def daily_trade_spxl_200sma(request):
    api = set_alpaca_environment(
        env=alpaca_environment
    )  # or 'paper' based on your needs
    result = daily_trade_sma(api, "SPXL")
    print(result)
    return result, 200


@app.route("/index_alert", methods=["POST"])
def index_alert(request):
    return check_unified_index_alert(request)


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
    if action == "monthly_invest_all":
        return monthly_invest_all_strategies(api, force_execute=force_execute)
    elif action == "monthly_buy_hfea":
        return make_monthly_buys(api, force_execute=force_execute)
    elif action == "rebalance_hfea":
        return rebalance_portfolio(api)
    elif action == "monthly_nine_sig_contributions":
        return make_monthly_nine_sig_contributions(api, force_execute=force_execute)
    elif action == "quarterly_nine_sig_signal":
        return execute_quarterly_nine_sig_signal(api, force_execute=force_execute)
    elif action == "monthly_buy_spxl":
        return monthly_buying_sma(api, "SPXL", force_execute=force_execute)
    elif action == "sell_spxl_below_200sma":
        return daily_trade_sma(api, "SPXL")
    elif action == "buy_spxl_above_200sma":
        return daily_trade_sma(api, "SPXL")
    elif action == "index_alert":
        return check_unified_index_alert(request)
    else:
        return "No valid action provided."


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--action",
        choices=[
            "monthly_invest_all",
            "monthly_buy_hfea",
            "rebalance_hfea",
            "monthly_nine_sig_contributions",
            "quarterly_nine_sig_signal",
            "monthly_buy_spxl",
            "sell_spxl_below_200sma",
            "buy_spxl_above_200sma",
            "index_alert"
        ],
        required=True,
        help="Action to perform: 'monthly_invest_all' runs all three monthly strategies with coordinated budgets (recommended)",
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

# local execution:
# RECOMMENDED - Run all monthly strategies with coordinated budgets:
# python3 main.py --action monthly_invest_all --env paper --force
#
# Individual strategy execution (for testing):
# python3 main.py --action monthly_buy_hfea --env paper --force
# python3 main.py --action monthly_buy_spxl --env paper --force
# python3 main.py --action monthly_nine_sig_contributions --env paper --force
#
# Other actions:
# python3 main.py --action rebalance_hfea --env paper
# python3 main.py --action quarterly_nine_sig_signal --env paper --force
# python3 main.py --action sell_spxl_below_200sma --env paper
# python3 main.py --action buy_spxl_above_200sma --env paper
# python3 main.py --action index_alert --env paper  # For unified index alerts (use with request body)

# consider shifting to short term bonds when 200sma is below https://app.alpaca.markets/trade/BIL?asset_class=stocks
