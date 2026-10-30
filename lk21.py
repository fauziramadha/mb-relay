#!/usr/bin/env python3
# =====================================================================
#  lk21.py — Sumber LK21 (LayarKaca21) untuk pipeline ZeeStream
#  ======================= V2 (Task 96b, 29 Okt 2026) ==================
#
#  REVOLUSI RESOLVER — MASALAH ouo & JALUR KELUAR:
#    Blok unduh LK21 (Mixdrop/StreamTape/dll) SELALU dibalut shortlink
#    ouo.io yang dilindungi Cloudflare Turnstile → MUSTAHIL dilewati
#    dari server. (Itulah penyebab run pertama gagal exit 1.)
#    TAPI player LK21 berupa iframe host BYSE (justplay.cam/e/<code>)
#    yang TIDAK dibalut ouo — dan platform Byse bisa di-resolve 100%
#    server-side:
#
#      1. GET  /api/videos/<code>/embed/details   (header X-Embed-Origin
#         = halaman LK21) → embed_frame_url (host player asli, prefix
#         rotasi per-request, dipakai sebagai Referer).
#      2. POST .../embed/captcha                  → tantangan PoW
#         (nonce + difficulty 16 bit, hash custom keluarga blake).
#      3. Solve PoW (pow-solve.js via node ATAU pow-solve.c via gcc —
#         keduanya identik bit-per-bit, ~0.3-1.5 dtk).
#      4. POST .../embed/captcha/verify           → token captcha.
#      5. POST /api/videos/access/challenge       → nonce perangkat.
#      6. ECDSA P-256 (kunci dibuat sendiri, sign nonce) →
#         POST /api/videos/access/attest          → fingerprint viewer
#         (viewer_id/device_id/token/confidence) — fingerprint browser
#         DILOLOSKAN meski dibuat server (confidence 0.88).
#      7. POST .../embed/playback {fingerprint:{token,viewer_id,
#         device_id,confidence}} + X-Captcha-Token + header X-Embed
#         (Origin/Referer = LK21, Parent = iframe) → payload
#         terenkripsi.
#      8. Dekripsi AES-256-GCM (kunci = key_parts[v-1]‖key_parts[31-v])
#         → JSON {sources:[HLS], tracks}.
#      9. master.m3u8 → varian tertinggi → segmen .ts (+ grup subtitle
#         WebVTT "Bahasa Indonesia" di hampir semua judul) — semuanya
#         URL bertanda tangan 3 jam, diunduh multi-thread, disatukan
#         jadi satu file → upload Abyss (flow zs_dl.py lama).
#
#  Jadi sumber LK21 TETAP ADA di workflow (pilihan sumber: moviebox |
#  lk21) — hanya mekanisme resolusinya yang pindah jalur embed.
#
#  Fallback lawas tetap dipertahankan bila halaman LK21 menyediakan
#  host file tanpa ouo (langka) atau embed GDrive.
# =====================================================================
import base64
import http.cookiejar
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

# --- dependensi kripto (ECDSA attest + AES-GCM decrypt) ---
# Codespace python:3.12 tidak punya paket 'cryptography' bawaan —
# bootstrap pip sekali (--target folder tetap; path user-site dibuat
# TERLALU AKHIR oleh interpreter jadi harus di-insert manual).
# Wheel manylinux, tanpa kompilasi, ~5-15 dtk pertama saja.
_PYDEPS = "/tmp/zs-pydeps"
try:
    import cryptography.hazmat.primitives.hashes as _hashes
    import cryptography.hazmat.primitives.asymmetric.ec as _ec
    from cryptography.hazmat.primitives.asymmetric.utils import \
        decode_dss_signature
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    import subprocess as _sp
    import sys as _sys
    if not os.path.exists(os.path.join(_PYDEPS, "cryptography")):
        r = _sp.run([_sys.executable, "-m", "pip", "install", "--quiet",
                     "--target", _PYDEPS, "cryptography"],
                    timeout=300, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("pip install cryptography gagal: "
                               + (r.stderr or r.stdout)[-400:])
    if _PYDEPS not in _sys.path:
        _sys.path.insert(0, _PYDEPS)
    import cryptography.hazmat.primitives.hashes as _hashes
    import cryptography.hazmat.primitives.asymmetric.ec as _ec
    from cryptography.hazmat.primitives.asymmetric.utils import \
        decode_dss_signature
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

LK_DOMAINS = [
    "https://tv.nontonfilm.red",     # utama (terverifikasi 29 Okt 2026)
    "https://tv1.nontonfilm.red",    # mirror cadangan (belum teruji)
    "https://tv2.nontonfilm.red",    # mirror cadangan (belum teruji)
]
_domain_cache = (None, 0.0)

JAR = http.cookiejar.CookieJar()
OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(JAR))

_HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------
#  HTTP dasar
# ---------------------------------------------------------------------

