import os
import time
import requests
import pandas as pd
from PIL import Image
from google import genai
from google.genai import types
from playwright.sync_api import sync_playwright

# ENV / SECRETS
TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# STRATEJİ AYARLARI (Short & Long Ortak Limitleri)
RSI_LIMIT = 70
CHANGE_24H_LIMIT = 8
VOLUME_MULTIPLIER = 3.0       # Long için hacim katlama barajı (Örn: 3 katı)
BUY_SELL_RATIO_LIMIT = 1.5    # Long için alım/satım güç oranı barajı

def send_telegram_text(msg):
    if TOKEN and CHAT_ID and msg:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        # Mesaj Telegram sınırından (4096) uzunsa bölerek garanti gönderir
        if len(msg) > 4000:
            parts = [msg[i:i+4000] for i in range(0, len(msg), 4000)]
            for part in parts:
                try:
                    requests.post(url, json={
                        "chat_id": CHAT_ID, "text": part, "parse_mode": "Markdown", "disable_web_page_preview": True
                    }, timeout=10)
                    time.sleep(0.5)
                except Exception as e:
                    print(f"Parçalı Telegram metin hatası: {e}")
            return

        try:
            res = requests.post(url, json={
                "chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown", "disable_web_page_preview": True
            }, timeout=10)
            if res.status_code != 200:
                print(f"⚠️ Telegram Metin Gönderilemedi! Kod: {res.status_code}. Düz metin deneniyor...")
                requests.post(url, json={"chat_id": CHAT_ID, "text": msg, "disable_web_page_preview": True}, timeout=10)
        except Exception as e: 
            print(f"Telegram metin hatası: {e}")

def send_telegram_photo(photo_path, caption):
    if TOKEN and CHAT_ID and os.path.exists(photo_path):
        url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
        
        # Fotoğraf altı yazısı 1024 karakter sınırını aşarsa, yapıyı bozmamak için ayrı ayrı gönderir
        if len(caption) > 1000:
            print("📝 Analiz uzun olduğu için fotoğraf ve rapor metni ayrı gönderiliyor...")
            try:
                with open(photo_path, 'rb') as photo:
                    requests.post(url, data={"chat_id": CHAT_ID, "caption": "📸 GEMINI ALFA ANALİZ GRAFİĞİ"}, files={"photo": photo}, timeout=15)
                time.sleep(1)
                send_telegram_text(caption)
            except Exception as e:
                print(f"Uzun analiz split hatası: {e}")
            return

        try:
            res = requests.post(url, data={"chat_id": CHAT_ID, "caption": caption, "parse_mode": "Markdown"}, files={"photo": photo}, timeout=15)
            if res.status_code != 200:
                print(f"⚠️ Telegram Fotoğraflı Gönderim Başarısız! Düz metin deneniyor...")
                send_telegram_text(caption)
        except Exception as e: 
            print(f"Telegram fotoğraf hatası: {e}")

def get_data(endpoint, params={}):
    base = "https://www.okx.com"
    try:
        res = requests.get(base + endpoint, params=params, timeout=10).json()
        return res.get('data', [])
    except: 
        return []

def find_custom_sr(df, pivot_len=2):
    if len(df) < (pivot_len * 2 + 1):
        return [], []
    highs = df['h'].astype(float).values
    lows = df['l'].astype(float).values
    p_highs, p_lows = [], []
    for i in range(pivot_len, len(highs) - pivot_len):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            p_highs.append(highs[i])
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            p_lows.append(lows[i])
    return p_highs, p_lows

def find_major_resistance(df_4h):
    """Long botu için 4H majör direnç seviyesini döner"""
    p_highs, _ = find_custom_sr(df_4h, pivot_len=2)
    if p_highs:
        return max(p_highs)
    return 0.0

