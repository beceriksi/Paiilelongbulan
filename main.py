import asyncio
import aiohttp
import ccxt.pro as ccxtpro
import pandas as pd
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

# OKX Top 100 için minimum 24H hacim
MIN_24H_VOLUME_USDT = 1_000_000

# Telegram minimum skor
MIN_SIGNAL_SCORE = 70

# ============================================================
# EN ÖNEMLİ FİLTRE
# ============================================================

# Para akışının coin'in normal 1H hacmine göre
# minimum anlamlı olması gerekir.
#
# Örneğin normal 1H hacim = 10M USDT ise:
# toplam flow en az 1.5M olmalı.
#
MIN_FLOW_TOTAL_RATIO = 0.15

# Net alımın normal 1H hacme oranı
MIN_NET_FLOW_RATIO = 0.08

# Minimum buy oranı
MIN_BUY_RATIO = 0.60

# Küçük mutlak akışların tamamen elenmesi
MIN_NET_FLOW_USDT = 25_000


# ============================================================
# SIGNAL COOLDOWN
# ============================================================

SIGNAL_COOLDOWN_SECONDS = 4 * 60 * 60

SCORE_LEVELS = [70, 80, 90]


# ============================================================
# LARGE TRADE
# ============================================================

BASE_LARGE_TRADE_USDT = 25_000


# ============================================================
# MARKET FLOW
# ============================================================

FLOW_WINDOW_SECONDS = 60 * 60


# ============================================================
# RELATIVE VOLUME
# ============================================================

VOLUME_STRONG = 1.5
VOLUME_EXTREME = 2.5


# ============================================================
# CACHE
# ============================================================

ONE_HOUR_REFRESH = 120
FOUR_HOUR_REFRESH = 600
ONE_DAY_REFRESH = 1800

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
# TELEGRAM BATCH
# ============================================================

pending_signals = {}

pending_lock = asyncio.Lock()


# ============================================================
# TELEGRAM
# ============================================================

async def send_telegram_message(message):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:

        print("❌ Telegram Secret bulunamadı.")

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

                if response.status != 200:

                    text = await response.text()

                    print(
                        "Telegram HTTP hata:",
                        response.status,
                        text
                    )

    except Exception as e:

        print(
            "Telegram bağlantı hatası:",
            e
        )


# ============================================================
# HELPERS
# ============================================================

def safe_float(value, default=0):

    try:

        if value is None:
            return default

        return float(value)

    except:

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
# EMA
# ============================================================

def calculate_ema(series, period):

    return series.ewm(
        span=period,
        adjust=False
    ).mean()


# ============================================================
# RSI
# ============================================================

def calculate_rsi(series, period=14):

    delta = series.diff()

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    rs = avg_gain / avg_loss.replace(
        0,
        1e-10
    )

    rsi = 100 - (
        100 / (1 + rs)
    )

    return rsi


# ============================================================
# MACD
# ============================================================

