import asyncio
import aiohttp
import ccxt.pro as ccxtpro
import pandas as pd
import pandas_ta as ta
import os
import time

from collections import deque
from statistics import median


# ============================================================
# TELEGRAM - GITHUB SECRETS
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


# ============================================================
# ANA AYARLAR
# ============================================================

TOP_COINS = 100

# OKX Top 100'e girebilmek için minimum 24H USDT hacmi
MIN_24H_VOLUME_USDT = 1_000_000

# Telegram'a gönderilecek minimum skor
MIN_SIGNAL_SCORE = 70

# 70 / 80 / 90 seviyeleri
SCORE_LEVELS = [70, 80, 90]

# Aynı seviyeyi tekrar göndermeden önce
SIGNAL_COOLDOWN_SECONDS = 4 * 60 * 60

# Büyük işlem
LARGE_TRADE_USDT = 25_000

# Son 1 saat market flow
FLOW_WINDOW_SECONDS = 60 * 60

# Relative Volume
VOLUME_STRONG = 2.0
VOLUME_EXTREME = 3.0

# Cache yenileme
ONE_HOUR_REFRESH = 120
FOUR_HOUR_REFRESH = 600
ONE_DAY_REFRESH = 1800

# Aynı anda maksimum REST isteği
MAX_CONCURRENT_REQUESTS = 5


# ============================================================
# OKX
# ============================================================

exchange = ccxtpro.okx({
    "enableRateLimit": True,
    "options": {
        "defaultType": "spot"
    }
})


# ============================================================
# GLOBAL CACHE
# ============================================================

market_data = {}

market_data_lock = asyncio.Lock()

api_semaphore = asyncio.Semaphore(
    MAX_CONCURRENT_REQUESTS
)


# ============================================================
# SIGNAL STATE
# ============================================================

signal_state = {}


# ============================================================
# TELEGRAM
# ============================================================

async def send_telegram_message(message):

    if not TELEGRAM_BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN bulunamadı.")
        return

    if not TELEGRAM_CHAT_ID:
        print("❌ TELEGRAM_CHAT_ID bulunamadı.")
        return

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }

    try:

        async with aiohttp.ClientSession() as session:

            async with session.post(
                url,
                json=payload,
                timeout=15
            ) as response:

                if response.status == 200:

                    print("✅ Telegram mesajı gönderildi.")

                else:

                    body = await response.text()

                    print(
                        f"❌ Telegram HTTP hata: "
                        f"{response.status} | {body}"
                    )

    except Exception as e:

        print(
            f"❌ Telegram bağlantı hatası: {e}"
        )


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
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]
    )


# ============================================================
# MARKET FLOW
# ============================================================

