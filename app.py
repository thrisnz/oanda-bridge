from flask import Flask, request
import requests
import os
import math

app = Flask(__name__)

ACCOUNT = os.environ.get("ACCOUNT")
API_KEY = os.environ.get("API_KEY")
SECRET = os.environ.get("SECRET")

BASE_URL = "https://api-fxtrade.oanda.com/v3"


def get_position(instrument):
    r = requests.get(
        f"{BASE_URL}/accounts/{ACCOUNT}/openPositions",
        headers={"Authorization": f"Bearer {API_KEY}"}
    )

    data = r.json()
    print("RAW POSITIONS:", data, flush=True)

    for pos in data.get("positions", []):
        if pos["instrument"] == instrument:
            long_units = float(pos["long"]["units"])
            short_units = float(pos["short"]["units"])
            current = long_units - short_units
            print("CURRENT POSITION:", current, flush=True)
            return current

    print("NO POSITION FOUND → 0", flush=True)
    return 0.0


def send_order(units, instrument):
    print("SENDING ORDER:", units, instrument, flush=True)

    r = requests.post(
        f"{BASE_URL}/accounts/{ACCOUNT}/orders",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "order": {
                "instrument": instrument,
                "units": str(units),
                "type": "MARKET",
                "positionFill": "DEFAULT"
            }
        }
    )

    print("OANDA RESPONSE:", r.status_code, r.text, flush=True)


@app.route("/", methods=["POST"])
def webhook():
    data = request.get_json(force=True)

    print("\n===== NEW WEBHOOK =====", flush=True)
    print("INCOMING:", data, flush=True)

    if not data:
        return "bad request", 400

    if data.get("key") != SECRET:
        print("AUTH FAILED", flush=True)
        return "unauthorized", 403

    action_raw = str(data["action"])
    action = action_raw.lower().strip()
    size = abs(float(data["size"]))  # always positive
    instrument = data["ticker"]

    print("ACTION RAW:", action_raw, flush=True)
    print("ACTION PARSED:", action, flush=True)
    print("SIZE:", size, flush=True)
    print("INSTRUMENT:", instrument, flush=True)

    # 🔥 CURRENT POSITION
    current = get_position(instrument)

    # 🔥 FORCE INTENDED DIRECTION (bulletproof)
    if "buy" in action:
        target = size          # long
    elif "sell" in action:
        target = -size         # short
    else:
        print("INVALID ACTION:", action, flush=True)
        return "error", 400

    # 🔥 CALCULATE REQUIRED CHANGE
    units = target - current

    print("TARGET POSITION:", target, flush=True)
    print("CURRENT POSITION:", current, flush=True)
    print("RAW UNITS NEEDED:", units, flush=True)

    # 🔥 FIX ROUNDING PROPERLY
    if units > 0:
        units_to_send = math.ceil(units)
    else:
        units_to_send = math.floor(units)

    print("FINAL UNITS SENT:", units_to_send, flush=True)

    # 🔥 EXECUTE
    if units_to_send != 0:
        send_order(units_to_send, instrument)
    else:
        print("NO TRADE NEEDED", flush=True)

    return "ok"


if __name__ == "__main__":
    print("VERSION FINAL LIVE", flush=True)
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
