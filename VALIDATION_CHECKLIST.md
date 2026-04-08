# Polymarket Weather Trading Bot - VALIDATION CHECKLIST & INSTRUCTIONS

**Versi:** 2.1  
**Tanggal:** 8 April 2026  
**Tujuan:** Pastikan seluruh kode dan logic sudah 100% sesuai dengan UPDATE PLAN 2.1, tidak ada bottleneck kritis, dan bot aman untuk dijalankan autonomously di VPS 24/7.

Simpan file ini di root project dan gunakan sebagai checklist sebelum menjalankan bot di production.

---

## 1. Checklist Struktur Folder (Harus Ada Semua)

- [ ] `config.py`
- [ ] `.env` (jangan di-commit)
- [ ] `main.py`
- [ ] `polymarket_client.py`
- [ ] `station_mapping.py`
- [ ] `weather_engine.py`
- [ ] `edge_calculator.py`
- [ ] `risk_manager.py`
- [ ] `llm_decision.py`
- [ ] `executor.py`
- [ ] `memory_store.py`
- [ ] `utils.py`
- [ ] `requirements.txt`
- [ ] `UPDATE_PLAN_2.1.md` (file ini)
- [ ] `VALIDATION_CHECKLIST.md` (file ini)

---

## 2. Validasi Station Mapping (Kritis)

- [ ] File `station_mapping.py` sudah dibuat dengan data akurat (lat, lon, elevation, precision).
- [ ] `weather_engine.py` menggunakan `STATIONS` bukan `TARGET_CITIES` lama.
- [ ] Setiap kota target menggunakan **airport station** yang sesuai dengan Wunderground resolution Polymarket.
- [ ] Hong Kong menggunakan Hong Kong Observatory jika ada market tersebut.

**Bottleneck yang dicek:** Model-Station Mismatch -> sudah diatasi.

---

## 3. Validasi Weather Engine Logic

- [ ] Menggunakan **hourly=temperature_2m** (bukan daily max langsung).
- [ ] Menghitung **max temperature per ensemble member** untuk hari target.
- [ ] Menggunakan multi-model: ECMWF IFS 51 members + GFS 31 members.
- [ ] Ada fallback jika ensemble error (single model ECMWF atau GFS).
- [ ] Probability dihitung dengan `round()` sesuai `precision` di station mapping (1 atau 0.1).
- [ ] Ada cache minimal 10-15 menit.

**Bottleneck yang dicek:** Probabilitas tidak akurat -> sudah diatasi.

---

## 4. Validasi Edge Calculator

- [ ] Core rule: model prob >= 70% **dan** market price <= 8c
- [ ] Ada logic **No-Spread**: buy No pada bin +2°C / +3°C jika prob < 8%
- [ ] Cheap insurance: 15-20% di outer bins (+/-1-2°C)
- [ ] Near-resolution priority: market < 12 jam ke resolve mendapat bobot lebih tinggi
- [ ] Filter ketat: edge >= 25%

**Bottleneck yang dicek:** Edge terlalu longgar -> sudah diatasi.

---

## 5. Validasi LLM Decision (Anti Lupa Konteks)

- [ ] Menggunakan **LangGraph** (StateGraph) bukan single LLM call.
- [ ] Ada minimal 4 agents: Data Agent, Analyst Agent, Risk Agent, Decision Agent.
- [ ] Memory persistent: SQLite (trade history) + Chroma Vector DB.
- [ ] Setiap decision selalu inject:
  - Current bankroll & open positions
  - Last 10-20 trade history + outcome
  - Win rate per kota/station
  - Historical bias station
- [ ] Output selalu structured JSON (pakai Pydantic) dengan field: action, bin, size_usdc, insurance_pct, confidence, reason.
- [ ] Min confidence LLM = 82%. Jika di bawah -> otomatis SKIP.
- [ ] Ada fallback ke pure rule-based jika LLM error.

**Bottleneck yang dicek:** LLM lupa konteks / hallucination -> sudah diatasi.

---

## 6. Validasi Executor & SDK

- [ ] Menggunakan `py-clob-client` resmi.
- [ ] Bisa membuat multiple orders sekaligus (Yes favorite + No-spread + insurance).
- [ ] Private key dan API creds di-derive dengan benar.
- [ ] USDC sudah di-approve ke Polymarket Exchange contract.
- [ ] Ada error handling untuk order rejection / slippage.

**Bottleneck yang dicek:** Gagal execute order -> sudah diatasi.

---

## 7. Validasi Risk Manager

- [ ] Kelly fraction maksimal 2-4% per trade
- [ ] Daily loss cap 5% -> pause otomatis
- [ ] Circuit breaker: 3 loss berturut-turut -> pause 24 jam
- [ ] Position sizing memperhitungkan liquidity dan slippage
- [ ] Paper mode toggle yang jelas

**Bottleneck yang dicek:** Over-risk / blow up account -> sudah diatasi.

---

## 8. Validasi Anti-Block & VPS 24/7

- [ ] Scan interval = 15 menit + random jitter 10-30 detik
- [ ] Menggunakan `requests.Session()` dengan exponential backoff
- [ ] Custom User-Agent yang sopan
- [ ] Cache market list & forecast
- [ ] Health check Telegram tiap 1 jam
- [ ] Bot dijalankan via **systemd service** (restart=always)
- [ ] Mulai hanya dengan 6 kota target
- [ ] Logging lengkap ke file + Telegram

**Bottleneck yang dicek:** Rate limit / IP block -> sudah diatasi.

---

## 9. Validasi Main Loop (main.py)

- [ ] Flow lengkap: Scanner -> Weather -> Edge Filter -> LLM Decision -> Risk -> Executor -> Log
- [ ] Ada try-except global dengan graceful shutdown
- [ ] Telegram notifier untuk:
  - Bot started
  - Setiap trade executed
  - Daily PnL summary
  - Error kritis / circuit breaker

---

## 10. Final Safety & Testing Instructions

Sebelum menjalankan autonomous mode:

1. Jalankan bot di **paper mode** minimal **7 hari penuh**.
2. Catat:
   - Win rate harian
   - Average edge
   - LLM confidence rata-rata
   - Jumlah SKIP vs TRADE
   - Frekuensi error
3. Periksa log apakah ada bottleneck latency (>15 detik per cycle).
4. Pastikan tidak ada hard-coded sensitive data (private key hanya di .env).
5. Backup folder project dan wallet seed sebelum live.

**Jika semua checklist di atas centang -> bot sudah sesuai rencana dan siap production.**

---

## Emergency Commands (via Telegram)

- `/status` -> tampilkan bankroll, open positions, daily PnL
- `/pause` -> pause autonomous trading
- `/resume` -> resume trading
- `/paper` -> toggle paper mode

---

**Catatan Akhir:**

File ini adalah **gatekeeper** sebelum bot kamu live.  
Jalankan checklist ini setiap kali ada perubahan besar di kode.

Jika ada item yang tidak bisa dicentang -> perbaiki dulu sebelum melanjutkan.
