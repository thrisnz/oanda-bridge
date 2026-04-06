from flask import Flask, request
import requests
import os

app = Flask(__name__)

ACCOUNT = os.environ.get("ACCOUNT")
API_KEY = os.environ.get("API_KEY")
SECRET = os.environ.get("SECRET")

# LIVE OANDA endpoint
BASE_URL = "https://api-fxtrade.oanda.com/v3"


def get_position(instrument):
    r = requests.get(
        f"{BASE_URL}/accounts/{ACCOUNT}/openPositions",
        headers={"Authorization": f"Bearer {API_KEY}"}
    )

    data = r.json()

    for pos in data.get("positions", []):
        if pos["instrument"] == instrument:
            long_units = float(pos["long"]["units"])   # ✅ FIXED
            short_units = float(pos["short"]["units"]) # ✅ FIXED
            return long_units - short_units

    return 0.0


def send_order(units, instrument):
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

    print("OANDA RESPONSE:", r.status_code, r.text)


@app.route("/", methods=["POST"])
def webhook():
    data = request.json

    print("INCOMING:", data)

    # auth check
    if data.get("key") != SECRET:
        print("AUTH FAILED:", data.get("key"), "vs", SECRET)
        return "unauthorized", 403

    action = data["action"]
    size = float(data["size"])   # allow flexibility
    instrument = data["ticker"]

    current = get_position(instrument)
    print("CURRENT POSITION:", current)

    # 🎯 TARGET POSITION LOGIC (clean + universal)
    target = size if action == "buy" else -size
    delta = target - current

    print("TARGET:", target, "DELTA:", delta)

    # avoid sending zero orders
    if abs(delta) > 0:
        send_order(int(delta), instrument)

    return "ok"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
