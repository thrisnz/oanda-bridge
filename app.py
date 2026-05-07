# =====================================================================
# HYBRID_v1.5 + SL_ENGINE_v1.4 | % LADDER (FINAL STABLE)
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
# 2. DYNAMIC SL ENGINE (% BASED)
#    - BE → LOCK1 → LOCK2 → LOCK3
#    - based on PRICE MOVEMENT (%)
#    - NOT P/L

# ========================= LADDER =============================
# BE:
#   move >= 0.0007 → SL ≈ entry (with buffer)
#
# LOCK1:
#   move >= 0.0021 → lock profit
#
# LOCK2:
#   move >= 0.0050 → more lock
#
# LOCK3:
#   move >= 0.0078 → max lock

# ========================= PRICING ==============================
# BUY  → use BID
# SELL → use ASK
#
# NOT MID
# ensures realistic execution behavior

# ========================= CRITICAL FIX =========================
# - CLOSED TRADES ARE REMOVED
# - prevents SL corruption

# ========================= VERIFY ======================
# ✔ initial SL appears immediately
# ✔ BE triggers early
# ✔ SL only moves forward
# ✔ no ghost trades
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

BE_LEVEL   = 0.0007
BE_BUFFER  = 0.0005

LOCK1_TRIG = 0.0021
LOCK1_LOCK = 0.0014

LOCK2_TRIG = 0.0050
LOCK2_LOCK = 0.0035

LOCK3_TRIG = 0.0078
LOCK3_LOCK = 0.0055

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

    if move >= BE_LEVEL:
        new_sl = entry * (1 - BE_BUFFER * side)

    if move >= LOCK1_TRIG:
        new_sl = entry * (1 + LOCK1_LOCK * side)

    if move >= LOCK2_TRIG:
        new_sl = entry * (1 + LOCK2_LOCK * side)

    if move >= LOCK3_TRIG:
        new_sl = entry * (1 + LOCK3_LOCK * side)

    if new_sl is not None:
        update_sl(trade_id, trade, new_sl)

def sl_loop():
    while True:
        try:
            if not SL_INSTRUMENTS:
                time.sleep(1)
                continue

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
                        if trade["inst"] == inst:
                            price = bid if trade["side"] == 1 else ask
                            process_trade(tid, trade, price)

        except Exception as e:
            print("[SL LOOP ERROR]", e, flush=True)

        time.sleep(1)

# ===== ORDER / WEBHOOK / RUN (UNCHANGED) =====