class MarketFlow:

    def __init__(self):

        self.trades = deque()

        self.buy_volume = 0
        self.sell_volume = 0

        self.large_buy_volume = 0
        self.large_sell_volume = 0

        self.large_buy_count = 0
        self.large_sell_count = 0

        self.last_price = 0

        self.lock = asyncio.Lock()


    async def add_trade(self, trade):

        try:

            price = safe_float(
                trade.get("price")
            )

            amount = safe_float(
                trade.get("amount")
            )

            side = trade.get("side")

            if price <= 0 or amount <= 0:
                return

            value = price * amount

            now = time.monotonic()

            self.last_price = price

            async with self.lock:

                self.trades.append(
                    (
                        now,
                        value,
                        side
                    )
                )

                if side == "buy":

                    self.buy_volume += value

                elif side == "sell":

                    self.sell_volume += value

                if value >= LARGE_TRADE_USDT:

                    if side == "buy":

                        self.large_buy_volume += value
                        self.large_buy_count += 1

                    elif side == "sell":

                        self.large_sell_volume += value
                        self.large_sell_count += 1

                await self.cleanup_locked(now)

        except Exception:

            pass


    async def cleanup_locked(self, now):

        cutoff = (
            now -
            FLOW_WINDOW_SECONDS
        )

        while (
            self.trades
            and
            self.trades[0][0] < cutoff
        ):

            _, value, side = (
                self.trades.popleft()
            )

            if side == "buy":

                self.buy_volume -= value

            elif side == "sell":

                self.sell_volume -= value

            if value >= LARGE_TRADE_USDT:

                if side == "buy":

                    self.large_buy_volume -= value
                    self.large_buy_count -= 1

                elif side == "sell":

                    self.large_sell_volume -= value
                    self.large_sell_count -= 1

        self.buy_volume = max(
            self.buy_volume,
            0
        )

        self.sell_volume = max(
            self.sell_volume,
            0
        )

        self.large_buy_volume = max(
            self.large_buy_volume,
            0
        )

        self.large_sell_volume = max(
            self.large_sell_volume,
            0
        )

        self.large_buy_count = max(
            self.large_buy_count,
            0
        )

        self.large_sell_count = max(
            self.large_sell_count,
            0
        )


    async def cleanup_loop(self):

        while True:

            try:

                async with self.lock:

                    await self.cleanup_locked(
                        time.monotonic()
                    )

            except Exception as e:

                print(
                    f"Flow cleanup hatası: {e}"
                )

            await asyncio.sleep(30)


    async def snapshot(self):

        async with self.lock:

            await self.cleanup_locked(
                time.monotonic()
            )

            buy = max(
                self.buy_volume,
                0
            )

            sell = max(
                self.sell_volume,
                0
            )

            large_buy = max(
                self.large_buy_volume,
                0
            )

            large_sell = max(
                self.large_sell_volume,
                0
            )

            total = buy + sell

            if total > 0:

                buy_ratio = buy / total

            else:

                buy_ratio = 0.5

            large_total = (
                large_buy +
                large_sell
            )

            if large_total > 0:

                large_buy_ratio = (
                    large_buy /
                    large_total
                )

            else:

                large_buy_ratio = 0.5

            return {

                "buy_volume": buy,
                "sell_volume": sell,
                "total_volume": total,
                "buy_ratio": buy_ratio,

                "large_buy_volume":
                    large_buy,

                "large_sell_volume":
                    large_sell,

                "large_buy_count":
                    self.large_buy_count,

                "large_sell_count":
                    self.large_sell_count,

                "large_buy_ratio":
                    large_buy_ratio,

                "last_price":
                    self.last_price
            }


# ============================================================
# REST OHLCV
# ============================================================

async def fetch_ohlcv_safe(
    symbol,
    timeframe,
    limit
):

    async with api_semaphore:

        try:

            return await exchange.fetch_ohlcv(
                symbol,
                timeframe=timeframe,
                limit=limit
            )

        except Exception as e:

            print(
                f"OHLCV hata | "
                f"{symbol} | "
                f"{timeframe} | "
                f"{e}"
            )

            return None


# ============================================================
# CACHE UPDATE
# ============================================================

async def update_symbol_timeframe(
    symbol,
    timeframe,
    limit
):

    candles = await fetch_ohlcv_safe(
        symbol,
        timeframe,
        limit
    )

    if not candles:
        return

    df = create_dataframe(
        candles
    )

    async with market_data_lock:

        if symbol not in market_data:

            market_data[symbol] = {}

        market_data[symbol][
            timeframe
        ] = {

            "df": df,

            "updated_at":
                time.monotonic()
        }


# ============================================================
# CACHE REFRESH
# ============================================================

async def refresh_1d(symbols):

    print("\n📅 1D cache yenileniyor...")

    tasks = [

        update_symbol_timeframe(
            symbol,
            "1d",
            60
        )

        for symbol in symbols
    ]

    await asyncio.gather(
        *tasks,
        return_exceptions=True
    )

    print("✅ 1D cache tamamlandı.")


async def refresh_4h(symbols):

    print("\n📊 4H cache yenileniyor...")

    tasks = [

        update_symbol_timeframe(
            symbol,
            "4h",
            80
        )

        for symbol in symbols
    ]

    await asyncio.gather(
        *tasks,
        return_exceptions=True
    )

    print("✅ 4H cache tamamlandı.")


