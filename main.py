import asyncio
import aiohttp
import ccxt.async_support as ccxt
import pandas as pd
import os
import json
import time
 
from statistics import median
 
 
# ============================================================
# TELEGRAM
# ============================================================
 
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
 
 
# ============================================================
# ANA AYARLAR
# ============================================================
 
TOP_COINS = 100
MIN_24H_VOLUME_USDT = 1_000_000
 
# Telegram'a girebilmek için minimum kalite
MIN_SIGNAL_SCORE = 78
 
# Tek Telegram mesajında maksimum coin
MAX_TELEGRAM_SIGNALS = 5
 
MAX_CONCURRENT_REQUESTS = 5
 
 
# ============================================================
# EARLY ENTRY AYARLARI (STRATEJİ DEĞİŞMEDİ)
# ============================================================
 
MAX_RISE_AFTER_CROSS = 12.0
HARD_MAX_RISE_AFTER_CROSS = 15.0
MAX_CROSS_AGE_DAYS = 14
 
# Uyarı eşiği: kesişimden sonra bu yüzdenin üzerinde yükselmişse
# mesajda ayrıca "aşırı uzaklaşma" uyarısı gösterilir.
RISE_WARNING_THRESHOLD = 10.0
 
 
# ============================================================
# OBV TABANLI BİRİKİM (WHALE FLOW YERİNE)
# ============================================================
# Websocket ile anlık trade akışı yerine, zaten çekilen günlük
# mum verisinden hesaplanan On-Balance-Volume kullanılır.
# Fikir: fiyat çok hareket etmeden hacim OBV'yi yukarı itiyorsa
# bu "sessiz birikim" (accumulation) anlamına gelir.
 
OBV_LOOKBACK_DAYS = 14
 
# OBV yüzde değişimi ile fiyat yüzde değişimi arasındaki fark
# bu eşiğin üzerindeyse "güçlü birikim" kabul edilir.
MIN_OBV_DIVERGENCE = 8.0
 
 
# ============================================================
# SIGNAL COOLDOWN
# ============================================================
 
SIGNAL_COOLDOWN_SECONDS = 6 * 60 * 60
STATE_FILE = "state.json"
 
 
# ============================================================
# OKX (REST-ONLY, WEBSOCKET YOK)
# ============================================================
 
exchange = ccxt.okx({
    "enableRateLimit": True,
    "options": {"defaultType": "spot"}
})
 
market_data = {}
api_semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
 
 
# ============================================================
# TELEGRAM
# ============================================================
 
async def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ Telegram Secret bulunamadı.")
        return
 
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
 
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=15) as response:
                if response.status != 200:
                    text = await response.text()
                    print("Telegram hata:", response.status, text)
    except Exception as e:
        print("Telegram bağlantı hatası:", e)
 
 
# ============================================================
# STATE (COOLDOWN) — dosyaya yazılır, GitHub Actions'ta
# workflow içinde repo'ya commit edilerek kalıcı hale getirilir.
# ============================================================
 
def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}
 
 
def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)
 
 
def can_send_signal(state, symbol):
    now = time.time()
    last = state.get(symbol, 0)
    return (now - last) >= SIGNAL_COOLDOWN_SECONDS
 
 
# ============================================================
# HELPERS
# ============================================================
 
def safe_float(value, default=0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default
 
 
def create_dataframe(ohlcv):
    return pd.DataFrame(
        ohlcv,
        columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
 
 
def calculate_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()
 
 
def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))
 
 
def calculate_obv(df):
    direction = df["close"].diff()
    signed_volume = df["volume"].where(direction > 0, -df["volume"])
    signed_volume = signed_volume.where(direction != 0, 0)
    return signed_volume.cumsum()
 
 
# ============================================================
# REST FETCH
# ============================================================
 
async def fetch_ohlcv_safe(symbol, timeframe, limit):
    async with api_semaphore:
        try:
            return await exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        except Exception as e:
            print(f"OHLCV hata | {symbol} | {timeframe} | {e}")
            return None
 
 
