# ============================================
# LOGIC VERSION: HYBRID_v1.1
#
# CHANGE LOG:
# v1.1 → Restored TP/SL post-fill attachment (CRITICAL FIX)
# v1.0 → Hybrid model (flip=target, add=incremental)
#
# ============================================
# NOTE TO FUTURE SELF (DO NOT REMOVE):
#
# CORE MODEL:
# - FLIP → TARGET MODE (delta = desired - current)
# - ADD  → INCREMENTAL (ONLY if losing AND DD >= threshold)
# - NEW  → INCREMENTAL
#
# CRITICAL RULES:
# - Market order is NOT a complete trade
# - TP/SL MUST be attached AFTER fill using tradeID
# - If tradeOpened is None → this was reduce/close → DO NOT attach TP
#
# DO NOT:
# - Remove TP logic when modifying execution
# - Change delta logic without checking flip/add behavior
#
# ALWAYS VERIFY AFTER ANY CHANGE:
# 1. Flip works
# 2. Add is DD-gated
# 3. TP is attached
#
# ============================================

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


# ---------- ORDER (WITH TP/SL) ----------

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

        # ===== CRITICAL: ONLY APPLY TP IF NEW TRADE =====
        if not trade_opened:
            print("NO NEW TRADE (reduce only)", flush=True)
            return True

        trade_id = trade_opened["tradeID"]
        price = float(fill["price"])

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
            print("TP/SL ATTACHED:", payload, flush=True)
        else:
            print("WARNING: NO TP/SL PROVIDED", flush=True)

        return True

    except Exception as e:
        print("ORDER ERROR:", e, flush=True)
        return False


# ---------- WEBHOOK ----------

@app.route("/", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True) or {}
    print("\n=== NEW SIGNAL ===", data, flush=True)
    print("[VERSION] HYBRID_v1.1", flush=True)

    if not data or "key" not in data:
        return "ignored"

    if data.get("key") != SECRET:
        return "unauthorized", 403

    action = data["action"].lower()
    size = float(data["size"])
    inst = data["ticker"].upper()

    # ===== TP/SL INPUT =====
    tp = float(data.get("tp")) if data.get("tp") is not None else None
    sl = float(data.get("sl")) if data.get("sl") is not None else None

    print(f"[INPUT] action={action} size={size} tp={tp}", flush=True)

    cur = get_position(inst)

    if cur is None:
        print("POSITION UNKNOWN")
        return "fail"

    same_direction = (action == "buy" and cur > 0) or (action == "sell" and cur < 0)
    flip = (action == "buy" and cur < 0) or (action == "sell" and cur > 0)

    unrealized, dd = get_instrument_dd(inst)

    print(f"CURRENT={cur} DD={dd:.4f}", flush=True)

    # ---------- DECISION ----------

    if cur == 0:
        delta = size if action == "buy" else -size
        reason = "NEW ENTRY"

    elif flip:
        desired = abs(size) if action == "buy" else -abs(size)
        delta = desired - cur
        reason = "FLIP (TARGET MODE)"

    elif same_direction:
        if unrealized < 0 and dd >= ADD_THRESHOLD:
            delta = size if action == "buy" else -size
            reason = "ADD (INCREMENTAL)"
        else:
            print("ADD BLOCKED", flush=True)
            return "skip"

    else:
        return "skip"

    if abs(delta) < MIN_UNITS:
        print("NO CHANGE", flush=True)
        return "skip"

    print(f"[PLAN] delta={delta} | {reason}", flush=True)

    # ===== SAFETY CHECK =====
    if tp is None:
        print("WARNING: ORDER HAS NO TP", flush=True)

    if not send_order(delta, inst, tp, sl):
        return "fail"

    return "ok"


# ---------- RUN ----------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
