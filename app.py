# =====================================================================
# HYBRID_v1.4 + SL_ENGINE_v1.3 | TRUE P/L LADDER (FINAL)
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
# - ONLY attach SL AFTER tradeOpened exists
# - tradeOpened == None → reduce only → DO NOT attach SL
#
# OANDA BEHAVIOR:
# - REDUCE_FIRST:
#     → closes opposite trades first
#     → then opens remainder

# ========================= SL SYSTEM ================================
# TWO LAYERS:
#
# 1. INITIAL SL (HARD FLOOR)
#    - applied immediately after trade opens
#    - protects against instant adverse move
#
# 2. DYNAMIC SL ENGINE (TRUE P/L BASED)
#    - BE → LOCK1 → LOCK2 → LOCK3
#    - TRIGGERED by REAL unrealized P/L (NOT %)
#    - SL placement remains PRICE-BASED (broker requirement)
#
# ========================= LADDER LOGIC =============================
# BE:
#   unrealizedPL >= $10 → SL = entry ± buffer (~-$3)
#
# LOCK1:
#   unrealizedPL >= $30 → SL locks ~$20 profit
#
# LOCK2:
#   unrealizedPL >= $70 → SL locks ~$50 profit
#
# LOCK3:
#   unrealizedPL >= $110 → SL locks ~$80 profit
#
# NOTE:
# - SL ONLY MOVES FORWARD (never backward)
# - later stages overwrite earlier ones automatically

# ========================= PRICING ==============================
# BUY  → use BID
# SELL → use ASK
#
# NOT MID
# because execution happens on bid/ask, not midpoint

# ========================= KEY INSIGHT ==============================
# - OLD SYSTEM: trigger = % move (caused mismatch vs P/L)
# - NEW SYSTEM: trigger = REAL P/L → matches what you see on screen
# - eliminates: “$20 profit but no SL shift” issue
#
# ========================= PERFORMANCE ==============================
# - 1x pricing call per loop
# - 1x openTrades call per loop (for P/L)
# - loop interval: 1 second
#
# ========================= VERIFY AFTER DEPLOY ======================
# ✔ initial SL appears immediately
# ✔ BE triggers around +$10
# ✔ locks trigger at correct P/L levels
# ✔ SL only moves forward
# ✔ no missing protection
# =====================================================================


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
    except Exception as e:
        print("POSITION ERROR:", e, flush=True)
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

    # ===== FORWARD-ONLY PROTECTION =====
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

def process_trade(trade_id, trade, price, pl):
    entry = trade["entry"]
    side = trade["side"]

    new_sl = None

    # ===== TRUE P/L LADDER =====

    # BE (with buffer ~ -$3)
    if pl >= 10:
        new_sl = entry * (1 - (3 / entry) * side)

    # LOCK1 (~$30 → lock ~$20)
    if pl >= 30:
        new_sl = entry * (1 + (20 / entry) * side)

    # LOCK2 (~$70 → lock ~$50)
    if pl >= 70:
        new_sl = entry * (1 + (50 / entry) * side)

    # LOCK3 (~$110 → lock ~$80)
    if pl >= 110:
        new_sl = entry * (1 + (80 / entry) * side)

    if new_sl is not None:
        update_sl(trade_id, trade, new_sl)

def sl_loop():
    while True:
        try:
            if not SL_INSTRUMENTS:
                time.sleep(1)
                continue

            # ===== GET LIVE TRADES (P/L + CLEANUP) =====
            trades_map = {}

            r_trades = requests.get(
                f"{BASE_URL}/accounts/{ACCOUNT}/openTrades",
                headers=headers(),
                timeout=5
            )

            if r_trades.status_code == 200:
                for t in r_trades.json()["trades"]:
                    trades_map[t["id"]] = float(t["unrealizedPL"])

            # ===== GET PRICES =====
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

                    for tid, trade in list(sl_trades.items()):

                        # REMOVE CLOSED TRADES
                        if tid not in trades_map:
                            sl_trades.pop(tid, None)
                            continue

                        if trade["inst"] == inst:
                            price = bid if trade["side"] == 1 else ask
                            pl = trades_map.get(tid, 0)

                            process_trade(tid, trade, price, pl)

        except Exception as e:
            print("[SL LOOP ERROR]", e, flush=True)

        time.sleep(1)

# ===== ORDER =====

def send_order(units, inst, sl=None):
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
            return True

        trade_id = trade_opened["tradeID"]
        price = float(fill["price"])

        register_trade(trade_id, units, price, inst)

        # ===== INITIAL SL =====
        if sl is not None:
            if units > 0:
                sl_price = price - sl
            else:
                sl_price = price + sl

            try:
                requests.put(
                    f"{BASE_URL}/accounts/{ACCOUNT}/trades/{trade_id}/orders",
                    headers=headers(),
                    json={"stopLoss": {"price": str(round(sl_price, 3))}},
                    timeout=5
                )
                print("[INIT SL] →", sl_price, flush=True)
            except Exception as e:
                print("[INIT SL ERROR]", e, flush=True)

        return True

    except Exception as e:
        print("ORDER ERROR:", e, flush=True)
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

    if not send_order(delta, inst, sl):
        return "fail"

    return "ok"

# ===== RUN =====

if SL_ENABLED:
    threading.Thread(target=sl_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
