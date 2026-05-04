# HYBRID_v1.1 + BE_v1.0 | DO NOT MODIFY PROTECTED ZONES WITHOUT READING NOTES

# NOTES:
# CORE MODEL:
# - FLIP = TARGET MODE (delta = desired - current)
# - ADD  = INCREMENTAL ONLY if losing AND DD >= threshold
# - NEW  = INCREMENTAL
#
# CRITICAL:
# - TP/SL MUST be attached AFTER fill (tradeID)
# - tradeOpened == None → reduce only → DO NOT attach TP
#
# DO NOT TOUCH:
# - send_order execution flow
# - TP/SL logic
# - delta logic
#
# VERIFY AFTER CHANGES:
# - flip works
# - add DD gating works
# - TP attaches
#
# BE NOTES:
# - ARM ≈ 0.08%
# - MIN_RUN ≈ 0.16%
# - RETRACE 25%
# - BE isolated from execution
# - disable via BE_ENABLED = False

from flask import Flask, request
import requests, os, threading, time

app = Flask(__name__)

ACCOUNT = os.environ.get("ACCOUNT")
API_KEY = os.environ.get("API_KEY")
SECRET = os.environ.get("SECRET")
BASE_URL = "https://api-fxtrade.oanda.com/v3"

ADD_THRESHOLD = 0.03
MIN_UNITS = 1

# ===== BE CONFIG =====
BE_ENABLED = True
ARM_TRIGGER = 0.0008
MIN_RUN = 0.0016
RETRACE = 0.25
OFFSET = 0.5

be_trades = {}
BE_INSTRUMENTS = set()

# ===== HELPERS (PROTECTED) =====

def headers():
    return {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

def get_position(inst):
    try:
        r = requests.get(f"{BASE_URL}/accounts/{ACCOUNT}/openPositions", headers=headers(), timeout=5)
        if r.status_code != 200:
            print("POSITION ERROR:", r.text, flush=True)
            return None
        for p in r.json().get("positions", []):
            if p["instrument"] == inst:
                return float(p["long"]["units"]) + float(p["short"]["units"])
        return 0.0
    except Exception as e:
        print("POSITION EXCEPTION:", e, flush=True)
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
    except Exception as e:
        print("DD EXCEPTION:", e, flush=True)
        return 0, 0

# ===== BREAKEVEN ENGINE =====

def register_be_trade(trade_id, units, price, inst):
    be_trades[trade_id] = {
        "inst": inst,
        "side": "buy" if units > 0 else "sell",
        "entry": price,
        "armed": False,
        "best": price,
        "done": False
    }
    BE_INSTRUMENTS.add(inst)

def update_sl(trade_id, price):
    try:
        requests.put(
            f"{BASE_URL}/accounts/{ACCOUNT}/trades/{trade_id}/orders",
            headers=headers(),
            json={"stopLoss": {"price": str(round(price, 3))}},
            timeout=5
        )
        print("[BE] SL →", price, flush=True)
    except Exception as e:
        print("[BE ERROR]", e, flush=True)

def process_be(trade_id, trade, price):
    if trade["done"]:
        return

    entry = trade["entry"]

    if trade["side"] == "buy":
        move = (price - entry) / entry

        if not trade["armed"] and move >= ARM_TRIGGER:
            trade["armed"] = True

        if trade["armed"]:
            trade["best"] = max(trade["best"], price)
            run = (trade["best"] - entry) / entry
            retrace = (trade["best"] - price) / entry

            if run >= MIN_RUN and retrace >= RETRACE * run:
                update_sl(trade_id, entry + OFFSET)
                trade["done"] = True

    else:
        move = (entry - price) / entry

        if not trade["armed"] and move >= ARM_TRIGGER:
            trade["armed"] = True

        if trade["armed"]:
            trade["best"] = min(trade["best"], price)
            run = (entry - trade["best"]) / entry
            retrace = (price - trade["best"]) / entry

            if run >= MIN_RUN and retrace >= RETRACE * run:
                update_sl(trade_id, entry - OFFSET)
                trade["done"] = True

def be_loop():
    while True:
        try:
            if not BE_INSTRUMENTS:
                time.sleep(1)
                continue

            r = requests.get(
                f"{BASE_URL}/accounts/{ACCOUNT}/pricing",
                headers=headers(),
                params={"instruments": ",".join(BE_INSTRUMENTS)},
                timeout=5
            )

            if r.status_code == 200:
                for p in r.json()["prices"]:
                    inst = p["instrument"]
                    bid = float(p["bids"][0]["price"])
                    ask = float(p["asks"][0]["price"])
                    mid = (bid + ask) / 2

                    for tid, trade in list(be_trades.items()):
                        if trade["inst"] == inst:
                            process_be(tid, trade, mid)

        except Exception as e:
            print("[BE LOOP ERROR]", e, flush=True)

        time.sleep(1)

# ===== ORDER (PROTECTED) =====

def send_order(units, inst, tp=None, sl=None):
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

        if r.status_code != 201:
            return False

        fill = r.json().get("orderFillTransaction")
        if not fill:
            return False

        trade_opened = fill.get("tradeOpened")

        if not trade_opened:
            print("NO NEW TRADE", flush=True)
            return True

        trade_id = trade_opened["tradeID"]
        price = float(fill["price"])

        if BE_ENABLED:
            register_be_trade(trade_id, units, price, inst)

        payload = {}

        if tp is not None:
            tp_price = price + tp if units > 0 else price - tp
            payload["takeProfit"] = {"price": str(round(tp_price, 3))}

        if sl is not None:
            sl_price = price - sl if units > 0 else price + sl
            payload["stopLoss"] = {"price": str(round(sl_price, 3))}

        if payload:
            requests.put(
                f"{BASE_URL}/accounts/{ACCOUNT}/trades/{trade_id}/orders",
                headers=headers(),
                json=payload,
                timeout=5
            )
            print("TP/SL ATTACHED", payload, flush=True)

        return True

    except Exception as e:
        print("ORDER ERROR:", e, flush=True)
        return False

# ===== WEBHOOK (PROTECTED) =====

@app.route("/", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True) or {}

    if not data or data.get("key") != SECRET:
        return "unauthorized", 403

    action = data["action"].lower()
    size = float(data["size"])
    inst = data["ticker"].upper()

    tp = float(data.get("tp")) if data.get("tp") is not None else None
    sl = float(data.get("sl")) if data.get("sl") is not None else None

    cur = get_position(inst)
    if cur is None:
        return "fail"

    same_direction = (action == "buy" and cur > 0) or (action == "sell" and cur < 0)
    flip = (action == "buy" and cur < 0) or (action == "sell" and cur > 0)

    unrealized, dd = get_instrument_dd(inst)

    if cur == 0:
        delta = size if action == "buy" else -size
    elif flip:
        desired = abs(size) if action == "buy" else -abs(size)
        delta = desired - cur
    elif same_direction:
        if unrealized < 0 and dd >= ADD_THRESHOLD:
            delta = size if action == "buy" else -size
        else:
            return "skip"
    else:
        return "skip"

    if abs(delta) < MIN_UNITS:
        return "skip"

    if not send_order(delta, inst, tp, sl):
        return "fail"

    return "ok"

# ===== RUN =====

if BE_ENABLED:
    threading.Thread(target=be_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
