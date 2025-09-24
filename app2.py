from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import math
from datetime import datetime, date, timedelta
import random
import time

app = Flask(__name__)
CORS(app)

# ------------------------------------
#  NSE API ENDPOINTS & HEADERS
# ------------------------------------
NSE_INDICES_URL = "https://www.nseindia.com/api/option-chain-indices?symbol={}"
NSE_EQUITIES_URL = "https://www.nseindia.com/api/option-chain-equities?symbol={}"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.82 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.159 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/88.0.4324.96 Safari/537.36"
]

HEADERS = {
    "User-Agent": random.choice(USER_AGENTS),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/"
}

# ------------------------------------
#  MANUAL INPUTS FOR r & q
# ------------------------------------
MANUAL_RISK_FREE_RATE = 0.06509  # Example: 6.80% annual
MANUAL_DIVIDEND_YIELD = 0.0      # Example: 0.0

# ------------------------------------
#  UPDATED HOLIDAY LIST FOR 2025
# ------------------------------------
# The user has specifically given this list, ensuring no Sunday/duplicate:
HOLIDAYS_2025 = [
    "26-Feb-2025",  # Wed Mahashivratri
    "14-Mar-2025",  # Fri Holi
    "31-Mar-2025",  # Mon Id-Ul-Fitr
    "10-Apr-2025",  # Thu Mahavir Jayanti
    "14-Apr-2025",  # Mon Ambedkar Jayanti
    "18-Apr-2025",  # Fri Good Friday
    "01-May-2025",  # Thu Maharashtra Day
    "15-Aug-2025",  # Fri Independence/Parsi
    "27-Aug-2025",  # Wed Ganesh Chaturthi
    "02-Oct-2025",  # Thu Gandhi Jayanti / Dussehra
    "21-Oct-2025",  # Tue Diwali Laxmi Pujan
    "22-Oct-2025",  # Wed Balipratipada
    "05-Nov-2025",  # Wed Prakash Gurpurb
    "25-Dec-2025"   # Thu Christmas
]

# ------------------------------------
#  TOTAL TRADING DAYS FOR 2025 = 247
# ------------------------------------
TOTAL_TRADING_DAYS_2025 = 247

# ------------------------------------
#  FETCH OPTION CHAIN
# ------------------------------------
def fetch_option_chain(symbol, retries=3):
    session = requests.Session()
    session.headers.update(HEADERS)

    symbol_upper = symbol.upper()
    if symbol_upper in ["NIFTY", "BANKNIFTY"]:
        url = NSE_INDICES_URL.format(symbol_upper)
    else:
        url = NSE_EQUITIES_URL.format(symbol_upper)

    for attempt in range(retries):
        try:
            session.get("https://www.nseindia.com", timeout=5)
            time.sleep(2)
            response = session.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            return data["records"]["data"]
        except requests.exceptions.RequestException as e:
            print(f"Attempt {attempt+1} failed: {e}")
            time.sleep(3)

    return {"error": f"NSE API Request Failed after {retries} retries"}

# ------------------------------------
#  COUNT TRADING DAYS BETWEEN
# ------------------------------------
def count_trading_days_between(start_str, end_str):
    """
    Count how many trading days (Mon-Fri) lie between start_str and end_str,
    excluding any holiday date in HOLIDAYS_2025, which must be in '%d-%b-%Y' format.
    """
    try:
        start_date = datetime.strptime(start_str, "%d-%b-%Y").date()
        end_date   = datetime.strptime(end_str,   "%d-%b-%Y").date()
    except ValueError:
        return 0

    if start_date > end_date:
        return 0

    # Convert the list of holiday strings to date objects
    holiday_dates = set()
    for h_str in HOLIDAYS_2025:
        try:
            hd = datetime.strptime(h_str, "%d-%b-%Y").date()
            holiday_dates.add(hd)
        except ValueError:
            pass

    day_count = 0
    curr_date = start_date
    while curr_date <= end_date:
        # Monday=0 ... Sunday=6
        if curr_date.weekday() < 5 and curr_date not in holiday_dates:
            day_count += 1
        curr_date += timedelta(days=1)

    return day_count