async def refresh_1h(symbols):

    print("\n⚡ 1H cache yenileniyor...")

    tasks = [

        update_symbol_timeframe(
            symbol,
            "1h",
            50
        )

        for symbol in symbols
    ]

    await asyncio.gather(
        *tasks,
        return_exceptions=True
    )

    print("✅ 1H cache tamamlandı.")


# ============================================================
# 1D ANALYSIS
# ============================================================

def analyze_1d_from_cache(symbol):

    try:

        df = market_data[
            symbol
        ]["1d"]["df"].copy()

        if len(df) < 35:

            return {
                "score": 0,
                "ema_aligned": False,
                "price_change_24h": 0
            }

        # Açık günlük mumu kullanma
        df = df.iloc[:-1].copy()

        df["ema10"] = ta.ema(
            df["close"],
            length=10
        )

        df["ema20"] = ta.ema(
            df["close"],
            length=20
        )

        df["ema30"] = ta.ema(
            df["close"],
            length=30
        )

        current = df.iloc[-1]
        previous = df.iloc[-2]

        score = 0

        ema_aligned = (
            current["ema10"] >
            current["ema20"]
            and
            current["ema20"] >
            current["ema30"]
        )

        if ema_aligned:
            score += 20

        if current["ema10"] > previous["ema10"]:
            score += 3

        if current["close"] > current["ema10"]:
            score += 2

        price_change = (
            (
                current["close"] -
                previous["close"]
            )
            /
            previous["close"]
        ) * 100

        return {

            "score": score,

            "ema_aligned":
                ema_aligned,

            "price_change_24h":
                price_change,

            "close":
                current["close"]
        }

    except Exception as e:

        print(
            f"1D analysis hata "
            f"{symbol}: {e}"
        )

        return {
            "score": 0,
            "ema_aligned": False,
            "price_change_24h": 0,
            "close": 0
        }


# ============================================================
# 4H TREND
# ============================================================

def analyze_4h_from_cache(symbol):

    try:

        df = market_data[
            symbol
        ]["4h"]["df"].copy()

        if len(df) < 55:

            return {
                "score": 0,
                "bullish": False,
                "trend": "UNKNOWN"
            }

        df = df.iloc[:-1].copy()

        df["ema20"] = ta.ema(
            df["close"],
            length=20
        )

        df["ema50"] = ta.ema(
            df["close"],
            length=50
        )

        current = df.iloc[-1]
        previous = df.iloc[-2]

        score = 0

        bullish = (
            current["close"] >
            current["ema20"]
            and
            current["ema20"] >
            current["ema50"]
        )

        bearish = (
            current["close"] <
            current["ema20"]
            and
            current["ema20"] <
            current["ema50"]
        )

        if bullish:

            # 4H yukarı trend BONUS
            score += 15

            if current["ema20"] > previous["ema20"]:

                score += 3

            trend = "UP"

        elif bearish:

            # ÖNEMLİ:
            # 4H bearish sinyali öldürmüyor.
            score += 0

            trend = "DOWN"

        else:

            trend = "NEUTRAL"

        return {

            "score": score,

            "bullish":
                bullish,

            "trend":
                trend
        }

    except Exception as e:

        print(
            f"4H analysis hata "
            f"{symbol}: {e}"
        )

        return {
            "score": 0,
            "bullish": False,
            "trend": "UNKNOWN"
        }


# ============================================================
# 1H MOMENTUM
# ============================================================