async def update_symbol_timeframe(symbol, timeframe, limit):
    candles = await fetch_ohlcv_safe(symbol, timeframe, limit)
    if not candles:
        return
 
    df = create_dataframe(candles)
 
    if symbol not in market_data:
        market_data[symbol] = {}
 
    market_data[symbol][timeframe] = df
 
 
async def fetch_all(symbols):
    tasks = []
    for symbol in symbols:
        tasks.append(update_symbol_timeframe(symbol, "1d", 100))
        tasks.append(update_symbol_timeframe(symbol, "4h", 100))
        tasks.append(update_symbol_timeframe(symbol, "1h", 60))
    await asyncio.gather(*tasks, return_exceptions=True)
 
 
# ============================================================
# 1D ANALYSIS (STRATEJİ DEĞİŞMEDİ)
# ============================================================
 
def analyze_1d(symbol):
    try:
        df = market_data[symbol]["1d"].copy()
        if len(df) < 50:
            return None
 
        df = df.iloc[:-1].copy()
 
        df["ema10"] = calculate_ema(df["close"], 10)
        df["ema20"] = calculate_ema(df["close"], 20)
        df["ema30"] = calculate_ema(df["close"], 30)
 
        current = df.iloc[-1]
 
        ema_aligned = (
            current["ema10"] > current["ema20"]
            and current["ema20"] > current["ema30"]
        )
 
        cross_index = None
        for i in range(len(df) - 1, 0, -1):
            now = df.iloc[i]
            prev = df.iloc[i - 1]
            crossed = (
                now["ema10"] > now["ema20"]
                and prev["ema10"] <= prev["ema20"]
            )
            if crossed:
                cross_index = i
                break
 
        if cross_index is None:
            return {
                "score": 0,
                "ema_aligned": ema_aligned,
                "cross_age": 999,
                "rise_after_cross": 999,
                "early": False,
                "df": df
            }
 
        cross_age = len(df) - 1 - cross_index
        cross_price = safe_float(df.iloc[cross_index]["close"])
        current_price = safe_float(current["close"])
 
        if cross_price > 0:
            rise_after_cross = ((current_price - cross_price) / cross_price) * 100
        else:
            rise_after_cross = 999
 
        early = (
            ema_aligned
            and cross_age <= MAX_CROSS_AGE_DAYS
            and rise_after_cross < HARD_MAX_RISE_AFTER_CROSS
        )
 
        score = 0
 
        if ema_aligned:
            score += 10
 
        if cross_age <= 3:
            score += 20
        elif cross_age <= 7:
            score += 15
        elif cross_age <= 10:
            score += 10
        elif cross_age <= 14:
            score += 5
        else:
            score -= 10
 
        if rise_after_cross <= 5:
            score += 15
        elif rise_after_cross <= 8:
            score += 10
        elif rise_after_cross <= 12:
            score += 3
        else:
            score -= 15
 
        if rise_after_cross >= 12:
            score -= 10
 
        return {
            "score": score,
            "ema_aligned": ema_aligned,
            "cross_age": cross_age,
            "rise_after_cross": rise_after_cross,
            "cross_price": cross_price,
            "current_price": current_price,
            "early": early,
            "df": df
        }
 
    except Exception as e:
        print(f"1D hata {symbol}: {e}")
        return None
 
 
# ============================================================
# 4H ANALYSIS (STRATEJİ DEĞİŞMEDİ)
# ============================================================
 
def analyze_4h(symbol):
    try:
        df = market_data[symbol]["4h"].copy()
        if len(df) < 55:
            return None
 
        df = df.iloc[:-1].copy()
        df["ema20"] = calculate_ema(df["close"], 20)
        df["ema50"] = calculate_ema(df["close"], 50)
 
        current = df.iloc[-1]
        previous = df.iloc[-2]
 
        bullish = (
            current["close"] > current["ema20"]
            and current["ema20"] > current["ema50"]
        )
 
        score = 0
        if bullish:
            score += 10
        if current["ema20"] > previous["ema20"]:
            score += 3
 
        return {"score": score, "bullish": bullish}
 
    except Exception:
        return None
 
 
