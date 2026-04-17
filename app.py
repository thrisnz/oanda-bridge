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
            return None  # 🔥 DO NOT assume 0

        for p in r.json().get("positions", []):
            if p["instrument"] == inst:
                return float(p["long"]["units"]) + float(p["short"]["units"])

        return 0.0  # no position

    except Exception as e:
        print("POSITION EXCEPTION:", e, flush=True)
        return None


def get_instrument_dd(inst):
    try:
        r = requests.get(
            f"{BASE_URL}/accounts/{ACCOUNT}/openTrades",
            headers=headers(),
            timeout=5
        )

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

        print(f"PL={unrealized} MARGIN={margin} DD={dd:.4f}", flush=True)

        return unrealized, dd

    except Exception as e:
        print("DD EXCEPTION:", e, flush=True)
        return 0, 0


# ---------- ORDER ----------

def send_order(units, inst, sl=None, tp=None):
    print("SENDING:", units, inst, flush=True)

    try:
        r = requests.post(
            f"{BASE_URL}/accounts/{ACCOUNT}/orders",
            headers=headers(),
            json={
                "order": {
                    "instrument": inst,
                    "units": str(int(units)),  # WTICO safe
                    "type": "MARKET",
                    "timeInForce": "FOK",
                    "positionFill": "REDUCE_FIRST"
                }
            },
            timeout=5
        )

        print("OANDA:", r.status_code, r.text, flush=True)

        if r.status_code != 201:
            return False

        fill = r.json().get("orderFillTransaction")
        if not fill:
            return False

        trade_opened = fill.get("tradeOpened")

        if not trade_opened:
            print("REDUCE ONLY", flush=True)
            return True

        price = float(fill["price"])
        trade_id = trade_opened["tradeID"]

        payload = {}

        if sl is not None:
            sl_price = price - sl if units > 0 else price + sl
            payload["stopLoss"] = {"price": str(round(sl_price, 3))}

        if tp is not None:
            tp_price = price + tp if units > 0 else price - tp
            payload["takeProfit"] = {"price": str(round(tp_price, 3))}

        if payload:
            requests.put(
                f"{BASE_URL}/accounts/{ACCOUNT}/trades/{trade_id}/orders",
                headers=headers(),
                json=payload,
                timeout=5
            )

        return True

    except Exception as e:
        print("ORDER ERROR:", e, flush=True)
        return False


# ---------- WEBHOOK ----------

@app.route("/", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True) or {}
    print("\n=== NEW SIGNAL ===", data, flush=True)

    # ignore keep-alive junk
    if not data or "key" not in data:
        return "ignored"

    if data.get("key") != SECRET:
        return "unauthorized", 403

    try:
        action = data["action"].lower()
        size = float(data["size"])
        inst = data["ticker"].upper()
    except:
        return "bad payload", 400

    sl = parse_float(data.get("sl"))
    tp = parse_float(data.get("tp"))

    cur = get_position(inst)

    if cur is None:
        print("POSITION UNKNOWN - ABORT", flush=True)
        return "fail"

    desired = abs(size) if action == "buy" else -abs(size)
    delta = desired - cur

    print(f"CURRENT={cur} TARGET={desired} DELTA={delta}", flush=True)

    if abs(delta) < MIN_UNITS:
        print("NO CHANGE", flush=True)
        return "skip"

    # ---------- DD FILTER ----------

    unrealized, dd = get_instrument_dd(inst)

    allow = False

    if cur == 0:
        allow = True
    elif unrealized < 0 and dd >= ADD_THRESHOLD:
        allow = True
    elif (action == "buy" and cur < 0) or (action == "sell" and cur > 0):
        allow = True

    print("ALLOW:", allow, flush=True)

    if not allow:
        return "skip"

    # ---------- EXECUTION (BULLETPROOF) ----------

    flip = (cur < 0 and desired > 0) or (cur > 0 and desired < 0)

    if flip:
        print("FLIP DETECTED", flush=True)

        # try fast flip
        if not send_order(delta, inst, sl, tp):
            print("FALLBACK: CLOSE THEN OPEN", flush=True)

            if abs(cur) > 0:
                if not send_order(-cur, inst):
                    print("CLOSE FAILED", flush=True)
                    return "fail"

            if not send_order(desired, inst, sl, tp):
                print("OPEN FAILED", flush=True)
                return "fail"

    else:
        if not send_order(delta, inst, sl, tp):
            return "fail"

    return "ok"


# ---------- RUN ----------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
