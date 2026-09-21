# mb-relay — Relay unduhan MovieBox untuk Colab ZeeStream

Server mini ini menjadi **"IP suntikan"** untuk Google Colab: CDN file MovieBox
(enacdn) menolak jaringan Google Colab (HTTP 426) dan Cloudflare Worker (427),
tapi **menerima jaringan Azure Codespaces (teruji 22 Sep: 206 LOLOS)**.
Colab mengunduh **melalui relay ini** — jadi internet Colab yang dipakai,
**bukan kuota pribadimu**.

Relay ini **SUDAH DIBUATKAN & SUDAH AKTIF** untuk akun ini:
- Codespace: **bug-free-garbanzo** (repo ini, private)
- Port 8080: **sudah Public**
- URL relay: `https://bug-free-garbanzo-qgj7q7jv7w6h9749-8080.app.github.dev`
  (URL ini sudah terisi otomatis di CELL 4R notebook `MovieBox_Colab_Downloader.ipynb`)

---

## CARA PAKAI
1. Di Colab: jalankan **CELL 4R** notebook → muncul `MODE RELAY SIAP ✔`
2. Jalankan **CELL 4** (unduh) → **CELL 5** (upload Abyss) — selesai.

## Kalau relay TIDUR (idle 30 menit → Codespace mati otomatis)
1. Buka https://github.com/codespaces (login akun ini)
2. Baris **bug-free garbanzo** → klik **⋮** → **Restart** → tunggu ±1 menit
3. Jalankan ulang CELL 4R → CELL 4 (unduhan **resume otomatis** dari posisi terakhir)

> Port 8080 tetap Public setelah restart — tidak perlu diatur ulang.

## Kalau Codespace ini terhapus
Buka repo ini → tombol hijau **Code** → tab **Codespaces** →
**Create codespace on main** → relay jalan otomatis (devcontainer
`postStartCommand`) → tab **PORTS** → klik kanan **8080** →
**Port Visibility → Public** → salin URL barunya → ganti `RELAY_URL` di notebook.

Alternatif platform: **Hugging Face Space** (paket `mb-relay-hf-space.zip`,
SDK Docker, port 7860) → URL `https://<user>-mb-relay.hf.space`.

---

## KENAPA INI AMAN?
- **Bukan open proxy** — relay hanya mau mengunduh dari
  `*.hakunaymatata.com` (CDN file + subtitle MovieBox); URL lain → 403.
- **Tanpa akun/API key/kartu** — file relay tidak menyimpan apa pun.
- Endpoint: `/healthz` (ping), `/ip` (IP relay), `/probe?url=` (uji 1KB),
  `/dl?url=&ref=` (streaming unduhan, dukung Range/resume).

## CATATAN
- Codespaces gratis untuk akun pribadi: 60 jam/bulan (mesin 2 core) —
  cukup untuk puluhan film per bulan. Tidur otomatis 30 menit idle
  menghemat kuota.
- Kecepatan terukur (22 Sep): **5,4 MB/s** streaming via relay →
  film 2,4 GB selesai ±7,5 menit.
