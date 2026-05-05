# HYBRID_v1.1 + SL_ENGINE_v1.0 | SINGLE DYNAMIC SL ONLY

# =========================================================
# NOTES — READ BEFORE TOUCHING ANYTHING
# =========================================================
#
# CORE MODEL:
# - FLIP = TARGET MODE
#     → delta = desired_position - current_position
#
# - ADD = INCREMENTAL (ONLY if losing AND DD >= threshold)
#
# - NEW = INCREMENTAL (when no position)
#
# ---------------------------------------------------------
# CRITICAL EXECUTION RULES:
#
# - positionFill = REDUCE_FIRST
#     → closes opposite trades before opening new ones
#
# - tradeOpened == None
#     → means NO new trade was created (reduce-only)
#     → DO NOT attach SL / DO NOT register SL engine
#
# - ALWAYS trust OANDA response, not assumptions
#
# ---------------------------------------------------------
# SL ENGINE DESIGN:
#
# - ONE dynamic SL only (NO multiple SLs)
#
# - Ladder structure:
#
#     ENTRY
#       ↓
#     BE (Break Even)
#       ↓
#     LOCK1 (secure partial profit)
#       ↓
#     LOCK2 (secure deeper profit)
#
# - SL ONLY MOVES FORWARD (never backwards)
#
# ---------------------------------------------------------
# LADDER LEVELS (percentage-based):
#
# BE_LEVEL:
#   → when price moves enough → SL = entry (risk-free)
#
# LOCK1:
#   → further move → lock small profit
#
# LOCK2:
#   → strong move → lock larger profit
#
# ---------------------------------------------------------
# IMPORTANT BEHAVIOR:
#
# - Ladder overwrites itself:
#     LOCK2 overrides LOCK1
#     LOCK1 overrides BE
#
# - No TP required for system to function
#   (SL acts as profit capture)
#
# ---------------------------------------------------------
# VERIFY AFTER ANY CHANGE:
#
# ✔ flip logic works
# ✔ add DD gating works
# ✔ trades register correctly
# ✔ SL updates forward only
# ✔ logs visible in Render
#
# =========================================================

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

# tuned for faster scalping behavior
BE_LEVEL    = 0.0015
LOCK1_TRIG  = 0.0035
LOCK1_LOCK  = 0.0022
LOCK2_TRIG  = 0.0075
LOCK2_LOCK  = 0.0050

sl_trades = {}
SL_INSTRUMENTS = set()

# ===== HELPERS =====

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
            print("DD ERROR:", r.text, flush=True)
            return 0, 0
        trades = [t for t in r.json().get("trades", []) if t["instrument"] == inst]
        if not trades:
            return 0, 0
        unrealized = sum(float(t["unrealizedPL"]) for t in trades)
        margin = sum(float(t["marginUsed"]) for t in trades)
        if margin == 0:
            return 0, 0
        return unrealized, abs(unrealized) / margin
    except Exception as e:
        print("DD EXCEPTION:", e, flush=True)
        return 0, 0

# ===== SL ENGINE =====

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

    # prevent backward movement
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

    print(f"[SL CHECK] move={move:.5f}", flush=True)

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

# ===== ORDER =====

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

        if SL_ENABLED:
            register_trade(trade_id, units, price, inst)

        return True

    except Exception as e:
        print("ORDER ERROR:", e, flush=True)
        return False

# ===== WEBHOOK =====

@app.route("/", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True) or {}

    print("\n=== NEW SIGNAL ===", data, flush=True)

    if not data or data.get("key") != SECRET:
        print("AUTH FAILED", flush=True)
        return "unauthorized", 403

    action = data["action"].lower()
    size = float(data["size"])
    inst = data["ticker"].upper()

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
            print("SKIP SAME DIRECTION", flush=True)
            return "skip"
    else:
        return "skip"

    print(f"[PLAN] delta={delta}", flush=True)

    if abs(delta) < MIN_UNITS:
        print("SKIP MIN UNITS", flush=True)
        return "skip"

    if not send_order(delta, inst):
        return "fail"

    return "ok"

# ===== RUN =====

if SL_ENABLED:
    threading.Thread(target=sl_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
