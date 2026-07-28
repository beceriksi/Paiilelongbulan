"""
Solana Narrative Bot — tek dosyalık, en basit hali.

NE YAPAR:
1. DexScreener'dan şu an hareketli/trend olan Solana tokenlarını çeker.
2. Bu tokenların isim/sembollerine bakıp hangi kelimenin (temanın) o an
   çok tekrar ettiğini KENDİ HESAPLAR — sana kelime girmeni istemez.
3. Likidite/hacim eşiklerini geçen ve daha önce bildirilmemiş tokenlar için
   temel bir güvenlik taraması (RugCheck) yapar.
4. Sonucu Telegram'a mesaj olarak atar.
5. Neyi daha önce bildirdiğini seen.json'a yazar, tekrar etmez.

SADECE İKİ SIR (SECRET) GEREKİR: TG_BOT_TOKEN ve TG_CHAT_ID.
Kurulum için README.md'ye bak.
"""

import json
import os
import re
import time
from collections import Counter

import requests

# ---------------- Ayarlar (istersen değiştir, dokunmasan da çalışır) ----------------

MIN_LIQUIDITY_USD = 5000        # bu likiditenin altındaki havuzları yok say
MIN_VOLUME_24H_USD = 20000      # bu 24s hacmin altındaki havuzları yok say
MAX_ALERTS_PER_RUN = 5          # bir çalıştırmada en fazla kaç bildirim atılsın
TOP_N_FOR_THEME = 40            # temayı hesaplarken en hareketli kaç tokene bakılsın

STOPWORDS = {
    "coin", "token", "sol", "solana", "the", "of", "and", "inu", "ai",
    "official", "fun", "pump", "v2", "new", "meme", "com",
}

SEEN_FILE = "seen.json"

TG_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

DEX_BASE = "https://api.dexscreener.com"
RUGCHECK_URL = "https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary"


# ---------------- Yardımcı fonksiyonlar ----------------

def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return json.load(f)
    return {}


def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(seen, f, indent=2)


def get_trending_pairs():
    """DexScreener'dan Solana'daki boosted (öne çıkan) tokenları ve genel
    'solana' aramasını birleştirip döner."""
    pairs = []

    try:
        r = requests.get(f"{DEX_BASE}/token-boosts/latest/v1", timeout=15)
        r.raise_for_status()
        for item in r.json():
            if item.get("chainId") != "solana":
                continue
            addr = item.get("tokenAddress")
            if not addr:
                continue
            stats = get_pair_for_token(addr)
            if stats:
                pairs.append(stats)
    except Exception as e:
        print(f"[warn] boosted fetch failed: {e}")

    try:
        r = requests.get(f"{DEX_BASE}/latest/dex/search", params={"q": "solana"}, timeout=15)
        r.raise_for_status()
        for p in r.json().get("pairs", []):
            if p.get("chainId") == "solana":
                pairs.append(p)
    except Exception as e:
        print(f"[warn] search fetch failed: {e}")

    # dedupe by base token address, keep the entry with highest liquidity
    by_addr = {}
    for p in pairs:
        addr = (p.get("baseToken") or {}).get("address")
        if not addr:
            continue
        liq = (p.get("liquidity") or {}).get("usd", 0) or 0
        if addr not in by_addr or liq > ((by_addr[addr].get("liquidity") or {}).get("usd", 0) or 0):
            by_addr[addr] = p

    return list(by_addr.values())


def get_pair_for_token(address):
    try:
        r = requests.get(f"{DEX_BASE}/token-pairs/v1/solana/{address}", timeout=15)
        r.raise_for_status()
        data = r.json()
        if not data:
            return None
        return max(data, key=lambda p: (p.get("liquidity") or {}).get("usd", 0) or 0)
    except Exception:
        return None


def detect_theme(pairs):
    """En hareketli tokenların isim/sembollerinden şu anki popüler kelimeyi
    (narrative) otomatik çıkarır."""
    ranked = sorted(pairs, key=lambda p: (p.get("volume") or {}).get("h24", 0) or 0, reverse=True)
    words = Counter()
    for p in ranked[:TOP_N_FOR_THEME]:
        base = p.get("baseToken") or {}
        text = f"{base.get('name', '')} {base.get('symbol', '')}"
        for w in re.findall(r"[a-zA-Z]{3,}", text.lower()):
            if w not in STOPWORDS:
                words[w] += 1
    return words.most_common(5)  # [(kelime, kaç token isminde geçti), ...]


def get_risk_summary(mint_address):
    try:
        r = requests.get(RUGCHECK_URL.format(mint=mint_address), timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        return {
            "score": data.get("score"),
            "risks": [risk.get("name") for risk in data.get("risks", [])],
        }
    except Exception:
        return None


def send_alert(text):
    if not (TG_BOT_TOKEN and TG_CHAT_ID):
        print("[warn] TG_BOT_TOKEN/TG_CHAT_ID yok, mesaj yerine buraya yazdırılıyor:\n", text)
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            data={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=15,
        )
        if r.status_code != 200:
            print(f"[warn] telegram gönderim hatası: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"[warn] telegram gönderim hatası: {e}")


def format_alert(pair, risk, theme_words):
    base = pair.get("baseToken") or {}
    liq = (pair.get("liquidity") or {}).get("usd")
    vol = (pair.get("volume") or {}).get("h24")
    name = base.get("name") or base.get("symbol") or "?"
    addr = base.get("address", "?")
    url = pair.get("url", "")

    theme_str = ", ".join(f"{w} ({c})" for w, c in theme_words) if theme_words else "belirgin bir tema yok"

    lines = [
        "🔎 <b>Yeni aday</b>",
        f"Token: {name}",
        f"Adres: <code>{addr}</code>",
    ]
    if liq is not None:
        lines.append(f"Likidite: ${liq:,.0f}")
    if vol is not None:
        lines.append(f"24s Hacim: ${vol:,.0f}")
    if url:
        lines.append(f"Grafik: {url}")
    if risk:
        lines.append(f"Rug-check skoru: {risk.get('score')}")
        if risk.get("risks"):
            lines.append(f"Bayraklar: {', '.join(risk['risks'])}")
    lines.append(f"\n🔥 Şu an trend olan kelimeler: {theme_str}")
    lines.append("⚠️ Otomatik tarama, yatırım tavsiyesi değildir. Kontratı kendin de kontrol et.")
    return "\n".join(lines)


# ---------------- Ana akış ----------------

def run():
    seen = load_seen()
    pairs = get_trending_pairs()
    print(f"{len(pairs)} aday tarandı.")

    theme_words = detect_theme(pairs)
    print(f"Tespit edilen tema: {theme_words}")

    filtered = [
        p for p in pairs
        if ((p.get("liquidity") or {}).get("usd", 0) or 0) >= MIN_LIQUIDITY_USD
        and ((p.get("volume") or {}).get("h24", 0) or 0) >= MIN_VOLUME_24H_USD
    ]
    filtered.sort(key=lambda p: (p.get("volume") or {}).get("h24", 0) or 0, reverse=True)

    sent = 0
    for pair in filtered:
        if sent >= MAX_ALERTS_PER_RUN:
            break
        addr = (pair.get("baseToken") or {}).get("address")
        if not addr or addr in seen:
            continue

        risk = get_risk_summary(addr)
        send_alert(format_alert(pair, risk, theme_words))
        seen[addr] = {"first_seen": int(time.time())}
        sent += 1

    save_seen(seen)
    print(f"Bitti. {sent} yeni bildirim gönderildi.")


if __name__ == "__main__":
    run()
