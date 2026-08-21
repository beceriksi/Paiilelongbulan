# OKX SPOT SMART RADAR - QUALITY FLOW VERSION
# Telegram: only quality signals with meaningful real money inflow.
# No pandas-ta required.

import asyncio
import aiohttp
import ccxt.pro as ccxtpro
import pandas as pd
import os
import time
from collections import deque
from statistics import median

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

TOP_COINS = 100
MIN_24H_VOLUME_USDT = 1_000_000
MIN_SIGNAL_SCORE = 70

# Quality filter: the signal must have real positive buy pressure.
MIN_FLOW_TOTAL_RATIO = 0.15
MIN_NET_FLOW_RATIO = 0.08
MIN_BUY_RATIO = 0.60
MIN_NET_FLOW_USDT = 25_000

SIGNAL_COOLDOWN_SECONDS = 4 * 60 * 60
SCORE_LEVELS = [70, 80, 90]
BASE_LARGE_TRADE_USDT = 25_000
FLOW_WINDOW_SECONDS = 60 * 60

VOLUME_STRONG = 1.5
VOLUME_EXTREME = 2.5

ONE_HOUR_REFRESH = 120
FOUR_HOUR_REFRESH = 600
ONE_DAY_REFRESH = 1800
MAX_CONCURRENT_REQUESTS = 5
BATCH_INTERVAL_SECONDS = 300

# Strong move warning. It does NOT block the signal.
FAST_MOVE_WARNING = 15.0

exchange = ccxtpro.okx({
    "enableRateLimit": True,
    "options": {"defaultType": "spot"}
})

market_data = {}
market_data_lock = asyncio.Lock()
api_semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
signal_state = {}
pending_signals = {}
pending_lock = asyncio.Lock()

async def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram Secret bulunamadı.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=15) as response:
                if response.status != 200:
                    print("Telegram HTTP hata:", response.status, await response.text())
    except Exception as e:
        print("Telegram bağlantı hatası:", e)

def safe_float(value, default=0):
    try:
        return default if value is None else float(value)
    except Exception:
        return default

def create_dataframe(ohlcv):
    return pd.DataFrame(ohlcv, columns=["timestamp","open","high","low","close","volume"])

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
            price = safe_float(trade.get("price"))
            amount = safe_float(trade.get("amount"))
            side = trade.get("side")
            if price <= 0 or amount <= 0 or side not in ("buy", "sell"):
                return
            value = price * amount
            now = time.monotonic()
            async with self.lock:
                self.last_price = price
                self.trades.append((now, value, side))
                if side == "buy": self.buy_volume += value
                else: self.sell_volume += value
                if value >= BASE_LARGE_TRADE_USDT:
                    if side == "buy":
                        self.large_buy_volume += value; self.large_buy_count += 1
                    else:
                        self.large_sell_volume += value; self.large_sell_count += 1
                await self.cleanup_locked(now)
        except Exception:
            pass

    async def cleanup_locked(self, now):
        cutoff = now - FLOW_WINDOW_SECONDS
        while self.trades and self.trades[0][0] < cutoff:
            _, value, side = self.trades.popleft()
            if side == "buy": self.buy_volume -= value
            else: self.sell_volume -= value
            if value >= BASE_LARGE_TRADE_USDT:
                if side == "buy":
                    self.large_buy_volume -= value; self.large_buy_count -= 1
                else:
                    self.large_sell_volume -= value; self.large_sell_count -= 1
        self.buy_volume = max(0, self.buy_volume)
        self.sell_volume = max(0, self.sell_volume)
        self.large_buy_volume = max(0, self.large_buy_volume)
        self.large_sell_volume = max(0, self.large_sell_volume)
        self.large_buy_count = max(0, self.large_buy_count)
        self.large_sell_count = max(0, self.large_sell_count)

    async def cleanup_loop(self):
        while True:
            try:
                async with self.lock:
                    await self.cleanup_locked(time.monotonic())
            except Exception as e:
                print("Flow cleanup hata:", e)
            await asyncio.sleep(30)

    async def snapshot(self):
        async with self.lock:
            await self.cleanup_locked(time.monotonic())
            buy, sell = max(0,self.buy_volume), max(0,self.sell_volume)
            lb, ls = max(0,self.large_buy_volume), max(0,self.large_sell_volume)
            total = buy + sell
            ltotal = lb + ls
            return {
                "buy_volume": buy, "sell_volume": sell, "total_volume": total,
                "net_flow": buy-sell, "buy_ratio": buy/total if total else 0.5,
                "large_buy_volume": lb, "large_sell_volume": ls,
                "large_buy_count": self.large_buy_count,
                "large_sell_count": self.large_sell_count,
                "large_buy_ratio": lb/ltotal if ltotal else 0.5,
                "last_price": self.last_price
            }