# ------------------------------------
#  CALCULATE TIME TO EXPIRY
# ------------------------------------
def calculate_time_to_expiry(expiry_str):
    """
    1) If expiry is in 2025, T = (trading_days_left) / 247
    2) Return (T, fraction_str, days_left).
    """
    try:
        expiry_date = datetime.strptime(expiry_str, "%d-%b-%Y").date()
    except ValueError:
        return (0.0, "0/0", 0)

    # If you only handle year=2025 in this code:
    if expiry_date.year != 2025:
        return (0.0, "0/0", 0)

    today_str = datetime.today().strftime("%d-%b-%Y")
    days_left = count_trading_days_between(today_str, expiry_str)

    frac_str = f"{days_left}/{TOTAL_TRADING_DAYS_2025}"
    if TOTAL_TRADING_DAYS_2025 <= 0:
        return (0.0, frac_str, days_left)

    T = days_left / TOTAL_TRADING_DAYS_2025
    return (T, frac_str, days_left)

# ------------------------------------
#  NEAREST IV IF MISSING
# ------------------------------------
def get_nearest_iv(strike_price, option_chain, option_type):
    sorted_by_strike = sorted(option_chain, key=lambda x: abs(x["strikePrice"] - strike_price))
    for c in sorted_by_strike:
        if option_type in c and "impliedVolatility" in c[option_type]:
            try:
                iv_perc = float(c[option_type]["impliedVolatility"])
                if iv_perc > 0:
                    return iv_perc / 100.0, c["strikePrice"]
            except (ValueError, TypeError):
                continue
    return None, None

# ------------------------------------
#  DETERMINE N STEPS
# ------------------------------------
def determine_n_steps(IV, T, is_index=True, is_atm=True):
    if IV > 0.4:
        return [10, 20, 50, 100, 200, 400]
    elif IV > 0.2:
        return [10, 20, 50, 100, 200]
    elif IV > 0.1:
        return [4, 10, 20, 40, 100]
    elif T < 0.05:
        return [4, 10, 20, 40, 50]
    elif is_index and is_atm:
        return [10, 20, 50, 100, 200]
    else:
        return [4, 10, 20, 40]

# ------------------------------------
#  SINGLE-STEP TRINOMIAL
# ------------------------------------
def trinomial_single_step(S, K, r, sigma, T, q, n, option_type):
    dt = T / n if n else 0
    if dt <= 0:
        return {
            "n": n,
            "dt": 0,
            "u": 1,
            "d": 1,
            "m": 1,
            "p_u": 0,
            "p_m": 0,
            "p_d": 0,
            "discount_factor": 1,
            "price_up": S,
            "price_mid": S,
            "price_down": S,
            "payoff_up": 0,
            "payoff_mid": 0,
            "payoff_down": 0,
            "option_value_at_root": 0
        }

    u = math.exp(sigma * math.sqrt(2.0 * dt))
    d = 1.0 / u
    m = 1.0

    exp_rq = math.exp((r - q) * dt)
    numerator = exp_rq - d
    denominator = (u - d)

    if abs(denominator) < 1e-14:
        p_u = p_m = p_d = 1.0 / 3.0
    else:
        frac = numerator / denominator
        p_u = 0.5 * (frac**2)
        p_m = 1.0 - (frac**2)
        p_d = 1.0 - p_u - p_m

    discount_factor = math.exp(-r * dt)

    price_up   = S * u
    price_mid  = S * m
    price_down = S * d

    if option_type == "CE":
        payoff_up   = max(0.0, price_up - K)
        payoff_mid  = max(0.0, price_mid - K)
        payoff_down = max(0.0, price_down - K)
    else:  # "PE" => Put
        payoff_up   = max(0.0, K - price_up)
        payoff_mid  = max(0.0, K - price_mid)
        payoff_down = max(0.0, K - price_down)

    option_value_root = discount_factor * (
        p_u * payoff_up + p_m * payoff_mid + p_d * payoff_down
    )

    return {
        "n": n,
        "dt": dt,
        "u": u,
        "d": d,
        "m": m,
        "p_u": p_u,
        "p_m": p_m,
        "p_d": p_d,
        "discount_factor": discount_factor,
        "price_up": price_up,
        "price_mid": price_mid,
        "price_down": price_down,
        "payoff_up": payoff_up,
        "payoff_mid": payoff_mid,
        "payoff_down": payoff_down,
        "option_value_at_root": option_value_root
    }

# ------------------------------------
#  MULTI-STEP WRAPPER
# ------------------------------------
def trinomial_tree_price(S, K, r, sigma, T, q, n_values, option_type):
    if sigma <= 0:
        sigma = 0.01
    results = []
    for n in n_values:
        step_result = trinomial_single_step(S, K, r, sigma, T, q, n, option_type)
        results.append(step_result)
    return results