def lk_http(url, referer=None, hdrs=None, timeout=40, retry=3):
    """GET dgn cookie jar + retry 502/503/504/429 (sama seperti mb_http)."""
    h = {"User-Agent": UA,
         "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "*/*;q=0.8"),
         "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7"}
    if referer:
        h["Referer"] = referer
    if hdrs:
        h.update(hdrs)
    last = None
    for i in range(max(1, retry)):
        try:
            return OPENER.open(urllib.request.Request(url, headers=h),
                               timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code not in (502, 503, 504, 429):
                raise
            last = e
        except Exception as e:
            last = e
        time.sleep(3 * (i + 1))
    raise last


def _b64url(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64u_dec(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def lk_domain():
    """Domain LK21 aktif — coba satu per satu, cache 30 menit."""
    global _domain_cache
    val, ts = _domain_cache
    if val and time.time() - ts < 1800:
        return val
    for d in LK_DOMAINS:
        try:
            r = lk_http(d + "/?s=test", timeout=15, retry=1)
            body = r.read(60000).decode("utf-8", "replace")
            r.close()
            if "gmr-" in body or 'id="download"' in body or \
                    "nontonfilm" in body.lower():
                _domain_cache = (d.rstrip("/"), time.time())
                return _domain_cache[0]
        except Exception:
            continue
    raise RuntimeError("Tidak ada domain LK21 yang hidup dari: "
                       + ", ".join(LK_DOMAINS))


# ---------------------------------------------------------------------
#  pencarian & detail
# ---------------------------------------------------------------------

def lk_search(kw, domain=None):
    """Cari judul → daftar {title, year, slug, kind, ep} (posting
    film/serial). Task 96d: dukung skema URL LK21 baru — series di
    /tv/<judul>/ dan episode di /eps/<judul>-episode-N/ (slug kini berisi
    path RELATIF PENUH, mis. "tv/agent-kim-reactivated"), sambil tetap
    membaca slug tunggal gaya lama (/kung-fu-soccer-2026/)."""
    domain = domain or lk_domain()
    r = lk_http(f"{domain}/?s={urllib.parse.quote(kw)}")
    html = r.read().decode("utf-8", "replace")
    r.close()
    root = urllib.parse.urlsplit(domain).netloc
    out, seen = [], set()
    for m in re.finditer(
            rf'href="https?://{re.escape(root)}/((?:tv|eps)/)?'
            rf'([a-z0-9][a-z0-9-]{{3,90}})/"',
            html):
        subdir, last = (m.group(1) or "").strip("/"), m.group(2)
        slug = (subdir + "/" if subdir else "") + last
        if slug in seen or any(s in slug for s in (
                "film-semi", "blog-category", "cast/", "genre/", "country/",
                "director/", "contact-us", "dmca", "faq", "best-rating",
                "order-by", "report-link", "iklan", "index-movie", "blog/",
                "tag/", "season/")):
            continue
        seen.add(slug)
        # episode post? (skema baru: eps/<judul>-episode-N)
        ep = 0
        m_ep = re.search(r"-episode-(\d+)$", last)
        base = last[:m_ep.start()] if m_ep else last
        kind = "episode" if m_ep else ("tv" if subdir == "tv" else "")
        if m_ep:
            ep = int(m_ep.group(1))
            kind = "episode"
        parts = base.split("-")
        year = parts[-1] if parts and parts[-1].isdigit() and \
            len(parts[-1]) == 4 else ""
        title = " ".join(parts[:-1] if year else parts).title()
        out.append({"slug": slug, "title": title or slug, "year": year,
                    "kind": kind, "ep": ep})
    return out


# --- Task 96d: skor relevansi + pencarian tahan-benda -----------------
#  DUA jebakan LK21 yang membuat run user gagal ("Agent Kim Reactivated"):
#  (1) judul yang TIDAK ada → tema menampilkan daftar unggahan terbaru
#      (sampah, termasuk judul dewasa) — dipilih buta oleh pick [0];
#  (2) WP-search kadang gagal utk query panjang ("Agent Kim Reactivated"
#      → 0 hasil) padahal judulnya ADA dan ketemu dgn "Agent Kim".
#  Solusi: coba query penuh → potongan kata depan; skor setiap hasil
#  terhadap query ASLI; tolong yang skor < ambang; filter judul dewasa.

_ADULT_WORDS = ("sex", "porn", "bokep", "hentai", "erotic", "xxx",
                "film-semi", "semi-", "-semi")


def _tok(s):
    """Token kata: lowercase alnum, buang 's' posesif/plural ringan."""
    toks = re.findall(r"[a-z0-9]+", (s or "").lower())
    return {(t[:-1] if len(t) > 3 and t.endswith("s") else t) for t in toks}


def _score(query, item):
    """Skor relevansi 0..1: seberapa banyak token query (tanpa token
    tahun) muncul di token judul/slug hasil. Bonus kecil bila tahun
    query cocok. Toko tahun diabaikan supaya "Moana 2026" tetap cocok
    dgn judul "Moana"."""
    q_raw = _tok(query)
    q_year = {t for t in q_raw if t.isdigit() and len(t) == 4}
    q = q_raw - q_year
    if not q:
        q = q_raw or {""}
    hay = _tok((item.get("title") or "") + " " + (item.get("slug") or ""))
    hit = len(q & hay) / max(1, len(q))
    if q_year and item.get("year") and item["year"] in q_year:
        hit = min(1.0, hit + 0.15)
    return hit


def lk_find(query, domain=None, log=None):
    """Pencarian pintar (Task 96d): multi-strategi + skor relevansi.
    Kembalikan (results_terurut, query_dipakai) — results memuat field
    'score'; hasil skor < LK_MIN_SCORE (0.6) dianggap tidak relevan
    oleh pemanggil. Strategi: query penuh → buang kata terakhir
    berulang (min 1 kata). Berhenti saat ada hasil skor ≥ ambang.
    Semua hasil tetap DISKOR terhadap query ASLI, bukan potongannya —
    supaya query "Agent" sendirian tidak bisa menipu."""
    def say(*a):
        if log:
            log(*a)

    q_words = query.split()
    attempts, tried = [], set()
    n = len(q_words)
    while n >= 1 and len(attempts) < 4:
        q_try = " ".join(q_words[:n])
        if q_try.lower() in tried:
            break
        tried.add(q_try.lower())
        attempts.append(q_try)
        n -= 1

    best, best_q, best_top = [], query, 0.0
    for q_try in attempts:
        try:
            raw = lk_search(q_try, domain)
        except Exception as e:
            say(f"  lk_search('{q_try}') gagal: {repr(e)[:80]}")
            continue
        # filter dewasa SEBELUM diskor — jangan pernah tawarkan judul
        # konten dewasa sebagai hasil otomatis
        clean = [it for it in raw
                 if not any(w in (it.get("slug") or "").lower()
                            or w in " ".join(_tok(it.get("title") or ""))
                            for w in _ADULT_WORDS)]
        for it in clean:
            it["score"] = round(_score(query, it), 2)
        ranked = sorted(clean, key=lambda x: -x["score"])
        top = ranked[0]["score"] if ranked else 0.0
        say(f"  query '{q_try}' → {len(raw)} mentah, {len(clean)} bersih, "
            f"skor teratas {top:.2f}")
        if top > best_top:
            best, best_q, best_top = ranked, q_try, top
        if best_top >= 0.6:
            break

    # konsolidasi: jangan biarkan 10 post-episode judul yang sama
    # membanjiri daftar — 1 entri per judul-dasar. Halaman /tv/ (series)
    # menang; kalau tidak ada, wakil = post-episode nomor terkecil.
    groups = {}
    for it in best:
        key = re.sub(r"^(?:tv|eps)/", "", it["slug"])
        key = re.sub(r"-episode-\d+$", "", key)
        cur = groups.get(key)
        if cur is None:
            groups[key] = it
        elif cur["kind"] != "tv" and it["kind"] == "tv":
            # halaman /tv/ (series) selalu menang jadi wakil grup
            groups[key] = it
        elif (cur["kind"] == "episode" and it["kind"] == "episode"
              and it["ep"] < cur["ep"]):
            groups[key] = it
    best = sorted(groups.values(), key=lambda x: -x["score"])
    return best, best_q


def lk_detail(slug, domain=None):
    """Bedah halaman detail → blok unduh + iframe player (Byse) + embed
    gdrive + subtitle + link episode (utk series)."""
    domain = domain or lk_domain()
    if not slug.startswith("http"):
        url = f"{domain}/{slug.strip('/')}/"
    else:
        url = slug
    r = lk_http(url)
    html = r.read().decode("utf-8", "replace")
    r.close()

    # --- link episode utk SERIES ---
    episode_links = []
    # Task 96d: tangkap path PENUH — episode kini hidup di bawah
    # /eps/ (dan mungkin prefiks lain); tanpa prefiks, lk_detail akan
    # salah membangun URL halaman episode.
    for m in re.finditer(
            rf'href="https?://[^"]*?/((?:[a-z0-9-]{{1,12}}/)?'
            rf'[a-z0-9-]+-episode-\d+)/?"', html):
        ep_slug = m.group(1)
        if ep_slug not in episode_links:
            episode_links.append(ep_slug)

    # --- iframe player (Byse & keluarga /e/<code>) — JALUR UTAMA V2 ---
    # Task 96d: src kadang punya path tambahan setelah kode
    # (/e/<code>/judul-720p…) — byse_resolve hanya butuh /e/<code> di awal.
    byse = []
    for m in re.finditer(
            r'<iframe[^>]+src="(https?://([^/]+)/e/([a-zA-Z0-9_-]{4,}))'
            r'(?:/[a-zA-Z0-9_.%-]*)?"', html):
        if m.group(1) not in byse:
            byse.append(m.group(1))

    # --- blok unduh (fallback; mayoritas ouo → dilewati) ---
    root_host = urllib.parse.urlsplit(domain).netloc
    items, subs = [], []
    i = html.find('id="download"')
    seg = html[i:i + 12000] if i >= 0 else ""
    for m in re.finditer(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', seg,
                         re.S):
        href, label = m.group(1), re.sub(r"<[^>]+>|\s+", " ",
                                         m.group(2)).strip()
        real = ouo_unwrap(href)
        low = (real or href).lower()
        is_sub = low.endswith(".srt") or low.endswith(".vtt") or \
            "/subtitle" in label.lower() or "sub indo" in label.lower()
        if real is None:
            if is_sub:
                subs.append("")
            else:
                items.append({"label": label, "quality": "", "url": None,
                              "gate": "ouo-challenge"})
            continue
        if is_sub:
            subs.append(real)
            continue
        host = urllib.parse.urlsplit(real).hostname or ""
        if host.endswith(root_host) or not host or not label:
            continue
        q = re.search(r"(\d{3,4})\s*p", label.lower())
        items.append({"label": label, "quality": q.group(1) if q else "",
                      "url": real, "gate": ""})

    # --- embed gdrive di area player (fallback lawas) ---
    gdrive = []
    for m in re.finditer(r'drive\.google\.com/file/d/([a-zA-Z0-9_-]{10,})',
                         html):
        if m.group(1) not in gdrive:
            gdrive.append(m.group(1))

    t = re.search(r"<h1[^>]*>([^<]{2,150})</h1>", html)
    title = t.group(1).strip() if t else slug
    return {"url": url, "title": title, "items": items, "subs": subs,
            "gdrive": gdrive, "episode_links": episode_links, "byse": byse}


# ---------------------------------------------------------------------
#  shortlink ouo (hanya pola bocor ?s=; sisanya memang tak mungkin)
# ---------------------------------------------------------------------

def ouo_unwrap(url):
    if not url:
        return None
    m = re.search(r"[?&]s=(https?://.+)$", url)
    if m and "ouo.io" in url:
        return urllib.parse.unquote(m.group(1).strip())
    if "ouo.io" in url or "ouo.press" in url or "shrinkme" in url:
        return None
    return url.strip()


# ---------------------------------------------------------------------
#  PoW Byse — solver multi-backend (node → gcc → python), paritas sama
# ---------------------------------------------------------------------

def _pow_node(nonce, difficulty, log):
    js = os.path.join(_HERE, "pow-solve.js")
    r = subprocess.run(["node", js, nonce, str(difficulty), "30000"],
                       capture_output=True, text=True, timeout=60)
    out = r.stdout.strip()
    return out if out and out not in ("TIMEOUT",) and not \
        out.startswith("ERROR") else None


def _pow_c(nonce, difficulty, log):
    exe = "/tmp/zs-pow-solve"
    if not os.path.exists(exe):
        src = os.path.join(_HERE, "pow-solve.c")
        r = subprocess.run(["gcc", "-O2", "-o", exe, src],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return None
    r = subprocess.run([exe, nonce, str(difficulty), "30000"],
                       capture_output=True, text=True, timeout=60)
    out = r.stdout.strip()
    return out if out and out != "TIMEOUT" else None


def _pow_python(nonce, difficulty, log):
    """Fallback murni Python (LAMBAT ±2-4 mnt utk difficulty 16) —
    dipakai hanya bila node & gcc sama-sama absen. Urutan counter identik
    dengan versi JS/C sehingga solusi sama persis."""
    def rotl(t, e):
        e &= 31
        return t if e == 0 else ((t << e) | (t >> (32 - e))) & 0xFFFFFFFF

    def ye(t):
        t[0] = (t[0] + t[1]) & 0xFFFFFFFF
        t[3] = rotl(t[3] ^ t[0], 16)
        t[2] = (t[2] + t[3]) & 0xFFFFFFFF
        t[1] = rotl(t[1] ^ t[2], 12)
        t[0] = (t[0] + t[1]) & 0xFFFFFFFF
        t[3] = rotl(t[3] ^ t[0], 8)
        t[2] = (t[2] + t[3]) & 0xFFFFFFFF
        t[1] = rotl(t[1] ^ t[2], 7)

    def gr(data):
        e = [1779033703, 3144134277, 1013904242, 2773480762]
        for b in data:
            e[0] = (e[0] + b) & 0xFFFFFFFF
            e[0] = rotl(e[0], 7)
            ye(e)
        for _ in range(8):
            ye(e)
        r = [0] * 512
        for i in range(512):
            ye(e)
            r[i] = (e[0] ^ e[2]) & 0xFFFFFFFF
        for _ in range(2):
            for s in range(512):
                a = r[s] & 511
                c = (r[s] + r[a]) & 0xFFFFFFFF
                c = rotl(c, 13)
                c ^= (r[(s + 1) & 511] * 2654435761) & 0xFFFFFFFF
                r[s] = c
                e[0] = (e[0] ^ c) & 0xFFFFFFFF
                ye(e)
        out = [0] * 8
        for i in range(8):
            ye(e)
            s = e[0]
            for c in range(64):
                d = r[i * 64 + c]
                s = (s + d) & 0xFFFFFFFF
                s = rotl(s, 5)
                s = s ^ ((d * 2246822519) & 0xFFFFFFFF)
            out[i] = (s ^ e[2]) & 0xFFFFFFFF
        return out

    def zbits(words):
        n = 0
        for w in words:
            if w == 0:
                n += 32
                continue
            return n + (32 - w.bit_length())
        return n

    if difficulty <= 0:
        return "0"
    t0 = time.time()
    prefix = nonce + ":"
    s = 0
    while True:
        h = gr(bytearray([c & 255 for c in (prefix + str(s)).encode()]))
        if zbits(h) >= difficulty:
            return str(s)
        s += 1
        if s % 4096 == 0 and time.time() - t0 > 120:
            if log:
                log("   ⚠ PoW python lambat (bila sering: pasang node/gcc)")
            return None


def solve_pow(nonce, difficulty, log=None):
    """Selesaikan tantangan PoW Byse → string angka, atau None."""
    for fn, label in ((_pow_node, "node"), (_pow_c, "gcc"),
                      (_pow_python, "python")):
        try:
            sol = fn(nonce, difficulty, log)
        except Exception as e:
            if log:
                log(f"   ⚠ PoW [{label}]: {repr(e)[:70]}")
            sol = None
        if sol:
            if log:
                log(f"   PoW via [{label}]: {sol}")
            return sol
    return None


# ---------------------------------------------------------------------
#  RESOLVER BYSE — inti V2
# ---------------------------------------------------------------------

# fingerprint viewer di-cache modul — attest sekali, dipakai ulang
# sampai expires_at (bulk 100 episode tidak 100x attestation)
_FP_CACHE = {"fp": None, "exp": 0.0}


def _byse_api(base, path, method="GET", body=None, extra=None, referer=None):
    h = {"User-Agent": UA,
         "Accept": "application/json, text/plain, */*", "Origin": base}
    if referer:
        h["Referer"] = referer
    if body is not None:
        h["Content-Type"] = "application/json"
    if extra:
        h.update(extra)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                headers=h)
    try:
        with OPENER.open(req, timeout=40) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return None, {"err": repr(e)[:120]}


