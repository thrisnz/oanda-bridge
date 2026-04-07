import pandas as pd

# =========================
# LOAD DATA
# =========================
df = pd.read_csv("trades.csv")

# basic cleanup
df = df.dropna()
df = df.sort_values("time")

print("\n=== LAST 5 TRADES ===")
print(df.tail())


# =========================
# BASIC STATS
# =========================
print("\n=== BASIC STATS ===")
print(df.describe())


# =========================
# PNL ESTIMATION
# =========================
# assume next trade is exit (approximation for now)
df["next_price"] = df["price"].shift(-1)

# PnL = price change * direction
df["direction"] = df["units"].apply(lambda x: 1 if x > 0 else -1)
df["pnl"] = (df["next_price"] - df["price"]) * df["direction"] * abs(df["units"])

print("\n=== PNL ===")
print("Total PnL:", round(df["pnl"].sum(), 2))
print("Average per trade:", round(df["pnl"].mean(), 2))


# =========================
# ADVERSE MOVE (ROUGH)
# =========================
# proxy for now (NOT true MAE yet)
df["adverse_move"] = (df["next_price"] - df["price"]) * -1 * df["direction"]

print("\n=== ADVERSE MOVE (ROUGH) ===")
print(df["adverse_move"].describe())


# =========================
# SL SUGGESTION (ROUGH)
# =========================
sl_80 = df["adverse_move"].quantile(0.80)
sl_90 = df["adverse_move"].quantile(0.90)

print("\n=== SL SUGGESTION (ROUGH) ===")
print("80% coverage SL:", round(sl_80, 2))
print("90% coverage SL:", round(sl_90, 2))


# =========================
# EXTRA INSIGHT
# =========================
wins = df[df["pnl"] > 0]
losses = df[df["pnl"] <= 0]

print("\n=== WIN / LOSS ===")
print("Win rate:", round(len(wins) / len(df) * 100, 2), "%")
print("Avg win:", round(wins["pnl"].mean(), 2))
print("Avg loss:", round(losses["pnl"].mean(), 2))