# ============================================================
# 1H ANALYSIS (STRATEJİ DEĞİŞMEDİ)
# ============================================================
 
def analyze_1h(symbol):
    try:
        df = market_data[symbol]["1h"].copy()
        if len(df) < 35:
            return None
 
        closed = df.iloc[:-1].copy()
        closed["ema20"] = calculate_ema(closed["close"], 20)
        closed["rsi"] = calculate_rsi(closed["close"], 14)
 
        current = closed.iloc[-1]
        previous = closed.iloc[-2]
 
        score = 0
 
        if current["close"] > current["ema20"]:
            score += 5
        if current["ema20"] > previous["ema20"]:
            score += 3
 
        rsi = safe_float(current["rsi"])
 
        if 50 <= rsi <= 65:
            score += 5
        elif 65 < rsi <= 72:
            score += 3
        elif rsi > 75:
            score -= 8
 
        current_volume = safe_float(df.iloc[-1]["volume"])
        historical = [
            safe_float(x) for x in closed["volume"].tail(24).tolist()
            if safe_float(x) > 0
        ]
        normal_volume = median(historical) if historical else 0
 
        relative_volume = (
            current_volume / normal_volume if normal_volume > 0 else 0
        )
 
        if 1.0 <= relative_volume <= 1.8:
            score += 4
        elif relative_volume > 2.5:
            score -= 2
 
        if score >= 13:
            label = "🔥 Sağlıklı Momentum"
        elif score >= 8:
            label = "🟢 Momentum Pozitif"
        else:
            label = "🟡 Zayıf"
 
        return {
            "score": score,
            "rsi": rsi,
            "relative_volume": relative_volume,
            "closed_price": safe_float(current["close"]),
            "momentum_label": label
        }
 
    except Exception:
        return None
 
 
# ============================================================
# PRICE POSITION (STRATEJİ DEĞİŞMEDİ)
# ============================================================
 
def analyze_price_position(symbol):
    try:
        df = market_data[symbol]["1h"].copy()
        closed = df.iloc[:-1].copy()
 
        current_price = safe_float(df.iloc[-1]["close"])
        resistance = safe_float(closed["high"].tail(20).max())
 
        if current_price > 0:
            distance = ((resistance - current_price) / current_price) * 100
        else:
            distance = 0
 
        score = 0
        if 0 <= distance <= 3:
            score += 5
 
        if len(closed) >= 24:
            old_price = safe_float(closed.iloc[-24]["close"])
            change_24h = (
                ((current_price - old_price) / old_price) * 100
                if old_price > 0 else 0
            )
        else:
            change_24h = 0
 
        if change_24h > 12:
            score -= 8
        if change_24h > 18:
            score -= 12
 
        return {
            "score": score,
            "distance_percent": distance,
            "change_24h": change_24h
        }
 
    except Exception:
        return {"score": 0, "distance_percent": 0, "change_24h": 0}
 
 
# ============================================================
# OBV BİRİKİM ANALİZİ (WEBSOCKET FLOW/WHALE YERİNE)
# ============================================================
 
