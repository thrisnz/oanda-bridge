from flask import Flask, request
import requests
import os

app = Flask(__name__)

ACCOUNT = os.environ.get("ACCOUNT")
API_KEY = os.environ.get("API_KEY")
SECRET = os.environ.get("SECRET")

BASE_URL = "https://api-fxtrade.oanda.com/v3"


# ✅ GET NET POSITION (correct sign handling)
def get_position(instrument):
    r = requests.get(
        f"{BASE_URL}/accounts/{ACCOUNT}/openPositions",
        headers={"Authorization": f"Bearer {API_KEY}"}
    )

    data = r.json()

    for pos in data.get("positions", []):
        if pos["instrument"] == instrument:
            long_units = float(pos["long"]["units"])
            short_units = float(pos["short"]["units"])

            # 🔥 FIX: ADD, not subtract
            return long_units + short_units

    return 0.0


# ✅ SEND ORDER
def send_order(units, instrument):
    print("SENDING:", units, instrument, flush=True)

    r = requests.post(
        f"{BASE_URL}/accounts/{ACCOUNT}/orders",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "order": {
                "instrument": instrument,
                "units": str(int(units)),  # OANDA expects string int
                "type": "MARKET",
                "positionFill": "DEFAULT"
            }
        }
    )

    print("OANDA:", r.status_code, r.text, flush=True)


# ✅ WEBHOOK
@app.route("/", methods=["POST"])
def webhook():
    data = request.get_json(force=True)

    print("\n=== NEW SIGNAL ===", flush=True)
    print(data, flush=True)

    if data.get("key") != SECRET:
        return "unauthorized", 403

    action = data["action"].lower()
    size = float(data["size"])
    instrument = data["ticker"]

    # 🎯 CURRENT POSITION
    current = get_position(instrument)

    # 🎯 TARGET POSITION (your intent)
    if action == "buy":
        target = abs(size)
    elif action == "sell":
        target = -abs(size)
    else:
        return "bad action", 400

    # 🎯 WHAT WE NEED TO SEND
    units = target - current

    print("CURRENT:", current, flush=True)
    print("TARGET:", target, flush=True)
    print("DELTA:", units, flush=True)

    # 🚫 avoid zero orders
    if int(units) != 0:
        send_order(units, instrument)
    else:
        print("NO TRADE", flush=True)

    return "ok"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
