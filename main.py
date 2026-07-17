import os
import time
import requests
import asyncio

# GitHub Secrets üzerinden gelen Telegram bilgileri
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Radardaki ağlar
CHAINS = ["base", "solana", "ethereum", "bsc", "robinhood"]

def send_telegram(message):
    """Telegram grubuna anlık olarak sinyal fırlatır."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram Token veya Chat ID bulunamadı! GitHub Secrets kontrol edilmeli.")
        return
        
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
        url = f"https://api.dexscreener.com/latest/dex/chains/{chain}"
        try:
            response = requests.get(url, timeout=10).json()
            pairs = response.get("pairs", [])[:30]
            
            total_h1_vol = sum(float(p.get("volume", {}).get("h1", 0)) for p in pairs)
            chain_scores[chain] = total_h1_vol
        except Exception:
            chain_scores[chain] = 0

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

    for pair in pairs[:50]:
        token_name = pair.get("baseToken", {}).get("name", "Unknown")
        token_symbol = pair.get("baseToken", {}).get("symbol", "Gems")
        token_address = pair.get("baseToken", {}).get("address", "")
        
        # 1. Havuz Güvenlik Filtresi (Rug Kontrolü)
        liquidity = float(pair.get("liquidity", {}).get("usd", 0))
        if liquidity < 5000:
            continue
            
        # 2. Zekice Bot Eleme Filtresi (Wash Trade Engelleyici)
        h1_txns = pair.get("txns", {}).get("h1", {})
        buys = float(h1_txns.get("buys", 0))
        sells = float(h1_txns.get("sells", 0))
        
        if buys == 0 or sells == 0:
            continue
            
        buy_sell_ratio = buys / sells
        price_change = float(pair.get("priceChange", {}).get("m5", 0))
        
        # 🎯 ZEKİCE KRİTER: Fiyat henüz uçmamış (%0 - %15) ama alım baskısı 2.5 katından fazlaysa
        if 0 < price_change <= 15.0 and buy_sell_ratio >= 2.5:
            
            # Havuz Yaşı Hesabı (Kaç saat önce açıldı?)
            pair_created_at = pair.get("pairCreatedAt", 0) / 1000
            age_hours = (time.time() - pair_created_at) / 3600 if pair_created_at > 0 else 0
            
            # Telegram Mesaj Formatı
            msg = (
                f"🚨 *GEM TOPLAMA ALARMI ({chain.upper()})*\n\n"
                f"🪙 *Token:* {token_name} ({token_symbol})\n"
                f"⏳ *Havuz Yaşı:* {age_hours:.1f} Saat Önce Açıldı\n"
                f"📈 *Son 5 Dakika Fiyat Hareketi:* %{price_change}\n"
                f"📊 *Alım Baskısı (Buy/Sell):* {buy_sell_ratio:.2f}\n"
                f"💰 *Mevcut Likidite:* ${liquidity:,.0f}\n\n"
                f"🔗 *Sözleşme Adresi (Kopyala):*\n`{token_address}`\n\n"
                f"🔍 [DexScreener Grafiği]({pair.get('url')})"
            )
            
            print(f"🎯 Sinyal Yakalandı: {token_symbol} - Telegram'a gönderiliyor...")
            send_telegram(msg)
            time.sleep(2)

async def main():
    # İlk açılışta bağlantıyı doğrulamak için Telegram'a sinyal çakalım
    send_telegram("🤖 *GemBot Sistemi Aktif!* Para akışı ve toplanan gem taraması başlıyor...")
    
    hot_chain = find_trending_chain()
    analyze_gems(hot_chain)

if __name__ == "__main__":
    asyncio.run(main())
