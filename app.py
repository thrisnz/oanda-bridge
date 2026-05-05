# HYBRID_v1.1 + SL_ENGINE_v1.0 | SINGLE DYNAMIC SL ONLY

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
# SL ENGINE:
# - ONE SL only
# - BE → LOCK1 → LOCK2
# - SL ONLY MOVES FORWARD
# - NO TP (recommended)
#
# VERIFY AFTER CHANGES:
# - flip works
# - add DD gating works
# - TP attaches (if used)
# - SL never moves backward

from flask import Flask, request
import requests, os, threading, time

app = Flask(__name__)

ACCOUNT = os.environ.get("ACCOUNT")
API_KEY = os.environ.get("API_KEY")
SECRET = os.environ.get("SECRET")
BASE_URL = "https://api-fxtrade.oanda.com/v3"

ADD_THRESHOLD = 0.03
MIN_UNITS = 1

# ===== SL ENGINE CONFIG =====

SL_ENABLED = True

BE_LEVEL    = 0.0018 # ~$15
LOCK1_TRIG  = 0.0066   # ~$30
LOCK1_LOCK  = 0.0044   # ~$20
LOCK2_TRIG  = 0.0155   # ~$70
LOCK2_LOCK  = 0.0110   # ~$50

sl_trades = {}
SL_INSTRUMENTS = set()

# ===== HELPERS =====

def headers():
    return {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

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
        return unrealized, abs(unrealized) / margin
    except:
        return 0, 0

# ===== SL ENGINE =====

def register_trade(trade_id, units, price, inst):
    sl_trades[trade_id] = {
        "inst": inst,
        "side": 1 if units > 0 else -1,
        "entry": price,
        "sl": None
    }
    SL_INSTRUMENTS.add(inst)

def update_sl(trade_id, trade, new_sl):

    current_sl = trade["sl"]
    side = trade["side"]

    if current_sl is not None:
        if side == 1 and new_sl <= current_sl:
            return
        if side == -1 and new_sl >= current_sl:
            return

    try:
        requests.put(
            f"{BASE_URL}/accounts/{ACCOUNT}/trades/{trade_id}/orders",
            headers=headers(),
            json={"stopLoss": {"price": str(round(new_sl, 3))}},
            timeout=5
        )
        trade["sl"] = new_sl
        print("[SL UPDATE] →", new_sl, flush=True)

    except Exception as e:
        print("[SL ERROR]", e, flush=True)

def process_trade(trade_id, trade, price):

    entry = trade["entry"]
    side = trade["side"]

    move = (price - entry) / entry * side

    new_sl = None

    # BE
    if move >= BE_LEVEL:
        new_sl = entry

    # LOCK1
    if move >= LOCK1_TRIG:
        new_sl = entry * (1 + LOCK1_LOCK * side)

    # LOCK2
    if move >= LOCK2_TRIG:
        new_sl = entry * (1 + LOCK2_LOCK * side)

    if new_sl is not None:
        update_sl(trade_id, trade, new_sl)

def sl_loop():
    while True:
        try:
            if not SL_INSTRUMENTS:
                time.sleep(1)
                continue

            r = requests.get(
                f"{BASE_URL}/accounts/{ACCOUNT}/pricing",
                headers=headers(),
                params={"instruments": ",".join(SL_INSTRUMENTS)},
                timeout=5
            )

            if r.status_code == 200:
                for p in r.json()["prices"]:
                    inst = p["instrument"]
                    bid = float(p["bids"][0]["price"])
                    ask = float(p["asks"][0]["price"])
                    mid = (bid + ask) / 2

                    for tid, trade in list(sl_trades.items()):
                        if trade["inst"] == inst:
                            process_trade(tid, trade, mid)

        except Exception as e:
            print("[SL LOOP ERROR]", e, flush=True)

        time.sleep(1)

# ===== ORDER (PROTECTED) =====

def send_order(units, inst, tp=None, sl=None):

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

        if r.status_code != 201:
            return False

        fill = r.json().get("orderFillTransaction")
        if not fill:
            return False

        trade_opened = fill.get("tradeOpened")

        if not trade_opened:
            return True

        trade_id = trade_opened["tradeID"]
        price = float(fill["price"])

        if SL_ENABLED:
            register_trade(trade_id, units, price, inst)

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

        return True

    except:
        return False

# ===== WEBHOOK =====

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

if SL_ENABLED:
    threading.Thread(target=sl_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
