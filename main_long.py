import os
import time
import requests
import pandas as pd
from PIL import Image
from google import genai
from google.genai import types
from playwright.sync_api import sync_playwright

# ENV / SECRETS (Yeni Telegram Long Botunuzun Bilgileri)
TOKEN = os.getenv('TELEGRAM_LONG_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_LONG_CHAT_ID')
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# MUHAFAZAKAR SWING AYARLARI
VOLUME_MULTIPLIER = 2.5      # Son 1H hacmi, 20 saatlik ortalamanın en az 2.5 katı olmalı (Bariz fark)
BUY_SELL_RATIO_LIMIT = 1.25  # Alıcı baskısı %125 ve üzeri olmalı

def send_telegram_text(msg):
    if TOKEN and CHAT_ID and msg:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        try:
            requests.post(url, json={
                "chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown", "disable_web_page_preview": True
            }, timeout=10)
        except Exception as e: 
            print(f"Telegram metin hatası: {e}")

def send_telegram_photo(photo_path, caption):
    if TOKEN and CHAT_ID and os.path.exists(photo_path):
        url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
        try:
            with open(photo_path, 'rb') as photo:
                requests.post(url, data={"chat_id": CHAT_ID, "caption": caption, "parse_mode": "Markdown"}, files={"photo": photo}, timeout=15)
        except Exception as e: 
            print(f"Telegram fotoğraf hatası: {e}")

def get_data(endpoint, params={}):
    base = "https://www.okx.com"
    try:
        res = requests.get(base + endpoint, params=params, timeout=10).json()
        return res.get('data', [])
    except: 
        return []

def find_major_resistance(df, pivot_len=3):
    if len(df) < (pivot_len * 2 + 1):
        return 0
    highs = df['h'].astype(float).values
    # 4 Saatlik grafiklerdeki en net majör direnç seviyesini bulur
    for i in range(len(highs) - 1 - pivot_len, pivot_len, -1):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i-3] and \
           highs[i] > highs[i+1] and highs[i] > highs[i+2] and highs[i] > highs[i+3]:
            return highs[i]
    return highs.max()

def get_smart_buy_volume(symbol):
    res = get_data("/api/v5/rubik/stat/taker-volume", {"instId": symbol, "period": "1H"})
    if res and len(res) > 0 and len(res[0]) > 2:
        buy_v = float(res[0][1])
        sell_v = float(res[0][2])
        ratio = round(buy_v / sell_v, 2) if sell_v > 0 else 1.0
        return buy_v, sell_v, ratio
    return 0, 0, 1.0

def take_tradingview_screenshot(tv_symbol, output_path="chart_long.png"):
    # Güvenli analiz için TradingView'den 1 Saatlik grafik çekiyoruz (interval=60)
    url = f"https://s.tradingview.com/widgetembed/?frameElementId=tradingview_chart&symbol=OKX:{tv_symbol}.P&interval=60&theme=dark&style=1"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.goto(url)
            page.wait_for_timeout(3500)
            page.screenshot(path=output_path)
            browser.close()
        return True
    except Exception as e:
        print(f"Grafik görüntüsü alınamadı ({tv_symbol}): {e}")
        return False

def analyze_long_charts_with_gemini(signals_data):
    if not GEMINI_API_KEY or not signals_data:
        return None

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        contents = []
        
        prompt = """
        Sen kurumsal bir fon yöneticisisin ve profesyonel bir Swing (1-3 gün vadeli) Long/Spot trader'ısın.
        Sana şu anda tarayıcı botumdan yakalanan, saatlik grafikte majör dirençlerini kurumsal hacimle kıran veya kırmak üzere olan güvenli coinlerin grafiklerini gönderiyorum.
        Senden ricam analizi şu iki aşamada kurumsal ve net bir dille hazırlaman:
        
        1. BÖLÜM: MAJÖR AKÜMÜLASYON VE TREND ANALİZİ (SIRA İLE)
        Gelen sırayı bozmadan, her coinin büyük resmine (1H/4H yapılarına) bakarak 1-2 cümlelik net bir swing yorumu yaz.
        Format:
        • **[COIN ADI]**: [Hap kurumsal teknik yorumun]
        
        2. BÖLÜM: 👑 GEMINI ALFA SWING SEÇİMİ
        İçlerinden sahte kırılım ihtimali en düşük olan, arkasındaki alım hacmi bariz bir şekilde oturan ve birkaç gün arkamıza yaslanıp taşıyabileceğimiz EN GÜVENLİ 1 ADET coini seç:
        🎯 **Stratejik Alım Bölgesi:** [COIN ADI]
        💡 **Kurumsal Gerekçe:** [Mum yapılarını, hacim patlamasını ve 4H büyük resimdeki kırılım gücünü detaylandır]
        🛑 **Swing Stop Seviyesi:** [Fiyatın bu seviyenin altına inmesi durumunda formasyonun iptal olacağı net destek noktası]
        
        Doğrudan konuya gir, şablon dışı gereksiz cümleler kurma.
        """
        
        contents.append(prompt)
        
        for item in signals_data:
            if os.path.exists(item['img_path']):
                img = Image.open(item['img_path'])
                contents.append(img)
                contents.append(f"Coin: {item['symbol']} Teknik Özeti:\n{item['text_data']}\n\n")
        
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=contents,
        )
        return response.text
    except Exception as e:
        print(f"Gemini analiz hatası: {e}")
        return None

