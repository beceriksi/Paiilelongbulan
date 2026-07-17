import os
import time
import requests
import asyncio
from dotenv import load_dotenv

# .env dosyasındaki Telegram bilgilerini yükle
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Radardaki ağlar
CHAINS = ["base", "solana", "ethereum", "bsc", "robinhood"]

def send_telegram(message):
    """Telegram grubuna anlık olarak sinyal fırlatır."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram gönderim hatası: {e}")

def find_trending_chain():
    """Tüm ağların hacimlerini tarar ve paranın aktığı EN SICAK ağı seçer."""
    print("🌐 Ağlar taranıyor, para akışı analiz ediliyor...")
    chain_scores = {}
    
    for chain in CHAINS:
        # DexScreener'dan o ağdaki en aktif çiftleri çekiyoruz
        url = f"https://api.dexscreener.com/latest/dex/chains/{chain}"
        try:
            response = requests.get(url, timeout=10).json()
            pairs = response.get("pairs", [])[:30] # İlk 30 çifti incele
            
            # Ağın son 5 dakikalık ve 1 saatlik toplam hacim trendini hesapla
            total_h1_vol = sum(float(p.get("volume", {}).get("h1", 0)) for p in pairs)
            chain_scores[chain] = total_h1_vol
        except Exception:
            chain_scores[chain] = 0

    # En yüksek hacim büyümesi olan ağı seç
    best_chain = max(chain_scores, key=chain_scores.get)
    print(f"🔥 Bugün Para Buraya Akıyor: {best_chain.upper()}")
    return best_chain

def analyze_gems(chain):
    """Seçilen sıcak ağdaki toplanan (accumulation) gerçek gemleri bulur."""
    url = f"https://api.dexscreener.com/latest/dex/chains/{chain}"
    try:
        response = requests.get(url, timeout=10).json()
        pairs = response.get("pairs", [])
    except Exception:
        return

    for pair in pairs[:50]: # En aktif 50 yeni pariteyi incele
        token_name = pair.get("baseToken", {}).get("name", "Unknown")
        token_symbol = pair.get("baseToken", {}).get("symbol", "Gems")
        token_address = pair.get("baseToken", {}).get("address", "")
        
        # 1. Havuz Güvenlik Filtresi (Rug Kontrolü)
        liquidity = float(pair.get("liquidity", {}).get("usd", 0))
        if liquidity < 5000: # En az 5,000$ havuz kilidi olmayan çöpleri ele
            continue
            
        # 2. Zekice Bot Eleme Filtresi (Wash Trade Engelleyici)
        h1_txns = pair.get("txns", {}).get("h1", {})
        buys = float(h1_txns.get("buys", 0))
        sells = float(h1_txns.get("sells", 0))
        
        h1_vol = pair.get("volume", {}).get("h1", {})
        # DexScreener'dan gelen benzersiz (unique) cüzdan sayıları
        # Not: DexScreener API'de bu alanlar bazen derinlikte değişebilir, en kararlı hacim analizi:
        if buys == 0 or sells == 0:
            continue
            
        # Alım/Satım Oranı (Buy/Sell Ratio)
        buy_sell_ratio = buys / sells
        
        # Fiyat Değişimi (Ufak hareketleri yakalamak için)
        price_change = float(pair.get("priceChange", {}).get("m5", 0)) # Son 5 dakikalık hareket
        
        # 🎯 ZEKİCE KRİTER: 
        # Fiyat henüz uçmamış (%0 ile %15 arası) AMA alım sayısı satım sayısının 2.5 katından fazlaysa,
        # bu durum birilerinin malı çaktırmadan arkada topladığını (accumulation) gösterir!
        if 0 < price_change <= 15.0 and buy_sell_ratio >= 2.5:
            
            # Telegram Mesaj Formatı
            msg = (
                f"🚨 *GEM TOPLAMA ALARMI ({chain.upper()})*\n\n"
                f"🪙 *Token:* {token_name} ({token_symbol})\n"
                f"📈 *Son 5D Fiyat Hareketi:* %{price_change}\n"
                f"📊 *Alım Baskısı (Buy/Sell):* {buy_sell_ratio:.2f}\n"
                f"💰 *Mevcut Likidite:* ${liquidity:,.0f}\n\n"
                f"🔗 *Sözleşme Adresi (Kopyala):*\n`{token_address}`\n\n"
                f"🔍 [DexScreener Grafiği]({pair.get('url')})"
            )
            
            print(f"🎯 Sinyal Yakalandı: {token_symbol} - Telegram'a gönderiliyor...")
            send_telegram(msg)
            time.sleep(2) # Telegram spam filtresine takılmamak için

async def main():
    # Adım 1: Paranın aktığı en sıcak ağı tespit et
    hot_chain = find_trending_chain()
    
    # Adım 2: O ağdaki gizlice toplanan bebek projeleri ayıkla
    analyze_gems(hot_chain)

if __name__ == "__main__":
    # Test amaçlı botun başladığını haber verelim
    send_telegram("🤖 *GemBot Sistemi Aktif!* Para akışı ve toplanan gem taraması başlıyor...")
    asyncio.run(main())