def _fake_client():
    """Atribut 'perangkat' yang diterima server attestation (confidence
    0.88 — teruji). Tidak ada pemeriksaan hash nyata di sisi server."""
    return {"user_agent": UA,
            "brand_full_versions": [
                {"brand": "Chromium", "version": "131.0.0.0"},
                {"brand": "Not_A Brand", "version": "24.0.0.0"},
                {"brand": "Google Chrome", "version": "131.0.0.0"}],
            "platform": "Windows", "architecture": "x86", "bitness": "64",
            "platform_version": "15.0.0", "model": "",
            "ua_full_version": "131.0.0.0", "pixel_ratio": 1,
            "screen_width": 1920, "screen_height": 1080, "color_depth": 24,
            "languages": ["id-ID", "id", "en-US", "en"],
            "timezone": "Asia/Jakarta", "hardware_concurrency": 8,
            "device_memory": 8, "touch_points": 0,
            "webgl_vendor": "Google Inc. (NVIDIA)",
            "webgl_renderer": ("ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 "
                               "Direct3D11 vs_5_0 ps_5_0)"),
            "canvas_hash": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
            "audio_hash": "9f8e7d6c5b4a392817f6e5d4c3b2a190",
            "webgl_params_hash": "55667788990011223344556677889900",
            "fonts_hash": "00112233445566778899aabbccddeeff",
            "codecs_hash": "deadbeefcafebabe0123456789abcdef",
            "media_devices": "ai1ao1vi2", "pointer_type": "mouse",
            "extra": {"vendor": "Google Inc.",
                      "appVersion": "5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/131.0.0.0 Safari/537.36"}}


