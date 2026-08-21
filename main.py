import asyncio
import aiohttp
import ccxt.pro as ccxtpro
import pandas as pd
import os
import time

from collections import deque
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


# ============================================================
# EARLY ENTRY AYARLARI
# ============================================================

# EMA kesişiminden sonra maksimum kabul edilen yükseliş
MAX_RISE_AFTER_CROSS = 12.0

# 15%+ yükselmiş coinleri kesinlikle istemiyoruz
HARD_MAX_RISE_AFTER_CROSS = 15.0

# EMA kesişiminin çok eski olmaması
MAX_CROSS_AGE_DAYS = 14


# ============================================================
# PARA AKIŞI
# ============================================================

# Flow / normal 1H hacim
MIN_FLOW_TOTAL_RATIO = 0.10

# Net para girişi / normal 1H hacim
MIN_NET_FLOW_RATIO = 0.06

# Minimum net para
MIN_NET_FLOW_USDT = 50_000

# Minimum alış oranı
MIN_BUY_RATIO = 0.58


# ============================================================
# FLOW GELİŞİMİ
# ============================================================

FLOW_WINDOW_SECONDS = 60 * 60


# ============================================================
# LARGE TRADE
# ============================================================

BASE_LARGE_TRADE_USDT = 25_000


# ============================================================
# SIGNAL COOLDOWN
# ============================================================

SIGNAL_COOLDOWN_SECONDS = 6 * 60 * 60


# ============================================================
# CACHE
# ============================================================

ONE_HOUR_REFRESH = 300
FOUR_HOUR_REFRESH = 900
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
# GLOBAL
# ============================================================

market_data = {}

market_data_lock = asyncio.Lock()

api_semaphore = asyncio.Semaphore(
    MAX_CONCURRENT_REQUESTS
)

