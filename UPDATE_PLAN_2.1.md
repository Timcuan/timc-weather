# Polymarket Weather Trading Bot - UPDATE PLAN LENGKAP (Versi 2.1)

**Tanggal Update:** 8 April 2026  
Menggabungkan: strategi asli thread, temuan terbaru dari X (dicek real-time), station correction, autonomous trading via SDK, LLM multi-agent dengan memory, anti-block VPS 24/7, dan ramuan strategi yang lebih powerful.

---

## 1. Ringkasan Strategi Asli dari Thread @s4yonnara (ID 2041515358389948453)

Strategi "embarrassingly simple" dari @208208 (358 win streak):

- Buka weather model (ECMWF / GFS)
- Cari bin dengan **probabilitas >=70%**
- Cek Polymarket -> bin yang sama masih **murah** (contoh 3c)
- **Buy the gap** + tambah **cheap insurance** di outer ranges (+/-1-2C)
- "Weight heavy toward the model. Outer ranges = cheap insurance"
- Formula tidak berubah: Models accurate. Polymarket slow to update.

Contoh Shenzhen: 24C $0.01 | 25C $0.60 (model favor) | 26C $0.02 -> risk $0.63 -> payout $1.00

**Insight Penting dari Reply Thread:**
- 358 win bukan luck -> trader skip semua bet di mana edge tidak jelas.
- Weather markets jauh lebih tidak efisien dibanding crypto/politik.
- Banyak orang belum tahu pasar ini ada.

---

## 2. Ramuan Strategi Powerful (Upgrade dari Related Threads X - 8 April 2026)

Dari scan thread terkait (@Vvtentt101, @WeatherEdgeFind, dll.) kita dapatkan tambahan yang sangat berguna:

1. **Core Rule** (dari @208208)  
   Model prob >=70% + market price <=8c -> Buy Yes gap.

2. **Multi-Model Consensus**  
   Gunakan Open-Meteo Ensemble (ECMWF 51 + GFS 31) + setidaknya 1 sumber tambahan (Tomorrow.io jika memungkinkan).

3. **No-Spread Edge**  
   Auto buy **No** pada bin +2C / +3C kalau model bilang prob <8% (cuaca jarang over-shoot max temp).

4. **Cheap Insurance**  
   Alokasi 15-20% di outer bins (+/-1-2C) + No-spread.

5. **Strict Discipline**  
   Hanya trade kalau LLM confidence >=82% dan edge >=25%. Kalau tidak clear -> **SKIP** (ini kunci 358 win streak).

6. **Near-Resolution Boost**  
   Prioritaskan market yang resolve dalam <12 jam (gap sering lebih besar).

7. **Risk Rule**  
   Max 2-4% Kelly per trade, daily loss cap 5%, circuit breaker jika 3 loss berturut-turut.

**Ramuan ini membuat strategi jauh lebih tajam dan sustainable.**

---

## 3. Arsitektur Bot Lengkap (Autonomous + LLM)

```text
polymarket-weather-bot/
├── config.py                  # Semua threshold, API keys, LLM provider
├── .env                       # PRIVATE_KEY, TELEGRAM_*, LLM_API_KEY, dll.
├── main.py                    # Scheduler 15 menit + main autonomous loop
├── polymarket_client.py       # Gamma API + CLOB prices
├── station_mapping.py         # Accurate airport station (wajib!)
├── weather_engine.py          # Hourly ensemble + multi-source consensus
├── edge_calculator.py         # Rule-based filter cepat + No-spread
├── risk_manager.py            # Kelly sizing, daily limit, circuit breaker
├── llm_decision.py            # LangGraph multi-agent (Data + Analyst + Risk + Decision)
├── executor.py                # py-clob-client (autonomous buy Yes + No + insurance)
├── memory_store.py            # SQLite + Chroma Vector DB (persistent context)
├── utils.py                   # Session, retry, jitter, anti-block
├── data/
│   ├── trades.sqlite
│   └── vector_db/
└── requirements.txt
```

**Flow Autonomous Tiap 15 Menit:**
1. Scanner + weather_engine (station-specific + multi-model)
2. Edge calculator (rule-based quick filter)
3. LLM multi-agent LangGraph (dengan full context + ramuan powerful di prompt)
4. Risk manager approve/reject
5. Executor jalankan order via py-clob-client (multiple orders sekaligus)
6. Simpan semua ke memory + kirim summary ke Telegram

---

## 4. Station Mapping (Koreksi Kritis)

Buat file `station_mapping.py`:

```python
STATIONS = {
    "shenzhen": {"name": "Bao'an Intl (ZGSZ)", "lat": 22.639, "lon": 113.811, "elevation": 4, "unit": "celsius", "precision": 1},
    "taipei":   {"name": "Songshan Airport", "lat": 25.069, "lon": 121.552, "elevation": 6, "unit": "celsius", "precision": 1},
    "seoul":    {"name": "Incheon Intl (RKSI)", "lat": 37.469, "lon": 126.451, "elevation": 7, "unit": "celsius", "precision": 1},
    "tokyo":    {"name": "Haneda (RJTT)", "lat": 35.549, "lon": 139.779, "elevation": 6, "unit": "celsius", "precision": 1},
    "hongkong": {"name": "Hong Kong Observatory", "lat": 22.302, "lon": 114.174, "elevation": 32, "unit": "celsius", "precision": 1},
    # Tambah kota lain sesuai market aktif
}
```

Gunakan ini di weather_engine.py (bukan lat/lon kota center).

## 5. Anti-Block & VPS 24/7

- Scan setiap 15 menit + random jitter 10-30 detik.
- Gunakan requests.Session() dengan exponential backoff + custom User-Agent.
- Cache agresif (markets 5-10 menit, forecast 15 menit).
- Health check Telegram tiap jam.
- Jalankan via systemd service (restart=always).
- Mulai dengan 6 kota saja.
- Lokasi VPS direkomendasikan: Singapore, Hong Kong, atau Japan.

## 6. Safety Layers (Wajib)

- Paper trading mode (toggle di config)
- Max daily loss 5% -> pause otomatis
- Max position per market 2-4% Kelly
- Circuit breaker (3 loss berturut-turut -> pause 24 jam)
- Min LLM confidence 82%
- Human override via Telegram commands
- Logging lengkap setiap decision & order

## 7. requirements.txt

```txt
py-clob-client
langgraph
langchain
langchain-anthropic  # atau groq / openai
pydantic
chromadb
python-dotenv
schedule
python-telegram-bot
pandas
numpy
requests
```

## 8. Action Plan Implementasi (Langsung Ikuti Urutan Ini)

1. Buat folder project dan copy semua file sesuai arsitektur.
2. Isi .env (PRIVATE_KEY wallet Polygon USDC + Telegram + LLM API key).
3. Approve USDC ke Polymarket Exchange contract (sekali saja).
4. Update station_mapping.py dan weather_engine.py dengan hourly ensemble + station data.
5. Implement multi-agent di llm_decision.py dengan ramuan powerful di prompt.
6. Test dulu di paper mode minimal 5-7 hari.
7. Monitor PnL, win rate, dan LLM confidence.
8. Kalau stabil -> aktifkan autonomous dengan position kecil.

Catatan Penting:  
Thread asli tidak bocor detail teknis baru, tapi insight dari related threads sangat membantu untuk meningkatkan edge.  
Bot ini menggabungkan kesederhanaan strategi @208208 dengan disiplin tinggi + LLM yang punya memory + autonomous execution.