async def fetch_ohlcv_safe(symbol, timeframe, limit):
    async with api_semaphore:
        try:
            return await exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        except Exception as e:
            print(f"OHLCV hata | {symbol} | {timeframe} | {e}")
            return None

async def update_symbol_timeframe(symbol, timeframe, limit):
    candles = await fetch_ohlcv_safe(symbol, timeframe, limit)
    if not candles: return
    df = create_dataframe(candles)
    async with market_data_lock:
        market_data.setdefault(symbol, {})[timeframe] = {"df": df, "updated_at": time.monotonic()}

async def refresh_timeframe(symbols, timeframe, limit, label):
    print(f"\n{label} cache yenileniyor...")
    await asyncio.gather(*(update_symbol_timeframe(s, timeframe, limit) for s in symbols), return_exceptions=True)
    print(f"{label} tamamlandı.")

async def refresh_1d(symbols): await refresh_timeframe(symbols,"1d",60,"1D")
async def refresh_4h(symbols): await refresh_timeframe(symbols,"4h",80,"4H")
async def refresh_1h(symbols): await refresh_timeframe(symbols,"1h",50,"1H")

def analyze_1d_from_cache(symbol):
    try:
        df = market_data[symbol]["1d"]["df"].copy()
        if len(df) < 35: return {"score":0,"ema_aligned":False,"price_change_24h":0,"ema10":0,"ema20":0,"ema30":0}
        df = df.iloc[:-1].copy()
        df["ema10"] = calculate_ema(df.close,10); df["ema20"] = calculate_ema(df.close,20); df["ema30"] = calculate_ema(df.close,30)
        c,p = df.iloc[-1],df.iloc[-2]
        aligned = c.ema10 > c.ema20 > c.ema30
        score = 0
        if aligned: score += 18
        if c.ema10 > p.ema10: score += 2
        if c.close > c.ema10: score += 2
        change = ((c.close-p.close)/p.close)*100 if p.close else 0
        return {"score":score,"ema_aligned":aligned,"price_change_24h":change,"close":c.close,"ema10":c.ema10,"ema20":c.ema20,"ema30":c.ema30}
    except Exception as e:
        print(f"1D hata {symbol}: {e}")
        return {"score":0,"ema_aligned":False,"price_change_24h":0,"ema10":0,"ema20":0,"ema30":0}

def analyze_4h_from_cache(symbol):
    try:
        df=market_data[symbol]["4h"]["df"].copy()
        if len(df)<55:return {"score":0,"bullish":False}
        df=df.iloc[:-1].copy(); df["ema20"]=calculate_ema(df.close,20); df["ema50"]=calculate_ema(df.close,50)
        c,p=df.iloc[-1],df.iloc[-2]; bullish=c.close>c.ema20>c.ema50; score=15 if bullish else 0
        if c.ema20>p.ema20:score+=3
        if c.close<c.ema20<c.ema50:score-=8
        return {"score":score,"bullish":bullish}
    except Exception as e:
        print(f"4H hata {symbol}: {e}"); return {"score":0,"bullish":False}

