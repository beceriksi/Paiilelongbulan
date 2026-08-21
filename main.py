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

# OKX Top 100'e girebilmek için minimum 24H USDT hacmi
MIN_24H_VOLUME_USDT = 1_000_000

# Telegram minimum skor
MIN_SIGNAL_SCORE = 70

# Aynı skor seviyesini tekrar göndermeden önce
SIGNAL_COOLDOWN_SECONDS = 4 * 60 * 60

# Büyük işlem eşiği
LARGE_TRADE_USDT = 25_000

# Market flow son 1 saat
FLOW_WINDOW_SECONDS = 60 * 60

# Relative volume
VOLUME_STRONG = 2.0
VOLUME_EXTREME = 3.0

# Cache yenileme süreleri
ONE_HOUR_REFRESH = 120
FOUR_HOUR_REFRESH = 600
ONE_DAY_REFRESH = 1800

# Aynı anda maksimum REST isteği
MAX_CONCURRENT_REQUESTS = 5

# Sinyal seviyeleri
SCORE_LEVELS = [70, 80, 90]


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

        timeout = aiohttp.ClientTimeout(
            total=15
        )

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            async with session.post(
                url,
                json=payload
            ) as response:

                if response.status != 200:

                    body = await response.text()

                    print(
                        "Telegram HTTP hata:",
                        response.status,
                        body
                    )

                else:

                    print(
                        "📨 Telegram mesajı gönderildi."
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
# TECHNICAL INDICATORS
# pandas-ta kullanılmıyor
# ============================================================

def calculate_ema(
    series,
    period
):

    return series.ewm(
        span=period,
        adjust=False
    ).mean()


def calculate_rsi(
    series,
    period=14
):

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

    avg_loss = avg_loss.replace(
        0,
        float("nan")
    )

    rs = (
        avg_gain /
        avg_loss
    )

    rsi = (
        100 -
        (
            100 /
            (1 + rs)
        )
    )

    return rsi


def calculate_macd(
    series,
    fast=12,
    slow=26,
    signal=9
):

    ema_fast = calculate_ema(
        series,
        fast
    )

    ema_slow = calculate_ema(
        series,
        slow
    )

    macd_line = (
        ema_fast -
        ema_slow
    )

    signal_line = calculate_ema(
        macd_line,
        signal
    )

    histogram = (
        macd_line -
        signal_line
    )

    return (
        macd_line,
        signal_line,
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


    async def add_trade(
        self,
        trade
    ):

        try:

            price = safe_float(
                trade.get("price")
            )

            amount = safe_float(
                trade.get("amount")
            )

            side = trade.get("side")

            if price <= 0:
                return

            if amount <= 0:
                return

            value = (
                price *
                amount
            )

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

                await self.cleanup_locked(
                    now
                )

        except Exception:

            pass


    async def cleanup_locked(
        self,
        now
    ):

        cutoff = (
            now -
            FLOW_WINDOW_SECONDS
        )

        while (
            self.trades
            and
            self.trades[0][0] < cutoff
        ):

            (
                _,
                value,
                side
            ) = self.trades.popleft()

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


    async def cleanup_loop(
        self
    ):

        while True:

            try:

                async with self.lock:

                    await self.cleanup_locked(
                        time.monotonic()
                    )

            except Exception as e:

                print(
                    "Flow cleanup hatası:",
                    e
                )

            await asyncio.sleep(
                30
            )


    async def snapshot(
        self
    ):

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

            total = (
                buy +
                sell
            )

            if total > 0:

                buy_ratio = (
                    buy /
                    total
                )

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

                "buy_volume":
                    buy,

                "sell_volume":
                    sell,

                "total_volume":
                    total,

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

            "df":
                df,

            "updated_at":
                time.monotonic()
        }


# ============================================================
# 1D CACHE
# ============================================================

async def refresh_1d(
    symbols
):

    print(
        "\n📅 1D cache yenileniyor..."
    )

    tasks = []

    for symbol in symbols:

        tasks.append(
            update_symbol_timeframe(
                symbol,
                "1d",
                60
            )
        )

    await asyncio.gather(
        *tasks,
        return_exceptions=True
    )

    print(
        "✅ 1D cache tamamlandı."
    )


# ============================================================
# 4H CACHE
# ============================================================

async def refresh_4h(
    symbols
):

    print(
        "\n📊 4H cache yenileniyor..."
    )

    tasks = []

    for symbol in symbols:

        tasks.append(
            update_symbol_timeframe(
                symbol,
                "4h",
                80
            )
        )

    await asyncio.gather(
        *tasks,
        return_exceptions=True
    )

    print(
        "✅ 4H cache tamamlandı."
    )


# ============================================================
# 1H CACHE
# ============================================================

async def refresh_1h(
    symbols
):

    print(
        "\n⚡ 1H cache yenileniyor..."
    )

    tasks = []

    for symbol in symbols:

        tasks.append(
            update_symbol_timeframe(
                symbol,
                "1h",
                50
            )
        )

    await asyncio.gather(
        *tasks,
        return_exceptions=True
    )

    print(
        "✅ 1H cache tamamlandı."
    )


# ============================================================
# 1D ANALYSIS
# ============================================================

def analyze_1d_from_cache(
    symbol
):

    try:

        df = market_data[
            symbol
        ]["1d"]["df"].copy()

        if len(df) < 35:

            return {
                "score": 0,
                "ema_aligned": False,
                "price_change_24h": 0,
                "close": 0
            }

        # Açık günlük mumu kullanma
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

            score += 20

        if (
            current["ema10"] >
            previous["ema10"]
        ):

            score += 3

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
# 4H ANALYSIS
# ============================================================

def analyze_4h_from_cache(
    symbol
):

    try:

        df = market_data[
            symbol
        ]["4h"]["df"].copy()

        if len(df) < 55:

            return {
                "score": 0,
                "bullish": False
            }

        # Açık 4H mumu kullanma
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

        # 4H yukarı trend önemli bonus
        if bullish:

            score += 15

        if (
            current["ema20"] >
            previous["ema20"]
        ):

            score += 3

        # Tamamen bearish ise
        # bonus vermiyoruz ama sinyali öldürmüyoruz.
        if (
            current["close"] <
            current["ema20"]
            and
            current["ema20"] <
            current["ema50"]
        ):

            score -= 10

        return {

            "score":
                score,

            "bullish":
                bullish
        }

    except Exception as e:

        print(
            f"4H analysis hata "
            f"{symbol}: {e}"
        )

        return {
            "score": 0,
            "bullish": False
        }


# ============================================================
# 1H ANALYSIS
# ============================================================

def analyze_1h_from_cache(
    symbol
):

    try:

        df = market_data[
            symbol
        ]["1h"]["df"].copy()

        if len(df) < 30:

            return None

        # Açık 1H mumunu momentum hesabından çıkar
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

        # ----------------------------------------------------
        # 1H MOMENTUM
        # ----------------------------------------------------

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

        if (
            current["rsi"] >
            previous["rsi"]
        ):

            score += 2

        if current["rsi"] >= 50:

            score += 2

        # Aşırı alım cezası
        if current["rsi"] > 75:

            score -= 5

        if (
            current["macd_hist"] >
            previous["macd_hist"]
        ):

            score += 3

        if (
            current["macd_hist"] > 0
        ):

            score += 2

        # ----------------------------------------------------
        # 1H HACİM
        # ----------------------------------------------------

        # Burada açık 1H mumun hacmini kullanıyoruz.
        # Bu bir 1m/5m sinyali değildir.
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

        if (
            relative_volume >=
            VOLUME_STRONG
        ):

            score += 15

        if (
            relative_volume >=
            VOLUME_EXTREME
        ):

            score += 5

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
                safe_float(
                    current["rsi"]
                ),

            "macd_hist":
                safe_float(
                    current["macd_hist"]
                ),

            "closed_price":
                safe_float(
                    current["close"]
                )
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

        # Direncin %2'si içindeyse
        if (
            0 <=
            distance_percent <=
            2
        ):

            score += 10

            near_breakout = True

        # Direnç kırıldıysa
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

        # Çok fazla yükselmiş coinleri
        # otomatik olarak biraz aşağı çek
        if change_24h > 25:

            score -= 15

        elif change_24h > 15:

            score -= 10

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
# FLOW SCORE
# ============================================================

def calculate_flow_score(
    flow
):

    score = 0

    buy_ratio = flow[
        "buy_ratio"
    ]

    # --------------------------------------------------------
    # BUY / SELL FLOW
    # --------------------------------------------------------

    if buy_ratio >= 0.65:

        score += 10

    elif buy_ratio >= 0.58:

        score += 5

    elif buy_ratio <= 0.35:

        score -= 7

    # --------------------------------------------------------
    # LARGE TRADE FLOW
    # --------------------------------------------------------

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

    # Büyük işlem aktivitesi
    if (
        large_total >=
        LARGE_TRADE_USDT * 10
    ):

        score += 5

        large_buy_ratio = (
            large_buy /
            large_total
        )

        if large_buy_ratio >= 0.60:

            score += 3

        # Hem büyük alıcı hem büyük satıcı
        elif (
            large_buy >=
            LARGE_TRADE_USDT * 5
            and
            large_sell >=
            LARGE_TRADE_USDT * 5
        ):

            # Yön yerine aktivite bonusu
            score += 2

    return score


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

        flow_score = calculate_flow_score(
            flow_data
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
            flow_score
            +
            price["score"]
        )

        # ----------------------------------------------------
        # LIQUIDITY BONUS
        # ----------------------------------------------------

        if market_24h_volume >= 10_000_000:

            total_score += 5

        elif market_24h_volume >= 5_000_000:

            total_score += 3

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

        priority = (
            "🚨 HIGH PRIORITY"
        )

    elif level >= 80:

        priority = (
            "🟢 STRONG WATCH"
        )

    else:

        priority = (
            "🟡 RADAR"
        )

    buy_ratio = (
        flow["buy_ratio"] *
        100
    )

    large_buy_ratio = (
        flow["large_buy_ratio"] *
        100
    )

    message = (

        f"{priority}\n\n"

        f"🪙 *Coin:* `{symbol}`\n"
        f"⭐ *Score:* `{score}/100`\n"
        f"🎯 *Yeni Seviye:* `{level}+`\n\n"

        f"📅 *1D TREND*\n"

        f"EMA 10 > 20 > 30: "
        f"{'🟢 EVET' if daily['ema_aligned'] else '⚪ HAYIR'}\n"

        f"1D Değişim: "
        f"`{daily['price_change_24h']:.2f}%`\n\n"

        f"📈 *4H TREND*\n"

        f"Bullish: "
        f"{'🟢 EVET' if four_h['bullish'] else '⚪ HAYIR'}\n\n"

        f"⚡ *1H MOMENTUM*\n"

        f"RSI: "
        f"`{one_h['rsi']:.1f}`\n"

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

        f"Large Buy Ratio: "
        f"`{large_buy_ratio:.1f}%`\n"

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

        f"💡 *Yorum:* "

        f"Bu coin kısa vadeli scalp sinyali "
        f"olarak değil, spot manuel inceleme "
        f"için radara alındı. "

        f"4H trend, 1H momentum, hacim ve "
        f"son 1 saatlik alıcı/satıcı aktivitesi "
        f"birlikte değerlendirilmiştir.\n\n"

        f"⚠️ *1m / 5m sinyali değildir.*"
    )

    return message


# ============================================================
# SIGNAL LEVEL LOGIC
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

    # İlk kez bu seviyeye ulaştı
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
        current_level ==
        last_level
        and
        now -
        last_time >=
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

    # İlk cache verilerinin hazır olması için
    await asyncio.sleep(
        10
    )

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

                print(
                    f"🔎 {symbol:<15} "
                    f"Score: "
                    f"{score:>3}/100 | "
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

                if (
                    new_level >=
                    MIN_SIGNAL_SCORE
                ):

                    print(
                        f"\n🚨 SİNYAL "
                        f"{symbol} "
                        f"| {score}/100 "
                        f"| LEVEL "
                        f"{new_level}+\n"
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

        await asyncio.sleep(
            60
        )


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

    # İlk verileri al
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

    print(
        "\n🚀 İlk market cache hazır."
    )

    # --------------------------------------------------------
    # 1D LOOP
    # --------------------------------------------------------

    async def daily_loop():

        while True:

            await asyncio.sleep(
                ONE_DAY_REFRESH
            )

            await refresh_1d(
                symbols
            )

    # --------------------------------------------------------
    # 4H LOOP
    # --------------------------------------------------------

    async def four_hour_loop():

        while True:

            await asyncio.sleep(
                FOUR_HOUR_REFRESH
            )

            await refresh_4h(
                symbols
            )

    # --------------------------------------------------------
    # 1H LOOP
    # --------------------------------------------------------

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
        "        OKX SPOT SMART RADAR V3"
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
        "Flow: Buy/Sell + Large Trades"
    )

    print(
        "1m/5m: Sinyal YOK"
    )

    print(
        "pandas-ta: KULLANILMIYOR"
    )

    print(
        "=============================================="
    )

    print()

    if not TELEGRAM_BOT_TOKEN:

        print(
            "❌ TELEGRAM_BOT_TOKEN Secret bulunamadı."
        )

    else:

        print(
            "✅ TELEGRAM_BOT_TOKEN bulundu."
        )

    if not TELEGRAM_CHAT_ID:

        print(
            "❌ TELEGRAM_CHAT_ID Secret bulunamadı."
        )

    else:

        print(
            "✅ TELEGRAM_CHAT_ID bulundu."
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

    # --------------------------------------------------------
    # CACHE MANAGER
    # --------------------------------------------------------

    cache_task = asyncio.create_task(
        cache_manager(
            symbols
        )
    )

    # --------------------------------------------------------
    # COIN WORKERS
    # --------------------------------------------------------

    worker_tasks = []

    for symbol in symbols:

        worker_tasks.append(

            asyncio.create_task(
                coin_worker(
                    symbol,
                    volume_map[symbol]
                )
            )
        )

    try:

        await asyncio.gather(
            cache_task,
            *worker_tasks
        )

    except asyncio.CancelledError:

        cache_task.cancel()

        for task in worker_tasks:

            task.cancel()

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
