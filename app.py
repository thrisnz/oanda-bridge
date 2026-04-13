from flask import Flask, request
import requests, os

app = Flask(__name__)

ACCOUNT = os.environ.get("ACCOUNT")
API_KEY = os.environ.get("API_KEY")
SECRET = os.environ.get("SECRET")
BASE_URL = "https://api-fxtrade.oanda.com/v3"

ADD_THRESHOLD = 0.05
MIN_UNITS = 1


# ---------- HELPERS ----------

def headers():
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }


def parse_float(v):
    try:
        return None if v in ("", None) else float(v)
    except:
        return None


def get_position(inst):
    try:
        r = requests.get(
            f"{BASE_URL}/accounts/{ACCOUNT}/openPositions",
            headers=headers(),
            timeout=5
        )

        if r.status_code != 200:
            print("POSITION ERROR:", r.text, flush=True)
            return 0.0

        for p in r.json().get("positions", []):
            if p["instrument"] == inst:
                long_units = float(p["long"]["units"])
                short_units = float(p["short"]["units"])
                return long_units + short_units

    except Exception as e:
        print("POSITION EXCEPTION:", e, flush=True)

    return 0.0


def get_instrument_dd(inst):
    try:
        r = requests.get(
            f"{BASE_URL}/accounts/{ACCOUNT}/openTrades",
            headers=headers(),
            timeout=5
        )

        if r.status_code != 200:
            print("DD ERROR:", r.text, flush=True)
            return 0, 0

        trades = [t for t in r.json().get("trades", []) if t["instrument"] == inst]

        if not trades:
            return 0, 0

        unrealized = sum(float(t["unrealizedPL"]) for t in trades)
        margin = sum(float(t["marginUsed"]) for t in trades)

        if margin == 0:
            return 0, 0

        dd = abs(unrealized) / margin

        print("DD:", dd, "PL:", unrealized, flush=True)

        return unrealized, dd

    except Exception as e:
        print("DD EXCEPTION:", e, flush=True)
        return 0, 0


# ---------- ORDER ----------

def send_delta_order(delta, inst, sl=None, tp=None):
    print("SENDING DELTA:", delta, inst, flush=True)

    try:
        r = requests.post(
            f"{BASE_URL}/accounts/{ACCOUNT}/orders",
            headers=headers(),
            json={
                "order": {
                    "instrument": inst,
                    "units": str(int(delta)),
                    "type": "MARKET",
                    "positionFill": "DEFAULT"
                }
            },
            timeout=5
        )

        print("OANDA:", r.status_code, r.text, flush=True)

        if r.status_code != 201:
            return

        data = r.json()

        fill = data.get("orderFillTransaction")
        if not fill:
            print("NO FILL (likely rejected or canceled)", flush=True)
            return

        price = float(fill.get("price", 0))
        trade_opened = fill.get("tradeOpened")

        if not trade_opened:
            print("NO TRADE OPENED (pure close or net reduce)", flush=True)
            return

        trade_id = trade_opened["tradeID"]

        payload = {}

        # SL
        if sl is not None:
            sl_price = price - sl if delta > 0 else price + sl
            payload["stopLoss"] = {"price": str(round(sl_price, 3))}
            print("SL:", sl_price, flush=True)

        # TP
        if tp is not None:
            tp_price = price + tp if delta > 0 else price - tp
            payload["takeProfit"] = {"price": str(round(tp_price, 3))}
            print("TP:", tp_price, flush=True)

        if payload:
            r2 = requests.put(
                f"{BASE_URL}/accounts/{ACCOUNT}/trades/{trade_id}/orders",
                headers=headers(),
                json=payload,
                timeout=5
            )

            print("ATTACH:", r2.status_code, r2.text, flush=True)

    except Exception as e:
        print("ORDER ERROR:", e, flush=True)


# ---------- WEBHOOK ----------

@app.route("/", methods=["POST"])
def webhook():
    data = request.get_json(force=True)
    print("\n=== NEW SIGNAL ===", data, flush=True)

    if data.get("key") != SECRET:
        return "unauthorized", 403

    action = data["action"].lower()
    size = float(data["size"])
    inst = data["ticker"].upper()

    sl = parse_float(data.get("sl"))
    tp = parse_float(data.get("tp"))

    cur = get_position(inst)

    # 🎯 TARGET POSITION
    target = size if action == "buy" else -size

    # 🎯 DELTA (THIS FIXES EVERYTHING)
    delta = target - cur

    print("CURRENT:", cur, "TARGET:", target, "DELTA:", delta, flush=True)

    if abs(delta) < MIN_UNITS:
        print("NO CHANGE NEEDED", flush=True)
        return "skip"

    # 🔥 DD CONTROL
    unrealized, dd = get_instrument_dd(inst)

    allow_add = False

    if cur == 0:
        allow_add = True
    elif unrealized < 0 and dd >= ADD_THRESHOLD:
        allow_add = True
    elif (action == "buy" and cur < 0) or (action == "sell" and cur > 0):
        # always allow flip
        allow_add = True

    print("ALLOW:", allow_add, flush=True)

    if not allow_add:
        return "skip"

    send_delta_order(delta, inst, sl, tp)

    return "ok"


# ---------- RUN ----------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
