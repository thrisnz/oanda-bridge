from flask import Flask, request
import requests
import os

app = Flask(__name__)

ACCOUNT = os.environ.get("ACCOUNT")
API_KEY = os.environ.get("API_KEY")

# LIVE OANDA endpoint (not practice)
BASE_URL = "https://api-fxtrade.oanda.com/v3"


def get_position(instrument):
    r = requests.get(
        f"{BASE_URL}/accounts/{ACCOUNT}/openPositions",
        headers={"Authorization": f"Bearer {API_KEY}"}
    )

    data = r.json()

    for pos in data.get("positions", []):
        if pos["instrument"] == instrument:
            long_units = int(pos["long"]["units"])
            short_units = int(pos["short"]["units"])
            return long_units - short_units

    return 0


def send_order(units, instrument):
    requests.post(
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


@app.route("/", methods=["POST"])
def webhook():
    data = request.json

    # simple auth
    if data.get("key") != "g7Kp!92xLq":
        return "unauthorized", 403

    action = data["action"]
    size = int(data["size"])
    instrument = data["ticker"]  # pure pass-through

    current = get_position(instrument)

    if action == "buy":
        if current < 0:
            send_order(abs(current), instrument)  # close short
        send_order(size, instrument)  # open long

    elif action == "sell":
        if current > 0:
            send_order(-abs(current), instrument)  # close long
        send_order(-size, instrument)  # open short

    return "ok"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