def get_smart_volume(symbol, df_1h):
    """Short botu için hacim yapısını analiz eder"""
    res = get_data("/api/v5/rubik/stat/taker-volume", {"instId": symbol, "period": "1H"})
    buy_v, sell_v = 0, 0
    if res and len(res) > 0 and len(res[0]) > 2 and float(res[0][1]) > 0:
        buy_v = float(res[0][1])
        sell_v = float(res[0][2])
    else:
        if not df_1h.empty:
            last = df_1h.iloc[-1]
            c, o, h, l, v = float(last['c']), float(last['o']), float(last['h']), float(last['l']), float(last['v'])
            body = abs(c - o)
            total_range = (h - l) if (h - l) > 0 else 0.0001
            if c > o: 
                buy_v = v * (0.5 + (body / total_range) * 0.5)
                sell_v = v - buy_v
            else: 
                sell_v = v * (0.5 + (body / total_range) * 0.5)
                buy_v = v - sell_v
    return buy_v, sell_v, round(buy_v / sell_v, 2) if sell_v > 0 else 1.0

def get_smart_buy_volume(symbol):
    """Long botu için alım-odaklı hacim gücünü kontrol eder"""
    res = get_data("/api/v5/rubik/stat/taker-volume", {"instId": symbol, "period": "1H"})
    if res and len(res) > 0 and len(res[0]) > 2:
        buy_v = float(res[0][1])
        sell_v = float(res[0][2])
        ratio = round(buy_v / sell_v, 2) if sell_v > 0 else 1.0
        return buy_v, sell_v, ratio
    return 1.0, 1.0, 1.0

def check_volume_divergence(df):
    if len(df) < 5:
        return ""
    p = df['c'].astype(float).iloc[-5:].values
    v = df['v'].astype(float).iloc[-5:].values
    if p[-1] > p[0] and v[-1] < v[-2]: 
        return "⚠️ *AYI UYUMSUZLUĞU (Düşüş Beklentisi)*"
    return ""

def check_candle_trigger_15m(symbol):
    try:
        c_15m = get_data("/api/v5/market/candles", {"instId": symbol, "bar": "15m", "limit": "5"})
        if not c_15m or len(c_15m) == 0:
            return "⏳ 15m veri alınamadı", 0
        df_15m = pd.DataFrame(c_15m, columns=['ts','o','h','l','c','v','vc','vq','conf']).iloc[::-1].reset_index(drop=True)
        o, h, l, c = float(df_15m.iloc[-1]['o']), float(df_15m.iloc[-1]['h']), float(df_15m.iloc[-1]['l']), float(df_15m.iloc[-1]['c'])
        body = abs(c - o)
        upper_wick = (h - max(o, c)) if h > max(o, c) else 0
        if c < o or upper_wick > (body * 0.8):
            return "🚨 *ACİL DURUM: REAKSİYON MUMU GELDİ (15m)*", 1
        else:
            return "⏳ 15m Grafik Hala Güçlü Yeşil Gövdeli", 0
    except:
        return "⏳ 15m Durumu Okunamadı", 0

def get_funding_rate(symbol):
    res = get_data("/api/v5/public/funding-rate", {"instId": symbol})
    if res and len(res) > 0:
        rate = float(res[0].get('fundingRate', 0))
        rate_pct = rate * 100
        if rate_pct < -0.05:
            return f"`%{rate_pct:.3f}` ⚠️ *AŞIRI NEGATİF FL*"
        else:
            return f"`%{rate_pct:.3f}`"
    return "`Veri Alınamadı`"

def take_tradingview_screenshot(tv_symbol, output_path="chart.png"):
    url = f"https://s.tradingview.com/widgetembed/?frameElementId=tradingview_chart&symbol=OKX:{tv_symbol}.P&interval=60&theme=dark&style=1"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.goto(url)
            page.wait_for_timeout(4000)
            page.screenshot(path=output_path)
            browser.close()
        return True
    except Exception as e:
        print(f"Grafik görüntüsü alınamadı ({tv_symbol}): {e}")
        return False