def analyze_1h_from_cache(symbol):

    try:

        df = market_data[
            symbol
        ]["1h"]["df"].copy()

        if len(df) < 30:
            return None

        # Son mum açık mum.
        # Momentum hesabında kapalı mumları kullanıyoruz.
        closed = df.iloc[:-1].copy()

        if len(closed) < 25:
            return None

        closed["ema20"] = ta.ema(
            closed["close"],
            length=20
        )

        closed["ema50"] = ta.ema(
            closed["close"],
            length=50
        )

        closed["rsi"] = ta.rsi(
            closed["close"],
            length=14
        )

        macd = ta.macd(
            closed["close"]
        )

        if macd is not None:

            closed["macd"] = (
                macd.iloc[:, 0]
            )

            closed["macd_signal"] = (
                macd.iloc[:, 1]
            )

            closed["macd_hist"] = (
                macd.iloc[:, 2]
            )

        current = closed.iloc[-1]
        previous = closed.iloc[-2]

        score = 0

        # ----------------------------------------------------
        # EMA MOMENTUM
        # ----------------------------------------------------

        if current["close"] > current["ema20"]:
            score += 5

        if current["ema20"] > previous["ema20"]:
            score += 3

        # ----------------------------------------------------
        # RSI
        # ----------------------------------------------------

        if current["rsi"] > previous["rsi"]:
            score += 2

        if current["rsi"] >= 50:
            score += 2

        # Aşırı şişmiş momentum
        if current["rsi"] > 75:
            score -= 5

        # ----------------------------------------------------
        # MACD
        # ----------------------------------------------------

        if "macd_hist" in closed.columns:

            if (
                current["macd_hist"] >
                previous["macd_hist"]
            ):

                score += 3

            if current["macd_hist"] > 0:

                score += 2

        # ----------------------------------------------------
        # 1H RELATIVE VOLUME
        # ----------------------------------------------------

        current_open_candle = df.iloc[-1]

        current_volume = safe_float(
            current_open_candle["volume"]
        )

        historical_volumes = (
            closed["volume"]
            .tail(24)
            .tolist()
        )

        historical_volumes = [

            safe_float(x)

            for x in historical_volumes

            if safe_float(x) > 0
        ]

        if historical_volumes:

            normal_volume = median(
                historical_volumes
            )

        else:

            normal_volume = 0

        if normal_volume > 0:

            relative_volume = (
                current_volume /
                normal_volume
            )

        else:

            relative_volume = 0

        # Hacim güçlü
        if relative_volume >= VOLUME_STRONG:

            score += 12

        # Aşırı hacim
        if relative_volume >= VOLUME_EXTREME:

            score += 5

        return {

            "score": score,

            "relative_volume":
                relative_volume,

            "current_volume":
                current_volume,

            "normal_volume":
                normal_volume,

            "rsi":
                current["rsi"],

            "closed_price":
                current["close"]
        }

    except Exception as e:

        print(
            f"1H analysis hata "
            f"{symbol}: {e}"
        )

        return None


# ============================================================
# PRICE POSITION
# ============================================================

def analyze_price_position_from_cache(
    symbol,
    flow
):

    try:

        df = market_data[
            symbol
        ]["1h"]["df"].copy()

        if len(df) < 25:

            return {
                "score": 0,
                "near_breakout": False,
                "distance_percent": 0,
                "change_24h": 0
            }

        closed = df.iloc[:-1].copy()

        current_price = flow.last_price

        if current_price <= 0:

            current_price = safe_float(
                df.iloc[-1]["close"]
            )

        resistance = (
            closed["high"]
            .tail(20)
            .max()
        )

        if current_price > 0:

            distance_percent = (
                (
                    resistance -
                    current_price
                )
                /
                current_price
            ) * 100

        else:

            distance_percent = 0

        score = 0

        near_breakout = False

        # Direncin %2'si içinde
        if 0 <= distance_percent <= 2:

            score += 8
            near_breakout = True

        # Direnç kırılmış
        if current_price > resistance:

            score += 10
            near_breakout = True

        # Yaklaşık 24H hareket
        if len(closed) >= 24:

            old_price = safe_float(
                closed.iloc[-24]["close"]
            )

            if old_price > 0:

                change_24h = (
                    (
                        current_price -
                        old_price
                    )
                    /
                    old_price
                ) * 100

            else:

                change_24h = 0

        else:

            change_24h = 0

        # Aşırı yükselişi cezalandır
        if change_24h > 25:

            score -= 15

        elif change_24h > 15:

            score -= 10

        return {

            "score": score,

            "near_breakout":
                near_breakout,

            "distance_percent":
                distance_percent,

            "change_24h":
                change_24h
        }

    except Exception as e:

        print(
            f"Price analysis hata "
            f"{symbol}: {e}"
        )

        return {
            "score": 0,
            "near_breakout": False,
            "distance_percent": 0,
            "change_24h": 0
        }


