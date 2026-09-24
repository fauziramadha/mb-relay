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
2. Tonton log berjalan (log pipeline Codespace di-stream ke log Actions).
3. Selesai (film 2 GB ± 25-40 menit; film kecil ± 15-20 menit) → **Codespace
   dimatikan otomatis** — kuota Codespaces tidak terbakar idle.

Kronologi di balik layar: bangunkan Codespace (± 1-3 menit, termasuk
self-heal port) → cari judul → unduh dari CDN (terukur ± 5,4 MB/s di dalam
Codespace) → upload ke Abyss → tunggu transcode (maks 15 menit, tidak
menghalangi register) → register ke situs (bila token diisi) → pasang
subtitle → matikan Codespace.

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

## Troubleshooting

| Gejala | Sebab & solusi |
|---|---|
| `Codespace mb-relay tidak ditemukan` | Codespace terhapus. Buka repo → tombol **Code** → tab **Codespaces** → **Create codespace on main**. Workflow otomatis menemukan Codespace repo yang terakhir dipakai — tidak ada nama yang di-hardcode. |
| `Relay tidak menjawab /healthz` | Port kembali private setelah tidur → `public_port.sh` menyembuhkan otomatis (± 1-3 mnt). Kalau lebih dari 5 menit: github.com/codespaces → **Restart**, lalu run ulang. |
| `426` di log unduhan | Kamu menjalankan versi lama (runner mengunduh langsung). Pastikan repo tersinkron (zs-dl.yml terbaru = runner hanya orkestrator). |
| Film tidak muncul di situs padahal run SUKSES | Input **token** kosong — film tetap ada di Abyss (slug tertera di log). Daftarkan manual: Admin → Server 3 → tempel slug, ATAU run ulang workflow dengan judul sama + token. |
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