# ==================== SHORT BOT ANALİZ VE TARAMA MANTIĞI ====================
def analyze_charts_with_gemini(signals_data):
    if not GEMINI_API_KEY or not signals_data:
        return None
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        contents = []
        prompt = """
        Sen üst düzey bir kripto para teknik analisti ve kurumsal bir short (açığa satış) trader'ısın.
        Sana şu anda tarayıcı botumdan yakalanan ve aşırı şişmiş (RSI > 70 ve %8 üstü yükselmiş) coinlerin 1 Saatlik grafiklerini ve anlık teknik verilerini gönderiyorum.
        Sana iletilen verilerin içinde anlık "15 DAKİKALIK REAKSİYON/ACİL DURUM MUMU" bilgisi de bulunmaktadır.
        
        Senden ricam analizi TAM OLARAK şu iki aşamada ve şu şablonda hazırlaman:
        
        1. BÖLÜM: TÜM LİSTEYE BAKIŞ (SANA GÖNDERİLEN SIRA İLE)
        Sana aşağıda verdiğim sırayı ASLA bozmadan, her coin için grafik yapısına ve özellikle "15m Acil Durum Reaksiyon Mumu" durumuna bakarak sadece 1-2 cümlelik net bir yorum yaz.
        Formatı şöyle olsun:
        • **[COIN ADI]**: [Buraya 1-2 cümlelik hap teknik yorumun. 15m acil durum onayı varsa bunu kesinlikle vurgula]
        
        2. BÖLÜM: 👑 GEMINI ALFA SEÇİMİ
        Yukarıdaki listeden kısa vadeli (scalping) SHORT pozisyon açmak için EN GÜVENLİ, yapısı en çok bozulan, 15m reaksiyon mumu tetiklenmiş olan 1 ADET coini seç ve detaylandır:
        🎯 **İşlem Yapılacak Coin:** [COIN ADI]
        💡 **Neden Bu Grafik? (Detaylı Teknik Gerekçe):** [Seçtiğin coinin mum hareketlerini, anlık 15m tepkisini ve hacim yapısını kurumsal bir dille açıkla]
        🛑 **Risk & Stop Yönetimi:** [İptal seviyesi veya dikkat edilecek direnç noktası]
        
        Lütfen bu şablonun dışına çıkma, doğrudan konuya gir.
        """
        contents.append(prompt)
        for item in signals_data:
            if os.path.exists(item['img_path']):
                img = Image.open(item['img_path'])
                contents.append(img)
                contents.append(f"Coin: {item['symbol']} Teknik Özeti:\n{item['text_data']}\n\n")
        
        response = client.models.generate_content(model='gemini-2.5-flash', contents=contents)
        return response.text
    except Exception as e:
        print(f"Gemini analiz hatası: {e}")
        return None

