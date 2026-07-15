# 💎 GemBot Pro: Professional-Grade On-Chain Cryptocurrecy Scanner

GemBot Pro, Solana ve popüler EVM zincirlerinde (Ethereum, Base, BSC, Arbitrum, Optimism, Polygon, Avalanche) yeni pariteleri ve likidite hareketlerini gerçek zamanlı izleyen, yapay zeka destekli, asenkron ve modüler bir "Gem Hunter" botudur.

## 🚀 Öne Çıkan Özellikler

- **Multi-Chain Desteği**: Helius, DexScreener, GoPlus ve Birdeye API'leri ile 8 farklı ağda çalışır.
- **Yüksek Hızlı Asenkron Mimari**: `asyncio` ve `aiohttp` ile bloklanmayan ağ istekleri ve saniyede yüzlerce çifti süzebilen veri hattı.
- **Derin On-Chain Analizi**: 
  - Kontrat Riskleri: Honeypot, Mint, Freeze, LP Lock, LP Burn.
  - Cüzdan İstihbaratı: Smart Wallet, Insider, Sniper, Fresh Wallet, Wallet Clustering, Bundle Detection.
  - Hacim ve Likidite Analizi: Wash Trading tespiti, Fake Volume tespiti, Hacim ve Likidite ivmesi.
- **Hibrit Skorlama Motoru**: 8 farklı ağırlıklı kritere dayalı statik puanlamayı, Numpy tabanlı lineer yapay zeka karar matrisiyle (`AIScorer`) harmanlayan sistem.
- **Dayanıklı İletişim**: Hata durumlarında otomatik yeniden deneme (retry) ve Telegram API limitlerine takılmayan asenkron mesaj kuyruğu (`Queue`).

---

## 📂 Klasör Yapısı

```text
GemBot-Pro/
│
├── main.py              # Uygulamanın giriş noktası ve asenkron döngü yöneticisi
├── config.py            # Çevre değişkenleri ve puan ağırlıkları
├── database.py          # Kalıcı SQLite depolama katmanı (aiosqlite)
├── telegram.py          # Asenkron Telegram bildirim motoru (rate-limit korumalı)
├── requirements.txt     # Bağımlılık paketleri listesi
├── .env.example         # Çevre değişkenleri şablonu
├── README.md            # Kurulum ve kullanım rehberi
│
├── scanners/            # Ağ ve API tarayıcı modülleri
│   ├── dexscreener.py
│   ├── goplus.py
│   ├── gmgn.py
│   ├── birdeye.py
│   ├── helius.py
│   ├── websocket.py
│   └── twitter.py
│
├── analyzers/           # Modüler veri işleme ve analizör sınıfları
│   ├── holders.py
│   ├── liquidity.py
│   ├── volume.py
│   ├── wallets.py
│   ├── deployer.py
│   ├── social.py
│   ├── washtrade.py
│   ├── rugcheck.py
│   └── gemscore.py
│
├── ai/                  # Makine öğrenmesi skor tahminleme ve eğitim araçları
│   ├── scorer.py
│   └── trainer.py
│
├── storage/             # Veri saklama ve önbellek
│   ├── sqlite.py
│   └── cache.py
│
├── logs/                # Çalışma günlükleri (gembot_pro.log)
└── tests/               # Birim testler
