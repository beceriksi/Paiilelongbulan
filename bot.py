"""
Solana Narrative Bot — tek dosyalık, en basit hali.

NE YAPAR:
1. DexScreener'dan şu an hareketli/trend olan Solana tokenlarını çeker.
2. Bu tokenların isim/sembollerine bakıp hangi kelimenin (temanın) o an
   çok tekrar ettiğini KENDİ HESAPLAR — sana kelime girmeni istemez.
3. Likidite/hacim eşiklerini geçen her aday için RugCheck üzerinden otomatik
   GÜVENLİK TARAMASI yapar: mint/freeze yetkisi kapalı mı, likidite kilitli mi,
   en büyük cüzdan arzın çok fazlasını mı tutuyor, RugCheck "tehlikeli"
   diye işaretlemiş mi. Bu kontrollerden GEÇMEYEN tokenlar için hiç mesaj
   atmaz — sessizce eler.
4. Sadece güvenlik taramasını geçen tokenlar için Telegram'a mesaj atar.
5. Neyi daha önce işlediğini (geçse de elense de) seen.json'a yazar, aynı
   tokeni tekrar tekrar kontrol etmez.

SADECE İKİ SIR (SECRET) GEREKİR: TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID.
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

# --- Güvenlik filtresi eşikleri ---
MAX_TOP_HOLDER_PCT = 25         # tek bir cüzdan arzın bu yüzdesinden fazlasını tutuyorsa ele
REQUIRE_MINT_REVOKED = True     # mint yetkisi kapalı olmalı (aksi halde arz sonsuz basılabilir)
REQUIRE_FREEZE_REVOKED = True   # freeze yetkisi kapalı olmalı (aksi halde cüzdanın dondurulabilir)
BLOCK_ON_DANGER_RISK = True     # RugCheck "danger" seviyesinde bir bayrak koyduysa ele

STOPWORDS = {
    "coin", "token", "sol", "solana", "the", "of", "and", "inu", "ai",
    "official", "fun", "pump", "v2", "new", "meme", "com",
}

SEEN_FILE = "seen.json"

TG_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

DEX_BASE = "https://api.dexscreener.com"
RUGCHECK_REPORT_URL = "https://api.rugcheck.xyz/v1/tokens/{mint}/report"


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


def get_safety_report(mint_address):
    """
    RugCheck'in tam raporunu çeker ve otomatik pass/fail kararı üretir.
    Veri alınamazsa ya da yorumlanamazsa GÜVENLİ TARAF SEÇİLİR: token elenir
    (fail-closed) — "veri yok ama alarm at" yerine "veri yoksa gösterme".
    Döner: {"passed": bool, "reasons": [...], "top_holder_pct": float|None,
             "mint_revoked": bool|None, "freeze_revoked": bool|None, "score": ...}
    """
    try:
        r = requests.get(RUGCHECK_REPORT_URL.format(mint=mint_address), timeout=20)
        if r.status_code != 200:
            return {"passed": False, "reasons": [f"rugcheck verisi alınamadı ({r.status_code})"]}
        data = r.json()
    except Exception as e:
        return {"passed": False, "reasons": [f"rugcheck isteği başarısız ({e})"]}

    reasons = []

    mint_authority = data.get("mintAuthority")
    freeze_authority = data.get("freezeAuthority")
    mint_revoked = mint_authority in (None, "", "11111111111111111111111111111111")
    freeze_revoked = freeze_authority in (None, "", "11111111111111111111111111111111")

    if REQUIRE_MINT_REVOKED and not mint_revoked:
        reasons.append("mint yetkisi hâlâ açık (arz sonradan basılabilir)")
    if REQUIRE_FREEZE_REVOKED and not freeze_revoked:
        reasons.append("freeze yetkisi hâlâ açık (cüzdanlar dondurulabilir)")

    top_holder_pct = None
    top_holders = data.get("topHolders") or []
    non_lp_holders = [h for h in top_holders if not h.get("insider") and not h.get("isLp")]
    if non_lp_holders:
        top_holder_pct = max((h.get("pct") or 0) for h in non_lp_holders)
        if top_holder_pct > MAX_TOP_HOLDER_PCT:
            reasons.append(f"en büyük cüzdan arzın %{top_holder_pct:.0f}'ini tutuyor")

    danger_risks = [
        risk.get("name") for risk in data.get("risks", [])
        if str(risk.get("level", "")).lower() == "danger"
    ]
    if BLOCK_ON_DANGER_RISK and danger_risks:
        reasons.append(f"RugCheck tehlike bayrağı: {', '.join(danger_risks)}")

    return {
        "passed": len(reasons) == 0,
        "reasons": reasons,
        "top_holder_pct": top_holder_pct,
        "mint_revoked": mint_revoked,
        "freeze_revoked": freeze_revoked,
        "score": data.get("score"),
    }


def send_alert(text):
    if not (TG_BOT_TOKEN and TG_CHAT_ID):
        print("[warn] TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID yok, mesaj yerine buraya yazdırılıyor:\n", text)
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


def format_alert(pair, safety, theme_words):
    base = pair.get("baseToken") or {}
    liq = (pair.get("liquidity") or {}).get("usd")
    vol = (pair.get("volume") or {}).get("h24")
    name = base.get("name") or base.get("symbol") or "?"
    addr = base.get("address", "?")
    url = pair.get("url", "")

    theme_str = ", ".join(f"{w} ({c})" for w, c in theme_words) if theme_words else "belirgin bir tema yok"

    lines = [
        "✅ <b>Güvenlik taramasını geçen aday</b>",
        f"Token: {name}",
        f"Adres: <code>{addr}</code>",
    ]
    if liq is not None:
        lines.append(f"Likidite: ${liq:,.0f}")
    if vol is not None:
        lines.append(f"24s Hacim: ${vol:,.0f}")
    if url:
        lines.append(f"Grafik: {url}")

    lines.append(f"Mint yetkisi: {'kapalı ✅' if safety.get('mint_revoked') else 'açık ⚠️'}")
    lines.append(f"Freeze yetkisi: {'kapalı ✅' if safety.get('freeze_revoked') else 'açık ⚠️'}")
    if safety.get("top_holder_pct") is not None:
        lines.append(f"En büyük cüzdan payı: %{safety['top_holder_pct']:.1f}")
    if safety.get("score") is not None:
        lines.append(f"RugCheck skoru: {safety['score']}")

    lines.append(f"\n🔥 Şu an trend olan kelimeler: {theme_str}")
    lines.append(
        "⚠️ Bu kontroller temel bir ön filtredir, garanti değildir. "
        "Otomatik tarama, yatırım tavsiyesi değildir."
    )
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
    checked = 0
    for pair in filtered:
        if sent >= MAX_ALERTS_PER_RUN:
            break
        addr = (pair.get("baseToken") or {}).get("address")
        if not addr or addr in seen:
            continue

        checked += 1
        safety = get_safety_report(addr)
        seen[addr] = {
            "first_seen": int(time.time()),
            "passed_safety": safety["passed"],
            "reasons": safety.get("reasons", []),
        }

        if not safety["passed"]:
            print(f"[eleme] {addr}: {', '.join(safety['reasons'])}")
            continue

        send_alert(format_alert(pair, safety, theme_words))
        sent += 1

    save_seen(seen)
    print(f"Bitti. {checked} aday güvenlik taramasından geçirildi, {sent} bildirim gönderildi.")


if __name__ == "__main__":
    run()