# ------------------------------------
#  FLASK ENDPOINT
# ------------------------------------
@app.route('/fetch_nse_option_chain', methods=['GET'])
def fetch_and_calculate():
    """
    Main endpoint. For every contract:
     1) compute trading_days_left (excluding weekends + the updated 2025 holiday list),
     2) T = trading_days_left / 247,
     3) do nearest IV, multi-step trinomial, etc.
    """
    symbol = request.args.get("symbol", "NIFTY")
    chain_data = fetch_option_chain(symbol)
    if isinstance(chain_data, dict) and "error" in chain_data:
        return jsonify(chain_data)

    all_results = []

    for contract in chain_data:
        expiry_str   = contract.get("expiryDate", "")
        strike_price = contract.get("strikePrice", 0)

        for opt_type in ["CE", "PE"]:
            if opt_type not in contract:
                continue

            opt_data = contract[opt_type]
            S = opt_data.get("underlyingValue", 0.0)

            # 1) T from (days_left / 247)
            T, fraction_str, days_left = calculate_time_to_expiry(expiry_str)

            # 2) raw calendar days (ignoring weekends) if you want to see difference
            raw_days_left = 0
            try:
                expiry_d = datetime.strptime(expiry_str, "%d-%b-%Y").date()
                raw_days_left = (expiry_d - datetime.today().date()).days
                if raw_days_left < 0:
                    raw_days_left = 0
            except ValueError:
                pass

            # 3) IV
            iv_raw = opt_data.get("impliedVolatility", 0)
            try:
                iv_raw = float(iv_raw)
            except (ValueError, TypeError):
                iv_raw = 0.0

            used_iv_status = "REAL"
            if iv_raw <= 0:
                nearest_iv, nearest_strike = get_nearest_iv(strike_price, chain_data, opt_type)
                if nearest_iv is not None:
                    iv_raw = nearest_iv * 100
                    used_iv_status = "FAKE"

            iv_decimal = iv_raw / 100.0 if iv_raw > 0 else 0.01

            # 4) n steps
            n_list = determine_n_steps(iv_decimal, T, True, True)

            # 5) Trinomial multi-step
            pricing_results = trinomial_tree_price(
                S=S,
                K=strike_price,
                r=MANUAL_RISK_FREE_RATE,
                sigma=iv_decimal,
                T=T,
                q=MANUAL_DIVIDEND_YIELD,
                n_values=n_list,
                option_type=opt_type
            )

            # Build final object
            expiry_with_t = f"{expiry_str} / T={T:.6f} (fraction={fraction_str})"
            iv_formatted  = f"{iv_raw:.2f} / {iv_decimal:.4f} / {used_iv_status}"

            contract_obj = {
                "symbol": symbol.upper(),
                "option_type": opt_type,
                "expiry_date": expiry_with_t,
                "raw_days_to_expiry": raw_days_left,
                "days_to_expiry": days_left,  # trading days left
                "day_fraction_excluding_weekends_holidays": fraction_str,
                "K": strike_price,
                "S": S,
                "IV": iv_formatted,
                "r": MANUAL_RISK_FREE_RATE,
                "q": MANUAL_DIVIDEND_YIELD,
                "pricing_steps": []
            }

            for step_calc in pricing_results:
                contract_obj["pricing_steps"].append({
                    "n": step_calc["n"],
                    "dt": step_calc["dt"],
                    "u": step_calc["u"],
                    "d": step_calc["d"],
                    "m": step_calc["m"],
                    "p_u": step_calc["p_u"],
                    "p_m": step_calc["p_m"],
                    "p_d": step_calc["p_d"],
                    "discount_factor": step_calc["discount_factor"],
                    "price_up": step_calc["price_up"],
                    "price_mid": step_calc["price_mid"],
                    "price_down": step_calc["price_down"],
                    "payoff_up": step_calc["payoff_up"],
                    "payoff_mid": step_calc["payoff_mid"],
                    "payoff_down": step_calc["payoff_down"],
                    "option_value_at_root": step_calc["option_value_at_root"]
                })

            all_results.append(contract_obj)

    return jsonify(all_results)

# ------------------------------------
#  RUN THE FLASK APP
# ------------------------------------
if __name__ == '__main__':
    print(f"Using updated 2025 holiday list (no Sunday duplicates).")
    print(f"TOTAL_TRADING_DAYS_2025 = {TOTAL_TRADING_DAYS_2025}")
    app.run(debug=True, port=5000)
