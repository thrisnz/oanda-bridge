# ============================================
# NOTE TO FUTURE SELF:
#
# HYBRID MODEL:
# - FLIPS use TARGET model (clean reversal)
# - ADDS use INCREMENTAL model (scaling)
#
# This avoids:
# - "DELTA = 0" problem
# - missed adds
# - flip blocking due to DD
#
# ============================================

from flask import Flask, request
import requests, os

app = Flask(__name__)

ACCOUNT = os.environ.get("ACCOUNT")
API_KEY = os.environ.get("API_KEY")
SECRET = os.environ.get("SECRET")
BASE_URL = "https://api-fxtrade.oanda.com/v3"

ADD_THRESHOLD = 0.03
MIN_UNITS = 1


# ---------- HELPERS ----------

def headers():
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }


def get_position(inst):
    try:
        r = requests.get(f"{BASE_URL}/accounts/{ACCOUNT}/openPositions", headers=headers(), timeout=5)

        if r.status_code != 200:
            return None

        for p in r.json().get("positions", []):
            if p["instrument"] == inst:
                return float(p["long"]["units"]) + float(p["short"]["units"])

        return 0.0

    except:
        return None


def get_instrument_dd(inst):
    try:
        r = requests.get(f"{BASE_URL}/accounts/{ACCOUNT}/openTrades", headers=headers(), timeout=5)

        if r.status_code != 200:
            return 0, 0

        trades = [t for t in r.json().get("trades", []) if t["instrument"] == inst]

        if not trades:
            return 0, 0

        unrealized = sum(float(t["unrealizedPL"]) for t in trades)
        margin = sum(float(t["marginUsed"]) for t in trades)

        if margin == 0:
            return 0, 0

        dd = abs(unrealized) / margin
        return unrealized, dd

    except:
        return 0, 0


def send_order(units, inst):
    print("SENDING:", units, inst, flush=True)

    try:
        r = requests.post(
            f"{BASE_URL}/accounts/{ACCOUNT}/orders",
            headers=headers(),
            json={
                "order": {
                    "instrument": inst,
                    "units": str(int(units)),
                    "type": "MARKET",
                    "timeInForce": "FOK",
                    "positionFill": "REDUCE_FIRST"
                }
            },
            timeout=5
        )

        print("OANDA:", r.status_code, r.text, flush=True)
        return r.status_code == 201

    except Exception as e:
        print("ORDER ERROR:", e, flush=True)
        return False


# ---------- WEBHOOK ----------

@app.route("/", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True) or {}
    print("\n=== NEW SIGNAL ===", data, flush=True)

    if not data or "key" not in data:
        return "ignored"

    if data.get("key") != SECRET:
        return "unauthorized", 403

    action = data["action"].lower()
    size = float(data["size"])
    inst = data["ticker"].upper()

    cur = get_position(inst)

    if cur is None:
        print("POSITION UNKNOWN")
        return "fail"

    same_direction = (action == "buy" and cur > 0) or (action == "sell" and cur < 0)
    flip = (action == "buy" and cur < 0) or (action == "sell" and cur > 0)

    unrealized, dd = get_instrument_dd(inst)

    print(f"CURRENT={cur} DD={dd:.4f}", flush=True)

    # ---------- DECISION ----------

    if cur == 0:
        delta = size if action == "buy" else -size
        reason = "NEW ENTRY"

    elif flip:
        desired = abs(size) if action == "buy" else -abs(size)
        delta = desired - cur
        reason = "FLIP (TARGET MODE)"

    elif same_direction:
        if unrealized < 0 and dd >= ADD_THRESHOLD:
            delta = size if action == "buy" else -size
            reason = "ADD (INCREMENTAL)"
        else:
            print("ADD BLOCKED")
            return "skip"

    else:
        return "skip"

    if abs(delta) < MIN_UNITS:
        print("NO CHANGE")
        return "skip"

    print(f"EXECUTING DELTA={delta} | {reason}", flush=True)

    if not send_order(delta, inst):
        return "fail"

    return "ok"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
