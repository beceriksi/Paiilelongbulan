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
                    print(f"🔥 {symbol} hacim katlama barajını geçti ({vol_ratio_clean:.1f}x). Alım gücü kontrol ediliyor...")
                    
                    # 2. BÜYÜK FİLTRE: Bu hacim alıcı odaklı mı?
                    _, _, buy_ratio = get_smart_buy_volume(symbol)
                    
                    if buy_ratio >= BUY_SELL_RATIO_LIMIT:
                        print(f"📈 {symbol} kurumsal alıcı filtresini geçti! Alım Oranı: {buy_ratio}")
                        
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
                            
                            # EKRAN GÖRÜNTÜSÜ ALINAMASA BİLE SİNYAL İPTAL OLMASIN (Dışarı çıkarıldı)
                            has_photo = take_tradingview_screenshot(tv_clean, img_name)
                            if not has_photo:
                                img_name = "NO_IMAGE"
                                
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
                            print(f"✅ [LONG] {symbol} başarıyla listeye eklendi.")
                            time.sleep(0.3)
                            
        except Exception as e:
            print(f"{symbol} taranırken döngü içi hata: {e}")
            continue

    # Raporlama Aşaması
    if detected_longs:
        print(f"📢 Tarama bitti. Long Telegram'ına gönderilecek toplam coin sayısı: {len(detected_longs)}")
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
                if l['img_path'] != "NO_IMAGE" and os.path.exists(l['img_path']):
                    if l['symbol'].split('-')[0].upper() in ai_report.upper():
                        selected_photo = l['img_path']
                        break
            
            if selected_photo:
                send_telegram_photo(selected_photo, ai_report)
            else:
                send_telegram_text(ai_report)
                
        for l in detected_longs:
            if l['img_path'] != "NO_IMAGE" and os.path.exists(l['img_path']):
                try:
                    os.remove(l['img_path'])
                except:
                    pass
    else:
        print("⏳ [LONG] Muhafazakar kriterlere uyan hiçbir coin bulunamadı.")
