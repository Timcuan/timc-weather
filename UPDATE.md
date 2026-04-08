# Polymarket Weather Trading Bot - Update & Final Hardening (Versi 1.1)

**Tanggal Update:** 8 April 2026  
**Strategi Dasar:** Thread @s4yonnara (@208208) - Model prob >=70% + buy gap + cheap insurance  
**Tujuan Update:** Membuat bot valid, akurat, dan aman berjalan **24/7 di VPS** tanpa kena block IP, Cloudflare throttling, atau rate limit.

---

## 1. Validasi Terbaru (Berdasarkan Data Real April 2026)

- **Resolution Source Polymarket**: Valid. Hampir semua market "Highest Temperature" resolve menggunakan **Wunderground History** dari **specific airport station** (contoh: LaGuardia Airport KLGA untuk NYC, Shenzhen Bao'an ZGSZ, Incheon RKSI untuk Seoul, Haneda RJTT untuk Tokyo).  
  -> **Kritikal**: Jangan gunakan lat/lon kota center. Harus pakai station-specific coordinates.
- **Open-Meteo Ensemble API**: Free tier non-commercial:
  - 600 calls/min, 5.000 calls/hour, 10.000 calls/day per IP.
  - Dengan 6-10 kota dan scan tiap 15 menit -> sangat aman (<< 100 calls/hari).
- **Polymarket Rate Limits (Gamma + CLOB)**:
  - Gamma `/markets`: 300 req / 10 detik
  - Gamma general: 4.000 req / 10 detik
  - CLOB general: 9.000 req / 10 detik
  - Enforcement via Cloudflare throttling (delay, bukan hard block). Risiko block rendah jika tidak spam.
- **Kesimpulan Validitas**: Strategi masih workable. Edge dari ensemble vs harga murah masih ada, terutama 30-120 menit setelah model run baru (00/06/12/18 UTC). Namun akurasi prob sangat bergantung pada station mapping yang tepat.

---

## 2. Koreksi Wajib (Harus Diimplementasikan)

### Koreksi #1 - Station Mapping (Paling Kritis)

Buat file baru `station_mapping.py`:

```python
STATIONS = {
    "shenzhen": {
        "name": "Shenzhen Bao'an Intl (ZGSZ)",
        "lat": 22.639,
        "lon": 113.811,
        "elevation": 4,
        "unit": "celsius",
        "precision": 1,
    },
    "taipei": {
        "name": "Taipei Songshan Airport",
        "lat": 25.069,
        "lon": 121.552,
        "elevation": 6,
        "unit": "celsius",
        "precision": 1,
    },
    "seoul": {
        "name": "Incheon Intl (RKSI)",
        "lat": 37.469,
        "lon": 126.451,
        "elevation": 7,
        "unit": "celsius",
        "precision": 1,
    },
    "tokyo": {
        "name": "Tokyo Haneda (RJTT)",
        "lat": 35.549,
        "lon": 139.779,
        "elevation": 6,
        "unit": "celsius",
        "precision": 1,
    },
    "hongkong": {
        "name": "Hong Kong Observatory (HKO)",
        "lat": 22.302,
        "lon": 114.174,
        "elevation": 32,
        "unit": "celsius",
        "precision": 1,  # kadang 0.1C, cek rules per market
        "source": "hko",
    },
    # Tambah kota lain sesuai market aktif
}
```

Gunakan data ini di `weather_engine.py` (bukan `TARGET_CITIES` lama).

### Koreksi #2 - Hitung Probability dari Hourly Data

Jangan pakai `daily=temperature_2m_max`.  
Gunakan `hourly=temperature_2m`, lalu hitung max per ensemble member untuk hari target.  
Ini meningkatkan akurasi distribusi bin secara signifikan.

### Koreksi #3 - Dynamic Rules & Logging

Tambah fungsi untuk cek precision (integer vs 0.1C) per market.  
Wajib SQLite logging: setiap edge + eventual resolved outcome untuk backtest real win rate.

---

## 3. Anti-Block & VPS 24/7 Hardening

### Scheduler & Frequency

- Scan setiap 15 menit dengan random jitter 10-30 detik.
- Align scan dengan model run baru (lebih agresif 5-10 menit setelah 00/06/12/18 UTC).

### Rate Limiting & Retry

- Gunakan `requests.Session()` dengan `Retry` (exponential backoff).
- Tambah `safe_sleep()` dengan jitter antar kota.
- Maksimal 1 request Gamma + 1 CLOB + 1 Open-Meteo per kota per cycle.

### Headers & User-Agent

```python
headers = {
    "User-Agent": "Mozilla/5.0 (compatible; PolymarketWeatherBot/1.1; +https://github.com/Timcuan/timc-weather)",
    "Accept": "application/json",
}
```

### Caching

- Cache market list: 5-10 menit
- Cache weather forecast: 10-15 menit

### Monitoring

- Kirim health check ke Telegram tiap 1 jam ("Bot still alive - X markets scanned today").
- Alert khusus jika dapat 429 atau error berulang.

### VPS Best Practices

- Lokasi VPS direkomendasikan: Singapore, Hong Kong, Japan, atau Germany (low latency ke Asia markets).
- Jalankan dengan `systemd` service (lebih stabil daripada `tmux`/`screen`).
- Firewall: allow hanya SSH dari IP kamu.
- Update sistem rutin via cron.
- Mulai dengan 6 kota saja untuk mengurangi traffic.

Contoh `systemd` service (`/etc/systemd/system/weatherbot.service`):

```ini
[Unit]
Description=Polymarket Weather Bot
After=network.target

[Service]
User=yourusername
WorkingDirectory=/home/yourusername/polymarket-weather-bot
ExecStart=/usr/bin/python3 /home/yourusername/polymarket-weather-bot/main.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

---

## 4. Action Plan Setelah Update

1. Update blueprint ke versi 1.1 (masukkan semua koreksi & anti-block di atas).
2. Generate ulang code di Claude dengan blueprint terbaru.
3. Tambahkan file `station_mapping.py`, `utils.py` (dengan session & jitter), dan SQLite logger.
4. Deploy ke VPS -> test manual 24 jam dulu.
5. Monitor log & Telegram alert selama 3-5 hari sebelum naikkan ke lebih banyak kota.

---

## 5. Risiko yang Tersisa & Mitigasi

- **Edge Decay**: Kompetisi bot meningkat -> naikkan threshold (`MIN_MODEL_PROB = 0.73`, `EDGE_MIN = 0.28`).
- **Slippage**: Hanya ambil market dengan liquidity > $10K.
- **Model Error**: Cuaca transisi/musim hujan lebih volatile -> tambah filter confidence.