def _byse_attest(base, watch_url, log=None):
    """Challenge + ECDSA sign + attest → fingerprint (di-cache modul)."""
    if _FP_CACHE["fp"] and time.time() < _FP_CACHE["exp"] - 30:
        return _FP_CACHE["fp"]
    st, ch = _byse_api(base, "/api/videos/access/challenge", method="POST",
                       body={}, referer=watch_url)
    if st != 200 or not ch.get("nonce"):
        raise RuntimeError(f"access/challenge gagal ({st})")
    priv = _ec.generate_private_key(_ec.SECP256R1())
    nums = priv.public_key().public_numbers()
    public_jwk = {"kty": "EC", "crv": "P-256",
                  "x": _b64url(nums.x.to_bytes(32, "big")),
                  "y": _b64url(nums.y.to_bytes(32, "big")),
                  "ext": True, "key_ops": ["verify"]}
    sig = priv.sign(ch["nonce"].encode(), _ec.ECDSA(_hashes.SHA256()))
    ri, si = decode_dss_signature(sig)
    payload = {"viewer_id": "", "device_id": "",
               "challenge_id": ch.get("challenge_id", ""),
               "nonce": ch["nonce"],
               "signature": _b64url(ri.to_bytes(32, "big") +
                                    si.to_bytes(32, "big")),
               "public_key": public_jwk, "client": _fake_client(),
               "storage": {}, "attributes": {"entropy": "high"}}
    st, att = _byse_api(base, "/api/videos/access/attest", method="POST",
                        body=payload, referer=watch_url)
    if st != 200 or not att.get("token"):
        raise RuntimeError(f"access/attest gagal ({st})")
    fp = {"token": att["token"], "viewer_id": att.get("viewer_id", ""),
          "device_id": att.get("device_id", ""),
          "confidence": att.get("confidence", 0)}
    try:
        import datetime
        dt = datetime.datetime.fromisoformat(
            att.get("expires_at", "").replace("Z", "+00:00"))
        _FP_CACHE["exp"] = dt.timestamp()
    except Exception:
        _FP_CACHE["exp"] = time.time() + 480
    _FP_CACHE["fp"] = fp
    if log:
        log(f"   attest OK (confidence {fp['confidence']})")
    return fp