# ============================================================
# MARKET FLOW SCORE
# ============================================================

def calculate_flow_score(flow):

    score = 0

    buy_ratio = flow[
        "buy_ratio"
    ]

    buy = flow[
        "buy_volume"
    ]

    sell = flow[
        "sell_volume"
    ]

    large_buy = flow[
        "large_buy_volume"
    ]

    large_sell = flow[
        "large_sell_volume"
    ]

    large_total = (
        large_buy +
        large_sell
    )

    # --------------------------------------------------------
    # GENEL BUY / SELL FLOW
    # --------------------------------------------------------

    if buy_ratio >= 0.65:

        score += 10

    elif buy_ratio >= 0.58:

        score += 5

    elif buy_ratio <= 0.35:

        score -= 7

    # --------------------------------------------------------
    # AKTİVİTE
    # --------------------------------------------------------

    # Hem alıcı hem satıcı büyük aktivite gösteriyorsa
    # yön kesin değildir ama hareket ihtimali artabilir.
    if (
        large_buy >= LARGE_TRADE_USDT * 5
        and
        large_sell >= LARGE_TRADE_USDT * 5
    ):

        score += 3

    # Büyük alıcı baskısı
    if large_total > 0:

        large_buy_ratio = (
            large_buy /
            large_total
        )

        if large_buy_ratio >= 0.65:

            score += 6

        elif large_buy_ratio >= 0.55:

            score += 3

    # --------------------------------------------------------
    # FLOW AKTİVİTESİ
    # --------------------------------------------------------

    total_flow = buy + sell

    if total_flow >= 100_000:

        score += 3

    if total_flow >= 500_000:

        score += 3

    return score


# ============================================================
# SCORE CALCULATION
# ============================================================

async def calculate_score(
    symbol,
    flow,
    market_24h_volume
):

    try:

        async with market_data_lock:

            if symbol not in market_data:
                return None

            if "1d" not in market_data[symbol]:
                return None

            if "4h" not in market_data[symbol]:
                return None

            if "1h" not in market_data[symbol]:
                return None

        daily = analyze_1d_from_cache(
            symbol
        )

        four_h = analyze_4h_from_cache(
            symbol
        )

        one_h = analyze_1h_from_cache(
            symbol
        )

        if one_h is None:
            return None

        flow_data = await flow.snapshot()

        flow_score = calculate_flow_score(
            flow_data
        )

        price = (
            analyze_price_position_from_cache(
                symbol,
                flow
            )
        )

        raw_score = (
            daily["score"]
            +
            four_h["score"]
            +
            one_h["score"]
            +
            flow_score
            +
            price["score"]
        )

        # ----------------------------------------------------
        # LIQUIDITY BONUS
        # ----------------------------------------------------

        liquidity_bonus = 0

        if market_24h_volume >= 10_000_000:

            liquidity_bonus = 5

        elif market_24h_volume >= 5_000_000:

            liquidity_bonus = 3

        raw_score += liquidity_bonus

        # ----------------------------------------------------
        # 100'E NORMALİZE ET
        # ----------------------------------------------------

        # Sistemimizin teorik maksimumu yaklaşık 100+
        # olabileceği için Telegram'da 100 üzerinden gösteriyoruz.
        score = max(
            0,
            min(
                100,
                raw_score
            )
        )

        return {

            "score":
                score,

            "raw_score":
                raw_score,

            "daily":
                daily,

            "4h":
                four_h,

            "1h":
                one_h,

            "flow":
                flow_data,

            "price":
                price,

            "market_24h_volume":
                market_24h_volume
        }

    except Exception as e:

        print(
            f"Score hata {symbol}: {e}"
        )

        return None


# ============================================================
# TOP 100
# ============================================================

