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
    print("RAW POSITIONS:", data)

    for pos in data.get("positions", []):
        print("CHECKING:", pos["instrument"])
        if pos["instrument"] == instrument:
            long_units = float(pos["long"]["units"])
            short_units = float(pos["short"]["units"])
            current = long_units - short_units
            print("MATCHED POSITION:", current)
            return current

    print("NO MATCH → POSITION = 0")
    return 0.0


def send_order(units, instrument):
    print("SENDING ORDER:", units, instrument)

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
    data = request.get_json(force=True)

    print("\n====== NEW REQUEST ======")
    print("INCOMING:", data)

    if not data:
        return "bad request", 400

    if data.get("key") != SECRET:
        print("AUTH FAILED")
        return "unauthorized", 403

    action = str(data["action"]).lower().strip()
    size = float(data["size"])
    instrument = data["ticker"]

    print("ACTION:", action)
    print("SIZE:", size)
    print("INSTRUMENT:", instrument)

    # 🔥 STEP 1 — WHAT DO WE HAVE
    current = get_position(instrument)

    # 🔥 STEP 2 — WHAT DO WE WANT (NO AMBIGUITY)
    if action == "buy":
        target = abs(size)       # always long
    elif action == "sell":
        target = -abs(size)      # always short
    else:
        print("INVALID ACTION")
        return "error", 400

    # 🔥 STEP 3 — WHAT DO WE SEND
    units = target - current

    print("CURRENT:", current)
    print("TARGET:", target)
    print("UNITS TO SEND:", units)

    # 🔥 STEP 4 — EXECUTE
    if abs(units) > 0:
        send_order(int(units), instrument)
    else:
        print("NO TRADE NEEDED")

    return "ok"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
