from flask import Flask, request
import requests
import os

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

    for pos in data.get("positions", []):
        if pos["instrument"] == instrument:
            long_units = float(pos["long"]["units"])
            short_units = float(pos["short"]["units"])
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
    # force JSON parse (TradingView quirk)
    data = request.get_json(force=True)

    print("INCOMING:", data)

    if not data:
        return "bad request", 400

    if data.get("key") != SECRET:
        print("AUTH FAILED")
        return "unauthorized", 403

    action = data["action"].lower()
    size = float(data["size"])
    instrument = data["ticker"]

    # 🔥 WHAT DO WE HAVE
    current_position = get_position(instrument)

    # 🔥 WHAT DO WE WANT
    if action == "buy":
        desired_position = size
    elif action == "sell":
        desired_position = -size
    else:
        print("INVALID ACTION:", action)
        return "error", 400

    # 🔥 WHAT DO WE NEED TO SEND
    units_to_send = desired_position - current_position

    print("HAVE:", current_position)
    print("WANT:", desired_position)
    print("SEND:", units_to_send)

    # send only if needed
    if abs(units_to_send) > 0:
        send_order(int(units_to_send), instrument)

    return "ok"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