def analyze_1h_from_cache(symbol):
    try:
        df=market_data[symbol]["1h"]["df"].copy()
        if len(df)<30:return None
        closed=df.iloc[:-1].copy()
        if len(closed)<25:return None
        closed["ema20"]=calculate_ema(closed.close,20); closed["ema50"]=calculate_ema(closed.close,50); closed["rsi"]=calculate_rsi(closed.close,14)
        closed["macd"],closed["macd_signal"],closed["macd_hist"]=calculate_macd(closed.close)
        c,p=closed.iloc[-1],closed.iloc[-2]; score=0
        if c.close>c.ema20:score+=5
        if c.ema20>p.ema20:score+=3
        if c.rsi>p.rsi:score+=2
        if c.rsi>=50:score+=2
        if c.rsi>78:score-=5
        if c.macd_hist>p.macd_hist:score+=3
        if c.macd_hist>0:score+=2
        current_volume=safe_float(df.iloc[-1].volume)
        vols=[safe_float(x) for x in closed.volume.tail(24) if safe_float(x)>0]
        normal_volume=median(vols) if vols else 0
        rvol=current_volume/normal_volume if normal_volume else 0
        if rvol>=VOLUME_STRONG:score+=10
        if rvol>=VOLUME_EXTREME:score+=5
        label="🔥 Güçlü Momentum" if score>=18 else ("🟢 Momentum Güçleniyor" if score>=11 else "🟡 Zayıf Momentum")
        return {"score":score,"relative_volume":rvol,"current_volume":current_volume,"normal_volume":normal_volume,"momentum_label":label,"rsi":c.rsi,"closed_price":c.close}
    except Exception as e:
        print(f"1H hata {symbol}: {e}"); return None

def analyze_price_position_from_cache(symbol,flow):
    try:
        df=market_data[symbol]["1h"]["df"].copy(); closed=df.iloc[:-1].copy()
        if len(closed)<25:return {"score":0,"near_breakout":False,"distance_percent":0,"change_24h":0}
        price=flow["last_price"] or safe_float(df.iloc[-1].close); resistance=closed.high.tail(20).max()
        distance=((resistance-price)/price)*100 if price else 0; score=0; near=False
        if 0<=distance<=2:score+=8;near=True
        if price>resistance:score+=10;near=True
        old=safe_float(closed.iloc[-24].close) if len(closed)>=24 else 0
        change=((price-old)/old)*100 if old else 0
        # Big run-up is a warning/penalty, never a hard block.
        if change>25:score-=8
        elif change>15:score-=4
        return {"score":score,"near_breakout":near,"distance_percent":distance,"change_24h":change}
    except Exception as e:
        print(f"Price hata {symbol}: {e}"); return {"score":0,"near_breakout":False,"distance_percent":0,"change_24h":0}

def analyze_flow(flow,normal):
    buy,sell,total,net,ratio=flow["buy_volume"],flow["sell_volume"],flow["total_volume"],flow["net_flow"],flow["buy_ratio"]
    if normal<=0:return {"score":0,"strong":False,"buy_ratio":ratio,"net_flow":net,"total":total,"flow_ratio":0,"net_ratio":0,"label":"⚪ Zayıf","activity":"Yetersiz"}
    fr=total/normal; nr=net/normal
    strong=ratio>=MIN_BUY_RATIO and fr>=MIN_FLOW_TOTAL_RATIO and nr>=MIN_NET_FLOW_RATIO and net>=MIN_NET_FLOW_USDT
    score=12 if strong else 0
    if ratio>=.65 and nr>=.15:score+=6
    if ratio>=.70 and nr>=.20:score+=5
    activity="🔥 Çok Güçlü" if fr>=.30 else ("🟢 Güçlü" if fr>=.20 else ("🟡 Orta" if fr>=.15 else "⚪ Zayıf"))
    label="🔥 Güçlü Para Girişi" if strong and nr>=.20 else ("🟢 Para Girişi" if strong else "⚪ Anlamlı Para Girişi Yok")
    return {"score":score,"strong":strong,"buy_ratio":ratio,"net_flow":net,"total":total,"flow_ratio":fr,"net_ratio":nr,"label":label,"activity":activity}

def analyze_whale(flow,normal):
    lb,ls=flow["large_buy_volume"],flow["large_sell_volume"]; total=lb+ls
    if normal<=0 or total/normal<.02:return {"meaningful":False,"label":""}
    if lb>=ls*1.5:return {"meaningful":True,"label":"🐋 Büyük Alıcı Aktivitesi"}
    if ls>=lb*1.5:return {"meaningful":True,"label":"🐋 Büyük Satıcı Aktivitesi"}
    return {"meaningful":True,"label":"🐋 Büyük Oyuncu Aktivitesi"}

