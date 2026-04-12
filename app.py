from flask import Flask, request
import requests, os, time

app = Flask(__name__)

ACCOUNT = os.environ.get("ACCOUNT")
API_KEY = os.environ.get("API_KEY")
SECRET = os.environ.get("SECRET")
BASE_URL = "https://api-fxtrade.oanda.com/v3"

ADD_THRESHOLD = 0.05   # 5% DD trigger
MIN_UNITS = 1


# ---------- HELPERS ----------

def parse_float(v):
    try:
        return None if v in ("", None) else float(v)
    except:
        return None


def get_position(inst):
    try:
        r = requests.get(
            f"{BASE_URL}/accounts/{ACCOUNT}/openPositions",
            headers={"Authorization": f"Bearer {API_KEY}"},
            timeout=5
        )

        if r.status_code != 200:
            print("POSITION ERROR:", r.text, flush=True)
            return 0.0

        for p in r.json().get("positions", []):
            if p["instrument"] == inst:
                return float(p["long"]["units"]) + float(p["short"]["units"])

    except Exception as e:
        print("POSITION EXCEPTION:", e, flush=True)

    return 0.0


def get_instrument_dd(inst):
    try:
        r = requests.get(
            f"{BASE_URL}/accounts/{ACCOUNT}/openTrades",
            headers={"Authorization": f"Bearer {API_KEY}"},
            timeout=5
        )

        if r.status_code != 200:
            print("DD ERROR:", r.text, flush=True)
            return 0, 0

        trades = r.json().get("trades", [])
        trades = [t for t in trades if t["instrument"] == inst]

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


def close_all_positions(inst):
    print("CLOSING ALL:", inst, flush=True)

    try:
        r = requests.put(
            f"{BASE_URL}/accounts/{ACCOUNT}/positions/{inst}/close",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "longUnits": "ALL",
                "shortUnits": "ALL"
            },
            timeout=5
        )

        print("CLOSE:", r.status_code, r.text, flush=True)

    except Exception as e:
        print("CLOSE ERROR:", e, flush=True)


def send_order(units, inst, sl=None, tp=None):
    print("SENDING:", units, inst, flush=True)

    try:
        r = requests.post(
            f"{BASE_URL}/accounts/{ACCOUNT}/orders",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "order": {
                    "instrument": inst,
                    "units": str(int(units)),
                    "type": "MARKET",
                    "positionFill": "OPEN_ONLY"
                }
            },
            timeout=5
        )

        print("OANDA:", r.status_code, r.text, flush=True)

        if r.status_code != 201:
            return

        fill = r.json().get("orderFillTransaction", {})
        price = float(fill["price"])
        trade_id = fill["tradeOpened"]["tradeID"]

        payload = {}

        # SL
        if sl is not None:
            sl_price = price - sl if units > 0 else price + sl
            payload["stopLoss"] = {"price": str(round(sl_price, 3))}
            print("SL:", sl_price, flush=True)

        # TP
        if tp is not None:
            tp_price = price + tp if units > 0 else price - tp
            payload["takeProfit"] = {"price": str(round(tp_price, 3))}
            print("TP:", tp_price, flush=True)

        if payload:
            r2 = requests.put(
                f"{BASE_URL}/accounts/{ACCOUNT}/trades/{trade_id}/orders",
                headers={
                    "Authorization": f"Bearer {API_KEY}",
                    "Content-Type": "application/json"
                },
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

    # 🔥 FLIP LOGIC
    if (action == "buy" and cur < 0) or (action == "sell" and cur > 0):
        print("FLIP DETECTED", flush=True)

        close_all_positions(inst)
        time.sleep(0.3)

        units = abs(size) if action == "buy" else -abs(size)

        if abs(units) >= MIN_UNITS:
            send_order(units, inst, sl, tp)

        return "flip"

    # 🔥 DD STACKING LOGIC
    unrealized, dd = get_instrument_dd(inst)

    allow_add = False

    if unrealized == 0:
        allow_add = True
    elif unrealized < 0 and dd >= ADD_THRESHOLD:
        allow_add = True

    print("ALLOW ADD:", allow_add, flush=True)

    if not allow_add:
        return "skip"

    # 🔥 EXECUTE NEW TRADE
    if action == "buy":
        units = abs(size)
    elif action == "sell":
        units = -abs(size)
    else:
        return "bad action", 400

    if abs(units) >= MIN_UNITS:
        send_order(units, inst, sl, tp)

    return "ok"


# ---------- ANALYZE ----------

@app.route("/analyze", methods=["GET"])
def analyze():
    import subprocess
    r = subprocess.run(["python", "analyzer.py"], capture_output=True, text=True)
    return f"<pre>{r.stdout}</pre>"


# ---------- RUN ----------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