def byse_resolve(embed_url, emb_page, log=None):
    """Resolve iframe Byse (…/e/<code>) → {hls, sub_vtt, quality, size,
    watch_url}. Semua langkah server-side; PoW via node/gcc/python.
    emb_page = URL halaman LK21 (utk header X-Embed)."""
    m = re.match(r"(https?://[^/]+)/e/([a-zA-Z0-9_-]{4,})", embed_url)
    if not m:
        return None
    front, code = m.group(1), m.group(2)
    emb_origin = urllib.parse.urlsplit(emb_page).scheme + "://" + \
        urllib.parse.urlsplit(emb_page).netloc

    # 1. details di FRONT (justplay) — dapat host player asli
    st, det = _byse_api(front, f"/api/videos/{code}/embed/details",
                        referer=f"{front}/e/{code}",
                        extra={"X-Embed-Origin": emb_origin})
    if st != 200 or not det.get("embed_frame_url"):
        if log:
            log(f"   ✘ byse details di front ({st}) — coba front sbg player")
        frame_url = f"{front}/d/{code}"
    else:
        frame_url = det["embed_frame_url"]
    base = "https://" + urllib.parse.urlsplit(frame_url).netloc

    # 2. PoW + verify
    st, cap = _byse_api(base, f"/api/videos/{code}/embed/captcha",
                       method="POST", body={}, referer=frame_url)
    if st != 200 or not cap.get("pow_nonce"):
        raise RuntimeError(f"embed/captcha gagal ({st})")
    sol = solve_pow(cap["pow_nonce"], cap.get("pow_difficulty", 16), log)
    if sol is None:
        raise RuntimeError("PoW tidak terpecahkan (node/gcc/python semua "
                           "gagal/timeout)")
    st, ver = _byse_api(base, f"/api/videos/{code}/embed/captcha/verify",
                        method="POST",
                        body={"pow_token": cap["pow_token"], "solution": sol},
                        referer=frame_url)
    if ver.get("status") != "ok":
        raise RuntimeError(f"PoW verify gagal ({st})")

    # 3. fingerprint attest (di-cache modul utk bulk)
    fp = _byse_attest(base, frame_url, log)

    # 4. playback
    st, play = _byse_api(base, f"/api/videos/{code}/embed/playback",
                         method="POST", body={"fingerprint": fp},
                         extra={"X-Captcha-Token": ver["token"],
                                "X-Embed-Origin": emb_origin,
                                "X-Embed-Referer": emb_page,
                                "X-Embed-Parent": f"{front}/e/{code}"},
                         referer=frame_url)
    if st != 200:
        raise RuntimeError(f"embed/playback gagal ({st}): "
                           f"{json.dumps(play)[:120]}")
    pb = play.get("playback") or {}
    if not pb.get("key_parts"):
        raise RuntimeError("payload playback tanpa key_parts")
    versi = int(pb.get("version") or 0)
    sel = [i for i in (versi, 31 - versi) if 1 <= i <= len(pb["key_parts"])]
    key = b"".join(_b64u_dec(pb["key_parts"][i - 1]) for i in sel)
    try:
        pt = AESGCM(key).decrypt(_b64u_dec(pb["iv"]),
                                 _b64u_dec(pb["payload"]), None)
        data = json.loads(pt.decode())
    except Exception as e:
        raise RuntimeError(f"dekripsi AES gagal: {repr(e)[:100]}")

    srcs = data.get("sources") or []
    if not srcs:
        raise RuntimeError("playback terdekripsi tanpa sources")
    # ambil resolusi tertinggi (label "480p"/"720p"/…)
    def hight(s):
        try:
            return int(s.get("height") or 0)
        except (TypeError, ValueError):
            return 0
    best = sorted(srcs, key=hight)[-1]
    res = {"hls": best.get("url"), "quality": best.get("label") or
           f"{hight(best)}p",
           "size": int(best.get("size_bytes") or 0),
           "watch_url": frame_url, "host": base, "code": code,
           "tracks": data.get("tracks") or []}
    if log:
        log(f"   byse OK: {res['quality']} "
            f"({res['size']/1e6:.0f} MB) via {urllib.parse.urlsplit(frame_url).netloc}")
    return res


# ---------------------------------------------------------------------
#  HLS: playlist → varian → segmen; unduh multi-thread; subtitle VTT
# ---------------------------------------------------------------------

