# OKX SPOT SMART RADAR - GITHUB ACTIONS VERSION (REST API)
import os
import json
import time
import requests
import pandas as pd
from statistics import median

# ==========================================
# AYARLAR
# ==========================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

TOP_COINS = 100
MIN_24H_VOLUME_USDT = 1_000_000
MIN_SIGNAL_SCORE = 70

MIN_FLOW_TOTAL_RATIO = 0.15
MIN_NET_FLOW_RATIO = 0.08
MIN_BUY_RATIO = 0.60
MIN_NET_FLOW_USDT = 25_000

SIGNAL_COOLDOWN_SECONDS = 4 * 60 * 60
SCORE_LEVELS = [70, 80, 90]
BASE_LARGE_TRADE_USDT = 25_000
FAST_MOVE_WARNING = 15.0

STATE_FILE = "signal_state.json"
BASE_URL = "https://www.okx.com"

# ==========================================
# YARDIMCI FONKSİYONLAR
# ==========================================
def load_signal_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_signal_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print("State kaydetme hatası:", e)

signal_state = load_signal_state()

def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram kimlik bilgileri eksik.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=15)
    except Exception as e:
        print("Telegram hatası:", e)

def safe_float(val, default=0.0):
    try:
        return float(val) if val is not None else default
    except Exception:
        return default

def calculate_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))

def calculate_macd(series):
    ema12 = calculate_ema(series, 12)
    ema26 = calculate_ema(series, 26)
    macd = ema12 - ema26
    signal = calculate_ema(macd, 9)
    return macd, signal, macd - signal

# ==========================================
# REST API VERİ ÇEKME
# ==========================================
def get_top_symbols():
    try:
        res = requests.get(f"{BASE_URL}/api/v5/market/tickers?instType=SPOT", timeout=10).json()
        if res.get("code") != "0": return []
        data = res.get("data", [])
        candidates = []
        for t in data:
            symbol = t["instId"]
            if not symbol.endswith("-USDT"): continue
            vol = safe_float(t.get("volCcy24h"))
            if vol >= MIN_24H_VOLUME_USDT:
                candidates.append((symbol, vol))
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[:TOP_COINS]
    except Exception as e:
        print("Ticker çekme hatası:", e)
        return []

def get_ohlcv(symbol, bar, limit):
    try:
        url = f"{BASE_URL}/api/v5/market/candles?instId={symbol}&bar={bar}&limit={limit}"
        res = requests.get(url, timeout=10).json()
        if res.get("code") != "0": return None
        raw = res.get("data", [])
        raw.reverse() # Eskiden yeniye sırala
        cols = ["ts", "o", "h", "l", "c", "vol", "volCcy", "volCcyQuote", "confirm"]
        df = pd.DataFrame(raw, columns=cols)
        for c in ["o", "h", "l", "c", "vol", "volCcyQuote"]:
            df[c] = df[c].astype(float)
        return df.rename(columns={"o":"open", "h":"high", "l":"low", "c":"close", "volCcyQuote":"volume"})
    except Exception:
        return None

def get_recent_trades_flow(symbol):
    try:
        url = f"{BASE_URL}/api/v5/market/trades?instId={symbol}&limit=500"
        res = requests.get(url, timeout=10).json()
        if res.get("code") != "0": return None
        trades = res.get("data", [])
        
        buy_vol, sell_vol = 0.0, 0.0
        lb_vol, ls_vol = 0.0, 0.0
        last_price = safe_float(trades[0]["px"]) if trades else 0.0

        for t in trades:
            price = safe_float(t["px"])
            sz = safe_float(t["sz"])
            side = t["side"]
            val = price * sz
            
            if side == "buy":
                buy_vol += val
                if val >= BASE_LARGE_TRADE_USDT: lb_vol += val
            else:
                sell_vol += val
                if val >= BASE_LARGE_TRADE_USDT: ls_vol += val

        total = buy_vol + sell_vol
        return {
            "buy_volume": buy_vol, "sell_volume": sell_vol, "total_volume": total,
            "net_flow": buy_vol - sell_vol, "buy_ratio": buy_vol / total if total else 0.5,
            "large_buy_volume": lb_vol, "large_sell_volume": ls_vol, "last_price": last_price
        }
    except Exception:
        return None

