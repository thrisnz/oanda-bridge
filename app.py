from flask import Flask, request
import requests, os

app = Flask(__name__)

ACCOUNT = os.environ.get("ACCOUNT")
API_KEY = os.environ.get("API_KEY")
SECRET = os.environ.get("SECRET")
BASE_URL = "https://api-fxtrade.oanda.com/v3"


# parse optional numeric fields
def parse_float(v):
    try:
        return None if v in ("", None) else float(v)
    except:
        return None


# pip size per instrument
def pip_size(inst):
    if inst.endswith("JPY"): return 0.01
    if inst.startswith("XAU"): return 0.1
    if inst.startswith("WTICO"): return 0.01
    return 0.0001


# get current net position
def get_position(inst):
    try:
        r = requests.get(
            f"{BASE_URL}/accounts/{ACCOUNT}/openPositions",
            headers={"Authorization": f"Bearer {API_KEY}"},
            timeout=5
        )
        if r.status_code != 200:
            print("POSITION ERROR:", r.text, flush=True)
            return 0.0

        for p in r.json().get("positions", []):
            if p["instrument"] == inst:
                return float(p["long"]["units"]) + float(p["short"]["units"])
    except Exception as e:
        print("POSITION EXCEPTION:", e, flush=True)

    return 0.0


# place market order, then attach SL/TP (in pips if provided)
def send_order(units, inst, sl_pips=None, tp_pips=None):
    print("SENDING:", units, inst, flush=True)

    try:
        # place order
        r = requests.post(
            f"{BASE_URL}/accounts/{ACCOUNT}/orders",
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={"order": {"instrument": inst, "units": str(int(units)), "type": "MARKET", "positionFill": "DEFAULT"}},
            timeout=5
        )

        print("OANDA:", r.status_code, r.text, flush=True)
        if r.status_code != 201:
            return

        fill = r.json().get("orderFillTransaction", {})
        price = float(fill["price"])
        trade_id = fill["tradeOpened"]["tradeID"]

        ps = pip_size(inst)
        payload = {}

        # stop loss
        if sl_pips is not None:
            d = sl_pips * ps
            sl = price - d if units > 0 else price + d
            payload["stopLoss"] = {"price": str(round(sl, 5))}
            print("SL:", sl_pips, payload["stopLoss"]["price"], flush=True)

        # take profit
        if tp_pips is not None:
            d = tp_pips * ps
            tp = price + d if units > 0 else price - d
            payload["takeProfit"] = {"price": str(round(tp, 5))}
            print("TP:", tp_pips, payload["takeProfit"]["price"], flush=True)

        # attach SL/TP if present
        if payload:
            r2 = requests.put(
                f"{BASE_URL}/accounts/{ACCOUNT}/trades/{trade_id}/orders",
                headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
                json=payload,
                timeout=5
            )
            print("ATTACH:", r2.status_code, r2.text, flush=True)

    except Exception as e:
        print("ORDER ERROR:", e, flush=True)


# webhook entry
@app.route("/", methods=["POST"])
def webhook():
    data = request.get_json(force=True)
    print("\n=== NEW SIGNAL ===", data, flush=True)

    if data.get("key") != SECRET:
        return "unauthorized", 403

    action = data["action"].lower()
    size = float(data["size"])
    inst = data["ticker"].upper()

    sl_pips = parse_float(data.get("sl"))
    tp_pips = parse_float(data.get("tp"))

    cur = get_position(inst)

    if action == "buy":
        tgt = abs(size)
    elif action == "sell":
        tgt = -abs(size)
    else:
        return "bad action", 400

    units = tgt - cur
    print("CURRENT:", cur, "TARGET:", tgt, "DELTA:", units, flush=True)

    if int(units) != 0:
        send_order(units, inst, sl_pips, tp_pips)
    else:
        print("NO TRADE", flush=True)

    return "ok"


# run analyzer manually
@app.route("/analyze", methods=["GET"])
def analyze():
    import subprocess
    r = subprocess.run(["python", "analyzer.py"], capture_output=True, text=True)
    return f"<pre>{r.stdout}</pre>"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