signal_state = {}

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
                        "Telegram hata:",
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

    return 100 - (
        100 / (1 + rs)
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

            _, value, side = self.trades.popleft()

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

                buy_ratio = buy / total

            else:

                buy_ratio = 0.5


            net_flow = buy - sell

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

        market_data[symbol][timeframe] = {

            "df":
                df,

            "updated_at":
                time.monotonic()
        }


# ============================================================
# CACHE REFRESH
# ============================================================

async def refresh_1d(symbols):

    tasks = [

        update_symbol_timeframe(
            symbol,
            "1d",
            100
        )

        for symbol in symbols
    ]

    await asyncio.gather(
        *tasks,
        return_exceptions=True
    )


async def refresh_4h(symbols):

    tasks = [

        update_symbol_timeframe(
            symbol,
            "4h",
            100
        )

        for symbol in symbols
    ]

    await asyncio.gather(
        *tasks,
        return_exceptions=True
    )


async def refresh_1h(symbols):

    tasks = [

        update_symbol_timeframe(
            symbol,
            "1h",
            60
        )

        for symbol in symbols
    ]

    await asyncio.gather(
        *tasks,
        return_exceptions=True
    )


# ============================================================
# 1D ANALYSIS
# ============================================================

def analyze_1d(symbol):

    try:

        df = market_data[
            symbol
        ]["1d"]["df"].copy()

        if len(df) < 50:

            return None


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


        ema_aligned = (

            current["ema10"] >
            current["ema20"]

            and

            current["ema20"] >
            current["ema30"]
        )


        # ----------------------------------------------------
        # EMA10 / EMA20 KESİŞİMİNİ BUL
        # ----------------------------------------------------

        cross_index = None

        for i in range(
            len(df) - 1,
            0,
            -1
        ):

            now = df.iloc[i]

            prev = df.iloc[i - 1]

            crossed = (

                now["ema10"] >
                now["ema20"]

                and

                prev["ema10"] <=
                prev["ema20"]
            )

            if crossed:

                cross_index = i

                break


        if cross_index is None:

            return {

                "score": 0,

                "ema_aligned":
                    ema_aligned,

                "cross_age":
                    999,

                "rise_after_cross":
                    999,

                "early":
                    False
            }


        cross_age = (
            len(df) -
            1 -
            cross_index
        )


        cross_price = safe_float(
            df.iloc[
                cross_index
            ]["close"]
        )


        current_price = safe_float(
            current["close"]
        )


        if cross_price > 0:

            rise_after_cross = (

                (
                    current_price -
                    cross_price
                )
                /
                cross_price
            ) * 100

        else:

            rise_after_cross = 999


        # ----------------------------------------------------
        # EARLY FILTER
        # ----------------------------------------------------

        early = (

            ema_aligned

            and

            cross_age <=
            MAX_CROSS_AGE_DAYS

            and

            rise_after_cross <
            HARD_MAX_RISE_AFTER_CROSS
        )


        score = 0


        # EMA yapısı
        if ema_aligned:

            score += 10


        # Yeni kesişim
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


        # Kesişimden sonra ne kadar yükseldi?
        if rise_after_cross <= 5:

            score += 15

        elif rise_after_cross <= 8:

            score += 10

        elif rise_after_cross <= 12:

            score += 3

        else:

            score -= 15


        # Fazla uzaklaşmışsa sert ceza
        if rise_after_cross >= 12:

            score -= 10


        return {

            "score":
                score,

            "ema_aligned":
                ema_aligned,

            "cross_age":
                cross_age,

            "rise_after_cross":
                rise_after_cross,

            "cross_price":
                cross_price,

            "current_price":
                current_price,

            "early":
                early
        }


    except Exception as e:

        print(
            f"1D hata {symbol}: {e}"
        )

        return None


# ============================================================
# 4H ANALYSIS
# ============================================================

def analyze_4h(symbol):

    try:

        df = market_data[
            symbol
        ]["4h"]["df"].copy()

        if len(df) < 55:

            return None


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


        bullish = (

            current["close"] >
            current["ema20"]

            and

            current["ema20"] >
            current["ema50"]
        )


        score = 0


        if bullish:

            score += 10


        if (
            current["ema20"] >
            previous["ema20"]
        ):

            score += 3


        return {

            "score":
                score,

            "bullish":
                bullish
        }


    except Exception:

        return None


# ============================================================
# 1H ANALYSIS
# ============================================================

def analyze_1h(symbol):

    try:

        df = market_data[
            symbol
        ]["1h"]["df"].copy()

        if len(df) < 35:

            return None


        closed = df.iloc[:-1].copy()


        closed["ema20"] = calculate_ema(
            closed["close"],
            20
        )

        closed["rsi"] = calculate_rsi(
            closed["close"],
            14
        )


        current = closed.iloc[-1]

        previous = closed.iloc[-2]


        score = 0


        if (
            current["close"] >
            current["ema20"]
        ):

            score += 5


        if (
            current["ema20"] >
            previous["ema20"]
        ):

            score += 3


        rsi = safe_float(
            current["rsi"]
        )


        if 50 <= rsi <= 65:

            score += 5

        elif 65 < rsi <= 72:

            score += 3

        elif rsi > 75:

            score -= 8


        # ----------------------------------------------------
        # RELATIVE VOLUME
        # ----------------------------------------------------

        current_open = df.iloc[-1]

        current_volume = safe_float(
            current_open["volume"]
        )


        historical = [

            safe_float(x)

            for x in closed[
                "volume"
            ].tail(24).tolist()

            if safe_float(x) > 0
        ]


        if historical:

            normal_volume = median(
                historical
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


        # Hacim sadece destekleyici
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

            "score":
                score,

            "rsi":
                rsi,

            "relative_volume":
                relative_volume,

            "current_volume":
                current_volume,

            "normal_volume":
                normal_volume,

            "closed_price":
                safe_float(
                    current["close"]
                ),

            "momentum_label":
                label
        }


    except Exception:

        return None


# ============================================================
# PRICE POSITION
# ============================================================

def analyze_price_position(
    symbol,
    flow
):

    try:

        df = market_data[
            symbol
        ]["1h"]["df"].copy()

        closed = df.iloc[:-1].copy()


        current_price = flow.last_price

        if current_price <= 0:

            current_price = safe_float(
                df.iloc[-1]["close"]
            )


        resistance = safe_float(
            closed["high"].tail(20).max()
        )


        if current_price > 0:

            distance = (

                (
                    resistance -
                    current_price
                )
                /
                current_price

            ) * 100

        else:

            distance = 0


        score = 0


        if 0 <= distance <= 3:

            score += 5


        # 24H hareket
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


        # Aşırı günlük hareket istemiyoruz
        if change_24h > 12:

            score -= 8

        if change_24h > 18:

            score -= 12


        return {

            "score":
                score,

            "distance_percent":
                distance,

            "change_24h":
                change_24h
        }


    except Exception:

        return {

            "score":
                0,

            "distance_percent":
                0,

            "change_24h":
                0
        }


# ============================================================
# FLOW ANALYSIS
# ============================================================

def analyze_flow(
    flow,
    normal_1h_volume
):

    buy = flow["buy_volume"]

    sell = flow["sell_volume"]

    total = flow["total_volume"]

    net = flow["net_flow"]

    buy_ratio = flow["buy_ratio"]


    if normal_1h_volume <= 0:

        return {

            "strong":
                False,

            "score":
                0,

            "buy_ratio":
                buy_ratio,

            "net_flow":
                net,

            "flow_ratio":
                0,

            "net_ratio":
                0,

            "activity":
                "⚪ Zayıf"
        }


    flow_ratio = (
        total /
        normal_1h_volume
    )


    net_ratio = (
        net /
        normal_1h_volume
    )


    strong = (

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


    if strong:

        score += 20


    if (
        buy_ratio >= 0.65
        and
        net_ratio >= 0.10
    ):

        score += 5


    if (
        buy_ratio >= 0.70
        and
        net_ratio >= 0.15
    ):

        score += 5


    if flow_ratio >= 0.30:

        activity = "🔥 Çok Güçlü"

    elif flow_ratio >= 0.20:

        activity = "🟢 Güçlü"

    elif flow_ratio >= 0.10:

        activity = "🟡 Orta"

    else:

        activity = "⚪ Zayıf"


    return {

        "strong":
            strong,

        "score":
            score,

        "buy_ratio":
            buy_ratio,

        "net_flow":
            net,

        "flow_ratio":
            flow_ratio,

        "net_ratio":
            net_ratio,

        "activity":
            activity
    }


# ============================================================
# WHALE
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

    total = (
        large_buy +
        large_sell
    )


    if normal_1h_volume <= 0:

        return {
            "meaningful":
                False
        }


    ratio = (
        total /
        normal_1h_volume
    )


    # Küçük whale aktivitesi gösterme
    if ratio < 0.03:

        return {
            "meaningful":
                False
        }


    if (
        large_buy >
        large_sell * 1.5
    ):

        return {

            "meaningful":
                True,

            "label":
                "🐋 Büyük Alıcı"

            ,
            "value":
                large_buy
        }


    if (
        large_sell >
        large_buy * 1.5
    ):

        return {

            "meaningful":
                True,

            "label":
                "🐋 Büyük Satıcı",

            "value":
                large_sell
        }


    return {

        "meaningful":
            False
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

        daily = analyze_1d(symbol)

        four_h = analyze_4h(symbol)

        one_h = analyze_1h(symbol)


        if (
            daily is None
            or
            four_h is None
            or
            one_h is None
        ):

            return None


        # ----------------------------------------------------
        # EN ÖNEMLİ EARLY FILTER
        # ----------------------------------------------------

        if not daily["early"]:

            return None


        flow_data = await flow.snapshot()


        normal_1h_volume = (

            one_h["normal_volume"]
            *
            one_h["closed_price"]
        )


        flow_analysis = analyze_flow(

            flow_data,

            normal_1h_volume
        )


        # Para girişi yoksa direkt eliyoruz
        if not flow_analysis["strong"]:

            return None


        whale = analyze_whale(

            flow_data,

            normal_1h_volume
        )


        price = analyze_price_position(

            symbol,

            flow
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
        # LİKİDİTE
        # ----------------------------------------------------

        if market_24h_volume >= 20_000_000:

            total_score += 5

        elif market_24h_volume >= 10_000_000:

            total_score += 3

        elif market_24h_volume >= 5_000_000:

            total_score += 1


        # ----------------------------------------------------
        # AŞIRI YÜKSELİŞ CEZASI
        # ----------------------------------------------------

        rise = daily[
            "rise_after_cross"
        ]


        if rise >= 10:

            total_score -= 8


        if rise >= 12:

            total_score -= 12


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


        return candidates[
            :TOP_COINS
        ]


    except Exception as e:

        print(
            "Top 100 hata:",
            e
        )

        return []


# ============================================================
# SIGNAL COOLDOWN
# ============================================================

def can_send_signal(
    symbol,
    score
):

    now = time.monotonic()


    state = signal_state.get(
        symbol,
        {
            "score":
                0,

            "time":
                0
        }
    )


    if (
        now -
        state["time"]
        <
        SIGNAL_COOLDOWN_SECONDS
    ):

        return False


    signal_state[symbol] = {

        "score":
            score,

        "time":
            now
    }


    return True


# ============================================================
# SIGNAL FORMAT
# ============================================================

def format_signal(
    symbol,
    result
):

    score = result["score"]

    daily = result["daily"]

    one_h = result["1h"]

    flow = result["flow_analysis"]

    four_h = result["4h"]

    price = result["price"]

    whale = result["whale"]


    if score >= 90:

        emoji = "🔥"

    elif score >= 85:

        emoji = "🟢"

    else:

        emoji = "🟡"


    lines = [

        f"{emoji} *{symbol}* — `{score}`",

        (
            f"📈 EMA dönüşü: "
            f"`{daily['cross_age']} gün önce`"
        ),

        (
            f"🎯 Kesişimden sonra: "
            f"`+{daily['rise_after_cross']:.1f}%`"
        ),

        (
            f"⚡ 1H: "
            f"{one_h['momentum_label']} "
            f"| RSI `{one_h['rsi']:.0f}`"
        ),

        (
            f"💰 Flow: "
            f"`+{flow['net_flow']:,.0f} USDT` "
            f"| Alış `%{flow['buy_ratio'] * 100:.0f}`"
        ),

        (
            f"🔥 Aktivite: "
            f"{flow['activity']}"
        ),

        (
            f"📊 RVOL: "
            f"`{one_h['relative_volume']:.1f}x`"
        ),

        (
            f"📍 4H: "
            f"{'🟢 Bullish' if four_h['bullish'] else '⚪ Nötr'}"
        )
    ]


    if price["distance_percent"] > 0:

        lines.append(
            (
                f"🚧 Direnç: "
                f"`%{price['distance_percent']:.1f}`"
            )
        )


    if whale.get("meaningful"):

        lines.append(
            (
                f"{whale['label']}: "
                f"`{whale['value']:,.0f} USDT`"
            )
        )


    return "\n".join(
        lines
    )


# ============================================================
# BATCH SENDER
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


            signals.sort(
                key=lambda x:
                    x["score"],
                reverse=True
            )


            signals = signals[
                :MAX_TELEGRAM_SIGNALS
            ]


            lines = [

                "🚨 *SMART EARLY RADAR*",

                "━━━━━━━━━━━━━━━━━━",

                "🎯 Trend dönüşü + "
                "anlamlı para girişi"
            ]


            for item in signals:

                lines.append("")

                lines.append(
                    format_signal(
                        item["symbol"],
                        item["result"]
                    )
                )


            lines.append(
                "\n━━━━━━━━━━━━━━━━━━"
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
                f"🔌 WS: {symbol}"
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
                f"WS hata {symbol}: {e}"
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

    await asyncio.sleep(120)


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

                    f"{score:>3}/100 | "

                    f"Flow "
                    f"+{result['flow_analysis']['net_flow']:,.0f} | "

                    f"Cross "
                    f"{result['daily']['cross_age']}d | "

                    f"Rise "
                    f"+{result['daily']['rise_after_cross']:.1f}%"
                )


                if score >= MIN_SIGNAL_SCORE:

                    if can_send_signal(
                        symbol,
                        score
                    ):

                        async with pending_lock:

                            pending_signals[
                                symbol
                            ] = {

                                "symbol":
                                    symbol,

                                "score":
                                    score,

                                "result":
                                    result
                            }


                        print(
                            f"🚨 QUALITY SIGNAL "
                            f"{symbol} "
                            f"{score}/100"
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
        "=========================================="
    )

    print(
        "       OKX SMART EARLY RADAR"
    )

    print(
        "=========================================="
    )

    print(
        f"Top Coin: {TOP_COINS}"
    )

    print(
        f"Minimum Score: {MIN_SIGNAL_SCORE}"
    )

    print(
        "EMA: Yeni trend dönüşü"
    )

    print(
        "Flow: Güçlü para girişi ZORUNLU"
    )

    print(
        "Early Entry: AKTİF"
    )

    print(
        "Telegram: MAX 5 COIN / TOPLU MESAJ"
    )

    print(
        "=========================================="
    )


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
        f"🚀 {len(symbols)} coin taranıyor..."
    )


    cache_task = asyncio.create_task(

        cache_manager(
            symbols
        )
    )


    batch_task = asyncio.create_task(

        batch_sender()
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