def scan_long():
    tickers = get_data("/api/v5/market/tickers", {"instType": "SWAP"})
    if not tickers: return

    underlyings = get_data("/api/v5/public/underlying", {"instType": "SWAP"})
    valid_cryptos = []
    if underlyings and isinstance(underlyings, list):
        for item in underlyings:
            if isinstance(item, dict) and 'underlying' in item:
                valid_cryptos.append(item['underlying'])
            elif isinstance(item, list) and len(item) > 0:
                valid_cryptos.append(item[0])

    detected_longs = []
    
    for t in tickers:
        symbol = t['instId']
        if "-USDT-" not in symbol: continue
        
        base_coin = symbol.split('-')[0]
        if valid_cryptos and base_coin not in valid_cryptos:
            continue
        
        try:
            last_p = float(t['last'])
            open_24h = float(t['open24h'])
            if open_24h == 0: continue
            chg = (last_p / open_24h - 1) * 100
            
            # FOMO olmamış, dinlenen veya yeni kalkış yapan coin filtresi
            if -3.0 <= chg <= 5.0:
                # 1 SAATLİK GRAFİK VERİSİ
                c_1h = get_data("/api/v5/market/candles", {"instId": symbol, "bar": "1H", "limit": "30"})
                if not c_1h or len(c_1h) < 25: continue
                
                df_1h = pd.DataFrame(c_1h, columns=['ts','o','h','l','c','v','vc','vq','conf']).iloc[::-1].reset_index(drop=True)
                df_1h['v'] = df_1h['v'].astype(float)
                df_1h['c'] = df_1h['c'].astype(float)
                
                # Saatlik hacim kontrolü
                last_vol = df_1h['v'].iloc[-1]
                avg_vol = df_1h['v'].iloc[-21:-1].mean()
                if avg_vol == 0: continue
                vol_ratio = last_vol / avg_vol
                
                # 1. BÜYÜK FİLTRE: Bariz bir saatlik hacim patlaması var mı?
                if vol_ratio >= VOLUME_MULTIPLIER:
                    # 2. BÜYÜK FİLTRE: Bu hacim alıcı odaklı mı?
                    _, _, buy_ratio = get_smart_buy_volume(symbol)
                    
                    if buy_ratio >= BUY_SELL_RATIO_LIMIT:
                        # 4 SAATLİK BÜYÜK RESİM KONTROLÜ
                        c_4h = get_data("/api/v5/market/candles", {"instId": symbol, "bar": "4H", "limit": "30"})
                        if not c_4h: continue
                        df_4h = pd.DataFrame(c_4h, columns=['ts','o','h','l','c','v','vc','vq','conf']).iloc[::-1].reset_index(drop=True)
                        
                        major_res = find_major_resistance(df_4h)
                        last_candle = df_1h.iloc[-1]
                        
                        # Eğer saatlik mum yeşilse ve fiyat yapısı güçlüyse
                        if float(last_candle['c']) > float(last_candle['o']):
                            status_note = "🚀 *MAJÖR BREAKOUT:* 4H Direnç hattı hacimli kırılıyor!" if last_p >= major_res else "📦 *GÜÇLÜ AKÜMÜLASYON:* Büyük direnç altında kurumsal toplama."
                            
                            tv_clean = symbol.replace('-SWAP', '').replace('-', '')
                            img_name = f"{symbol}_1h_swing.png"
                            
                            print(f"📸 [SWING LONG] {symbol} için 1H grafik çekiliyor...")
                            if take_tradingview_screenshot(tv_clean, img_name):
                                text_summary = f"Fiyat: {last_p} | 1H Hacim Oranı: {round(vol_ratio,1)}x | Alım Gücü: {buy_ratio} | 4H Direnç: {major_res}"
                                
                                detected_longs.append({
                                    "symbol": symbol,
                                    "img_path": img_name,
                                    "text_data": text_summary,
                                    "chg": round(chg, 1),
                                    "vol_ratio": round(vol_ratio, 1),
                                    "buy_ratio": buy_ratio,
                                    "note": status_note,
                                    "tv_link": f"https://www.tradingview.com/chart/?symbol=OKX:{tv_clean}.P"
                                })
                            time.sleep(0.5)
                            
        except Exception as e:
            print(f"{symbol} taranırken hata: {e}")
            continue

    # Raporlama Aşaması
    if detected_longs:
        # Kurumsal alım gücü en yüksek olana göre sırala
        detected_longs.sort(key=lambda x: x['buy_ratio'], reverse=True)
        
        header = "🟢 *SWING SPOT / LONG ALARMI (1H/4H)* 🟢\n━━━━━━━━━━━━━━━\n"
        list_elements = []
        for l in detected_longs:
            item_msg = (f"• *{l['symbol']}* | 24h: `% {l['chg']}`\n"
                        f"  👉 {l['note']}\n"
                        f"  📊 Saatlik Hacim: `{l['vol_ratio']}x`\n"
                        f"  ⚖️ Alım Oranı: `{l['buy_ratio']}`\n"
                        f"  🔗 [Grafiği Aç]({l['tv_link']})\n"
                        f"  ━━━━━━━━━━━━━━━")
            list_elements.append(item_msg)
            
        send_telegram_text(header + "\n".join(list_elements))
        
        print("🤖 Gemini büyük resmi analiz ediyor...")
        ai_report = analyze_long_charts_with_gemini(detected_longs)
        
        if ai_report:
            selected_photo = None
            for l in detected_longs:
                if l['symbol'].split('-')[0] in ai_report:
                    selected_photo = l['img_path']
                    break
            
            if selected_photo:
                send_telegram_photo(selected_photo, ai_report)
            else:
                send_telegram_text(ai_report)
                
        for l in detected_longs:
            if os.path.exists(l['img_path']):
                os.remove(l['img_path'])

if __name__ == "__main__":
    scan_long()