def scan():
    print("🔍 [SHORT BOT] OKX Tickers verisi çekiliyor...")
    tickers = get_data("/api/v5/market/tickers", {"instType": "SWAP"})
    if not tickers: 
        print("❌ OKX'ten hiçbir ticker verisi alınamadı!")
        return
    print(f"📊 Toplam {len(tickers)} adet swap çifti bulundu.")

    detected_signals = []
    for t in tickers:
        symbol = t['instId']
        if "-USDT-" not in symbol: continue
        try:
            last_p = float(t['last'])
            open_24h = float(t['open24h'])
            if open_24h == 0: continue
            chg = (last_p / open_24h - 1) * 100
            
            if chg > CHANGE_24H_LIMIT:
                print(f"🚀 {symbol} %8 barajını geçti (%{chg:.2f}). 1H Mumları isteniyor...")
                c_1h = get_data("/api/v5/market/candles", {"instId": symbol, "bar": "1H", "limit": "100"})
                if not c_1h: continue
                df_1h = pd.DataFrame(c_1h, columns=['ts','o','h','l','c','v','vc','vq','conf']).iloc[::-1].reset_index(drop=True)
                df_1h['c'] = df_1h['c'].astype(float)
                
                if len(df_1h) < 15: continue
                delta = df_1h['c'].diff()
                g = (delta.where(delta > 0, 0)).rolling(14).mean()
                l = (-delta.where(delta < 0, 0)).rolling(14).mean()
                last_g, last_l = g.iloc[-1], l.iloc[-1]
                rsi = 100 - (100 / (1 + last_g / last_l)) if last_l != 0 else (100 if last_g > 0 else 50)
                
                if rsi > RSI_LIMIT:
                    print(f"🔥 KRİTİK KOŞUL SAĞLANDI: {symbol} -> RSI: {rsi:.2f} | Değişim: %{chg:.2f}")
                    c_4h = get_data("/api/v5/market/candles", {"instId": symbol, "bar": "4H", "limit": "100"})
                    if not c_4h: continue
                    df_4h = pd.DataFrame(c_4h, columns=['ts','o','h','l','c','v','vc','vq','conf']).iloc[::-1].reset_index(drop=True)
                    
                    ph_4h, _ = find_custom_sr(df_4h)
                    res_4h = min([x for x in ph_4h if x > last_p]) if any(x > last_p for x in ph_4h) else (ph_4h[-1] if ph_4h else 0)
                    ph_1h, _ = find_custom_sr(df_1h)
                    res_1h = min([x for x in ph_1h if x > last_p]) if any(x > last_p for x in ph_1h) else (ph_1h[-1] if ph_1h else 0)

                    bv, sv, ratio = get_smart_volume(symbol, df_1h)
                    div = check_volume_divergence(df_1h)
                    candle_text, urgent_score = check_candle_trigger_15m(symbol)
                    funding_info = get_funding_rate(symbol)

                    if last_p > res_4h and res_4h != 0: note = "🔥 *KIRILIM:* 4H Direnç üstü kapanış, tehlikeli!"
                    elif res_1h != 0 and (res_1h - last_p) / last_p < 0.006: note = "🚨 *DİRENÇTE:* Fiyat dirençten satış yemeye çalışıyor."
                    elif ratio < 0.85: note = "📉 *SATIŞ BASKISI:* Direnç altı hacimli satışlar başladı."
                    elif ratio > 1.35: note = "🚫 *ZİRVEDE ALICI:* FOMO var, ekleme yapmak riskli."
                    else: note = "🛡️ *GÖZLEM:* Direnç altı kararsız yapı."

                    tv_clean = symbol.replace('-SWAP', '').replace('-', '')
                    img_name = f"{symbol}_1h.png"
                    has_photo = take_tradingview_screenshot(tv_clean, img_name)
                    if not has_photo: img_name = "NO_IMAGE"
                    
                    text_summary = f"Fiyat: {last_p} | 4H Dir: {res_4h} | 1H Dir: {res_1h} | 15m Durum: {candle_text} | Funding: {funding_info}"
                    detected_signals.append({
                        "symbol": symbol, "img_path": img_name, "text_data": text_summary, "rsi": round(rsi, 1),
                        "chg": round(chg, 1), "ratio": ratio, "div": div, "note": note, "candle": candle_text,
                        "urgent": urgent_score, "funding": funding_info,
                        "tv_link": f"https://www.tradingview.com/chart/?symbol=OKX:{tv_clean}.P"
                    })
                    time.sleep(0.2)
        except Exception as e:
            print(f"Short döngü içi hata yutuldu ({symbol}): {e}")
            continue

    if detected_signals:
        print(f"📢 Tarama bitti. Short Telegram'ına gönderilecek toplam coin sayısı: {len(detected_signals)}")
        detected_signals.sort(key=lambda x: x['urgent'], reverse=True)
        header = "🚨 *TEKNİK ALARM VEREN COİNLER (SHORT)* 🚨\n━━━━━━━━━━━━━━━\n"
        list_elements = []
        for s in detected_signals:
            div_text = f" | {s['div']}" if s['div'] else ""
            item_msg = (f"• *{s['symbol']}* | RSI: `{s['rsi']}` | %{s['chg']}\n"
                        f"  👉 {s['note']}{div_text}\n"
                        f"  ⏳ {s['candle']}\n"
                        f"  💰 Fonlama Oranı: {s['funding']}\n"
                        f"  ⚖️ Hacim Oranı: `{s['ratio']}`\n"
                        f"  🔗 [Grafiği Aç]({s['tv_link']})\n"
                        f"  ━━━━━━━━━━━━━━━")
            list_elements.append(item_msg)
        send_telegram_text(header + "\n".join(list_elements))
        
        try:
            print("🤖 Gemini short grafiklerini analiz ediyor...")
            ai_report = analyze_charts_with_gemini(detected_signals)
            if ai_report:
                selected_photo = None
                for s in detected_signals:
                    coin_pure = s['symbol'].split('-')[0].upper()
                    if s['img_path'] != "NO_IMAGE" and os.path.exists(s['img_path']):
                        if coin_pure in ai_report.upper():
                            selected_photo = s['img_path']
                            break
                if selected_photo: send_telegram_photo(selected_photo, ai_report)
                else: send_telegram_text(ai_report)
        except Exception as e:
            print(f"Gemini short aşaması hatası: {e}")
            
        for s in detected_signals:
            if s['img_path'] != "NO_IMAGE" and os.path.exists(s['img_path']):
                try: os.remove(s['img_path'])
                except: pass
    else:
        print("⏳ [SHORT] Kriterlere uyan hiçbir coin bulunamadı.")

