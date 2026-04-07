from flask import Flask, request
import requests
import os

app = Flask(__name__)

ACCOUNT = os.environ.get("ACCOUNT")
API_KEY = os.environ.get("API_KEY")
SECRET = os.environ.get("SECRET")

BASE_URL = "https://api-fxtrade.oanda.com/v3"


# =========================
# 🔧 UTIL
# =========================
def parse_float(value):
    try:
        if value == "" or value is None:
            return None
        return float(value)
    except:
        return None


# =========================
# 📊 GET POSITION
# =========================
def get_position(instrument):
    try:
        r = requests.get(
            f"{BASE_URL}/accounts/{ACCOUNT}/openPositions",
            headers={"Authorization": f"Bearer {API_KEY}"},
            timeout=5
        )

        if r.status_code != 200:
            print("POSITION ERROR:", r.text, flush=True)
            return 0.0

        data = r.json()

        for pos in data.get("positions", []):
            if pos["instrument"] == instrument:
                long_units = float(pos["long"]["units"])
                short_units = float(pos["short"]["units"])

                return long_units + short_units

    except Exception as e:
        print("POSITION EXCEPTION:", e, flush=True)

    return 0.0


# =========================
# 💰 GET PRICE
# =========================
def get_price(instrument):
    try:
        r = requests.get(
            f"{BASE_URL}/accounts/{ACCOUNT}/pricing",
            headers={"Authorization": f"Bearer {API_KEY}"},
            params={"instruments": instrument},
            timeout=5
        )

        data = r.json()
        price = float(data["prices"][0]["bids"][0]["price"])
        return price

    except Exception as e:
        print("PRICE ERROR:", e, flush=True)
        return None


# =========================
# 🚀 SEND ORDER (WITH SL)
# =========================
def send_order(units, instrument, sl_distance=None):
    print("SENDING:", units, instrument, flush=True)

    order = {
        "instrument": instrument,
        "units": str(int(units)),
        "type": "MARKET",
        "positionFill": "DEFAULT"
    }

    # 🔥 OPTIONAL SL
    if sl_distance is not None:
        price = get_price(instrument)

        if price is not None:
            if units > 0:
                sl_price = price - sl_distance
            else:
                sl_price = price + sl_distance

            order["stopLossOnFill"] = {
                "price": str(round(sl_price, 3))
            }

            print("SL DIST:", sl_distance, flush=True)
            print("SL PRICE:", sl_price, flush=True)
        else:
            print("SKIP SL (no price)", flush=True)

    try:
        r = requests.post(
            f"{BASE_URL}/accounts/{ACCOUNT}/orders",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json"
            },
            json={"order": order},
            timeout=5
        )

        print("OANDA:", r.status_code, r.text, flush=True)

    except Exception as e:
        print("ORDER ERROR:", e, flush=True)


# =========================
# 📡 WEBHOOK
# =========================
@app.route("/", methods=["POST"])
def webhook():
    data = request.get_json(force=True)

    print("\n=== NEW SIGNAL ===", flush=True)
    print(data, flush=True)

    if data.get("key") != SECRET:
        return "unauthorized", 403

    action = data["action"].lower()
    size = float(data["size"])
    instrument = data["ticker"].upper()

    # 🔥 OPTIONAL SL
    sl_distance = parse_float(data.get("sl"))

    print("SL:", sl_distance, flush=True)

    # 🎯 CURRENT POSITION
    current = get_position(instrument)

    # 🎯 TARGET POSITION
    if action == "buy":
        target = abs(size)
    elif action == "sell":
        target = -abs(size)
    else:
        return "bad action", 400

    # 🎯 DELTA
    units = target - current

    print("CURRENT:", current, flush=True)
    print("TARGET:", target, flush=True)
    print("DELTA:", units, flush=True)

    # 🚫 NO ZERO ORDERS
    if int(units) != 0:
        send_order(units, instrument, sl_distance)
    else:
        print("NO TRADE", flush=True)

    return "ok"


# =========================
# ▶ RUN
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