async def get_top_100_symbols(markets):

    candidates = []

    symbols = [

        symbol

        for symbol, market
        in markets.items()

        if (
            symbol.endswith("/USDT")
            and
            market.get("spot")
            and
            market.get("active")
        )
    ]

    print(
        f"OKX USDT spot market: "
        f"{len(symbols)}"
    )

    try:

        tickers = await exchange.fetch_tickers()

        for symbol in symbols:

            ticker = tickers.get(
                symbol
            )

            if not ticker:
                continue

            quote_volume = safe_float(
                ticker.get("quoteVolume")
            )

            if (
                quote_volume >=
                MIN_24H_VOLUME_USDT
            ):

                candidates.append(
                    (
                        symbol,
                        quote_volume
                    )
                )

        candidates.sort(
            key=lambda x: x[1],
            reverse=True
        )

        top = candidates[
            :TOP_COINS
        ]

        print(
            "\n========== OKX TOP 100 =========="
        )

        for index, (
            symbol,
            volume
        ) in enumerate(
            top,
            1
        ):

            print(
                f"{index:03d}. "
                f"{symbol:<16} "
                f"${volume:,.0f}"
            )

        print(
            "=================================\n"
        )

        return top

    except Exception as e:

        print(
            "Top 100 hata:",
            e
        )

        return []


# ============================================================
# TELEGRAM MESSAGE
# ============================================================

def create_signal_message(
    symbol,
    result,
    level
):

    score = result["score"]

    daily = result["daily"]
    four_h = result["4h"]
    one_h = result["1h"]
    flow = result["flow"]
    price = result["price"]

    if level >= 90:

        priority = "🚨 HIGH PRIORITY"

    elif level >= 80:

        priority = "🟢 STRONG WATCH"

    else:

        priority = "🟡 RADAR"

    buy_ratio = (
        flow["buy_ratio"] * 100
    )

    if four_h["trend"] == "UP":

        trend_text = "🟢 YUKARI"

    elif four_h["trend"] == "DOWN":

        trend_text = "🔴 AŞAĞI"

    else:

        trend_text = "⚪ NÖTR"

    message = (

        f"{priority}\n\n"

        f"🪙 *Coin:* `{symbol}`\n"
        f"⭐ *Score:* `{score:.0f}/100`\n"
        f"🎯 *Seviye:* `{level}+`\n\n"

        f"📅 *1D TREND*\n"

        f"EMA 10 > 20 > 30: "
        f"{'🟢 EVET' if daily['ema_aligned'] else '⚪ HAYIR'}\n"

        f"1D Değişim: "
        f"`{daily['price_change_24h']:.2f}%`\n\n"

        f"📈 *4H TREND*\n"

        f"Trend: `{trend_text}`\n"

        f"4H yukarı trend: "
        f"{'🟢 BONUS' if four_h['bullish'] else '⚪ YOK'}\n\n"

        f"⚡ *1H MOMENTUM*\n"

        f"RSI: `{one_h['rsi']:.1f}`\n"

        f"Relative Volume: "
        f"`{one_h['relative_volume']:.2f}x`\n"

        f"Normal 1H Volume: "
        f"`{one_h['normal_volume']:,.0f}`\n"

        f"Current 1H Volume: "
        f"`{one_h['current_volume']:,.0f}`\n\n"

        f"🔥 *SON 1 SAAT MARKET FLOW*\n"

        f"Buy: "
        f"`{flow['buy_volume']:,.0f} USDT`\n"

        f"Sell: "
        f"`{flow['sell_volume']:,.0f} USDT`\n"

        f"Buy Ratio: "
        f"`{buy_ratio:.1f}%`\n\n"

        f"🐋 *LARGE TRADE FLOW*\n"

        f"Large Buy: "
        f"`{flow['large_buy_volume']:,.0f} USDT`\n"

        f"Large Sell: "
        f"`{flow['large_sell_volume']:,.0f} USDT`\n"

        f"Large Buy Count: "
        f"`{flow['large_buy_count']}`\n"

        f"Large Sell Count: "
        f"`{flow['large_sell_count']}`\n\n"

        f"🎯 *PRICE POSITION*\n"

        f"Breakout mesafesi: "
        f"`{price['distance_percent']:.2f}%`\n"

        f"24H hareket: "
        f"`{price['change_24h']:.2f}%`\n\n"

        f"💧 *OKX 24H VOLUME*\n"

        f"`{result['market_24h_volume']:,.0f} USDT`\n\n"

        f"💡 *Yorum:*\n"

        f"Coin kısa vadeli 1m/5m scalp "
        f"sinyali olarak değil, spot manuel "
        f"inceleme için radara alındı.\n"

        f"4H trend + 1H momentum + hacim "
        f"+ son 1 saat market flow birlikte "
        f"değerlendirildi."
    )

    return message