async def calculate_score(symbol,flow,market_24h_volume):
    try:
        async with market_data_lock:
            if symbol not in market_data or any(x not in market_data[symbol] for x in ("1d","4h","1h")):return None
        daily=analyze_1d_from_cache(symbol); four=analyze_4h_from_cache(symbol); one=analyze_1h_from_cache(symbol)
        if one is None:return None
        fd=await flow.snapshot(); normal=one["normal_volume"]*one["closed_price"]
        fa=analyze_flow(fd,normal); whale=analyze_whale(fd,normal); price=analyze_price_position_from_cache(symbol,fd)
        score=daily["score"]+four["score"]+one["score"]+fa["score"]+price["score"]
        if market_24h_volume>=20_000_000:score+=5
        elif market_24h_volume>=10_000_000:score+=4
        elif market_24h_volume>=5_000_000:score+=2
        score=max(0,min(100,score))
        return {"score":score,"daily":daily,"4h":four,"1h":one,"flow":fd,"flow_analysis":fa,"whale":whale,"price":price,"market_24h_volume":market_24h_volume}
    except Exception as e:
        print(f"Score hata {symbol}: {e}");return None

async def get_top_100_symbols(markets):
    symbols=[s for s,m in markets.items() if s.endswith("/USDT") and m.get("spot") and m.get("active")]
    try:
        tickers=await exchange.fetch_tickers(); candidates=[]
        for s in symbols:
            t=tickers.get(s)
            if not t:continue
            v=safe_float(t.get("quoteVolume"))
            if v>=MIN_24H_VOLUME_USDT:candidates.append((s,v))
        candidates.sort(key=lambda x:x[1],reverse=True)
        return candidates[:TOP_COINS]
    except Exception as e:
        print("Top 100 hata:",e);return []

def get_new_signal_level(symbol,score):
    now=time.monotonic(); state=signal_state.get(symbol,{"last_level":0,"last_time":0}); last=state["last_level"]
    current=max([x for x in SCORE_LEVELS if score>=x],default=0)
    if current==0:return 0
    if current>last or (current==last and now-state["last_time"]>=SIGNAL_COOLDOWN_SECONDS):
        signal_state[symbol]={"last_level":current,"last_time":now};return current
    return 0

def format_signal(item):
    r=item["result"]; score=r["score"]; fa=r["flow_analysis"]; one=r["1h"]; four=r["4h"]; d=r["daily"]; p=r["price"]
    move_note=f" ⚠️ +{p['change_24h']:.0f}%" if p["change_24h"]>=FAST_MOVE_WARNING else ""
    trend="🟢 Bullish" if four["bullish"] else "⚪ Nötr/Bearish"
    return (f"{item['symbol']} · {score}/100\n"
            f"📈 4H {trend}  •  📅 1D EMA10>20>30 {'🟢' if d['ema_aligned'] else '⚪'}\n"
            f"⚡ RSI {one['rsi']:.1f}  •  Hacim {one['relative_volume']:.1f}x\n"
            f"💰 Net Flow +{fa['net_flow']:,.0f} USDT  •  Alış %{fa['buy_ratio']*100:.0f}\n"
            f"🔥 {fa['activity']}  •  🎯 Direnç %{max(p['distance_percent'],0):.1f}{move_note}")

async def batch_sender():
    while True:
        await asyncio.sleep(BATCH_INTERVAL_SECONDS)
        try:
            async with pending_lock:
                if not pending_signals:continue
                signals=list(pending_signals.values());pending_signals.clear()
            signals.sort(key=lambda x:x["score"],reverse=True)
            high=[x for x in signals if x["score"]>=90]; strong=[x for x in signals if 80<=x["score"]<90]; radar=[x for x in signals if 70<=x["score"]<80]
            lines=["🚨 *OKX SPOT SMART RADAR*","━━━━━━━━━━━━━━━━━━"]
            for title,arr in (("🔥 HIGH PRIORITY",high),("🟢 STRONG WATCH",strong),("🟡 RADAR",radar)):
                if arr:
                    lines.append(f"\n*{title}*")
                    lines.extend(format_signal(x) for x in arr)
            whale=[x for x in signals if x["result"]["whale"]["meaningful"]]
            if whale:
                lines.append("\n🐋 *BÜYÜK OYUNCU*")
                lines.extend(f"{x['symbol']} — {x['result']['whale']['label']}" for x in whale)
            lines.append("━━━━━━━━━━━━━━━━━━")
            lines.append(f"📊 {len(signals)} kaliteli sinyal")
            await send_telegram_message("\n".join(lines))
        except Exception as e:print("Batch sender hata:",e)