# ==================== LONG BOT ANALİZ VE TARAMA MANTIĞI ====================
def analyze_long_charts_with_gemini(signals_data):
    if not GEMINI_API_KEY or not signals_data:
        return None
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        contents = []
        prompt = """
        Sen üst düzey bir kripto para teknik analisti ve kurumsal bir long (spot/long) trader'ısın.
        Sana şu anda tarayıcı botumdan yakalanan, henüz FOMO olmamış, dinlenen veya yeni kalkış yapan (hacim katlamış ve alım odaklı baskı gören) coinlerin grafiklerini gönderiyorum.
        
        Senden ricam analizi TAM OLARAK şu iki aşamada ve şu şablonda hazırlaman:
        
        1. BÖLÜM: TÜM LİSTEYE BAKIŞ (SANA GÖNDERİLEN SIRA İLE)
        Sana aşağıda verdiğim sırayı ASLA bozmadan, her coin için grafik yapısına bakarak sadece 1-2 cümlelik net bir yükseliş potansiyeli yorumu yaz.
        Formatı şöyle olsun:
        • **[COIN ADI]**: [Buraya 1-2 cümlelik hap teknik yorumun. Hacim kırılımı durumunu kesinlikle vurgula]
        
        2. BÖLÜM: 👑 GEMINI LONG ALFA SEÇİMİ
        Yukarıdaki listeden swing veya long pozisyon açmak için EN GÜVENLİ, kurumsal alıcısı en yoğun olan 1 ADET coini seç ve detaylandır:
        🎯 **İşlem Yapılacak Coin:** [COIN ADI]
        💡 **Neden Bu Grafik? (Detaylı Teknik Gerekçe):** [Seçtiğin coinin mum hareketlerini ve alım odaklı hacim yapısını kurumsal bir dille açıkla]
        🛑 **Risk & Stop Yönetimi:** [İptal seviyesi veya dikkat edilecek destek noktası]
        
        Lütfen bu şablonun dışına çıkma, doğrudan konuya gir.
        """
        contents.append(prompt)
        for item in signals_data:
            if os.path.exists(item['img_path']):
                img = Image.open(item['img_path'])
                contents.append(img)
                contents.append(f"Coin: {item['symbol']} Teknik Özeti:\n{item['text_data']}\n\n")
        
        response = client.models.generate_content(model='gemini-2.5-flash', contents=contents)
        return response.text
    except Exception as e:
        print(f"Gemini Long analiz hatası: {e}")
        return None