def analyze_accumulation(daily_df):
    """
    Günlük mum verisinden OBV hesaplar. Fiyat çok hareket etmeden
    OBV yükseliyorsa bu "sessiz birikim" olarak yorumlanır.
    Websocket gerektirmez, zaten cache'lenmiş 1D veriyle çalışır.
    """
    try:
        df = daily_df.copy()
 
        if len(df) < OBV_LOOKBACK_DAYS + 2:
            return {"strong": False, "score": 0, "divergence": 0, "label": "⚪ Veri Yetersiz"}
 
        df["obv"] = calculate_obv(df)
 
        lookback = OBV_LOOKBACK_DAYS
 
        obv_start = safe_float(df["obv"].iloc[-lookback - 1])
        obv_end = safe_float(df["obv"].iloc[-1])
 
        price_start = safe_float(df["close"].iloc[-lookback - 1])
        price_end = safe_float(df["close"].iloc[-1])
 
        if obv_start != 0:
            obv_change_pct = ((obv_end - obv_start) / abs(obv_start)) * 100
        else:
            obv_change_pct = 0 if obv_end == 0 else 100
 
        if price_start > 0:
            price_change_pct = ((price_end - price_start) / price_start) * 100
        else:
            price_change_pct = 0
 
        divergence = obv_change_pct - price_change_pct
 
        strong = divergence >= MIN_OBV_DIVERGENCE and obv_end > obv_start
 
        if divergence >= 20:
            score = 20
            label = "🐋 Çok Güçlü Birikim"
        elif divergence >= MIN_OBV_DIVERGENCE:
            score = 12
            label = "🐋 Birikim Tespit Edildi"
        elif divergence >= 3:
            score = 5
            label = "🟢 Hafif Birikim"
        else:
            score = 0
            label = "⚪ Belirgin Birikim Yok"
 
        return {
            "strong": strong,
            "score": score,
            "divergence": divergence,
            "obv_change_pct": obv_change_pct,
            "price_change_pct": price_change_pct,
            "label": label
        }
 
    except Exception as e:
        print("OBV hata:", e)
        return {"strong": False, "score": 0, "divergence": 0, "label": "⚪ Hata"}
 
 
# ============================================================
# SCORE
# ============================================================
 
def calculate_score(symbol, market_24h_volume):
    try:
        daily = analyze_1d(symbol)
        four_h = analyze_4h(symbol)
        one_h = analyze_1h(symbol)
 
        if daily is None or four_h is None or one_h is None:
            return None
 
        # EN ÖNEMLİ EARLY FILTER — strateji değişmedi
        if not daily["early"]:
            return None
 
        accumulation = analyze_accumulation(daily["df"])
 
        # Eskiden "canlı para girişi zorunlu"ydu, şimdi
        # "geçmiş OBV birikimi zorunlu" — mantık aynı, veri kaynağı farklı.
        if not accumulation["strong"]:
            return None
 
        price = analyze_price_position(symbol)
 
        total_score = (
            daily["score"]
            + four_h["score"]
            + one_h["score"]
            + accumulation["score"]
            + price["score"]
        )
 
        if market_24h_volume >= 20_000_000:
            total_score += 5
        elif market_24h_volume >= 10_000_000:
            total_score += 3
        elif market_24h_volume >= 5_000_000:
            total_score += 1
 
        rise = daily["rise_after_cross"]
        if rise >= 10:
            total_score -= 8
        if rise >= 12:
            total_score -= 12
 
        total_score = max(0, min(100, total_score))
 
        return {
            "score": total_score,
            "daily": daily,
            "4h": four_h,
            "1h": one_h,
            "accumulation": accumulation,
            "price": price,
            "market_24h_volume": market_24h_volume
        }
 
    except Exception as e:
        print(f"Score hata {symbol}: {e}")
        return None
 
 
# ============================================================
# TOP 100
# ============================================================
 
async def get_top_100_symbols(markets):
    candidates = []
 
    symbols = [
        symbol for symbol, market in markets.items()
        if symbol.endswith("/USDT") and market.get("spot") and market.get("active")
    ]
 
    try:
        tickers = await exchange.fetch_tickers()
 
        for symbol in symbols:
            ticker = tickers.get(symbol)
            if not ticker:
                continue
 
            quote_volume = safe_float(ticker.get("quoteVolume"))
            if quote_volume >= MIN_24H_VOLUME_USDT:
                candidates.append((symbol, quote_volume))
 
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[:TOP_COINS]
 
    except Exception as e:
        print("Top 100 hata:", e)
        return []
 
 
# ============================================================
# SIGNAL FORMAT
# ============================================================
 
