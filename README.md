# mb-relay — Relay unduhan MovieBox untuk Colab ZeeStream

Server mini ini menjadi **"IP suntikan"** untuk Google Colab: CDN file MovieBox
(enacdn) menolak jaringan Google Colab (HTTP 426) dan Cloudflare Worker (427),
tapi menerima server biasa. Colab mengunduh **melalui relay ini** — jadi
internet Colab yang dipakai, **bukan kuota pribadimu**.

Gratis (Codespaces: 60 jam/bulan), tanpa VPS, tanpa kartu bank.
Hanya bisa mengunduh host MovieBox (`*.hakunaymatata.com`) — bukan open proxy.

---

## CARA PAKAI (±2 menit, sekali saja)

1. **Upload repo ini ke GitHub-mu** (kalau belum):
   - GitHub → New repository → nama: `mb-relay` → Create
   - Klik **"uploading an existing file"**
   - Drag & drop **semua file & folder** repo ini (termasuk `.devcontainer`)
     → Commit changes
2. **Buka Codespace**:
   - Tombol hijau **Code** → tab **Codespaces** → **Create codespace on main**
   - Tunggu ±1 menit → relay **JALAN OTOMATIS** (lihat tulisan
     `mb-relay berjalan di port 8080` + hasil self-test `LOLOS`)
3. **Buka portnya ke publik**:
   - Tab **PORTS** (sebelah TERMINAL) → klik kanan baris **8080** →
     **Port Visibility → Public**
   - Klik kanan lagi → **Copy port URL** → bentuknya:
     `https://nama-acak-8080.app.github.dev`
4. **Tempel URL itu** di `RELAY_URL` pada **CELL 4R** notebook
   `MovieBox_Colab_Downloader.ipynb` → jalankan cell → jalankan CELL 4.
   Selesai — unduhan lewat relay, kuotamu aman.

### Kalau relay tidak jalan otomatis
Ketik di terminal Codespace:
```
python3 relay.py
```
Lalu lanjut langkah 3 (Ports → Public → copy URL).

### Kalau folder .devcontainer tidak ikut ter-upload
Buat manual: **Add file → Create new file** → nama
`.devcontainer/devcontainer.json` → tempel isi file `devcontainer.json`
dari folder ini → commit → buka ulang Codespace.

---

## KENAPA INI AMAN?

- **Bukan open proxy** — relay hanya mau mengunduh dari
  `*.hakunaymatata.com` (CDN file + subtitle MovieBox); URL lain → 403.
- **Tanpa akun/ API key/ kartu** — file relay tidak menyimpan apa pun.
- Endpoint: `/healthz` (ping), `/ip` (IP relay), `/probe?url=` (uji 1KB),
  `/dl?url=&ref=` (streaming unduhan, dukung Range/resume).

## KATATAN

- Codespace **tidur otomatis setelah 30 menit idle**. Kalau relay mati di
  tengah unduhan: buka Codespace lagi (klik ▶ / reopen), tunggu relay nyala,
  lalu jalankan ulang CELL 4 — **resume dari posisi terakhir** otomatis.
- Kalau self-test relay bilang **DITOLAK (426/427)** → jaringan Codespaces
  (Azure) sedang diblokir → pakai alternatif **Hugging Face Space**
  (`mb-relay-hf-space.zip`): buat akun gratis huggingface.co → New Space →
  SDK Docker → upload `relay.py` + `Dockerfile` + `README.md` → tunggu
  Running → URL `https://<user>-mb-relay.hf.space` dipakai sebagai RELAY_URL.
