from flask import Flask, request
import requests
import os

app = Flask(__name__)

ACCOUNT = os.environ.get("ACCOUNT")
API_KEY = os.environ.get("API_KEY")

def get_position():
    r = requests.get(
        f"https://api-fxpractice.oanda.com/v3/accounts/{ACCOUNT}/openPositions",
        headers={"Authorization": f"Bearer {API_KEY}"}
    )
    data = r.json()
    if not data["positions"]:
        return 0
    pos = data["positions"][0]
    return int(pos["long"]["units"]) - int(pos["short"]["units"])

def send_order(units):
    requests.post(
        f"https://api-fxpractice.oanda.com/v3/accounts/{ACCOUNT}/orders",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "order": {
                "instrument": "LTC_USD",
                "units": str(units),
                "type": "MARKET",
                "positionFill": "DEFAULT"
            }
        }
    )

@app.route("/", methods=["POST"])
def webhook():
    data = request.json

    if data.get("key") != "abc123":
        return "unauthorized", 403

    action = data["action"]
    size = int(data["size"])

    current = get_position()

    if action == "buy":
        if current < 0:
            send_order(abs(current))
        send_order(size)

    if action == "sell":
        if current > 0:
            send_order(-abs(current))
        send_order(-size)

    return "ok"

#app.run()
import os
port = int(os.environ.get("PORT", 10000))
app.run(host="0.0.0.0", port=port)