# ============================================================
# SIGNAL LEVEL
# ============================================================

def get_new_signal_level(
    symbol,
    score
):

    now = time.monotonic()

    state = signal_state.get(
        symbol,
        {
            "last_level": 0,
            "last_time": 0
        }
    )

    last_level = state[
        "last_level"
    ]

    last_time = state[
        "last_time"
    ]

    current_level = 0

    for level in SCORE_LEVELS:

        if score >= level:

            current_level = level

    if current_level == 0:

        signal_state[
            symbol
        ] = {

            "last_level": 0,

            "last_time":
                last_time
        }

        return 0

    # Yeni üst seviyeye geçtiyse
    if current_level > last_level:

        signal_state[
            symbol
        ] = {

            "last_level":
                current_level,

            "last_time":
                now
        }

        return current_level

    # Aynı seviyede cooldown
    if (
        current_level == last_level
        and
        now - last_time >=
        SIGNAL_COOLDOWN_SECONDS
    ):

        signal_state[
            symbol
        ] = {

            "last_level":
                current_level,

            "last_time":
                now
        }

        return current_level

    return 0


# ============================================================
# WEBSOCKET LISTENER
# ============================================================

async def trade_listener(
    symbol,
    flow
):

    retry_delay = 2

    while True:

        try:

            print(
                f"🔌 WS bağlanıyor: "
                f"{symbol}"
            )

            while True:

                trades = (
                    await exchange.watch_trades(
                        symbol
                    )
                )

                for trade in trades:

                    await flow.add_trade(
                        trade
                    )

                retry_delay = 2

        except asyncio.CancelledError:

            raise

        except Exception as e:

            print(
                f"⚠️ WS hata "
                f"{symbol}: {e}"
            )

            print(
                f"🔄 {retry_delay} saniye "
                f"sonra yeniden bağlanacak..."
            )

            await asyncio.sleep(
                retry_delay
            )

            retry_delay = min(
                retry_delay * 2,
                60
            )


# ============================================================
# EVALUATOR
# ============================================================

async def evaluator(
    symbol,
    flow,
    market_24h_volume
):

    while True:

        try:

            result = await calculate_score(
                symbol,
                flow,
                market_24h_volume
            )

            if result is not None:

                score = result["score"]

                print(
                    f"🔎 {symbol:<15} "
                    f"Score: "
                    f"{score:>3.0f}/100 | "
                    f"4H: "
                    f"{result['4h']['trend']:<7} | "
                    f"RVOL: "
                    f"{result['1h']['relative_volume']:.2f}x | "
                    f"Buy: "
                    f"{result['flow']['buy_ratio'] * 100:.0f}%"
                )

                new_level = (
                    get_new_signal_level(
                        symbol,
                        score
                    )
                )

                if new_level >= MIN_SIGNAL_SCORE:

                    print(
                        f"\n🚨 SİNYAL "
                        f"{symbol} "
                        f"| {score:.0f}/100 "
                        f"| LEVEL {new_level}+\n"
                    )

                    message = (
                        create_signal_message(
                            symbol,
                            result,
                            new_level
                        )
                    )

                    await send_telegram_message(
                        message
                    )

        except Exception as e:

            print(
                f"Evaluator hata "
                f"{symbol}: {e}"
            )

        # 1 dakikada bir değerlendirme.
        # Bu 1m/5m sinyali değildir.
        await asyncio.sleep(60)


