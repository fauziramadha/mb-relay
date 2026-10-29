# mb-relay — Pipeline unduhan film ZeeStream (MovieBox → Abyss → situs)

Repo ini menjalankan **pipeline otomatis ZeeStream**: cari film/serial di
MovieBox → unduh → upload ke Abyss → pasang subtitle → register ke katalog
**Server 3** situs [www.zeestream.biz.id](https://www.zeestream.biz.id).

**100% gratis, TANPA VPS, TANPA kartu, TANPA kuota internet pribadi.**

> ## ⚠️ BACA DULU — ini BUKAN lagi "relay untuk Colab"
> Versi lama repo ini adalah relay untuk notebook Colab (akun Colab yang
> mengunduh *lewat* relay). **Itu sudah bukan cara pakai utamanya.**
> Sejak **Relay v1.1**, repo ini adalah **pipeline GitHub Actions**:
> kamu cukup klik *Run workflow*, sisanya otomatis — Colab tidak dipakai
> lagi. Notebook Colab lama masih bisa memakai endpoint `/dl` relay ini
> sebagai jalur cadangan, tapi itu opsional (lihat bagian paling bawah).

---

## Arsitektur (Relay v1.1) — siapa mengerjakan apa

```
┌────────────────────────┐        ┌──────────────────────────────┐
│  GitHub Actions runner │        │        Codespace mb-relay     │
│      (ORKESTRATOR)     │  HTTPS │        (PEKERJA NYATA)        │
│                        │───────▶│  jaringan Azure → CDN LOLOS   │
│  • cari Codespace      │ X-ZS-  │                              │
│  • bangunkan           │  Key   │  di DALAM Codespace:         │
│  • kirim parameter     │        │  • cari judul di MovieBox    │
│  • pantau log          │        │  • UNDUH video dari CDN      │
│  • matikan lagi        │        │  • upload ke Abyss           │
│                        │        │  • pasang subtitle           │
│  0 byte film lewat     │        │  • register ke situs         │
│  runner (jaringannya   │        │  relay.py = pintu /exec      │
│  DIBLOKIR CDN → 426)   │        │  zs_dl.py  = pipeline        │
└────────────────────────┘        └──────────────────────────────┘
```

Fakta teruji (workflow `zs-probe` + run nyata, Sep 2026):

| Jaringan | Menuju CDN MovieBox (`*.hakunaymatata.com`) |
|---|---|
| Runner GitHub Actions | **DITOLAK 426** → karena itu runner hanya orkestrator |
| Cloudflare Worker | **DITOLAK 427** |
| Azure Codespaces | **LOLOS 206** → karena itu Codespace yang mengunduh |

Miskonsepsi yang pernah terjadi: header lama `zs_dl.py` menulis "berjalan di
runner, jaringan diterima CDN" — **itu keliru** dan sudah diperbaiki.
Unduhan NYATA selalu terjadi **di dalam Codespace**; runner tidak pernah
menyentuh data film.

---

## Isi repo

| File | Peran |
|---|---|
| `.github/workflows/zs-dl.yml` | **Workflow utama "ZS Download (MovieBox ke Abyss)"** — orkestrator |
| `.github/workflows/zs-probe.yml` | Uji egress runner/Codespace ke CDN (diagnostik, jarang dipakai) |
| `zs_dl.py` | Pipeline yang **dijalankan di dalam Codespace**: cari → unduh → upload → subtitle → register |
| `relay.py` | Server mini di Codespace (pintu `/exec`, `/jobstart`, `/joblog`, plus `/healthz /ip /probe /dl`) |
| `public_port.sh` | Self-healing: jadikan port 8080 Public tiap kali Codespace start |
| `.devcontainer/devcontainer.json` | Auto-start relay + `git pull` terbaru + self-heal port |

**Self-update**: setiap Codespace start, devcontainer menjalankan
`git pull` dulu — kode di Codespace selalu = kode di repo ini. Push perbaikan
ke repo = Codespace otomatis memakainya saat dibangunkan berikutnya.

---

## Secrets yang wajib terpasang (sekali saja)

**Repo → Settings → Secrets and variables → Actions:**

| Secret | Untuk apa |
|---|---|
| `CODES_PAT` | PAT GitHub (scope `repo` + `codespace`) — workflow mencari/membangunkan/mematikan Codespace via API |
| `ZS_EXEC_KEY` | Kunci rahasia relay — cocokkan dengan secret Codespaces di bawah |
| `ABYSS_KEY` | API key akun Abyss (upload) |
| `ABYSS_EMAIL` | Email akun Abyss |
| `ABYSS_PASSWORD` | Password akun Abyss (untuk pasang subtitle native player) |

**GitHub → Settings → Codespaces → Codespaces secrets:**

| Secret | Catatan |
|---|---|
| `ZS_EXEC_KEY` | **Nilai HARUS sama** dengan versi Actions-nya — `/exec` menolak tanpa cocok |

> Tidak ada kredensial yang ditulis di file repo — semuanya lewat secrets.

---

## CARA PAKAI (rutin — cuma 1 klik)

1. Buka tab **Actions** repo ini → workflow **ZS Download (MovieBox ke Abyss)**
   → **Run workflow**, isi:
   - **judul** *(wajib)* — kata kunci pencarian; bahasa Inggris lebih akurat
   - **pilihan** — kalau hasil pencarian lebih dari satu, indeks mulai 0
   - **musim** / **episode** — khusus SERIAL (film biarkan 1)
   - **kualitas** — `auto` (tertinggi gratis) / `1080` / `720` / `360`
   - **subtitle** — `in_id` (Indonesia) / `ms` (Melayu) / `en` / `none`
   - **token** — token register dari **Admin → Server 3** situs
     (isi agar film otomatis masuk katalog; kosong = upload Abyss saja)
   - **slug_lama** — slug Abyss dari run sebelumnya (mis.
     `SGZKkyR9C`) — isi **hanya** bila film sudah pernah terupload
     tapi register waktu itu belum jalan (lupa token / error).
     Mode ini **TANPA unduh & upload ulang** — register pakai slug
     lama, kualitas/size terisi otomatis dari Abyss, subtitle tetap
     diambil. Cuma ± 3-5 menit.
2. Tonton log berjalan (log pipeline Codespace di-stream ke log Actions).
3. Selesai (film 2 GB ± 25-40 menit; film kecil ± 15-20 menit) → **Codespace
   dimatikan otomatis** — kuota Codespaces tidak terbakar idle.

Kronologi di balik layar: bangunkan Codespace (± 1-3 menit, termasuk
self-heal port) → **bersihkan sisa film lama** (🧹 otomatis) → cari
judul → unduh dari CDN (terukur ± 5,4 MB/s di dalam Codespace) →
upload ke Abyss → **file lokal dihapus otomatis segera setelah upload
sukses** (slug keluar = film aman di Abyss) → tunggu transcode (maks 15
menit, tidak menghalangi register) → register ke situs (bila token
diisi) → pasang subtitle → matikan Codespace.

**Disk Codespace selalu ramping (auto 🧹):** disk Codespace itu
persisten (di-*stop*, bukan dihapus) — tanpa pembersihan otomatis,
film lama menumpuk sampai run baru ditolak "Disk Codespace tidak
cukup". Sekarang ada 2 lapis: (1) awal run — semua sisa video run
sebelumnya dihapus **kecuali** film yang jadi target run ini (file
setengah-jadi bisa dilanjutkan/resume, film lengkap dipakai ulang
tanpa unduh ulang); (2) setelah upload sukses — file lokal dihapus
**segera**. Satu-satunya saat file bertahan: upload Abyss ditolak
(3x) supaya run ulang judul yang sama tinggal upload ulang tanpa
mengunduh lagi.

---

## BULK UNDUH SERIES (v1.3 — pilih banyak episode sekaligus)

Serial ratusan episode **tidak perlu dijalankan satu-satu lagi**. Isi input
baru **`episodes`** dengan daftar/rentang episode, contoh:

- `1-100` → episode 1 sampai 100
- `1,3,5-8` → episode 1, 3, 5, 6, 7, 8
- kosongkan → perilaku lama (mode tunggal pakai input `episode`)

Cara kerja di balik layar:

1. Pipeline mengunduh → upload → register **satu per satu otomatis**.
   Satu episode gagal **tidak** menghentikan sisanya (dicatat di ringkasan;
   jalankan ulang hanya spec episode yang gagal).
2. **Chunk otomatis ±2 jam**: run Actions hanya memantau maks ±2 jam, jadi
   `budget_min` (default 95 menit) — episode yang belum kebagian ditandai
   `ZS_BULK_REMAIN`, lalu runner **men-dispatch ulang workflow ini sendiri**
   dengan sisa spec (CODES_PAT → fallback GITHUB_TOKEN). Codespace sengaja
   **tidak dimatikan** antar-chunk supaya lanjut tanpa boot ulang.
3. Untuk register otomatis ke katalog, kirim **token scope-series**: di
   Admin → Server 3 → *Bulk Unduh Series*, token dibuat **tanpa episode**
   (berlaku seluruh season, TTL 72 jam) dan pipeline mengirim nomor episode
   di tiap register. (Token per-episode lama tetap didukung.)

Cara paling gampang: **Admin → Server 3 → Bulk Unduh Series** di situs —
centang episodenya (daftar dari TMDB), sekali klik, kartu itu juga
menampilkan status run terbaru. Bisa juga lewat tab Actions di sini.

Catatan kuota: 100 episode ≈ 10-20 jam kerja Codespace (dari kuota 60 jam
mesin/bulan) + ±10 runner jam dari 2.000 menit Actions/bulan — cukup untuk
±1-2 season besar per bulan.

---

## Subtitle — cara kerjanya sekarang

- Bahasa subtitle **mengalir sampai situs**: label CC di player = bahasa
  aslinya (Indonesia / English / Melayu / dst), bukan lagi teks tetap.
- Kalau bahasa pilihan tidak tersedia, pipeline **fallback** ke bahasa lain
  yang ada (tertulis di log).
- **Subtitle native di player Abyss** dipasang best-effort (login + PUT via
  curl + User-Agent browser — ini fix bug **403 Forbidden** lama: request
  Python tanpa UA ditolak Cloudflare di depan `api.abyss.to`). Gagal pun
  **tidak fatal** — halaman film situs tetap dapat subtitle via injeksi
  ZeeStream.
- **Timing subtitle kurang pas?** Atur di **Admin → Subtitle → Sinkron**:
  tombol ±0,5/±1 detik (jangkauan ±60 dtk, presisi 0,1) — langsung terasa
  di player, tanpa unduh/unggah ulang. Label bahasa juga bisa dikoreksi
  per baris di sana.

---

## Kuota & biaya (akun gratis)

| Sumber | Jatah gratis | Pakai di sini |
|---|---|---|
| Actions (repo **private**) | 2.000 menit/bulan | ± 20-40 mnt per film → **± 50-100 film/bln** |
| Actions (repo **public**) | **UNLIMITED** | kalau kuota private habis, repo bisa dibuat public |
| Codespaces | 120 core-jam/bln ≈ 60 jam mesin 2-core | auto-stop tiap film + tidur 30 mnt idle |
| Bandwidth pribadi | — | **0 byte** — unduhan & upload dari jaringan Codespace |

Catatan: 2.000 menit Actions **tidak** cukup untuk layanan 24 jam — tapi
pipeline ini memang on-demand (jalan hanya saat kamu meminta film).

---

## Dua sumber unduhan: MovieBox & LK21 (v2 — Task 96b, 29 Okt 2026)

Workflow `ZS Download` punya pilihan **`sumber`**: `moviebox` (utama) dan
`lk21` (katalog Indonesia — hardsub/softsub, judul yang sering tidak ada
di MovieBox, termasuk film China/Asia terbaru).

**Mengapa LK21 pernah gagal total (run 28-29 Okt):** semua tombol unduhan
LK21 dibungkus shortlink **ouo.io** yang dilindungi Cloudflare Turnstile —
mustahil dibuka dari server (exit 1 "no server-exposed host"). Shortener
serupa juga dipakai Pahe (clk.sh + Turnstile) — jalur unduh situs ID
memang telah terkunci.

**Jalur keluar (LK21 v2):** player LK21 adalah iframe platform **Byse**
(`justplay.cam/e/<kode>`) yang *tidak* dibungkus ouo — dan platform itu
bisa di-resolve 100% dari server:

1. `embed/details` (header `X-Embed-Origin` = halaman LK21) → URL player
   asli (prefix rotasi per-request, dipakai sebagai `Referer`);
2. tantangan **PoW** 16-bit (hash custom) dipecahkan `pow-solve.js` (node)
   / `pow-solve.c` (gcc, dikompilasi otomatis) / fallback Python murni;
3. **fingerprint viewer** ECDSA P-256 (`access/challenge` →
   `access/attest`) — dibuat server, diterima (confidence 0.88-0.9),
   di-cache modul untuk bulk;
4. `embed/playback` → payload **AES-256-GCM** → didekripsi →
   `master.m3u8` + grup subtitle **WebVTT "Bahasa Indonesia"**;
5. segmen `.ts` diunduh 16-thread → disatukan; subtitle VTT → SRT →
   CC player Abyss + katalog (flow upload/register lama dipertahankan).

HLS Byse dibatasi `sp=4000` (±500 KB/s per koneksi) — film 1.4 GB ±
25-50 menit. Untuk serial bulk tetap jalan lewat chunk otomatis ±2 jam.
Paket `cryptography` di-bootstrap otomatis via pip (`--target /tmp`).

**Batas yang diketahui:** resolusi bebas dari Byse embed umumnya 480p
(varian tertinggi yang mereka ekspos); kualitas `auto/1080/720/360` diabaikan
untuk Byse. Bila suatu judul tidak punya iframe Byse (jarang), pipeline
jatuh ke host tanpa-ouo / embed GDrive seperti sebelumnya.

---

## Troubleshooting

| Gejala | Sebab & solusi |
|---|---|
| `Disk Codespace tidak cukup` | Versi lama: film lama menumpuk. Versi sekarang: sisa lama otomatis dibersihkan di awal run — kalau masih tidak cukup, berarti filmnya memang lebih besar dari sisa disk; pilih resolusi lebih rendah, atau hapus Codespace lama lalu buat baru (repo → Code → Codespaces). |
| `Codespace mb-relay tidak ditemukan` | Codespace terhapus. Buka repo → tombol **Code** → tab **Codespaces** → **Create codespace on main**. Workflow otomatis menemukan Codespace repo yang terakhir dipakai — tidak ada nama yang di-hardcode. |
| `Relay tidak menjawab /healthz` | Port kembali private setelah tidur → `public_port.sh` menyembuhkan otomatis (± 1-3 mnt). Kalau lebih dari 5 menit: github.com/codespaces → **Restart**, lalu run ulang. |
| `426` di log unduhan | Kamu menjalankan versi lama (runner mengunduh langsung). Pastikan repo tersinkron (zs-dl.yml terbaru = runner hanya orkestrator). |
| Film tidak muncul di situs padahal run SUKSES | Input **token** kosong — film tetap ada di Abyss (slug tertera di log). Daftarkan manual: Admin → Server 3 → tempel slug, ATAU run ulang workflow dengan judul sama + token + **slug_lama** (register-saja, cepat, tanpa unduh ulang). |
| `HTTP 503` di log awal (get-domain / play / caption) | API BFF MovieBox **intermiten 503** (gangguan di sisi mereka, acak per-request). v1.2.3: pipeline otomatis retry 6× tiap 5 dtk — hampir pasti lolos. Kalau masih gagal 6× berarti sumber benar-benar down → tunggu beberapa jam lalu run ulang. |
| Transcode Abyss > 15 menit | Tidak masalah — register tetap dilakukan; status bisa dicek di dashboard Abyss (player.abyssplayer.com). |
| Subtitle tidak muncul di halaman film | Pastikan film terdaftar di katalog dulu, lalu cek **Admin → Subtitle**: bahasa/offset bisa dikoreksi & "pasang ulang ke player" di sana. |
| `subtitles 403` / `HTTP 403` saat pasang subtitle | Sudah diperbaiki di versi sekarang (curl + UA browser). Kalau masih muncul: Codespace belum menarik kode terbaru → Restart Codespace sekali lalu run ulang. |

---

## Keamanan

- **Bukan open proxy** — `/dl` hanya mengizinkan `*.hakunaymatata.com`
  (CDN file + subtitle MovieBox); URL lain ditolak.
- `/exec`, `/jobstart`, `/joblog` **hanya aktif bila header `X-ZS-Key`**
  cocok dengan `ZS_EXEC_KEY`; bila secret itu kosong, endpoint mati total.
- Tidak ada token/password yang tersimpan di file repo.
- Codespace dimatikan setiap selesai — permukaan serangan minimal.

---

## Rute alternatif (opsional): notebook Colab

Notebook `MovieBox_Colab_Downloader.ipynb` (arsip di sisi ZeeStream) masih
bisa memakai endpoint `/dl` relay ini untuk mengunduh dari dalam Colab —
berguna kalau kamu ingin kontrol per-langkah manual. Jalur utama dan yang
dirawat adalah **GitHub Actions** seperti di atas. (Rute Hugging Face Space
sudah **ditinggalkan** — hosting Docker/Gradio kini wajib langganan PRO;
rute Render juga ditinggalkan — wajib kartu + IP-nya diblokir CDN.)

---

## Riwayat penting

- **Relay v1.1** — teruji end-to-end di GitHub (Sep 2026): Actions →
  bangunkan Codespace → unduh (Azure 206) → upload Abyss → register →
  matikan Codespace; run SUCCESS 16,5 menit untuk film 181 MB 360p
  termasuk 15 menit tunggu transcode.
- **Fix subtitle 403** (Sep 2026) — login+PUT Abyss kini via curl + UA
  browser; bahasa subtitle asli ikut terdaftar di situs; CMS menambah
  pengaturan Sinkron (offset ± detik) + koreksi bahasa.
- **Fix disk penuh** (Okt 2026) — sisa film lama dibersihkan otomatis
  di awal run + file lokal dihapus segera setelah upload Abyss sukses
  (film 2,4 GB ditolak karena sisa disk tinggal 1 GB — gejala "tidak
  cukup" hilang total).
- **v1.2.2** (9 Okt 2026) — wake-step re-POST /start tiap 60 dtk saat
  Codespace masih Shutdown/ShuttingDown (race start yang ditelan →
  polling 7 menit sia-sia → run gagal).
- **v1.2.3** (9 Okt 2026) — dua perbaikan dari kasus nyata hari itu:
  (1) **retry 503** — BFF MovieBox intermiten 503 acak membuat run 24
  mati di `get-domain` padahal run 23 sukses 38 menit sebelumnya;
  sekarang `mb_http` ulang otomatis 6×/5 dtk (502/503/504/429).
  (2) **mode register-saja** via input `slug_lama` — film yang sudah
  terupload tapi belum masuk katalog (lupa token) tinggal register
  ulang tanpa unduh 2,4 GB lagi; register juga kini retry saat Abyss
  masih mem-proses (jawaban 202).