def hls_map(master_url, referer=None):
    """Master m3u8 → {variant_url, segments[], sub_uri, sub_segments[]}.
    Varian = bandwidth tertinggi; sub = grup SUBTITLES berbahasa id."""
    r = lk_http(master_url, referer=referer, hdrs={"Accept": "*/*"},
                timeout=30)
    master = r.read().decode("utf-8", "replace")
    r.close()
    variants, sub_uri = [], None
    lines = master.splitlines()
    for i, l in enumerate(lines):
        if l.startswith("#EXT-X-STREAM-INF"):
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if nxt and not nxt.startswith("#"):
                variants.append(nxt.strip())
        if l.startswith("#EXT-X-MEDIA:TYPE=SUBTITLES"):
            msub = re.search(r'LANGUAGE="([^"]*)"', l)
            muri = re.search(r'URI="([^"]+)"', l)
            if muri and (not sub_uri or
                         (msub and msub.group(1) in ("id", "ind", "id-ID"))):
                sub_uri = muri.group(1)
    if not variants:
        raise RuntimeError("master.m3u8 tanpa varian")
    best_bw, variant_url = -1, variants[0]
    for i, l in enumerate(lines):
        if l.startswith("#EXT-X-STREAM-INF"):
            mbw = re.search(r"BANDWIDTH=(\d+)", l)
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if mbw and nxt and not nxt.startswith("#") and \
                    int(mbw.group(1)) > best_bw:
                best_bw = int(mbw.group(1))
                variant_url = nxt.strip()
    r = lk_http(variant_url, referer=referer, hdrs={"Accept": "*/*"},
                timeout=30)
    var = r.read().decode("utf-8", "replace")
    r.close()
    segments = [l.strip() for l in var.splitlines()
                if l.strip() and not l.startswith("#")]
    sub_segments = []
    if sub_uri:
        try:
            r = lk_http(sub_uri, referer=referer, hdrs={"Accept": "*/*"},
                        timeout=30)
            sv = r.read().decode("utf-8", "replace")
            r.close()
            sub_segments = [l.strip() for l in sv.splitlines()
                            if l.strip() and not l.startswith("#")]
        except Exception:
            sub_segments = []
    return {"variant_url": variant_url, "segments": segments,
            "sub_uri": sub_uri, "sub_segments": sub_segments}


def download_hls(segments, out_path, referer=None, workers=6,
                 log=None, progress=None):
    """Unduh segmen .ts multi-thread → satu file. Return (ok, pesan).
    progress(done, total) dipanggil tiap segmen selesai."""
    total = len(segments)
    done = [0]
    fails = []
    lock = threading.Lock()

    def pull(idx, url, tries=4):
        for t in range(tries):
            try:
                r = lk_http(url, referer=referer, hdrs={"Accept": "*/*"},
                            timeout=60, retry=1)
                data = r.read()
                r.close()
                if len(data) < 188:
                    raise ValueError("segmen kosong")
                return idx, data
            except Exception:
                time.sleep(1 + t * 2)
        return idx, None

    # urutan hasil dgn slot per segmen supaya file tersusun benar
    slots = {}
    next_worker = [0]

    import queue
    q = queue.Queue()
    for i, u in enumerate(segments):
        q.put((i, u))

    def worker():
        while True:
            try:
                i, u = q.get_nowait()
            except Exception:
                return
            idx, data = pull(i, u)
            with lock:
                if data is None:
                    fails.append(i)
                else:
                    slots[idx] = data
                done[0] += 1
                if progress:
                    try:
                        progress(done[0], total)
                    except Exception:
                        pass
                q.task_done()

    ths = [threading.Thread(target=worker) for _ in range(max(1, workers))]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    if fails:
        return False, f"{len(fails)} segmen gagal (mis. {fails[:5]})"
    with open(out_path, "wb") as f:
        for i in range(total):
            f.write(slots.get(i, b""))
    return True, "selesai"


_PROMO_CUES = re.compile(
    r"jual|order|shopee|tokopedia|wa\b|08[0-9]{8,}|subtitle by|kunjungi",
    re.I)


def fetch_sub_vtt(sub_segments, referer=None, log=None, workers=6):
    """Unduh segmen .vtt (multi-thread) → satukan jadi teks VTT (filter
    cue promosi yang umum di sub ripper). Return teks vtt ATAU None."""
    if not sub_segments:
        return None
    import queue
    q = queue.Queue()
    for i, u in enumerate(sub_segments):
        q.put((i, u))
    slots, lock = [None] * len(sub_segments), threading.Lock()

    def worker():
        while True:
            try:
                i, u = q.get_nowait()
            except Exception:
                return
            try:
                r = lk_http(u, referer=referer, hdrs={"Accept": "*/*"},
                            timeout=40, retry=2)
                txt = r.read().decode("utf-8", "replace")
                r.close()
            except Exception:
                continue
            with lock:
                slots[i] = txt

    ths = [threading.Thread(target=worker) for _ in range(max(1, workers))]
    for t in ths:
        t.start()
    for t in ths:
        t.join()

    cues = []
    for txt in slots:
        if not txt:
            continue
        txt = re.sub(r"^WEBVTT[^\n]*\n+", "", txt.strip())
        blocks = [b for b in re.split(r"\n\s*\n", txt) if "-->" in b]
        for b in blocks:
            body = b.split("-->", 1)[1]
            if _PROMO_CUES.search(body) and len(body.split()) < 25:
                continue  # cue iklan ripper — buang
            b = b.strip()
            if cues and cues[-1] == b:
                continue  # cue duplikat di batas segmen vtt
            cues.append(b)
    if not cues:
        return None
    return "\n\n".join(cues)


def vtt_to_srt(text):
    """WebVTT → SRT (dipakai register/cc Abyss di zs_dl.py)."""
    if not text:
        return None
    t = re.sub(r"^WEBVTT[^\n]*\n+", "", text.strip())
    blocks = [b for b in re.split(r"\n\s*\n", t) if "-->" in b]
    out = []
    for n, b in enumerate(blocks, 1):
        lines = b.strip().splitlines()
        # buang baris nomor cue lama, konversi titik → koma di timing
        if len(lines) and re.fullmatch(r"\d+", lines[0].strip()):
            lines = lines[1:]
        for j, ln in enumerate(lines):
            if "-->" in ln:
                ln = ln.replace(".", ",")
            lines[j] = ln
        out.append(f"{n}\n" + "\n".join(lines).strip())
    return "\n\n".join(out) + "\n"


# ---------------------------------------------------------------------
#  extractor per-host lawas (fallback bila bukan Byse)
# ---------------------------------------------------------------------