# ============================================================
# COIN WORKER
# ============================================================

async def coin_worker(
    symbol,
    market_24h_volume
):

    flow = MarketFlow()

    cleanup_task = asyncio.create_task(
        flow.cleanup_loop()
    )

    ws_task = asyncio.create_task(
        trade_listener(
            symbol,
            flow
        )
    )

    evaluator_task = asyncio.create_task(
        evaluator(
            symbol,
            flow,
            market_24h_volume
        )
    )

    try:

        await asyncio.gather(
            ws_task,
            evaluator_task
        )

    finally:

        cleanup_task.cancel()
        ws_task.cancel()
        evaluator_task.cancel()


# ============================================================
# CACHE MANAGER
# ============================================================

async def cache_manager(symbols):

    # İlk cache
    await asyncio.gather(

        refresh_1d(
            symbols
        ),

        refresh_4h(
            symbols
        ),

        refresh_1h(
            symbols
        )
    )

    async def daily_loop():

        while True:

            await asyncio.sleep(
                ONE_DAY_REFRESH
            )

            await refresh_1d(
                symbols
            )

    async def four_hour_loop():

        while True:

            await asyncio.sleep(
                FOUR_HOUR_REFRESH
            )

            await refresh_4h(
                symbols
            )

    async def one_hour_loop():

        while True:

            await asyncio.sleep(
                ONE_HOUR_REFRESH
            )

            await refresh_1h(
                symbols
            )

    await asyncio.gather(

        daily_loop(),

        four_hour_loop(),

        one_hour_loop()
    )


# ============================================================
# MAIN
# ============================================================

async def main():

    print()

    print(
        "=============================================="
    )

    print(
        "       OKX SPOT SMART RADAR V4"
    )

    print(
        "=============================================="
    )

    print(
        f"Top Coin: {TOP_COINS}"
    )

    print(
        f"Minimum 24H Volume: "
        f"${MIN_24H_VOLUME_USDT:,.0f}"
    )

    print(
        f"Telegram Threshold: "
        f"{MIN_SIGNAL_SCORE}+"
    )

    print(
        "1D: EMA 10 > 20 > 30"
    )

    print(
        "4H: Trend yukarıysa BONUS"
    )

    print(
        "1H: Momentum + Relative Volume"
    )

    print(
        "Flow: Son 1H Buy/Sell + Large Trades"
    )

    print(
        "1m/5m: Sinyal YOK"
    )

    print(
        "Telegram: GitHub Secrets"
    )

    print(
        "=============================================="
    )

    print()

    if not TELEGRAM_BOT_TOKEN:

        print(
            "❌ TELEGRAM_BOT_TOKEN bulunamadı!"
        )

    if not TELEGRAM_CHAT_ID:

        print(
            "❌ TELEGRAM_CHAT_ID bulunamadı!"
        )

    markets = await exchange.load_markets()

    top_coins = (
        await get_top_100_symbols(
            markets
        )
    )

    if not top_coins:

        print(
            "❌ Uygun coin bulunamadı."
        )

        return

    symbols = [
        x[0]
        for x in top_coins
    ]

    volume_map = {

        symbol: volume

        for symbol, volume
        in top_coins
    }

    print(
        f"🚀 {len(symbols)} coin "
        f"radara alınıyor..."
    )

    cache_task = asyncio.create_task(
        cache_manager(
            symbols
        )
    )

    worker_tasks = [

        coin_worker(
            symbol,
            volume_map[symbol]
        )

        for symbol in symbols
    ]

    try:

        await asyncio.gather(
            cache_task,
            *worker_tasks
        )

    except asyncio.CancelledError:

        cache_task.cancel()

        raise


# ============================================================
# START
# ============================================================

async def run_bot():

    try:

        await main()

    except KeyboardInterrupt:

        print(
            "\nBot durduruldu."
        )

    finally:

        try:

            await exchange.close()

        except Exception:

            pass


if __name__ == "__main__":

    asyncio.run(
        run_bot()
    )
