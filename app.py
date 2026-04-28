# ============================================
# NOTE TO FUTURE SELF:
#
# This system uses a TARGET POSITION model.
# - TradingView sends desired total position (NOT incremental size)
# - Backend calculates delta = desired - current
#
# CORE RULES:
# 1. FLIPS are ALWAYS allowed (never blocked by DD)
# 2. ADDS are ONLY allowed when:
#    - position is losing
#    - DD >= ADD_THRESHOLD
# 3. If delta == 0 → NO ACTION (important!)
#
# Common confusion:
# - Sending same size twice does NOT add (target model)
# - Must increase size in signal to scale in
#
# ============================================

from flask import Flask, request
import requests, os

app = Flask(__name__)

ACCOUNT = os.environ.get("ACCOUNT")
API_KEY = os.environ.get("API_KEY")
SECRET = os.environ.get("SECRET")
BASE_URL = "https://api-fxtrade.oanda.com/v3"

ADD_THRESHOLD = 0.03   # 3% drawdown (margin-based)
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
        r = requests.get(f"{BASE_URL}/accounts/{ACCOUNT}/openPositions", headers=headers(), timeout=5)

        if r.status_code != 200:
            print("POSITION ERROR:", r.text, flush=True)
            return None

        for p in r.json().get("positions", []):
            if p["instrument"] == inst:
                # NOTE: long is +, short is -
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

        # NOTE: DD is relative to margin, NOT account
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
                    "units": str(int(units)),
                    "type": "MARKET",
                    "timeInForce": "FOK",
                    # NOTE: ensures closing before opening (no hedging)
                    "positionFill": "REDUCE_FIRST"
                }
            },
            timeout=5
        )

        print("OANDA:", r.status_code, r.text, flush=True)

        return r.status_code == 201

    except Exception as e:
        print("ORDER ERROR:", e, flush=True)
        return False


# ---------- WEBHOOK ----------

@app.route("/", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True) or {}
    print("\n=== NEW SIGNAL ===", data, flush=True)

    # NOTE: ignore empty / health check requests
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

    # ===== TARGET MODEL =====
    desired = abs(size) if action == "buy" else -abs(size)
    delta = desired - cur

    print(f"CURRENT={cur} TARGET={desired} DELTA={delta}", flush=True)

    # NOTE: if delta == 0 → nothing happens (common confusion)
    if abs(delta) < MIN_UNITS:
        print("NO CHANGE", flush=True)
        return "skip"

    # ===== CONTEXT =====
    same_direction = (action == "buy" and cur > 0) or (action == "sell" and cur < 0)
    flip = (action == "buy" and cur < 0) or (action == "sell" and cur > 0)

    # ===== DD =====
    unrealized, dd = get_instrument_dd(inst)

    # ===== DECISION =====
    if cur == 0:
        allow = True
        reason = "NEW ENTRY"

    elif flip:
        allow = True
        reason = "FLIP (ALWAYS ALLOWED)"

    elif same_direction:
        if unrealized < 0 and dd >= ADD_THRESHOLD:
            allow = True
            reason = "ADD (DD OK)"
        else:
            allow = False
            reason = "ADD BLOCKED"

    else:
        allow = False
        reason = "UNKNOWN BLOCK"

    print("ALLOW:", allow, "|", reason, flush=True)

    if not allow:
        return "skip"

    # ===== EXECUTION =====
    if flip:
        print("FLIP EXECUTION", flush=True)

        if not send_order(delta, inst, sl, tp):
            print("FALLBACK: CLOSE THEN OPEN", flush=True)

            if abs(cur) > 0:
                if not send_order(-cur, inst):
                    return "fail"

            if not send_order(desired, inst, sl, tp):
                return "fail"

    else:
        if not send_order(delta, inst, sl, tp):
            return "fail"

    return "ok"


# ---------- RUN ----------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