def calculate_macd(series):

    ema12 = calculate_ema(
        series,
        12
    )

    ema26 = calculate_ema(
        series,
        26
    )

    macd = ema12 - ema26

    signal = calculate_ema(
        macd,
        9
    )

    histogram = (
        macd - signal
    )

    return (
        macd,
        signal,
        histogram
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


                # Büyük işlem.
                # Mutlak 25K minimum.
                if value >= BASE_LARGE_TRADE_USDT:

                    if side == "buy":

                        self.large_buy_volume += value

                        self.large_buy_count += 1

                    elif side == "sell":

                        self.large_sell_volume += value

                        self.large_sell_count += 1


                await self.cleanup_locked(
                    now
                )

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


            if value >= BASE_LARGE_TRADE_USDT:

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


    async def cleanup_loop(self):

        while True:

            try:

                async with self.lock:

                    await self.cleanup_locked(
                        time.monotonic()
                    )

            except Exception as e:

                print(
                    "Flow cleanup hata:",
                    e
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

                buy_ratio = (
                    buy / total
                )

            else:

                buy_ratio = 0.5


            net_flow = (
                buy - sell
            )


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

                "buy_volume":
                    buy,

                "sell_volume":
                    sell,

                "total_volume":
                    total,

                "net_flow":
                    net_flow,

                "buy_ratio":
                    buy_ratio,

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
# REST
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

            "df":
                df,

            "updated_at":
                time.monotonic()
        }


# ============================================================
# CACHE REFRESH
# ============================================================

async def refresh_1d(symbols):

    print(
        "\n📅 1D cache yenileniyor..."
    )

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

    print(
        "✅ 1D tamamlandı."
    )


async def refresh_4h(symbols):

    print(
        "\n📊 4H cache yenileniyor..."
    )

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

    print(
        "✅ 4H tamamlandı."
    )


async def refresh_1h(symbols):

    print(
        "\n⚡ 1H cache yenileniyor..."
    )

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

    print(
        "✅ 1H tamamlandı."
    )


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


        # Açık mum yok
        df = df.iloc[:-1].copy()


        df["ema10"] = calculate_ema(
            df["close"],
            10
        )

        df["ema20"] = calculate_ema(
            df["close"],
            20
        )

        df["ema30"] = calculate_ema(
            df["close"],
            30
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

            score += 18


        if (
            current["ema10"] >
            previous["ema10"]
        ):

            score += 2


        if (
            current["close"] >
            current["ema10"]
        ):

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

            "score":
                score,

            "ema_aligned":
                ema_aligned,

            "price_change_24h":
                price_change,

            "close":
                current["close"]
        }


    except Exception as e:

        print(
            f"1D hata {symbol}: {e}"
        )

        return {
            "score": 0,
            "ema_aligned": False,
            "price_change_24h": 0
        }


# ============================================================
# 4H ANALYSIS
# ============================================================

def analyze_4h_from_cache(symbol):

    try:

        df = market_data[
            symbol
        ]["4h"]["df"].copy()

        if len(df) < 55:

            return {
                "score": 0,
                "bullish": False
            }


        df = df.iloc[:-1].copy()


        df["ema20"] = calculate_ema(
            df["close"],
            20
        )

        df["ema50"] = calculate_ema(
            df["close"],
            50
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


        if bullish:

            score += 15


        if (
            current["ema20"] >
            previous["ema20"]
        ):

            score += 3


        # Bearish trend puan düşürür
        # ama sinyali öldürmez.
        if (

            current["close"] <
            current["ema20"]

            and

            current["ema20"] <
            current["ema50"]
        ):

            score -= 8


        return {

            "score":
                score,

            "bullish":
                bullish
        }


    except Exception as e:

        print(
            f"4H hata {symbol}: {e}"
        )

        return {
            "score": 0,
            "bullish": False
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


        closed = df.iloc[:-1].copy()


        if len(closed) < 25:

            return None


        closed["ema20"] = calculate_ema(
            closed["close"],
            20
        )

        closed["ema50"] = calculate_ema(
            closed["close"],
            50
        )

        closed["rsi"] = calculate_rsi(
            closed["close"],
            14
        )


        (
            closed["macd"],
            closed["macd_signal"],
            closed["macd_hist"]
        ) = calculate_macd(
            closed["close"]
        )


        current = closed.iloc[-1]

        previous = closed.iloc[-2]


        score = 0


        # Fiyat EMA20 üzerinde
        if (
            current["close"] >
            current["ema20"]
        ):

            score += 5


        # EMA20 yükseliyor
        if (
            current["ema20"] >
            previous["ema20"]
        ):

            score += 3


        # RSI yükseliyor
        if (
            current["rsi"] >
            previous["rsi"]
        ):

            score += 2


        # RSI 50 üzerinde
        if current["rsi"] >= 50:

            score += 2


        # Aşırı şişmiş momentum
        if current["rsi"] > 78:

            score -= 5


        # MACD histogram güçleniyor
        if (
            current["macd_hist"] >
            previous["macd_hist"]
        ):

            score += 3


        if current["macd_hist"] > 0:

            score += 2


        # ----------------------------------------------------
        # CURRENT 1H VOLUME
        # ----------------------------------------------------

        current_open = df.iloc[-1]

        current_volume = safe_float(
            current_open["volume"]
        )


        historical_volumes = [

            safe_float(x)

            for x in closed[
                "volume"
            ].tail(24).tolist()

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


        if relative_volume >= VOLUME_STRONG:

            score += 10


        if relative_volume >= VOLUME_EXTREME:

            score += 5


        # Momentum label
        if score >= 18:

            momentum_label = (
                "🔥 Güçlü Momentum"
            )

        elif score >= 11:

            momentum_label = (
                "🟢 Momentum Güçleniyor"
            )

        else:

            momentum_label = (
                "🟡 Zayıf Momentum"
            )


        return {

            "score":
                score,

            "relative_volume":
                relative_volume,

            "current_volume":
                current_volume,

            "normal_volume":
                normal_volume,

            "rsi":
                current["rsi"],

            "momentum_label":
                momentum_label,

            "closed_price":
                current["close"]
        }


    except Exception as e:

        print(
            f"1H hata {symbol}: {e}"
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


        distance_percent = (

            (
                resistance -
                current_price
            )
            /
            current_price

        ) * 100


        score = 0

        near_breakout = False


        if (
            0 <= distance_percent <= 2
        ):

            score += 8

            near_breakout = True


        if current_price > resistance:

            score += 10

            near_breakout = True


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


        if change_24h > 25:

            score -= 12

        elif change_24h > 15:

            score -= 7


        return {

            "score":
                score,

            "near_breakout":
                near_breakout,

            "distance_percent":
                distance_percent,

            "change_24h":
                change_24h
        }


    except Exception as e:

        print(
            f"Price hata {symbol}: {e}"
        )

        return {
            "score": 0,
            "near_breakout": False,
            "distance_percent": 0,
            "change_24h": 0
        }


# ============================================================
# FLOW ANALYSIS
# ============================================================

def analyze_flow(
    flow,
    normal_1h_volume
):

    buy = flow[
        "buy_volume"
    ]

    sell = flow[
        "sell_volume"
    ]

    total = flow[
        "total_volume"
    ]

    net = flow[
        "net_flow"
    ]

    buy_ratio = flow[
        "buy_ratio"
    ]


    if normal_1h_volume <= 0:

        return {

            "score": 0,

            "strong": False,

            "buy_ratio":
                buy_ratio,

            "net_flow":
                net,

            "total":
                total,

            "flow_ratio":
                0,

            "net_ratio":
                0,

            "label":
                "⚪ Zayıf",

            "activity":
                "Yetersiz"
        }


    flow_ratio = (
        total /
        normal_1h_volume
    )


    net_ratio = (
        net /
        normal_1h_volume
    )


    # --------------------------------------------------------
    # GERÇEK PARA GİRİŞİ
    # --------------------------------------------------------

    strong_flow = (

        buy_ratio >=
        MIN_BUY_RATIO

        and

        flow_ratio >=
        MIN_FLOW_TOTAL_RATIO

        and

        net_ratio >=
        MIN_NET_FLOW_RATIO

        and

        net >=
        MIN_NET_FLOW_USDT
    )


    score = 0


    if strong_flow:

        score += 12


    if (
        buy_ratio >= 0.65
        and
        net_ratio >= 0.15
    ):

        score += 6


    if (
        buy_ratio >= 0.70
        and
        net_ratio >= 0.20
    ):

        score += 5


    # Aktivite
    if flow_ratio >= 0.30:

        activity = "🔥 Çok Güçlü"

    elif flow_ratio >= 0.20:

        activity = "🟢 Güçlü"

    elif flow_ratio >= 0.15:

        activity = "🟡 Orta"

    else:

        activity = "⚪ Zayıf"


    if strong_flow:

        if net_ratio >= 0.20:

            label = "🔥 Güçlü Para Girişi"

        else:

            label = "🟢 Para Girişi"


    else:

        label = "⚪ Anlamlı Para Girişi Yok"


    return {

        "score":
            score,

        "strong":
            strong_flow,

        "buy_ratio":
            buy_ratio,

        "net_flow":
            net,

        "total":
            total,

        "flow_ratio":
            flow_ratio,

        "net_ratio":
            net_ratio,

        "label":
            label,

        "activity":
            activity
    }


# ============================================================
# WHALE ANALYSIS
# ============================================================

def analyze_whale(
    flow,
    normal_1h_volume
):

    large_buy = flow[
        "large_buy_volume"
    ]

    large_sell = flow[
        "large_sell_volume"
    ]

    total_large = (
        large_buy +
        large_sell
    )


    if normal_1h_volume <= 0:

        return {
            "meaningful": False,
            "label": ""
        }


    # Büyük işlemlerin normal hacme oranı
    large_ratio = (
        total_large /
        normal_1h_volume
    )


    # Normal 1H hacmin en az %2'si
    if large_ratio < 0.02:

        return {
            "meaningful": False,
            "label": ""
        }


    if (
        large_buy > 0
        and
        large_sell > 0
    ):

        if large_buy >= large_sell * 1.5:

            return {
                "meaningful": True,
                "label":
                    "🐋 Büyük Alıcı Aktivitesi"
            }

        elif large_sell >= large_buy * 1.5:

            return {
                "meaningful": True,
                "label":
                    "🐋 Büyük Satıcı Aktivitesi"
            }

        else:

            return {
                "meaningful": True,
                "label":
                    "🐋 Büyük Oyuncu Aktivitesi"
            }


    if large_buy > large_sell:

        return {
            "meaningful": True,
            "label":
                "🐋 Büyük Alıcı Aktivitesi"
        }


    if large_sell > large_buy:

        return {
            "meaningful": True,
            "label":
                "🐋 Büyük Satıcı Aktivitesi"
        }


    return {
        "meaningful": False,
        "label": ""
    }


# ============================================================
# SCORE
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


        # ----------------------------------------------------
        # NORMAL 1H HACİM
        # ----------------------------------------------------

        normal_1h_volume = (
            one_h["normal_volume"] *
            one_h["closed_price"]
        )


        # OKX candle volume coin miktarıdır.
        # USDT karşılığını hesaplıyoruz.


        flow_analysis = analyze_flow(

            flow_data,

            normal_1h_volume
        )


        whale = analyze_whale(

            flow_data,

            normal_1h_volume
        )


        price = (
            analyze_price_position_from_cache(
                symbol,
                flow
            )
        )


        total_score = (

            daily["score"]

            +

            four_h["score"]

            +

            one_h["score"]

            +

            flow_analysis["score"]

            +

            price["score"]
        )


        # ----------------------------------------------------
        # LIQUIDITY BONUS
        # ----------------------------------------------------

        if market_24h_volume >= 20_000_000:

            total_score += 5

        elif market_24h_volume >= 10_000_000:

            total_score += 4

        elif market_24h_volume >= 5_000_000:

            total_score += 2


        total_score = max(
            0,
            min(
                100,
                total_score
            )
        )


        return {

            "score":
                total_score,

            "daily":
                daily,

            "4h":
                four_h,

            "1h":
                one_h,

            "flow":
                flow_data,

            "flow_analysis":
                flow_analysis,

            "whale":
                whale,

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

async def get_top_100_symbols(
    markets
):

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


    if (

        current_level ==
        last_level

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
# SIGNAL TEXT
# ============================================================

def format_signal(
    symbol,
    result,
    level
):

    score = result["score"]

    four_h = result["4h"]

    one_h = result["1h"]

    flow = result["flow_analysis"]

    price = result["price"]


    if level >= 90:

        emoji = "🔥"

    elif level >= 80:

        emoji = "🟢"

    else:

        emoji = "🟡"


    trend = (
        "🟢 Bullish"
        if four_h["bullish"]
        else
        "⚪ Nötr/Bearish"
    )


    return (

        f"{emoji} *{symbol}* — `{score}`\n"

        f"📈 4H: {trend}\n"

        f"⚡ 1H: "
        f"{one_h['momentum_label']}\n"

        f"📊 Hacim: "
        f"`{one_h['relative_volume']:.1f}x`\n"

        f"💰 Flow: "
        f"`+{flow['net_flow']:,.0f} USDT` "
        f"({flow['buy_ratio'] * 100:.0f}% alış)\n"

        f"🔥 Aktivite: "
        f"{flow['activity']}\n"

        f"🎯 Direnç: "
        f"`%{max(price['distance_percent'], 0):.1f}`"

    )


# ============================================================
# BATCH TELEGRAM
# ============================================================

async def batch_sender():

    while True:

        await asyncio.sleep(300)

        try:

            async with pending_lock:

                if not pending_signals:

                    continue

                signals = list(
                    pending_signals.values()
                )

                pending_signals.clear()


            # Score'a göre sırala
            signals.sort(
                key=lambda x: x["score"],
                reverse=True
            )


            high = [
                x for x in signals
                if x["score"] >= 90
            ]


            strong = [
                x for x in signals
                if 80 <= x["score"] < 90
            ]


            radar = [
                x for x in signals
                if 70 <= x["score"] < 80
            ]


            lines = []

            lines.append(
                "🚨 *OKX SPOT SMART RADAR*"
            )

            lines.append(
                "━━━━━━━━━━━━━━━━━━"
            )


            # HIGH
            if high:

                lines.append(
                    "\n🔥 *HIGH PRIORITY*"
                )

                for item in high:

                    lines.append(
                        format_signal(
                            item["symbol"],
                            item["result"],
                            item["level"]
                        )
                    )


            # STRONG
            if strong:

                lines.append(
                    "\n🟢 *STRONG WATCH*"
                )

                for item in strong:

                    lines.append(
                        format_signal(
                            item["symbol"],
                            item["result"],
                            item["level"]
                        )
                    )


            # RADAR
            if radar:

                lines.append(
                    "\n🟡 *RADAR*"
                )

                for item in radar:

                    lines.append(
                        format_signal(
                            item["symbol"],
                            item["result"],
                            item["level"]
                        )
                    )


            # ------------------------------------------------
            # WHALE
            # ------------------------------------------------

            whale_items = [

                x for x in signals

                if x["result"]["whale"]["meaningful"]
            ]


            if whale_items:

                lines.append(
                    "\n🐋 *BÜYÜK OYUNCU AKTİVİTESİ*"
                )

                for item in whale_items:

                    lines.append(
                        f"{item['symbol']} — "
                        f"{item['result']['whale']['label']}"
                    )


            lines.append(
                "\n━━━━━━━━━━━━━━━━━━"
            )

            lines.append(
                f"📊 {len(signals)} güçlü fırsat "
                f"tespit edildi."
            )


            message = "\n".join(
                lines
            )


            await send_telegram_message(
                message
            )


        except Exception as e:

            print(
                "Batch sender hata:",
                e
            )


# ============================================================
# WEBSOCKET
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

    # İlk 1 saat flow oluşmadan
    # sinyal vermesin.
    await asyncio.sleep(60)


    while True:

        try:

            result = await calculate_score(

                symbol,

                flow,

                market_24h_volume
            )


            if result is not None:

                score = result[
                    "score"
                ]


                flow_info = result[
                    "flow_analysis"
                ]


                print(

                    f"🔎 {symbol:<15} "

                    f"Score: "
                    f"{score:>3}/100 | "

                    f"Flow: "
                    f"{flow_info['net_flow']:,.0f} | "

                    f"Buy: "
                    f"{flow_info['buy_ratio'] * 100:.0f}% | "

                    f"RVOL: "
                    f"{result['1h']['relative_volume']:.2f}x"
                )


                # =================================================
                # EN ÖNEMLİ FİLTRE
                #
                # Skor 70+ olsa bile
                # güçlü para girişi yoksa TELEGRAM YOK.
                # =================================================

                if (

                    score >=
                    MIN_SIGNAL_SCORE

                    and

                    flow_info["strong"]
                ):

                    new_level = (
                        get_new_signal_level(
                            symbol,
                            score
                        )
                    )


                    if new_level >= MIN_SIGNAL_SCORE:

                        async with pending_lock:

                            pending_signals[
                                symbol
                            ] = {

                                "symbol":
                                    symbol,

                                "score":
                                    score,

                                "level":
                                    new_level,

                                "result":
                                    result
                            }


                        print(

                            f"🚨 GÜÇLÜ PARA GİRİŞİ "
                            f"{symbol} "
                            f"| {score}/100 "
                            f"| Flow "
                            f"+{flow_info['net_flow']:,.0f}"
                        )


        except Exception as e:

            print(
                f"Evaluator hata "
                f"{symbol}: {e}"
            )


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

async def cache_manager(
    symbols
):

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
        "4H: Trend Bonus"
    )

    print(
        "1H: Momentum + Relative Volume"
    )

    print(
        "Flow: GERÇEK PARA GİRİŞİ ZORUNLU"
    )

    print(
        "1m/5m: Sinyal YOK"
    )

    print(
        "Telegram: TOPLU MESAJ"
    )

    print(
        "=============================================="
    )

    print()


    markets = await exchange.load_markets()


    top_coins = (
        await get_top_100_symbols(
            markets
        )
    )


    if not top_coins:

        print(
            "Uygun coin bulunamadı."
        )

        return


    symbols = [

        x[0]

        for x in top_coins
    ]


    volume_map = {

        symbol:
            volume

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


    batch_task = asyncio.create_task(

        batch_sender()
    )


    worker_tasks = []


    for symbol in symbols:

        worker_tasks.append(

            coin_worker(
                symbol,
                volume_map[symbol]
            )
        )


    try:

        await asyncio.gather(

            cache_task,

            batch_task,

            *worker_tasks
        )


    except asyncio.CancelledError:

        cache_task.cancel()

        batch_task.cancel()

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

        except:

            pass


if __name__ == "__main__":

    asyncio.run(
        run_bot()
    )