def extract_dood(url, referer=None):
    """Doodstream family — pass_md5 klasik."""
    try:
        m = re.search(r"(https?://[^/]+)/[de]/([a-z0-9]+)", url)
        if not m:
            return None
        base, code = m.group(1), m.group(2)
        r = lk_http(f"{base}/e/{code}", referer=referer or
                    "https://tv.nontonfilm.red/", timeout=15, retry=1)
        html = r.read().decode("utf-8", "replace")
        r.close()
        fm = re.search(r"/pass_md5/([a-f0-9]{16,})/" + re.escape(code), html)
        host = re.search(r"(https?://[a-z0-9.-]+/pass_md5)", html)
        if not fm:
            return None
        pm = (host.group(1) if host else f"{base}/pass_md5") + \
            fm.group(0)[len("/pass_md5"):]
        r2 = lk_http(pm, referer=f"{base}/e/{code}", timeout=15, retry=1,
                     hdrs={"Accept": "*/*"})
        dest = r2.read(400).decode("utf-8", "replace").strip()
        r2.close()
        if not dest.startswith("http"):
            return None
        import random
        final = dest + "".join(random.choice(
            "abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(10)) + \
            ("?direct=1" if "?" not in dest else "")
        return final, pm
    except Exception:
        return None


def extract_streamtape(url, referer=None):
    """Streamtape — robotlink get_video (4 bagian)."""
    try:
        r = lk_http(url.replace("/v/", "/e/"), referer=referer or
                    "https://tv.nontonfilm.red/", timeout=15, retry=1)
        html = r.read().decode("utf-8", "replace")
        r.close()
        seg = re.search(r"/get_video\?id=(.*)", html)
        if not seg:
            return None
        tail = seg.group(1)[:600]
        ids = re.findall(r"'([^']{3,64})'", tail)
        if len(ids) < 4:
            return None
        host_m = re.search(r"https?://([a-z0-9.-]+)/(?:v|e)/", url)
        host = host_m.group(1) if host_m else "streamtape.com"
        return (f"https://{host}/get_video?id={ids[0]}&expires={ids[1]}"
                f"&ip={ids[2]}&token={ids[3]}"), None
    except Exception:
        return None


def extract_mixdrop(url, referer=None):
    """Mixdrop — MDCore.wurl."""
    try:
        r = lk_http(url.replace("/f/", "/e/"), referer=referer or
                    "https://tv.nontonfilm.red/", timeout=15, retry=1)
        html = r.read().decode("utf-8", "replace")
        r.close()
        m = re.search(r'wurl\s*[=:]\s*["\']([^"\']{10,160})["\']', html)
        if not m:
            return None
        w = m.group(1)
        if not w.startswith("//"):
            w = "//" + w.lstrip("/")
        return "https:" + w, None
    except Exception:
        return None


EXTRACTORS = {
    "dood": extract_dood,
    "streamtape": extract_streamtape,
    "mixdrop": extract_mixdrop,
}


def host_of(url):
    try:
        return urllib.parse.urlsplit(url).hostname or ""
    except Exception:
        return ""


def probe_video(url, referer=None, timeout=15):
    h = {"User-Agent": UA, "Range": "bytes=0-1023", "Accept": "*/*"}
    if referer:
        h["Referer"] = referer
    try:
        r = OPENER.open(urllib.request.Request(url, headers=h),
                        timeout=timeout)
        code = r.getcode()
        ctype = r.headers.get("Content-Type", "")
        crange = r.headers.get("Content-Range", "")
        r.read(1024)
        r.close()
        size = 0
        m = re.search(r"/(\d+)$", crange)
        if m:
            size = int(m.group(1))
        cl = r.headers.get("Content-Length")
        if not size and cl and code == 200:
            size = int(cl or 0)
        ok = code in (200, 206) and (
            "video" in ctype.lower() or "mp4" in ctype.lower()
            or "octet-stream" in ctype.lower())
        return ok, code, ctype, size
    except urllib.error.HTTPError as e:
        try:
            e.read(200)
        except Exception:
            pass
        return False, e.code, e.headers.get("Content-Type", ""), 0
    except Exception as e:
        return False, None, repr(e)[:60], 0


def resolve_direct(items, quality="auto", log=None):
    """Kaskade item unduh lawas (host terekspos tanpa ouo — langka)."""
    def say(*a):
        if log:
            log(*a)

    def qval(it):
        try:
            return int(it.get("quality") or 0)
        except (TypeError, ValueError):
            return 0
    want = str(quality or "auto").lower()
    if want in ("auto", ""):
        cand = sorted(items, key=qval, reverse=True)
    else:
        try:
            w = int(str(want).replace("p", ""))
        except ValueError:
            w = 0
        cand = [it for it in items if qval(it) == w] + \
            sorted([it for it in items if 0 < qval(it) < w], key=qval,
                   reverse=True)
    for it in cand:
        if not it.get("url"):
            continue
        url = it["url"]
        host = host_of(url)
        label = it["label"]
        for key, fn in EXTRACTORS.items():
            if key in host or (key == "dood" and
                               any(s in host for s in ("dood", "ds2", "dsv",
                                                       "d000", "doo"))):
                got = fn(url, referer="https://tv.nontonfilm.red/")
                if not got:
                    break
                durl, dref = got
                ok, code, ctype, size = probe_video(durl, dref)
                if ok:
                    return {"url": durl, "referer": dref,
                            "quality": it.get("quality") or "", "size": size}
                break
        else:
            ok, code, ctype, size = probe_video(url)
            if ok:
                return {"url": url, "referer": None,
                        "quality": it.get("quality") or "", "size": size}
        say(f"   ✘ {label}: {host or '?'} ({code})")
    return None


def resolve_gdrive(file_id, quality="", log=None):
    """Jalur gdrive embed (fallback lawas)."""
    try:
        u = ("https://drive.google.com/uc?export=download&id="
             + urllib.parse.quote(file_id))
        r = OPENER.open(urllib.request.Request(u, headers={"User-Agent": UA}),
                        timeout=40)
        head = r.read(4096)
        ctype = r.headers.get("Content-Type", "")
        if "text/html" in ctype.lower():
            html = head.decode("utf-8", "replace") + \
                r.read(30000).decode("utf-8", "replace")
            r.close()
            act = re.search(r'action="([^"]+)"', html)
            if not act:
                return None
            params = dict(re.findall(
                r'name="(\w+)" value="([^"]*)"', html))
            q = urllib.parse.urlencode(params)
            dest = act.group(1).replace("&amp;", "&")
            dest += ("&" if "?" in dest else "?") + q
            ok, code, ctype2, size = probe_video(dest)
            if ok:
                return {"url": dest, "referer": None, "quality": quality,
                        "size": size}
            return None
        size = int(r.headers.get("Content-Length") or 0)
        r.close()
        return {"url": u, "referer": None, "quality": quality, "size": size}
    except Exception:
        return None


# ---------------------------------------------------------------------
#  resolver tingkat judul (dipakai zs_dl.py) — PRIORITY: Byse embed
# ---------------------------------------------------------------------

def resolve_from_detail(det, quality="auto", log=None, season=1):
    """Resolve dari HASIL lk_detail. Urutan V2:
       (1) iframe Byse → HLS + subtitle Indonesia bawaan
       (2) host terekspos tanpa ouo (langka)
       (3) embed gdrive."""
    def say(*a):
        if log:
            log(*a)

    # ---- (1) BYSE — jalur utama ----
    for embed_url in det.get("byse") or []:
        say("  mencoba player Byse:", embed_url[:70])
        try:
            res = byse_resolve(embed_url, det["url"], log)
        except Exception as e:
            say(f"   ✘ byse: {repr(e)[:110]}")
            continue
        if res and res.get("hls"):
            sub_srt = None
            try:
                peta = hls_map(res["hls"], referer=res["watch_url"])
                res["sub_uri"] = peta.get("sub_uri")
                res["n_segments"] = len(peta.get("segments") or [])
                if peta.get("sub_segments"):
                    say(f"   subtitle Indonesia bawaan: "
                        f"{len(peta['sub_segments'])} segmen vtt")
                    vtt = fetch_sub_vtt(peta["sub_segments"],
                                        referer=res["watch_url"], log=log)
                    sub_srt = vtt_to_srt(vtt)
            except Exception as e:
                say(f"   ⚠ peta HLS: {repr(e)[:90]}")
            got = {"hls": res["hls"], "url": res["hls"],
                   "referer": res["watch_url"],
                   "quality": res.get("quality") or "",
                   "size": res.get("size") or 0,
                   "source_page": det["url"], "engine": "byse",
                   "sub_srt": sub_srt}
            return got

    # ---- (2) host terekspos (lawas) ----
    got = resolve_direct(det["items"], quality, log)
    if got:
        got["sub_url"] = next((s for s in det["subs"] if s), None)
        got["source_page"] = det["url"]
        return got

    # ---- (3) gdrive embed (lawas) ----
    for fid in det["gdrive"]:
        say("  mencoba embed GDrive:", fid)
        g = resolve_gdrive(fid, quality, log)
        if g:
            g["sub_url"] = next((s for s in det["subs"] if s), None)
            g["source_page"] = det["url"]
            return g
    return None


def resolve_title(title, pick=0, quality="auto", season=1, episode=1,
                  log=None):
    """Cari + pilih + resolve SATU target (film atau SxE series)."""
    def say(*a):
        if log:
            log(*a)
    dom = lk_domain()
    say("LK21 domain:", dom)
    results = lk_search(title, dom)
    if not results:
        raise RuntimeError(f"Tidak ada hasil '{title}' di LK21 — "
                           "coba kata kunci lain (bisa juga pakai "
                           "sumber MovieBox)")
    say(f"Hasil pencarian LK21 '{title}': {len(results)} judul")
    listing = ["\n## Hasil pencarian LK21\n"]
    for i, it in enumerate(results[:20]):
        line = f"[{i:>2}] {it.get('title', '?')} ({it.get('year', '-')})"
        say("  " + line)
        listing.append(f"- `{i}` **{it.get('title', '?')}** "
                       f"({it.get('year', '-')})")
    try:
        s = os.environ.get("GITHUB_STEP_SUMMARY")
        if s:
            with open(s, "a", encoding="utf-8") as f:
                f.write("\n".join(listing) + "\n")
    except Exception:
        pass
    if pick < 0 or pick >= len(results):
        raise RuntimeError(f"Pilihan indeks {pick} di luar daftar "
                           f"LK21 (0..{len(results)-1})")
    sel = results[pick]
    say(f"\nTerpilih: {sel['title']} ({sel['year']})")
    det = lk_detail(sel["slug"], dom)
    is_tv = bool(det["episode_links"])
    say("Tipe:", "SERIES" if is_tv else "Film",
        f"({len(det['episode_links'])} link episode)" if is_tv
        else f"(player: {len(det['byse'])} byse, unduh: "
             f"{len(det['items'])} item)")

    target_slug = sel["slug"]
    if is_tv:
        eps = sorted(
            set(int(re.search(r"episode-(\d+)$", e).group(1))
                for e in det["episode_links"]
                if re.search(r"episode-(\d+)$", e)))
        if episode not in eps:
            raise RuntimeError(f"Episode {episode} tidak ada di LK21 "
                               f"(tersedia: {eps[:20]}…)")
        # Task 96d: pakai path PENUH dari episode link bila ada
        # (skema /eps/...); fallback bangun slug gaya lama.
        target_slug = next(
            (e for e in det["episode_links"]
             if re.search(r"episode-(\d+)$", e)
             and int(re.search(r"episode-(\d+)$", e).group(1)) == episode),
            None)
        if not target_slug:
            base_slug = re.sub(r"-episode-\d+$", "", sel["slug"])
            target_slug = f"{base_slug}-episode-{episode}"
        det = lk_detail(target_slug, dom)

    got = resolve_from_detail(det, quality, log)
    if got:
        got.update({"kind": "tv" if is_tv else "movie",
                    "title": det["title"], "year": sel["year"]})
        return got
    raise RuntimeError(
        "LK21: tidak ada jalur unduh yang terekspos server-side untuk "
        "judul ini (player Byse absen/berubah, unduhan ouo, gdrive "
        "kosong) — coba sumber MovieBox.")