async def trade_listener(symbol,flow):
    retry=2
    while True:
        try:
            while True:
                trades=await exchange.watch_trades(symbol)
                for trade in trades:await flow.add_trade(trade)
                retry=2
        except asyncio.CancelledError:raise
        except Exception as e:
            print(f"WS hata {symbol}: {e}");await asyncio.sleep(retry);retry=min(retry*2,60)

async def evaluator(symbol,flow,volume):
    # Let the rolling flow build up; do not fire immediately after startup.
    await asyncio.sleep(300)
    while True:
        try:
            result=await calculate_score(symbol,flow,volume)
            if result:
                score=result["score"]; fa=result["flow_analysis"]
                print(f"🔎 {symbol:<15} {score:>3}/100 | Flow {fa['net_flow']:,.0f} | Buy {fa['buy_ratio']*100:.0f}% | RVOL {result['1h']['relative_volume']:.2f}x")
                if score>=MIN_SIGNAL_SCORE and fa["strong"]:
                    level=get_new_signal_level(symbol,score)
                    if level>=MIN_SIGNAL_SCORE:
                        async with pending_lock:
                            pending_signals[symbol]={"symbol":symbol,"score":score,"level":level,"result":result}
        except Exception as e:print(f"Evaluator hata {symbol}: {e}")
        await asyncio.sleep(60)

async def coin_worker(symbol,volume):
    flow=MarketFlow(); cleanup=asyncio.create_task(flow.cleanup_loop()); ws=asyncio.create_task(trade_listener(symbol,flow)); ev=asyncio.create_task(evaluator(symbol,flow,volume))
    try:await asyncio.gather(ws,ev)
    finally:
        for t in (cleanup,ws,ev):t.cancel()

async def cache_manager(symbols):
    await asyncio.gather(refresh_1d(symbols),refresh_4h(symbols),refresh_1h(symbols))
    async def daily():
        while True:await asyncio.sleep(ONE_DAY_REFRESH);await refresh_1d(symbols)
    async def four():
        while True:await asyncio.sleep(FOUR_HOUR_REFRESH);await refresh_4h(symbols)
    async def one():
        while True:await asyncio.sleep(ONE_HOUR_REFRESH);await refresh_1h(symbols)
    await asyncio.gather(daily(),four(),one())

async def main():
    print("==============================================")
    print("       OKX SPOT SMART RADAR - QUALITY")
    print("==============================================")
    print(f"Top Coin: {TOP_COINS}")
    print(f"Telegram Threshold: {MIN_SIGNAL_SCORE}+")
    print("1D EMA 10 > 20 > 30 + trend quality")
    print("4H trend + 1H momentum + relative volume")
    print("REAL PARA GİRİŞİ ZORUNLU")
    print("%15+ 24H hareket: uyarı/puan etkisi, SİNYALİ ENGELLEMEZ")
    print("Telegram: 5 dakikada TOPLU MESAJ")
    print("==============================================")
    markets=await exchange.load_markets();top=await get_top_100_symbols(markets)
    if not top:print("Uygun coin bulunamadı.");return
    symbols=[x[0] for x in top]; volumes=dict(top)
    cache_task=asyncio.create_task(cache_manager(symbols));batch_task=asyncio.create_task(batch_sender())
    workers=[coin_worker(s,volumes[s]) for s in symbols]
    try:await asyncio.gather(cache_task,batch_task,*workers)
    except asyncio.CancelledError:
        cache_task.cancel();batch_task.cancel();raise

async def run_bot():
    try:await main()
    except KeyboardInterrupt:print("Bot durduruldu.")
    finally:
        try:await exchange.close()
        except Exception:pass

if __name__=="__main__":asyncio.run(run_bot())
