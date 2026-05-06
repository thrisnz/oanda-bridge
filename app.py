# =====================================================================
# HYBRID_v1.3 + SL_ENGINE_v1.2 | INITIAL SL + DYNAMIC LADDER (CALIBRATED)
# =====================================================================

# ========================= CORE MODEL ===============================
# (unchanged)

# ========================= SL SYSTEM ================================
# TWO LAYERS:
#
# 1. INITIAL SL (HARD FLOOR)
#    - applied immediately after trade opens
#
# 2. DYNAMIC SL ENGINE (PERCENT-BASED, PRICE-CALIBRATED)
#    - BE → LOCK1 → LOCK2 → LOCK3
#    - calibrated to behave like:
#        BE ≈ $10 move
#        LOCK1 ≈ $30 move
#        LOCK2 ≈ $70 move
#        LOCK3 ≈ $110 move
#
#    - SL ONLY MOVES FORWARD
#    - each stage overwrites previous
#
# ========================= LADDER LOGIC =============================
# BE:
#   move >= BE_LEVEL → SL = entry ± buffer (NOT exact BE)
#
# LOCK1:
#   move >= LOCK1_TRIG → SL = entry + LOCK1_LOCK
#
# LOCK2:
#   move >= LOCK2_TRIG → SL = entry + LOCK2_LOCK
#
# LOCK3:
#   move >= LOCK3_TRIG → SL = entry + LOCK3_LOCK
#
# ========================= KEY INSIGHT ==============================
# - System uses % move, NOT $ directly
# - Values are calibrated to XAU (~4600–4700 range)
# - This preserves scaling while matching $ intuition
#
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

# ---- CALIBRATED LADDER (XAU ~4650) ----

BE_LEVEL    = 0.0022   # ~ $10 move
BE_BUFFER   = 0.0007   # ~ $3 cushion (prevents premature stop)

LOCK1_TRIG  = 0.0065   # ~ $30 move
LOCK1_LOCK  = 0.0043   # lock ~ $20

LOCK2_TRIG  = 0.0150   # ~ $70 move
LOCK2_LOCK  = 0.0105   # lock ~ $50

LOCK3_TRIG  = 0.0235   # ~ $110 move
LOCK3_LOCK  = 0.0180   # lock ~ $80

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

    # ===== BE (with buffer, NOT exact entry) =====
    if move >= BE_LEVEL:
        new_sl = entry * (1 - BE_BUFFER * side)

    # ===== LOCK1 (~$30) =====
    if move >= LOCK1_TRIG:
        new_sl = entry * (1 + LOCK1_LOCK * side)

    # ===== LOCK2 (~$70) =====
    if move >= LOCK2_TRIG:
        new_sl = entry * (1 + LOCK2_LOCK * side)

    # ===== LOCK3 (~$110) =====
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

# ===== ORDER + WEBHOOK + RUN (UNCHANGED) =====