def format_signal(symbol, result):
    score = result["score"]
    daily = result["daily"]
    one_h = result["1h"]
    four_h = result["4h"]
    price = result["price"]
    accumulation = result["accumulation"]
 
    if score >= 90:
        emoji = "🔥"
    elif score >= 85:
        emoji = "🟢"
    else:
        emoji = "🟡"
 
    lines = [
        f"{emoji} *{symbol}* — `{score}`",
        f"📈 EMA dönüşü: `{daily['cross_age']} gün önce`",
        f"🎯 Kesişimden sonra: `+{daily['rise_after_cross']:.1f}%`",
        f"⚡ 1H: {one_h['momentum_label']} | RSI `{one_h['rsi']:.0f}`",
        f"{accumulation['label']}: `divergence +{accumulation['divergence']:.1f}`",
        f"📍 4H: {'🟢 Bullish' if four_h['bullish'] else '⚪ Nötr'}"
    ]
 
    if price["distance_percent"] > 0:
        lines.append(f"🚧 Direnç: `%{price['distance_percent']:.1f}`")
 
    # Aşırı uzaklaşma uyarısı — istenen ek özellik
    rise = daily["rise_after_cross"]
    if rise >= RISE_WARNING_THRESHOLD:
        lines.append(
            f"⚠️ *Dikkat:* Kesişimden bu yana `%{rise:.1f}` yükselmiş — "
            f"coin çoktan uçmuş olabilir, giriş riskli."
        )
 
    return "\n".join(lines)
 
 
# ============================================================
# MAIN — TEK SEFERLİK ÇALIŞIR (GitHub Actions cron uyumlu)
# ============================================================
 
async def main():
    print("==========================================")
    print("       OKX SMART EARLY RADAR (REST-ONLY)")
    print("==========================================")
    print(f"Top Coin: {TOP_COINS}")
    print(f"Minimum Score: {MIN_SIGNAL_SCORE}")
    print("Whale/Flow yerine: OBV Birikim Analizi")
    print("==========================================")
 
    markets = await exchange.load_markets()
    top_coins = await get_top_100_symbols(markets)
 
    if not top_coins:
        print("Uygun coin bulunamadı.")
        await exchange.close()
        return
 
    symbols = [x[0] for x in top_coins]
    volume_map = {symbol: volume for symbol, volume in top_coins}
 
    print(f"🚀 {len(symbols)} coin taranıyor...")
 
    await fetch_all(symbols)
 
    state = load_state()
    qualified = []
 
    for symbol in symbols:
        result = calculate_score(symbol, volume_map[symbol])
        if result is None:
            continue
 
        score = result["score"]
        print(
            f"🔎 {symbol:<15} {score:>3}/100 | "
            f"OBV div +{result['accumulation']['divergence']:.1f} | "
            f"Cross {result['daily']['cross_age']}d | "
            f"Rise +{result['daily']['rise_after_cross']:.1f}%"
        )
 
        if score >= MIN_SIGNAL_SCORE and can_send_signal(state, symbol):
            qualified.append({"symbol": symbol, "score": score, "result": result})
 
    qualified.sort(key=lambda x: x["score"], reverse=True)
    qualified = qualified[:MAX_TELEGRAM_SIGNALS]
 
    if qualified:
        lines = [
            "🚨 *SMART EARLY RADAR*",
            "━━━━━━━━━━━━━━━━━━",
            "🎯 Trend dönüşü + geçmiş birikim (OBV)"
        ]
 
        for item in qualified:
            lines.append("")
            lines.append(format_signal(item["symbol"], item["result"]))
            state[item["symbol"]] = time.time()
 
        lines.append("\n━━━━━━━━━━━━━━━━━━")
        await send_telegram_message("\n".join(lines))
 
        save_state(state)
        print(f"✅ {len(qualified)} sinyal gönderildi.")
    else:
        print("Bu çalıştırmada kalifiye sinyal yok.")
 
    await exchange.close()
 
 
if __name__ == "__main__":
    asyncio.run(main())
 
