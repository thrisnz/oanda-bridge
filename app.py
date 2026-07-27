# =====================================================================
# HYBRID_v1.7 + SL_ENGINE_v1.6 | % LADDER (P/L CALIBRATED)
# =====================================================================

# ========================= CORE MODEL ===============================
# TRADE TYPES:
# - NEW  → open fresh position
# - ADD  → add ONLY when losing AND DD >= threshold
# - FLIP → full reversal using TARGET MODE (delta = desired - current)
#
# TARGET MODE (CRITICAL):
# - delta = desired_position - current_position
# - ensures full reversal in ONE order
# - relies on: positionFill = REDUCE_FIRST
#
# POSITION RULES:
# - NO hedging
# - ONE net position per instrument
# - ALWAYS operate via delta

# ========================= EXECUTION RULES ==========================
# ORDER FLOW:
# - send MARKET order
# - wait for fill
# - ONLY attach SL/TP AFTER tradeOpened exists
# - tradeOpened == None → reduce only → DO NOT attach SL/TP

# ========================= SL SYSTEM ================================
# TWO LAYERS:
# 1. INITIAL SL (from webhook)
# 2. DYNAMIC SL ENGINE (% BASED TRAILING)

# ========================= LADDER =============================
# calibrated behavior (approx):
# small move → BE
# mid move   → partial lock
# large move → tighter lock

from flask import Flask, request
import requests, os, threading, time

app = Flask(__name__)

ACCOUNT = os.environ.get("ACCOUNT")
API_KEY = os.environ.get("API_KEY")
SECRET  = os.environ.get("SECRET")

BASE_URL = "https://api-fxtrade.oanda.com/v3"

ADD_THRESHOLD = 0.035
MIN_UNITS = 1

SL_ENABLED = True

# ===== LADDER (PERCENT BASED) =====
BE_LEVEL   = 0.0022
BE_BUFFER  = 0.0010   # 🔥 important: avoids instant stopout at BE
LOCK1_TRIG = 0.0043
LOCK1_LOCK = 0.0023
LOCK2_TRIG = 0.0078
LOCK2_LOCK = 0.0045

# ===== STATE =====
sl_trades = {}
SL_INSTRUMENTS = set()

# ============================================================
# HELPERS
# ============================================================

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

        return unrealized, abs(unrealized) / margin
    except:
        return 0, 0

# ============================================================
# SL ENGINE
# ============================================================

def register_trade(trade_id, units, price, inst):
    print(f"[REGISTER] {trade_id} @ {price}", flush=True)

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

    # 🔥 prevent SL going backwards
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
    side  = trade["side"]

    move = (price - entry) / entry * side

    new_sl = None

    # ===== BREAK EVEN =====
    if move >= BE_LEVEL:
        new_sl = entry * (1 + BE_BUFFER * side)

    # ===== LOCK 1 =====
    if move >= LOCK1_TRIG:
        new_sl = entry * (1 + LOCK1_LOCK * side)

    # ===== LOCK 2 =====
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

            # ===== CLEAN CLOSED TRADES =====
            r_trades = requests.get(
                f"{BASE_URL}/accounts/{ACCOUNT}/openTrades",
                headers=headers(),
                timeout=5
            )

            open_ids = set()
            if r_trades.status_code == 200:
                open_ids = set(t["id"] for t in r_trades.json().get("trades", []))

            for tid in list(sl_trades.keys()):
                if tid not in open_ids:
                    sl_trades.pop(tid, None)

            # ===== PRICE FEED =====
            r = requests.get(
                f"{BASE_URL}/accounts/{ACCOUNT}/pricing",
                headers=headers(),
                params={"instruments": ",".join(SL_INSTRUMENTS)},
                timeout=5
            )

            if r.status_code == 200:
                for p in r.json()["prices"]:
                    inst = p["instrument"]
                    bid  = float(p["bids"][0]["price"])
                    ask  = float(p["asks"][0]["price"])

                    for tid, trade in list(sl_trades.items()):
                        if trade["inst"] == inst:
                            price = bid if trade["side"] == 1 else ask
                            process_trade(tid, trade, price)

        except Exception as e:
            print("[SL LOOP ERROR]", e, flush=True)

        time.sleep(1)

# ============================================================
# ORDER EXECUTION
# ============================================================

def send_order(units, inst, sl=None, tp=None):
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
            return True  # reduce-only

        trade_id = trade_opened["tradeID"]
        price = float(fill["price"])

        register_trade(trade_id, units, price, inst)

        # ===== INITIAL SL (PERCENT) =====
        if sl is not None:
            sl_price = price * (1 - sl) if units > 0 else price * (1 + sl)

            requests.put(
                f"{BASE_URL}/accounts/{ACCOUNT}/trades/{trade_id}/orders",
                headers=headers(),
                json={"stopLoss": {"price": str(round(sl_price, 3))}},
                timeout=5
            )
            print("[INIT SL] →", sl_price, flush=True)

        # ===== INITIAL TP (PERCENT) =====
        if tp is not None:
            tp_price = price * (1 + tp) if units > 0 else price * (1 - tp)

            requests.put(
                f"{BASE_URL}/accounts/{ACCOUNT}/trades/{trade_id}/orders",
                headers=headers(),
                json={"takeProfit": {"price": str(round(tp_price, 3))}},
                timeout=5
            )
            print("[INIT TP] →", tp_price, flush=True)

        return True

    except Exception as e:
        print("ORDER ERROR:", e, flush=True)
        return False

# ============================================================
# WEBHOOK
# ============================================================

@app.route("/", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True) or {}

    if not data or data.get("key") != SECRET:
        return "unauthorized", 403

    action = data["action"].lower()
    size   = float(data["size"])
    inst   = data["ticker"].upper()

    sl = float(data["sl"]) if "sl" in data else None
    tp = float(data["tp"]) if "tp" in data else None

    cur = get_position(inst)
    if cur is None:
        return "fail"

    same = (action == "buy" and cur > 0) or (action == "sell" and cur < 0)
    flip = (action == "buy" and cur < 0) or (action == "sell" and cur > 0)

    unrealized, dd = get_instrument_dd(inst)

    # ===== DECISION LOGIC =====
    if cur == 0:
        delta = size if action == "buy" else -size

    elif flip:
        desired = abs(size) if action == "buy" else -abs(size)
        delta = desired - cur

    elif same and cur != 0:
        if unrealized < 0 and dd >= ADD_THRESHOLD:
            delta = size if action == "buy" else -size
        else:
            return "skip"

    else:
        return "skip"

    # ===== DEBUG =====
    print(f"[DECISION] CUR={cur} ACT={action} SIZE={size}", flush=True)
    print(f"[RISK] PL={unrealized} DD={dd}", flush=True)
    print(f"[DELTA] {delta}", flush=True)

    if abs(delta) < MIN_UNITS:
        return "skip"

    if not send_order(delta, inst, sl, tp):
        return "fail"

    return "ok"

# ============================================================
# RUN
# ============================================================

if SL_ENABLED:
    threading.Thread(target=sl_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