def scan_long():
    print("🔍 [LONG BOT] OKX Tickers verisi çekiliyor...")
    tickers = get_data("/api/v5/market/tickers", {"instType": "SWAP"})
    if not tickers: 
        print("❌ OKX'ten ticker verisi alınamadı!")
        return
    print(f"📊 Toplam {len(tickers)} adet swap çifti bulundu.")

    detected_longs = []
    for t in tickers:
        symbol = t['instId']
        if "-USDT-" not in symbol: continue
        try:
            last_p = float(t['last'])
            open_24h = float(t['open24h'])
            if open_24h == 0: continue
            chg = (last_p / open_24h - 1) * 100
            
            # FOMO olmamış, yatay veya hafif yeni kalkan yapılar
            if -3.0 <= chg <= 5.0:
                c_1h = get_data("/api/v5/market/candles", {"instId": symbol, "bar": "1H", "limit": "30"})
                if not c_1h or len(c_1h) < 25: continue
                
                df_1h = pd.DataFrame(c_1h, columns=['ts','o','h','l','c','v','vc','vq','conf']).iloc[::-1].reset_index(drop=True)
                df_1h['v'] = df_1h['v'].astype(float)
                df_1h['c'] = df_1h['c'].astype(float)
                
                last_vol = df_1h['v'].iloc[-1]
                avg_vol = df_1h['v'].iloc[-21:-1].mean()
                if avg_vol == 0: continue
                vol_ratio = last_vol / avg_vol
                
                # 1. Filtre: Hacim katlaması (vol_ratio_clean Bug'ı tamamen giderildi)
                if vol_ratio >= VOLUME_MULTIPLIER:
                    print(f"🔥 {symbol} hacim katlama barajını geçti ({vol_ratio:.1f}x). Alım gücü kontrol ediliyor...")
                    
                    # 2. Filtre: Alıcı Odaklı Alım Gücü (Buy/Sell Hacim Oranı)
                    _, _, buy_ratio = get_smart_buy_volume(symbol)
                    if buy_ratio >= BUY_SELL_RATIO_LIMIT:
                        print(f"📈 {symbol} kurumsal alıcı filtresini geçti! Alım Oranı: {buy_ratio}")
                        
                        c_4h = get_data("/api/v5/market/candles", {"instId": symbol, "bar": "4H", "limit": "30"})
                        if not c_4h: continue
                        df_4h = pd.DataFrame(c_4h, columns=['ts','o','h','l','c','v','vc','vq','conf']).iloc[::-1].reset_index(drop=True)
                        
                        major_res = find_major_resistance(df_4h)
                        last_candle = df_1h.iloc[-1]
                        
                        if float(last_candle['c']) > float(last_candle['o']):
                            status_note = "🚀 *MAJÖR BREAKOUT:* 4H Direnç hattı hacimli kırılıyor!" if last_p >= major_res else "📦 *GÜÇLÜ AKÜMÜLASYON:* Büyük direnç altında kurumsal toplama."
                            
                            tv_clean = symbol.replace('-SWAP', '').replace('-', '')
                            img_name = f"{symbol}_1h_swing.png"
                            has_photo = take_tradingview_screenshot(tv_clean, img_name)
                            if not has_photo: img_name = "NO_IMAGE"
                                
                            text_summary = f"Fiyat: {last_p} | 1H Hacim Oranı: {round(vol_ratio,1)}x | Alım Gücü: {buy_ratio} | 4H Direnç: {major_res}"
                            detected_longs.append({
                                "symbol": symbol, "img_path": img_name, "text_data": text_summary, "chg": round(chg, 1),
                                "vol_ratio": round(vol_ratio, 1), "buy_ratio": buy_ratio, "note": status_note,
                                "tv_link": f"https://www.tradingview.com/chart/?symbol=OKX:{tv_clean}.P"
                            })
                            print(f"✅ [LONG] {symbol} başarıyla listeye eklendi.")
                            time.sleep(0.3)
        except Exception as e:
            print(f"❌ {symbol} taranırken döngü içi hata: {e}")
            continue

    if detected_longs:
        print(f"📢 Tarama bitti. Long Telegram'ına gönderilecek toplam coin sayısı: {len(detected_longs)}")
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
                coin_pure = l['symbol'].split('-')[0].upper()
                if l['img_path'] != "NO_IMAGE" and os.path.exists(l['img_path']):
                    if coin_pure in ai_report.upper():
                        selected_photo = l['img_path']
                        break
            if selected_photo: send_telegram_photo(selected_photo, ai_report)
            else: send_telegram_text(ai_report)
                
        for l in detected_longs:
            if l['img_path'] != "NO_IMAGE" and os.path.exists(l['img_path']):
                try: os.remove(l['img_path'])
                except: pass
    else:
        print("⏳ [LONG] Muhafazakar kriterlere uyan hiçbir coin bulunamadı.")

# ==================== ANA ÇALIŞTIRICI SİSTEMİMİZ ====================
if __name__ == "__main__":
    # Sırasıyla hem kısa vade short fırsatlarını hem de kurumsal long hacimlerini tarar
    scan()
    time.sleep(2)
    scan_long()