# ==========================================
# ANALİZ MOTORU
# ==========================================
def analyze_coin(symbol, market_24h_vol):
    df_1d = get_ohlcv(symbol, "1D", 40)
    df_4h = get_ohlcv(symbol, "4H", 60)
    df_1h = get_ohlcv(symbol, "1H", 40)
    flow = get_recent_trades_flow(symbol)

    if df_1d is None or df_4h is None or df_1h is None or flow is None:
        return None

    # 1D Analiz
    df_1d["ema10"] = calculate_ema(df_1d.close, 10)
    df_1d["ema20"] = calculate_ema(df_1d.close, 20)
    df_1d["ema30"] = calculate_ema(df_1d.close, 30)
    c_1d = df_1d.iloc[-1]
    aligned_1d = c_1d.ema10 > c_1d.ema20 > c_1d.ema30
    score_1d = 18 if aligned_1d else 0

    # 4H Analiz
    df_4h["ema20"] = calculate_ema(df_4h.close, 20)
    df_4h["ema50"] = calculate_ema(df_4h.close, 50)
    c_4h = df_4h.iloc[-1]
    bullish_4h = c_4h.close > c_4h.ema20 > c_4h.ema50
    score_4h = 15 if bullish_4h else 0

    # 1H Analiz
    df_1h["ema20"] = calculate_ema(df_1h.close, 20)
    df_1h["rsi"] = calculate_rsi(df_1h.close, 14)
    df_1h["macd"], _, df_1h["macd_hist"] = calculate_macd(df_1h.close)
    c_1h, p_1h = df_1h.iloc[-1], df_1h.iloc[-2]
    
    score_1h = 0
    if c_1h.close > c_1h.ema20: score_1h += 5
    if c_1h.rsi >= 50: score_1h += 4
    if c_1h.macd_hist > 0: score_1h += 5

    vols = [safe_float(x) for x in df_1h.volume.tail(24) if safe_float(x) > 0]
    norm_vol = median(vols) if vols else 1
    rvol = c_1h.volume / norm_vol if norm_vol else 0
    if rvol >= 1.5: score_1h += 10

    # Order Flow Analiz
    buy_ratio = flow["buy_ratio"]
    net_flow = flow["net_flow"]
    strong_flow = buy_ratio >= MIN_BUY_RATIO and net_flow >= MIN_NET_FLOW_USDT
    score_flow = 12 if strong_flow else 0

    # Fiyat konumu
    res = df_1h.high.tail(20).max()
    price = flow["last_price"] or c_1h.close
    dist = ((res - price) / price) * 100 if price else 0
    score_price = 8 if 0 <= dist <= 2 else (10 if price > res else 0)

    total_score = score_1d + score_4h + score_1h + score_flow + score_price
    if market_24h_vol >= 10_000_000: total_score += 5

    return {
        "symbol": symbol.replace("-USDT", "/USDT"),
        "score": min(100, total_score),
        "price": price,
        "rsi": c_1h.rsi,
        "rvol": rvol,
        "net_flow": net_flow,
        "buy_ratio": buy_ratio,
        "bullish_4h": bullish_4h,
        "aligned_1d": aligned_1d,
        "dist": dist,
        "strong_flow": strong_flow
    }

def get_new_signal_level(symbol, score):
    now = time.time()
    state = signal_state.get(symbol, {"last_level": 0, "last_time": 0})
    last = state["last_level"]
    current = max([x for x in SCORE_LEVELS if score >= x], default=0)
    if current == 0: return 0
    if current > last or (current == last and now - state["last_time"] >= SIGNAL_COOLDOWN_SECONDS):
        signal_state[symbol] = {"last_level": current, "last_time": now}
        return current
    return 0

# ==========================================
# MAIN EXECUTION
# ==========================================
def main():
    print("Market verileri taranıyor...")
    symbols = get_top_symbols()
    signals = []

    for sym, vol in symbols:
        res = analyze_coin(sym, vol)
        if res and res["score"] >= MIN_SIGNAL_SCORE and res["strong_flow"]:
            level = get_new_signal_level(res["symbol"], res["score"])
            if level >= MIN_SIGNAL_SCORE:
                signals.append(res)

    if not signals:
        print("Yeni sinyal bulunamadı.")
        save_signal_state(signal_state)
        return

    signals.sort(key=lambda x: x["score"], reverse=True)
    lines = ["🚨 *OKX SPOT SMART RADAR*", "━━━━━━━━━━━━━━━━━━"]
    
    for s in signals:
        lines.append(
            f"📌 *{s['symbol']}* · Skor: *{s['score']}/100*\n"
            f"💵 Fiyat: `{s['price']:.4f}`\n"
            f"📈 4H: {'🟢 Bullish' if s['bullish_4h'] else '⚪ Nötr'} • 📅 1D: {'🟢' if s['aligned_1d'] else '⚪'}\n"
            f"⚡ RSI: {s['rsi']:.1f} • RVOL: {s['rvol']:.1f}x\n"
            f"💰 Net Flow: +{s['net_flow']:,.0f} USDT • Alış: %{s['buy_ratio']*100:.0f}\n"
        )

    lines.append("━━━━━━━━━━━━━━━━━━")
    send_telegram_message("\n".join(lines))
    save_signal_state(signal_state)
    print(f"{len(signals)} sinyal gönderildi.")

if __name__ == "__main__":
    main()
