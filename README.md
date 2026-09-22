# mb-relay — Relay unduhan MovieBox untuk Colab ZeeStream

Server mini ini menjadi **"IP suntikan"** untuk Google Colab: CDN file MovieBox
(enacdn) menolak jaringan Google Colab (HTTP 426) dan Cloudflare Worker (427),
tapi **menerima jaringan Azure Codespaces (teruji: 206 LOLOS)**.
Colab mengunduh **melalui relay ini** — jadi internet Colab yang dipakai,
**bukan kuota pribadimu**.

Relay ini **SUDAH DIBUATKAN & AKTIF** untuk akun ini:
- Codespace: **organic-fiesta-5pjgqgjvx5wcw6q** (repo ini)
- URL relay: `https://organic-fiesta-5pjgqgjvx5wcw6q-8080.app.github.dev`
  (URL ini sudah terisi otomatis di CELL 4R notebook `MovieBox_Colab_Downloader.ipynb`)

## ✅ SELF-HEALING (fitur penting repo ini)

Visibilitas port Codespaces **reset ke "private" setiap kali Codespace
tidur lalu bangun** (idle 30 menit → mati otomatis). Repo ini menyembuhkannya
otomatis: **`public_port.sh`** dijalankan `postStartCommand` setiap kali
Codespace start → menjalankan `gh codespace ports visibility 8080:public`
(di dalam Codespaces, `gh` sudah ter-autentikasi via `GITHUB_TOKEN` bawaan)
→ port 8080 kembali **Public** tanpa klik manual apa pun.

Teruji penuh: buat baru → port langsung public; **STOP → START → tetap public**.

---

## CARA PAKAI
1. Di Colab: jalankan **CELL 4R** notebook → muncul `MODE RELAY SIAP ✔`
2. Jalankan **CELL 4** (unduh) → **CELL 5** (upload Abyss) — selesai.

## Kalau relay TIDUR dan CELL 4R masih gagal setelah menunggu ±2,5 menit
1. Buka https://github.com/codespaces (login akun ini)
2. Baris **mb-relay (organic-fiesta…)** → klik **⋮** → **Restart** → tunggu ±1 menit
3. Jalankan ulang CELL 4R → CELL 4 (unduhan **resume otomatis** dari posisi terakhir)

> Port otomatis dibuat public lagi oleh `public_port.sh` — tidak perlu klik Ports/Public manual.

## Kalau Codespace ini terhapus
Buka repo ini → tombol hijau **Code** → tab **Codespaces** →
**Create codespace on main** → relay jalan otomatis + port otomatis Public
(self-healing) → salin URL `https://<nama-baru>-8080.app.github.dev` →
ganti `RELAY_URL` di notebook.

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
- Kecepatan terukur: **5,4 MB/s** streaming via relay → film 2,4 GB ±7,5 menit.
- File repo: `relay.py` (relay), `public_port.sh` (self-healing port),
  `.devcontainer/devcontainer.json` (auto-start + gh-cli feature).
