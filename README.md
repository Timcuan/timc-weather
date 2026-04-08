# Polymarket Weather Trading Bot

Automation scanner untuk market **Highest Temperature** di Polymarket, memakai
forecast probabilistik dari **Open-Meteo Ensemble** untuk mendeteksi edge sesuai strategi thread.

Repository: [https://github.com/Timcuan/timc-weather](https://github.com/Timcuan/timc-weather)

## Fitur

- Scan market cuaca aktif per kota target
- Hitung probabilitas per bin suhu integer dari ensemble forecast
- Deteksi sinyal berdasarkan threshold:
  - `model_prob >= 0.70`
  - `market_price <= 0.08`
  - `edge >= 0.25`
- Rekomendasi **cheap insurance** outer bins (±1°C, ±2°C)
- Telegram alert dengan market link
- SQLite caching + logging untuk reliability dan latency

## Struktur

```text
polymarket-weather-bot/
├── config.py
├── .env
├── .env.example
├── main.py
├── polymarket_client.py
├── weather_engine.py
├── edge_calculator.py
├── utils.py
├── station_bias.py
├── data/
│   ├── cache.sqlite
│   └── logs/
├── requirements.txt
└── README.md
```

## Setup

1. Gunakan Python 3.11+
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Isi `.env`:

```env
TELEGRAM_TOKEN=...
TELEGRAM_CHAT_ID=...
BOT_TIMEZONE=Asia/Jakarta
LLM_PROVIDER=gemini
LLM_MODEL=gemini-2.5-pro
LLM_API_KEY=...
LLM_REQUEST_TIMEOUT_SECONDS=12
LLM_HTTP_RETRY_ATTEMPTS=1
```

4. Jalankan bot:

```bash
python main.py
```

## Telegram Commands

- `/help` daftar command
- `/status` status ringkas bot
- `/equity` equity dan pnl harian
- `/risk` status risk manager
- `/pause` pause scanner
- `/resume` resume scanner
- `/paper` paksa paper mode
- `/live` aktifkan live mode (jika client live ready)
- `/sync` paksa sinkronisasi resolved outcome

Catatan: command hanya diproses dari `TELEGRAM_ADMIN_IDS`.

## Cara Kerja

- Startup: kirim notifikasi "Bot started"
- Scheduler jalan setiap 15 menit
- Tiap cycle:
  - Ambil active weather markets dari Gamma API
  - Filter market "highest temperature" untuk kota target
  - Hitung probabilitas bin suhu dari Open-Meteo ensemble
  - Ambil harga BUY per outcome dari CLOB
  - Hitung edge + insurance
  - Jika lolos threshold, kirim alert Telegram
  - Simpan log scan/signal ke SQLite

## Catatan Implementasi

- Resolver station airport masih **fase 1** (pakai lat/lon kota)
- `station_bias.py` disiapkan untuk phase 2 bias correction
- Parser response Open-Meteo dibuat defensif karena variasi format antar model/member

## Tuning Threshold

Edit di `config.py`:

- `MIN_MODEL_PROB`
- `MAX_MARKET_PRICE`
- `EDGE_MIN`
- `INSURANCE_PCT`

## Gemini Notes

- Engine decision sudah mendukung `LLM_PROVIDER=gemini` dengan structured JSON schema.
- Jika Gemini gagal (API timeout, invalid key, response invalid), bot otomatis fallback ke deterministic rules agar loop 24/7 tetap jalan.

## Deploy systemd (VPS)

Template service ada di `ops/systemd/weatherbot.service`.

Install di server:

```bash
sudo bash scripts/install_systemd.sh <linux-user>
```

## Disclaimer

Bot ini untuk riset otomatisasi dan notifikasi. Selalu verifikasi market resolution rules sebelum eksekusi modal nyata.

## Changelog

Lihat detail perubahan di [CHANGELOG.md](/Users/aaa/Projects/Timc-weather/CHANGELOG.md).
