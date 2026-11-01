#!/usr/bin/env python3
# =====================================================================
#  zs_dl.py — ZeeStream pipeline: MovieBox → Abyss  (GitHub Actions)
# =====================================================================
#  Berjalan DI DALAM Codespace mb-relay (egress Azure — DITERIMA CDN
#  MovieBox, teruji 206). Diorkestrasi oleh workflow zs-dl.yml di runner
#  Actions (egress runner DIBLOKIR CDN 426 — teruji; runner HANYA
#  membangunkan Codespace, mengirim parameter, memantau log, lalu
#  mematikan Codespace lagi — 0 byte data film lewat runner).
#  TANPA Colab, TANPA kuota pribadi.
#  Biaya: repo PRIVATE = 2.000 menit Actions/bln (±20-40 mnt per film);
#  + kuota Codespaces 60 jam mesin/bln (auto-stop tiap film).
#  Repo PUBLIC = menit Actions UNLIMITED gratis.
#
#  ENV (dari workflow_dispatch inputs + secrets):
#    ZS_TITLE    (wajib) kata kunci pencarian judul
#    ZS_PICK     indeks hasil pencarian (default "0")
#    ZS_SE       musim, serial saja (default "1")
#    ZS_EP       episode, serial saja (default "1") — MODE TUNGGAL
#    ZS_EPISODES BULK: spec episode, mis. "1-100" / "1,3,5-8" (Task 92);
#                bila terisi maka mode bulk dipakai (TV saja) dan ZS_EP diabaikan
#    ZS_BUDGET_MIN soft-budget menit per chunk (default 95) — bila terlampaui,
#                sisa episode ditunda & marker ZS_BULK_REMAIN dicetak agar
#                runner men-dispatch chunk berikutnya otomatis
#    ZS_QUALITY  auto / 1080 / 720 / 360 (default "auto")
#    ZS_SUB      in_id / ms / en / none (default "in_id")
#    ZS_TOKEN    token register dari Admin → Server 3 (opsional; untuk bulk
#                kirim token SCOPE-SERIES — dibuat tanpa episode, register
#                mengirim "episode" per panggilan)
#    ABYSS_KEY / ABYSS_EMAIL / ABYSS_PASSWORD  (repo secrets)
# =====================================================================
#
# v1.3 (29 Okt 2026) — Task 92 MODE BULK:
#   - sekali jalan utk banyak episode (unduh→upload→register per episode,
#     gagal satu ≠ gagal semua; file lokal dihapus tiap episode)
#   - budget chunk 95 mnt (runner GHA max ±2 j monitoring); sisa episode
#     otomatis di-antrekan zs-dl.yml sebagai chunk baru (self-dispatch)
#   - token scope-series: register per episode dengan body.episode
#   - subtitle resmi MovieBox + CC Abyss native tetap per episode
#
#   - mb_http RETRY 6x/5dtk utk 502/503/504/429 — BFF MovieBox
#     intermiten 503 (terukur 40-60% request acak; run 24 mati di
#     get-domain karena belum ada retry).
#   - MODE REGISTER-SAJA via input slug_lama: film sudah ada di Abyss
#     (mis. run lalu lupa isi token) → register TANPA unduh/upload.
#   - register retry saat jawaban "still processing" (202).
import base64
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request

import lk21  # Task 96 — sumber kedua LK21 (satu repo, ikut git pull codespace)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
H5 = "https://officialmoviebox.com"
XCI = '{"timezone":"Asia/Jakarta"}'
REGISTER = "https://www.zeestream.biz.id/api/v3/register"
SUMMARY = os.environ.get("GITHUB_STEP_SUMMARY", "")

TITLE = (os.environ.get("ZS_TITLE") or "").strip()
PICK = int(os.environ.get("ZS_PICK") or 0)
SEASON = int(os.environ.get("ZS_SE") or 1)
EPISODE = int(os.environ.get("ZS_EP") or 1)
EPISODES_SPEC = (os.environ.get("ZS_EPISODES") or "").strip()
BUDGET_MIN = int(os.environ.get("ZS_BUDGET_MIN") or 0) or 95
QUALITY = (os.environ.get("ZS_QUALITY") or "auto").strip().lower()
SUB_LANG = (os.environ.get("ZS_SUB") or "in_id").strip().lower()
TOKEN = (os.environ.get("ZS_TOKEN") or "").strip()
REUSE_SLUG = (os.environ.get("ZS_REUSE_SLUG") or "").strip()
SOURCE = (os.environ.get("ZS_SOURCE") or "moviebox").strip().lower()
# [Task 97] sumber "manual": link file langsung (GDrive/Mega/URL mp4)
# dari portal unduh LK21 dadadidi.de yang digerbang Turnstile interaktif
# (tidak bisa otomatis server-side) — user menempel link finalnya.
LINK = (os.environ.get("ZS_LINK") or "").strip()
TIPE = (os.environ.get("ZS_TIPE") or "").strip().lower()  # movie|tv
ABYSS_KEY = os.environ.get("ABYSS_KEY", "")
ABYSS_EMAIL = os.environ.get("ABYSS_EMAIL", "")
ABYSS_PASSWORD = os.environ.get("ABYSS_PASSWORD", "")

# ---- override dari file JSON (argv[1]) — dipakai bila dijalankan dari
#      workflow Actions melalui SSH (nilai tidak perlu di-quote shell) ----
if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
    with open(sys.argv[1], encoding="utf-8") as _f:
        _p = json.load(_f)
    TITLE = str(_p.get("title") or TITLE).strip()
    PICK = int(_p["pick"]) if _p.get("pick") is not None else PICK
    SEASON = int(_p["se"]) if _p.get("se") is not None else SEASON
    EPISODE = int(_p["ep"]) if _p.get("ep") is not None else EPISODE
    EPISODES_SPEC = str(_p.get("episodes") or EPISODES_SPEC).strip()
    BUDGET_MIN = int(_p["budget_min"]) if _p.get("budget_min") else BUDGET_MIN
    QUALITY = str(_p.get("quality") or QUALITY).strip().lower()
    SUB_LANG = str(_p.get("sub") or SUB_LANG).strip().lower()
    TOKEN = str(_p.get("token") or TOKEN).strip()
    REUSE_SLUG = str(_p.get("reuse_slug") or REUSE_SLUG).strip()
    SOURCE = str(_p.get("source") or SOURCE).strip().lower()
    LINK = str(_p.get("link") or LINK).strip()
    TIPE = str(_p.get("tipe") or TIPE).strip().lower()
    ABYSS_KEY = _p.get("abyss_key") or ABYSS_KEY
    ABYSS_EMAIL = _p.get("abyss_email") or ABYSS_EMAIL
    ABYSS_PASSWORD = _p.get("abyss_password") or ABYSS_PASSWORD
    try:
        os.remove(sys.argv[1])  # hapus file param (berisi kredensial)
    except OSError:
        pass

if SUB_LANG in ("none", "no", "off", "tanpa", "-"):
    SUB_LANG = ""
if SOURCE not in ("moviebox", "lk21", "manual"):
    SOURCE = "moviebox"
if TIPE not in ("movie", "tv"):
    TIPE = ""
MB_UUID = str(__import__("uuid").uuid4())
MB_STRAT_OK = None
MB_DIAG = []


def say(*parts):
    print(" ".join(str(p) for p in parts), flush=True)


def summary_line(text):
    if SUMMARY:
        with open(SUMMARY, "a", encoding="utf-8") as f:
            f.write(text + "\n")


def mb_http(url, referer=None, hdrs=None, timeout=40):
    h = {"User-Agent": UA, "accept": "application/json"}
    if referer:
        h["Referer"] = referer
    if hdrs:
        h.update(hdrs)
    # RETRY transient (v1.2.3): BFF MovieBox intermiten 503 — pool
    # upstream flaky (terukur 9 Okt 2026: 40-60% request kena 503 acak,
    # endpoint HTML sehat; run 24 mati persis di get-domain). Ulang
    # singkat menembus hampir pasti; error lain (404/403) tetap raise.
    last = None
    for _attempt in range(6):
        try:
            return urllib.request.urlopen(
                urllib.request.Request(url, headers=h), timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code not in (502, 503, 504, 429):
                raise
            last = e
            say(f"  ⚠ HTTP {e.code} dari API MovieBox "
                f"(percobaan {_attempt + 1}/6) — ulang 5 dtk…")
            time.sleep(5)
    raise last


def mb_ip():
    for svc in ("https://api.ipify.org", "https://icanhazip.com"):
        try:
            return urllib.request.urlopen(
                urllib.request.Request(svc, headers={"User-Agent": "curl/8"}),
                timeout=12).read().decode().strip()
        except Exception:
            pass
    return "?"


def mb_domain():
    r = mb_http(f"{H5}/wefeed-h5api-bff/media-player/get-domain",
                hdrs={"X-Client-Type": "h5"})
    return (json.load(r).get("data") or "").rstrip("/")


def mb_nuxt(html):
    m = (re.search(r"window\.__NUXT_DATA__\s*=\s*(\[.*?\])\s*</script>", html, re.S)
         or re.search(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(\[.*?\])</script>', html, re.S))
    if not m:
        return None
    try:
        arr = json.loads(m.group(1))
    except Exception:
        return None
    memo = {}

    def res(i, depth=0):
        if depth > 40 or not isinstance(i, int) or i < 0 or i >= len(arr):
            return None
        if i in memo:
            return memo[i]
        memo[i] = None
        memo[i] = deref(arr[i], depth)
        return memo[i]

    def deref(v, depth):
        if isinstance(v, dict):
            return {k: (res(x, depth + 1) if isinstance(x, int) else deref(x, depth + 1))
                    for k, x in v.items()}
        if isinstance(v, list):
            return [res(x, depth + 1) if isinstance(x, int) else deref(x, depth + 1)
                    for x in v]
        return v

    return res(1, 0) if len(arr) > 1 else None


def mb_walk(obj, fn, path="r"):
    if isinstance(obj, dict):
        fn(obj, path)
        for k, v in obj.items():
            mb_walk(v, fn, path + "." + str(k))
    elif isinstance(obj, list):
        for n, v in enumerate(obj):
            mb_walk(v, fn, path + f"[{n}]")


def mb_search(kw):
    url = f"{H5}/newWeb/searchResult?keyword={urllib.parse.quote(kw)}"
    html = mb_http(url).read().decode("utf-8", "replace")
    root, out, seen = mb_nuxt(html), [], set()
    if root:
        def fn(o, path):
            if "detailPath" in o and "title" in o and "subjectType" in o:
                dp = str(o.get("detailPath") or "")
                if dp and dp not in seen:
                    seen.add(dp)
                    out.append(o)
        mb_walk(root, fn)
    return out


def mb_url_slug(text):
    """Task 99: deteksi input = URL/slug detail MovieBox → detailPath.
    Terima:
      - https://officialmoviebox.com/moviesDetail/<slug>?id=…&utm… (share)
      - domain apa pun + path moviesDetail/<slug>
      - slug pola MovieBox: kata-kata + sufiks 11-char campuran (harus
        ada huruf besar — kata Inggris biasa tidak akan lolos)
    Return detailPath atau None."""
    t = (text or "").strip()
    m = re.search(r"moviesDetail/([A-Za-z0-9][A-Za-z0-9-]{1,120})", t)
    if m:
        return m.group(1).rstrip("/-")
    if re.fullmatch(r"[a-z0-9][a-z0-9-]{1,120}-[A-Za-z0-9]{11}", t) \
       and re.search(r"[A-Z]", t.split("-")[-1]):
        return t
    return None


def mb_detail_meta(dp):
    """Task 99: metadata halaman detail MovieBox TANPA pencarian —
    untuk judul yang ada di website tapi TIDAK terindeks API pencarian
    (kejadian nyata: 'Take Charge of My Heart' & 'Wildwood[CAM]',
    30 Okt 2026). Kembalikan dict mirip hasil pencarian
    (title/subjectType/releaseDate/hasResource/detailPath) atau None."""
    html = mb_http(f"{H5}/moviesDetail/{dp}").read().decode("utf-8", "replace")
    root = mb_nuxt(html)
    if not root:
        return None
    best = {}

    def fn(o, path):
        if (isinstance(o, dict) and "title" in o and "subjectType" in o
                and str(o.get("detailPath") or "") == dp):
            best.clear()
            best.update(o)
    mb_walk(root, fn)
    if not best:
        return None
    return {"title": best.get("title") or dp,
            "subjectType": best.get("subjectType"),
            "releaseDate": best.get("releaseDate") or "",
            "detailPath": dp,
            "hasResource": best.get("hasResource")}


def mb_play(domain, detail_path, se=0, ep=0):
    url = (f"{domain}/wefeed-h5api-bff/subject/play?subjectId=0"
           f"&se={se}&ep={ep}&detailPath={urllib.parse.quote(detail_path)}")
    r = mb_http(url, referer=f"{domain}/spa/videoPlayPage/movies/{detail_path}",
                hdrs={"x-client-info": XCI, "cookie": f"uuid={MB_UUID}"})
    return json.load(r)


def mb_caption(domain, detail_path, stream):
    url = (f"{domain}/wefeed-h5api-bff/subject/caption?subjectId=0"
           f"&id={stream['id']}&format={stream.get('format') or 'MP4'}"
           f"&detailPath={urllib.parse.quote(detail_path)}")
    r = mb_http(url, referer=f"{domain}/spa/videoPlayPage/movies/{detail_path}",
                hdrs={"x-client-info": XCI, "cookie": f"uuid={MB_UUID}"})
    return json.load(r)


def mb_pick_caption(captions, prefer=None):
    order = list(prefer or ["in_id", "ms", "id", "en"])
    for code in order:
        for c in captions:
            if str(c.get("lan") or "").lower() == str(code).lower():
                return c
    return captions[0] if captions else None


# ---------------- kaskade unduh (anti blokir, tanpa relay) ----------------
def _hdrs_min(dom):
    return {"User-Agent": UA, "Referer": dom.rstrip("/") + "/"}


def _hdrs_full(dom):
    return {
        "User-Agent": UA,
        "Referer": dom.rstrip("/") + "/",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
        "Origin": dom.rstrip("/"),
        "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Sec-Fetch-Dest": "video",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-User": "?1",
    }


def _probe_urllib(url, hdrs):
    try:
        req = urllib.request.Request(
            url, headers={**hdrs, "Range": "bytes=0-1023"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.getcode(), r.headers.get("Content-Type", ""), r.read(1024)
    except urllib.error.HTTPError as e:
        try:
            body = e.read(600)
        except Exception:
            body = b""
        return e.code, e.headers.get("Content-Type", ""), body
    except Exception as e:
        return None, "", repr(e).encode()[:200]


def _probe_curl(url, hdrs):
    if not shutil.which("curl"):
        return None, "", b"(curl tidak tersedia)"
    args = ["curl", "-sS", "--max-time", "30", "-w",
            "\n__CURL__%{http_code} %{content_type}", "-r", "0-1023"]
    for k, v in hdrs.items():
        args += ["-H", f"{k}: {v}"]
    args.append(url)
    try:
        out = subprocess.run(args, capture_output=True, timeout=45).stdout
    except Exception as e:
        return None, "", repr(e).encode()[:200]
    parts = out.rsplit(b"\n__CURL__", 1)
    body = parts[0][:1024]
    if len(parts) == 2:
        code, ctype = parts[1].decode("utf-8", "replace").split(" ", 1)
        try:
            code = int(code)
        except ValueError:
            code = None
        return code, ctype.strip(), body
    return None, "", body


def _dl_urllib(url, hdrs, out_name, target_size):
    pos = os.path.getsize(out_name) if os.path.exists(out_name) else 0
    hdrs = dict(hdrs)
    if pos > 0:
        hdrs["Range"] = f"bytes={pos}-"
    try:
        r = urllib.request.urlopen(
            urllib.request.Request(url, headers=hdrs), timeout=90)
    except urllib.error.HTTPError as e:
        try:
            body = e.read(400).decode("utf-8", "replace")
        except Exception:
            body = ""
        return False, f"HTTP {e.code} {body[:160]}"
    code = getattr(r, "status", None) or r.getcode()
    mode = "ab" if (pos > 0 and code == 206) else "wb"
    if pos > 0 and code != 206:
        pos = 0
    done, mark = pos, -1
    try:
        with open(out_name, mode) as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if done >> 26 != mark:
                    mark = done >> 26
                    pct = (f" ({done*100//target_size}%)" if target_size else "")
                    say(f"     {done/1e6:.0f} MB{pct}")
    except Exception as e:
        return False, f"terputus di {done/1e6:.0f} MB ({e!r:.80})"
    if target_size and done >= target_size:
        return True, ""
    return False, f"terputus di {done/1e6:.0f} MB — sambung ulang"


def _run_cli(args, out_name, target_size):
    p = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                         stderr=subprocess.PIPE)
    last = -1
    while p.poll() is None:
        time.sleep(2)
        try:
            sz = os.path.getsize(out_name)
        except OSError:
            sz = 0
        if sz >> 26 != last:
            last = sz >> 26
            pct = (f" ({sz*100//target_size}%)" if target_size else "")
            say(f"     {sz/1e6:.0f} MB{pct}")
    err = (p.stderr.read() or b"").decode("utf-8", "replace").strip()[-200:]
    return p.returncode, err


def _dl_curl(url, hdrs, out_name, target_size):
    if not shutil.which("curl"):
        return False, "(curl tidak tersedia)"
    base = ["curl", "-sS", "--fail", "--location", "--retry", "3",
            "--retry-delay", "3", "--speed-limit", "1024",
            "--speed-time", "90"]
    for k, v in hdrs.items():
        base += ["-H", f"{k}: {v}"]
    rc, err = _run_cli(base + ["-C", "-", "-o", out_name, url],
                       out_name, target_size)
    if rc == 33:
        try:
            os.remove(out_name)
        except OSError:
            pass
        rc, err = _run_cli(base + ["-o", out_name, url], out_name, target_size)
    if rc == 0 and (not target_size or
                    os.path.getsize(out_name) >= target_size):
        return True, ""
    return False, f"curl exit {rc}: {err[:160]}"


def _dl_wget(url, hdrs, out_name, target_size):
    if not shutil.which("wget"):
        return False, "(wget tidak tersedia)"
    args = ["wget", "-q", "-c", "-O", out_name, "--tries", "5",
            "--timeout", "90"]
    for k, v in hdrs.items():
        args += ["--header", f"{k}: {v}"]
    args.append(url)
    rc, err = _run_cli(args, out_name, target_size)
    if rc == 0 and (not target_size or
                    os.path.getsize(out_name) >= target_size):
        return True, ""
    return False, f"wget exit {rc}: {err[:160]}"


MB_STRATS = [
    ("urllib", _hdrs_min, _probe_urllib, _dl_urllib),
    ("browser", _hdrs_full, _probe_urllib, _dl_urllib),
    ("curl-h2", _hdrs_min, _probe_curl, _dl_curl),
    ("wget", _hdrs_min, _probe_curl, _dl_wget),
]


def _choose_stream(streams, want_res):
    """Pilih stream: exact match resolusi; kalau tidak, tertinggi <= minta."""
    def res_of(s):
        try:
            return int(s.get("resolutions") or 0)
        except (TypeError, ValueError):
            return 0
    if want_res and str(want_res).lower() != "auto":
        try:
            want = int(str(want_res).replace("p", ""))
        except ValueError:
            want = 0
        if want:
            exact = next((s for s in streams if res_of(s) == want), None)
            if exact:
                return exact
            lower = sorted([s for s in streams if res_of(s) < want],
                           key=res_of, reverse=True)
            if lower:
                return lower[0]
            return min(streams, key=res_of)
    return max(streams, key=res_of)


def mb_smart_download(domain, detail_path, se, ep, want_res, out_name,
                      max_refresh=8):
    global MB_STRAT_OK
    host = "?"
    for attempt in range(max_refresh):
        try:
            pj = mb_play(domain, detail_path, se, ep)
        except urllib.error.HTTPError as e:
            say(f"   play HTTP {e.code} — refresh domain…")
            try:
                domain = mb_domain()
            except Exception:
                pass
            time.sleep(2)
            continue
        data = pj.get("data") or {}
        streams = [s for s in (data.get("streams") or []) if not s.get("vipLocked")]
        if not streams or not data.get("hasResource"):
            say("   Tidak ada stream free untuk judul/episode ini.")
            return False
        st = _choose_stream(streams, want_res)
        target_size = int(st.get("size") or 0)
        pos = os.path.getsize(out_name) if os.path.exists(out_name) else 0
        if target_size and pos >= target_size:
            say(f"   {out_name} sudah lengkap ({pos/1e6:.0f} MB) — lewati unduh.")
            return True
        host = urllib.parse.urlparse(st["url"]).netloc
        say(f"   unduh {st.get('resolutions')}p {target_size/1e6:.0f} MB "
            f"dari {host} (lanjut dari {pos/1e6:.1f} MB)…")

        order = ([MB_STRAT_OK] if MB_STRAT_OK
                 else [s[0] for s in MB_STRATS])
        for name in order:
            _, hbuild, probe, dl = next(
                s for s in MB_STRATS if s[0] == name)
            hdrs = hbuild(domain)
            code, ctype, body = probe(st["url"], hdrs)
            if code in (200, 206) and (
                    "video" in (ctype or "").lower()
                    or "mp4" in (ctype or "").lower()
                    or (ctype or "").lower() == "application/octet-stream"):
                ok, msg = dl(st["url"], hdrs, out_name, target_size)
                if ok:
                    MB_STRAT_OK = name
                    say(f"   SELESAI via [{name}]: {out_name} "
                        f"({os.path.getsize(out_name)/1e6:.0f} MB)")
                    return True
                say(f"   [{name}] unduh putus — {msg}")
            else:
                snip = body[:160].decode("utf-8", "replace").replace(
                    "\n", " ").strip()
                line = (f"[{name}] HTTP {code} {ctype or ''} "
                        f"{('— ' + snip) if snip else ''}").rstrip()
                say("   " + line)
                MB_DIAG.append(line)
        time.sleep(2)
    say("Gagal semua strategi. Jawaban CDN terakhir:")
    for d in MB_DIAG[-10:]:
        say("   " + d[:150])
    return False


# ---------------- main ----------------
def fail(msg):
    say("\n❌ " + msg)
    summary_line(f"\n## ❌ GAGAL\n\n{msg}")
    sys.exit(1)


def cleanup_workspace(keep=None):
    """Hapus sisa video dari run sebelumnya — disk Codespace PERSISTEN
    (Codespace di-stop tiap selesai, bukan dihapus) jadi film lama yang
    tak pernah terhapus akan menumpuk sampai disk penuh dan run baru
    ditolak "Disk tidak cukup". File yang jadi target run ini (keep)
    DIPERTAHANKAN agar unduhan setengah-jadi bisa dilanjutkan (resume)
    dan film yang sudah lengkap bisa dipakai ulang tanpa unduh ulang."""
    pats = ["*.mp4", "*.mkv", "*.webm", "*.ts", "*.avi", "*.mov",
            "*.flv", "*.m4v", "*.part", "*.tmp"]
    seen, freed = set(), 0
    for pat in pats:
        for f in glob.glob(pat):
            fa = os.path.abspath(f)
            if fa in seen or (keep and fa == os.path.abspath(keep)):
                continue
            seen.add(fa)
            try:
                sz = os.path.getsize(fa)
                os.remove(fa)
                freed += sz
                say(f"  🧹 hapus sisa lama: {os.path.basename(fa)} "
                    f"({sz/1e6:.0f} MB)")
            except OSError:
                pass
    # hygiene: file parameter run lama (berisi kredensial) kalau ada
    try:
        os.remove("/tmp/zs-params.json")
    except OSError:
        pass
    return freed


# ---------------- bulk (Task 92) ----------------
ABYSS_JWT = None  # login sekali per batch (lazy)


def parse_episode_spec(spec):
    """'1-3,5,7-9' → [1,2,3,5,7,8,9]. Validasi 1..5000, maks 1500 nomor."""
    out = set()
    for part in str(spec or "").replace(" ", "").split(","):
        if not part:
            continue
        m = re.match(r"^(\d{1,4})(?:-(\d{1,4}))?$", part)
        if not m:
            raise ValueError(f"bagian spec tidak valid: '{part}'")
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        if a > b:
            a, b = b, a
        if a < 1 or b > 5000:
            raise ValueError(f"episode harus 1-5000: '{part}'")
        if b - a + 1 > 1500:
            raise ValueError(f"rentang terlalu panjang: '{part}'")
        out.update(range(a, b + 1))
    if not out:
        raise ValueError("spec kosong")
    if len(out) > 1500:
        raise ValueError("maksimal 1500 episode per batch")
    return sorted(out)


def build_episode_spec(eps):
    """[1,2,3,5,7,8,9] → '1-3,5,7-9' (mirror compressEpisodes di CMS)."""
    eps = sorted(set(int(e) for e in eps))
    if not eps:
        return ""
    parts, start, prev = [], eps[0], eps[0]
    for i in range(1, len(eps) + 1):
        cur = eps[i] if i < len(eps) else None
        if cur != prev + 1:
            parts.append(str(start) if prev == start else f"{start}-{prev}")
            start = cur
        prev = cur
    return ",".join(parts)


# ---------------------------------------------------------------------
#  Upload Abyss v2 (Task 96c, 30 Okt): rotasi endpoint + guard sabar +
#  pemulihan "semua byte terkirim tapi jawaban tak datang".
#  Latar: ingress up.abyss.to sempat dicekik <2 KB/dtk (Warkop 3,13 GB
#  gagal 2 run beruntun) sementara ingress kembar up.hydrax.net terukur
#  6x lebih cepat (796 vs 136 KB/dtk) dengan kunci sama; server juga
#  kadang MENERIMA file penuh tapi tak menjawab dalam jendela curl
#  (kasus nyata: 64 MB "timeout" ternyata tersimpan, status Waiting)
#  → pulihkan via katalog api.hydrax.net/{KEY}/list.
# ---------------------------------------------------------------------
ABYSS_UP_ENDPOINTS = (
    "http://up.hydrax.net",    # tercepat terukur (HTTP)
    "https://up.hydrax.net",   # fallback TLS
    "https://up.abyss.to",     # ingress klasik
)


def abyss_list_items():
    """Katalog file akun Abyss (curl + UA; urllib ditolak CF)."""
    out, page = [], 1
    for _ in range(20):
        try:
            p = subprocess.run(
                ["curl", "-sS", "--max-time", "60", "-A", UA,
                 f"https://api.hydrax.net/{ABYSS_KEY}/list?page={page}"],
                capture_output=True, timeout=75)
            d = json.loads(p.stdout or b"{}")
        except Exception:
            break
        items = d.get("items") or []
        out += items
        nxt = int((d.get("pagination") or {}).get("next") or 0)
        if not items or not nxt or nxt <= page:
            break
        page = nxt
    return out


def abyss_upload(file_path, log=lambda s: None):
    """Upload 1 file ke Abyss → dict {status,slug,urlIframe} atau None.
    - rotasi endpoint (hydrax HTTP → hydrax TLS → abyss klasik)
    - guard kematian: putus hanya bila <2 KB/dtk selama 5 MENIT
    - jatah waktu dinamis per ukuran (asumsi pesimistis 350 KB/dtk)
    - pemulihan: bila SEMUA byte terkirim tanpa jawaban, cari slug
      baru di katalog (server lambat memproses ≠ upload gagal)
    """
    size = os.path.getsize(file_path)
    # [Task 103] cek integritas struktur video (log-only, tak pernah memblokir):
    # MP4 → validasi rantai NAL tiap sampel via moov; TS → sync byte 0x47.
    # Diagnosa 1 Nov 2026: sengaja WARNING-ONLY — sumber MovieBox terbukti
    # BERSIH (dugaan korupsi awal ternyata artefak unduhan-terpotong);
    # nilai nyata = telemetri bila kelak ada sumber yang benar-benar rusak.
    try:
        from zs_media_check import check_file as _mc
        rep = _mc(file_path)
        if rep.get("format") in ("mp4", "ts") and rep.get("checked", 0) > 50:
            if rep.get("corrupt"):
                log(f"  ⚠ INTEGRITAS: {rep['corrupt']}/{rep['checked']} sampel rusak "
                    f"(detik ~{rep.get('pts_corrupt', [])[:4]}) — file tetap diunggah, "
                    f"tontonan mungkin beku di titik itu")
            else:
                log(f"  ✓ integritas video OK ({rep['checked']} sampel {rep['format']})")
    except Exception:
        pass
    base = os.path.basename(file_path)
    pre = {it.get("slug") for it in abyss_list_items()}
    # jatah waktu per endpoint (mnt): utama longgar, fallback mengecil —
    # total terburuk ±2 jam utk memberi ruang rotasi dalam jendela run
    for ep, win in zip(ABYSS_UP_ENDPOINTS, (4800, 1800, 900)):
        max_time = min(max(int(size / (350 * 1024)) + 900, 900), win)
        resp = "/tmp/zs-abyss-resp.json"
        cmd = ["curl", "-sS", "-A", UA, "--max-time", str(max_time),
               "--speed-limit", "2048", "--speed-time", "300",
               "-o", resp, "-w", "%{size_upload} %{http_code}",
               "-F", f"file=@{file_path};type=video/mp4",
               f"{ep}/{ABYSS_KEY}"]
        log(f"  upload via {ep} (jatah {max_time // 60} mnt)…")
        try:
            p = subprocess.run(cmd, capture_output=True,
                               timeout=max_time + 120)
        except subprocess.TimeoutExpired:
            p = None
        up, sent = None, 0
        if p is not None:
            w = (p.stdout or b"").decode().split()
            sent = int(w[0]) if w and w[0].isdigit() else 0
            try:
                up = json.loads(open(resp, encoding="utf-8").read())
            except Exception:
                up = None
        if isinstance(up, dict) and up.get("status") and up.get("slug"):
            return up
        if sent >= size:
            log(f"  {sent/1e6:.0f} MB terkirim penuh tanpa jawaban — "
                "poll katalog (server sedang memproses)…")
            for _ in range(8):
                time.sleep(60)
                for it in abyss_list_items():
                    isz = int(it.get("size") or 0)
                    if (it.get("slug") not in pre
                            and it.get("name") == base
                            and isz and abs(isz - size) / size < 0.05):
                        log(f"  PEMULIHAN OK: slug {it['slug']} "
                            "muncul di katalog")
                        return {"status": True, "slug": it["slug"],
                                "urlIframe": ("https://player."
                                              f"abyssplayer.com/{it['slug']}")}
        log(f"  gagal via {ep} (exit {p.returncode if p else 'TO'}, "
            f"{sent/1e6:.0f}/{size/1e6:.0f} MB) — endpoint berikutnya…")
    return None


def register_one(slug, ep, res, size, sub_text, sub_lan, is_tv, retries=4):
    """Register SATU episode ke katalog situs. Body membawa 'episode' —
    dipakai token scope-series; token per-episode mengabaikannya (aman).
    Return dict jawaban situs; 'retry'=True → Abyss masih mem-proses."""
    if not TOKEN:
        return {"ok": True, "skip": "token-kosong"}
    subs = []
    if sub_text:
        subs = [{"lang": sub_lan or "Indonesia",
                 "filename": f"{lang_slug(sub_lan)}.srt",
                 "content": sub_text, "srt": True}]
    body = {"slug": slug, "subs": subs, "quality": f"{res}p", "size": size}
    if is_tv:
        body["episode"] = ep
    req = urllib.request.Request(
        f"{REGISTER}?token={urllib.parse.quote(TOKEN)}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "User-Agent": "zs-actions/1.0"})
    reg = {}
    for attempt in range(retries):
        try:
            reg = json.load(urllib.request.urlopen(req, timeout=120))
        except Exception as e:
            reg = {"ok": False, "error": repr(e)[:200]}
        if reg.get("ok") or not reg.get("retry"):
            return reg
        say(f"  Abyss masih mem-proses — tunggu 60 dtk, ulang register "
            f"({attempt + 1}/{retries})…")
        time.sleep(60)
    return reg


def process_episode(domain, dp, se, ep, judul, tahun, is_tv):
    """Satu episode: resolve → unduh → subtitle → upload → register → CC.
    Return dict (jangan sys.exit — pemanggil mencatat hasil & lanjut)."""
    pj = mb_play(domain, dp, se, ep)
    data = pj.get("data") or {}
    streams = [s for s in (data.get("streams") or []) if not s.get("vipLocked")]
    if not data.get("hasResource") or not streams:
        return {"ep": ep, "ok": False, "error": "tidak ada stream free"}
    stream = _choose_stream(streams, QUALITY)
    res = stream.get("resolutions")
    target_size = int(stream.get("size") or 0)
    if is_tv:
        FILE = f"{judul} S{se:02d}E{ep:02d} [{res}p].mp4"
    else:
        FILE = f"{judul} ({tahun}) [{res}p].mp4"
    FILE = re.sub(r'[<>:"/\\|?*]', "", FILE).strip()
    say(f"  {FILE} — {target_size/1e6:.0f} MB, unduh…")

    st_disk = shutil.disk_usage(".")
    if target_size * 1.15 > st_disk.free:
        return {"ep": ep, "ok": False,
                "error": f"disk kurang (butuh {target_size*1.15/1e9:.1f} GB, "
                         f"sisa {st_disk.free/1e9:.1f} GB)"}

    if not mb_smart_download(domain, dp, se, ep, res, FILE):
        try:
            os.remove(FILE)
        except OSError:
            pass
        return {"ep": ep, "ok": False, "error": "unduh gagal semua strategi"}
    size = os.path.getsize(FILE)
    say(f"  unduh OK ({size/1e6:.0f} MB) — subtitle…")

    # subtitle resmi MovieBox (best-effort, per episode)
    sub_text, sub_lan = None, None
    if SUB_LANG:
        try:
            caps = ((mb_caption(domain, dp, stream).get("data") or {})
                    .get("captions") or [])
            cap = mb_pick_caption(caps, prefer=[SUB_LANG])
            if cap and cap.get("url"):
                raw = mb_http(cap["url"], timeout=90).read()
                sub_text = raw.decode("utf-8-sig", "replace")
                sub_lan = cap.get("lanName") or cap.get("lan")
                if "-->" not in sub_text:
                    sub_text = None
        except Exception as e:
            say("  subtitle dilewati:", repr(e)[:90])

    # upload ke Abyss (Task 96c: rotasi endpoint + pemulihan katalog)
    say("  upload ke Abyss…")
    up = abyss_upload(FILE, log=say)
    if not (isinstance(up, dict) and up.get("status")):
        try:
            os.remove(FILE)
        except OSError:
            pass
        return {"ep": ep, "ok": False,
                "error": f"upload Abyss ditolak ({up or 'tanpa jawaban'})"}
    slug = up["slug"]
    # bebaskan disk SEGERA (file aman di Abyss — pola v1.2.1)
    try:
        os.remove(FILE)
    except OSError:
        pass
    say(f"  upload OK: slug {slug} — file lokal dihapus")

    # register ke katalog situs
    reg = register_one(slug, ep, res, size, sub_text, sub_lan, is_tv)
    r = {"ep": ep, "ok": True, "slug": slug, "res": res, "size": size,
         "sub_lan": sub_lan,
         "reg_ok": bool(reg.get("ok")) and not reg.get("skip"),
         "reg_skip": reg.get("skip"),
         "reg_error": None if reg.get("ok") else str(reg.get("error"))[:160]}
    if reg.get("retry"):
        r["reg_pending"] = True
        r["sub_text"] = sub_text  # utk ronde retry di akhir batch
    else:
        say("  register:", "OK" if reg.get("ok")
            else f"GAGAL — {str(reg.get('error'))[:120]}")

    # CC native Abyss (best-effort; login sekali per batch)
    global ABYSS_JWT
    if sub_text and ABYSS_EMAIL and ABYSS_PASSWORD:
        try:
            if ABYSS_JWT is None:
                ABYSS_JWT = abyss_login(ABYSS_EMAIL, ABYSS_PASSWORD)
            if ABYSS_JWT:
                lang = (sub_lan or "Indonesia").strip()
                code = abyss_put_subtitle(
                    ABYSS_JWT, slug, lang, f"{lang_slug(lang)}.vtt",
                    srt_to_vtt(sub_text))
                r["sub_player_ok"] = code == 200
        except Exception as e:
            say("  CC player Abyss dilewati:", repr(e)[:90])
    return r


def run_bulk(domain, dp, sel, is_tv, eps):
    """Loop utama mode bulk: satu episode per iterasi dengan budget chunk.
    Marker ZS_BULK_REMAIN:<spec> dibaca runner utk antre chunk lanjutan."""
    judul = sel.get("title") or "series"
    tahun = (sel.get("releaseDate") or "")[:4]
    se = SEASON
    say(f"\nMODE BULK: {len(eps)} episode (S{se}, judul '{judul}') — "
        f"budget chunk {BUDGET_MIN} menit")
    summary_line(f"- Mode: **BULK** — {len(eps)} episode S{se} "
                 f"(chunk ±{BUDGET_MIN} menit)")

    results, remaining, t0 = [], [], time.time()
    for i, ep in enumerate(eps):
        if i > 0 and (time.time() - t0) > BUDGET_MIN * 60:
            remaining = eps[i:]
            say(f"\n⏸ Budget chunk tercapai ({BUDGET_MIN} mnt) — "
                f"{len(remaining)} episode ditunda ke chunk berikutnya.")
            break
        say(f"\n=== EPISODE {ep} ({i + 1}/{len(eps)}) ===")
        try:
            r = process_episode(domain, dp, se, ep, judul, tahun, is_tv)
        except urllib.error.HTTPError as e:
            try:
                domain = mb_domain()
            except Exception:
                pass
            r = {"ep": ep, "ok": False, "error": f"play API HTTP {e.code}"}
        except Exception as e:
            r = {"ep": ep, "ok": False, "error": repr(e)[:160]}
        results.append(r)
        if r.get("ok"):
            say(f"  ✔ E{ep} SELESAI — slug {r.get('slug')}")
        else:
            say(f"  ✘ E{ep} GAGAL: {r.get('error')}")

    # ronde retry register yang masih "diproses Abyss" (max 3x60 dtk)
    pend = [r for r in results if r.get("reg_pending")]
    for r in pend:
        reg = register_one(r["slug"], r["ep"], r["res"], r["size"],
                           r.get("sub_text"), r.get("sub_lan"), is_tv,
                           retries=3)
        r["reg_ok"] = bool(reg.get("ok"))
        r.pop("reg_pending", None)
        r.pop("sub_text", None)
        r["reg_error"] = None if reg.get("ok") else str(reg.get("error"))[:160]
        say(f"  register ulang E{r['ep']}: "
            + ("OK" if r["reg_ok"] else "masih diproses Abyss"))

    ok_n = sum(1 for r in results if r.get("ok"))
    reg_n = sum(1 for r in results if r.get("reg_ok"))
    fail_n = len(results) - ok_n

    say("\n" + "=" * 58)
    say(f"BULK: {ok_n} episode terupload OK, {reg_n} terdaftar di situs, "
        f"{fail_n} gagal" + (f", {len(remaining)} ditunda" if remaining else ""))
    for r in results:
        if r.get("ok"):
            say(f"  E{r['ep']}: OK slug={r.get('slug')} {r.get('res')}p"
                + ("  [register tertunda — ulang via slug_lama]"
                   if r.get("reg_error") and not r.get("reg_ok") else ""))
        else:
            say(f"  E{r['ep']}: GAGAL — {r.get('error')}")
    summary_line(f"- Hasil chunk: {ok_n} upload OK, {reg_n} register OK, "
                 f"{fail_n} gagal")
    if fail_n:
        gagal = build_episode_spec([r["ep"] for r in results if not r.get("ok")])
        summary_line(f"- Gagal (jalankan ulang dengan episodes `{gagal}`): "
                     f"`{gagal}`")

    if remaining:
        spec = build_episode_spec(remaining)
        say(f"\nSISA {len(remaining)} episode → chunk berikutnya otomatis.")
        say(f"ZS_BULK_REMAIN:{spec}")
        summary_line(f"- Chunk berikutnya (otomatis): episodes `{spec}`")
    say("=" * 58)

    if results and ok_n == 0 and not remaining:
        sys.exit(1)


# ================= SOURCE LK21 (Task 96) =================
# ZeeStream punya 2 sumber: MovieBox (utama, subtitle resmi) & LK21
# (alternatif — katalog Indonesia, subtitle hardsub/SRT situs).
# Seluruh tahap upload Abyss + register + CC native DIPAKAI BERSAMA.

def lk21_download(got, out_name):
    """Kaskade strategi unduh zs_dl utk URL LK21 (referer khusus per host).
    Return True bila file lengkap (target size bila diketahui)."""
    # [Task 97] global WAJIB: tanpa ini assignment "MB_STRAT_OK = …" di
    # bawah menjadikannya variabel LOKAL → pembacaan awal (order=…) kena
    # UnboundLocalError (run manual #41005479615 — jalur ini tak pernah
    # tersentuh pipeline Byse/HLS sebelumnya, makanya laten).
    global MB_STRAT_OK
    ref = got.get("referer") or "https://tv.nontonfilm.red/"
    target = int(got.get("size") or 0)
    base = {"User-Agent": UA, "Referer": ref,
            "Accept": "*/*"}
    order = (["urllib", "curl-h2", "wget"] if not MB_STRAT_OK
             else [MB_STRAT_OK, "urllib", "curl-h2", "wget"])
    seen = set()
    for name in order:
        if name in seen:
            continue
        seen.add(name)
        hdrs = dict(base)
        probe = _probe_curl if name in ("curl-h2", "wget") else _probe_urllib
        dl = {"urllib": _dl_urllib, "curl-h2": _dl_curl,
              "wget": _dl_wget}[name]
        code, ctype, body = probe(got["url"], hdrs)
        if code in (200, 206) and (
                "video" in (ctype or "").lower()
                or "mp4" in (ctype or "").lower()
                or "octet-stream" in (ctype or "").lower()
                or (name == "wget" and code == 200)):
            ok, msg = dl(got["url"], hdrs, out_name, target)
            if ok:
                MB_STRAT_OK = name if name in (
                    "urllib", "curl-h2", "wget") else MB_STRAT_OK
                say(f"   SELESAI via [{name}]: {out_name} "
                    f"({os.path.getsize(out_name)/1e6:.0f} MB)")
                return True
            say(f"   [{name}] unduh putus — {msg}")
        else:
            snip = (body or b"")[:120].decode("utf-8", "replace").replace(
                "\n", " ").strip()
            say(f"   [{name}] HTTP {code} {ctype or ''} {snip}")
    return False


def lk21_hls_download(got, out_name):
    """Task 96b — unduh HLS Byse (segmen .ts multi-thread → satu file).
    Return True bila semua segmen lengkap."""
    try:
        peta = lk21.hls_map(got["hls"], referer=got.get("referer"))
    except Exception as e:
        say(f"   ✘ peta HLS gagal: {repr(e)[:110]}")
        return False
    segs = peta["segments"]
    if not segs:
        say("   ✘ playlist tanpa segmen")
        return False
    say(f"   {len(segs)} segmen HLS — unduh 6-thread…")
    last = [-1]
    t0 = time.time()

    def prog(done, total):
        pct = done * 100 // total
        if pct != last[0] and (pct % 5 == 0 or done == total):
            last[0] = pct
            mb = os.path.getsize(out_name) / 1e6 if os.path.exists(out_name) else 0
            el = time.time() - t0
            say(f"      {pct:3d}% ({done}/{total} seg, {el:.0f}s)")

    ok, msg = lk21.download_hls(segs, out_name + ".part",
                                referer=got.get("referer"), workers=6,
                                log=None, progress=prog)
    if not ok:
        say(f"   ✘ HLS: {msg}")
        try:
            os.remove(out_name + ".part")
        except OSError:
            pass
        return False
    os.replace(out_name + ".part", out_name)
    say(f"   SELESAI [HLS]: {out_name} "
        f"({os.path.getsize(out_name)/1e6:.0f} MB)")
    return True


def lk21_process_one(dom, sel, ep, is_tv, base_det=None):
    """LK21 satu target: resolve → unduh → sub SRT → upload → register.
    base_det = hasil lk_detail halaman series (96d: berisi link episode
    path penuh eps/… — dipakai supaya URL episode tidak salah bangun).
    Return dict hasil (tidak sys.exit — pemanggil melanjutkan)."""
    judul, tahun = sel.get("title") or "film", sel.get("year") or ""
    try:
        if is_tv:
            target = None
            for e in ((base_det or {}).get("episode_links") or []):
                m_ep = re.search(r"episode-(\d+)$", e)
                if m_ep and int(m_ep.group(1)) == ep:
                    target = e
                    break
            if target is None:
                slug_page = re.sub(r"-episode-\d+$", "", sel["slug"])
                target = f"{slug_page}-episode-{ep}"
            det = lk21.lk_detail(target, dom)
        else:
            det = lk21.lk_detail(sel["slug"], dom)
    except Exception as e:
        return {"ep": ep, "ok": False,
                "error": f"halaman detail gagal: {repr(e)[:100]}"}
    got = lk21.resolve_from_detail(
        det, QUALITY, log=lambda *a: say("  ", *a))
    if not got:
        return {"ep": ep, "ok": False,
                "error": "tak ada host LK21 yang terekspos server-side "
                         "(ouo murni/JS-gated) — coba sumber MovieBox"}
    is_byse = got.get("engine") == "byse"
    try:
        res = int(re.sub(r"[^0-9]", "", str(got.get("quality") or "")) or 480)
    except Exception:
        res = 480
    size_est = int(got.get("size") or 0)
    if is_tv:
        FILE = f"{judul} S{SEASON:02d}E{ep:02d} [{res}p].mp4"
    else:
        FILE = f"{judul} ({tahun}) [{res}p].mp4"
    FILE = re.sub(r'[<>:"/\\|?*]', "", FILE).strip()
    say(f"  {FILE} — dari {got['source_page'][:70]}")
    if is_byse:
        say(f"   player Byse → HLS {got.get('quality')} " +
            (f"≈{size_est/1e6:.0f} MB, " if size_est else "") +
            f"{got.get('n_segments', '?')} segmen")
    else:
        say(f"   unduh {res}p " +
            (f"≈{size_est/1e6:.0f} MB" if size_est else "(ukuran tak diketahui)")
            + "…")

    if size_est * 1.15 > shutil.disk_usage(".").free:
        return {"ep": ep, "ok": False,
                "error": f"disk kurang (butuh ≈{size_est*1.15/1e9:.1f} GB)"}

    if is_byse:
        ok = lk21_hls_download(got, FILE)
    else:
        ok = lk21_download(got, FILE)
    if not ok:
        try:
            os.remove(FILE)
        except OSError:
            pass
        return {"ep": ep, "ok": False, "error": "unduh gagal semua strategi"}
    size = os.path.getsize(FILE)
    say(f"  unduh OK ({size/1e6:.0f} MB) — subtitle…")

    # subtitle — V2: SRT bawaan Byse (sub WebVTT Indonesia dari HLS)
    # lebih dulu; baru SRT link situs (lawas)
    sub_text, sub_lan = None, None
    if SUB_LANG:
        if got.get("sub_srt") and "-->" in got["sub_srt"]:
            sub_text = got["sub_srt"]
            sub_lan = "Indonesia"
            say(f"  subtitle bawaan Byse: {len(sub_text)} karakter")
        else:
            sub_url = got.get("sub_url")
            if sub_url:
                try:
                    raw = lk21.lk_http(sub_url, timeout=60).read()
                    sub_text = raw.decode("utf-8-sig", "replace")
                    if "-->" not in sub_text:
                        sub_text = None
                    else:
                        sub_lan = "Indonesia"
                        say(f"  subtitle LK21: {len(sub_text)} karakter")
                except Exception as e:
                    say("  subtitle LK21 dilewati:", repr(e)[:80])
            else:
                say("  tanpa subtitle terpisah — isi Byse umumnya punya "
                    "track Indonesia di player")

    # upload ke Abyss (Task 96c: rotasi endpoint + pemulihan katalog)
    say("  upload ke Abyss…")
    up = abyss_upload(FILE, log=say)
    if not (isinstance(up, dict) and up.get("status")):
        try:
            os.remove(FILE)
        except OSError:
            pass
        return {"ep": ep, "ok": False,
                "error": f"upload Abyss ditolak ({up or 'tanpa jawaban'})"}
    slug = up["slug"]
    try:
        os.remove(FILE)
    except OSError:
        pass
    say(f"  upload OK: slug {slug} — file lokal dihapus")

    reg = register_one(slug, ep if is_tv else 1, res, size,
                       sub_text, sub_lan, is_tv)
    r = {"ep": ep, "ok": True, "slug": slug, "res": res, "size": size,
         "sub_lan": sub_lan,
         "reg_ok": bool(reg.get("ok")) and not reg.get("skip"),
         "reg_skip": reg.get("skip"),
         "reg_error": None if reg.get("ok") else str(reg.get("error"))[:160]}
    if reg.get("retry"):
        r["reg_pending"] = True
        r["sub_text"] = sub_text
    else:
        say("  register:", "OK" if reg.get("ok")
            else f"GAGAL — {str(reg.get('error'))[:120]}")

    global ABYSS_JWT
    if sub_text and ABYSS_EMAIL and ABYSS_PASSWORD:
        try:
            if ABYSS_JWT is None:
                ABYSS_JWT = abyss_login(ABYSS_EMAIL, ABYSS_PASSWORD)
            if ABYSS_JWT:
                lang = (sub_lan or "Indonesia").strip()
                code = abyss_put_subtitle(
                    ABYSS_JWT, slug, lang, f"{lang_slug(lang)}.vtt",
                    srt_to_vtt(sub_text))
                r["sub_player_ok"] = code == 200
        except Exception as e:
            say("  CC player Abyss dilewati:", repr(e)[:90])
    return r


def run_bulk_lk21(dom, sel, eps, base_det=None):
    """Bulk LK21 (series): pola sama dgn run_bulk MovieBox — satu episode
    per iterasi + budget chunk + marker ZS_BULK_REMAIN utk chunk lanjutan."""
    judul = sel.get("title") or "series"
    se = SEASON
    say(f"\nMODE BULK LK21: {len(eps)} episode (S{se}, judul '{judul}') — "
        f"budget chunk {BUDGET_MIN} menit")
    summary_line(f"- Mode: **BULK LK21** — {len(eps)} episode S{se} "
                 f"(chunk ±{BUDGET_MIN} menit)")
    results, remaining, t0 = [], [], time.time()
    for i, ep in enumerate(eps):
        if i > 0 and (time.time() - t0) > BUDGET_MIN * 60:
            remaining = eps[i:]
            say(f"\n⏸ Budget chunk tercapai ({BUDGET_MIN} mnt) — "
                f"{len(remaining)} episode ditunda ke chunk berikutnya.")
            break
        say(f"\n=== EPISODE {ep} ({i + 1}/{len(eps)}) ===")
        try:
            r = lk21_process_one(dom, sel, ep, True, base_det)
        except Exception as e:
            r = {"ep": ep, "ok": False, "error": repr(e)[:160]}
        results.append(r)
        if r.get("ok"):
            say(f"  ✔ E{ep} SELESAI — slug {r.get('slug')}")
        else:
            say(f"  ✘ E{ep} GAGAL: {r.get('error')}")

    pend = [r for r in results if r.get("reg_pending")]
    for r in pend:
        reg = register_one(r["slug"], r["ep"], r["res"], r["size"],
                           r.get("sub_text"), r.get("sub_lan"), True,
                           retries=3)
        r["reg_ok"] = bool(reg.get("ok"))
        r.pop("reg_pending", None)
        r.pop("sub_text", None)
        r["reg_error"] = None if reg.get("ok") else str(reg.get("error"))[:160]
        say(f"  register ulang E{r['ep']}: "
            + ("OK" if r["reg_ok"] else "masih diproses Abyss"))

    ok_n = sum(1 for r in results if r.get("ok"))
    reg_n = sum(1 for r in results if r.get("reg_ok"))
    fail_n = len(results) - ok_n
    say("\n" + "=" * 58)
    say(f"BULK LK21: {ok_n} episode terupload OK, {reg_n} terdaftar, "
        f"{fail_n} gagal" + (f", {len(remaining)} ditunda" if remaining else ""))
    for r in results:
        if r.get("ok"):
            say(f"  E{r['ep']}: OK slug={r.get('slug')} {r.get('res')}p")
        else:
            say(f"  E{r['ep']}: GAGAL — {r.get('error')}")
    summary_line(f"- Hasil chunk LK21: {ok_n} upload OK, {reg_n} register OK, "
                 f"{fail_n} gagal")
    if remaining:
        spec = build_episode_spec(remaining)
        say(f"\nSISA {len(remaining)} episode → chunk berikutnya otomatis.")
        say(f"ZS_BULK_REMAIN:{spec}")
        summary_line(f"- Chunk berikutnya (otomatis): episodes `{spec}`")
    say("=" * 58)
    if results and ok_n == 0 and not remaining:
        sys.exit(1)


def main_lk21(from_mb_fallback=False):
    """Alur sumber LK21 (Task 96d): cari PINTAR (multi-query + skor
    relevansi — LK21 menampilkan daftar unggahan terbaru saat judul tak
    ada, dan judul series hidup di skema /tv/ + /eps/ yang dulu tak
    terbaca) → pilih → (film | series tunggal/bulk). Tidak ada hasil
    relevan → fallback OTOMATIS ke MovieBox."""
    if REUSE_SLUG:
        fail("slug_lama (register-saja) hanya untuk sumber moviebox — "
             "LK21 tidak punya koneksi slug lama.")
    egress = mb_ip()
    say(f"ZS pipeline — SUMBER LK21\nIP egress: {egress}\n")
    summary_line(f"# ZS Download (LK21) — {TITLE}")
    summary_line(f"- IP runner: `{egress}` | sumber: LK21")
    dom = lk21.lk_domain()
    results, used_q = lk21.lk_find(TITLE, dom, log=lambda *a: say(" ", *a))
    relevant = [it for it in results if it.get("score", 0) >= 0.6]
    say(f"Hasil pencarian LK21 '{TITLE}' "
        f"(query efektif: '{used_q}'): {len(results)} judul — "
        f"{len(relevant)} relevan"
        + (f", {len(results) - len(relevant)} di bawah ambang (sembunyikan "
            "dari pilihan)" if results else ""))
    listing = ["\n## Hasil pencarian LK21\n"]
    for i, it in enumerate(relevant[:20]):
        jenis = ("SERIES" if it.get("kind") == "tv"
                  else f"EP{it.get('ep') or '?'}" if it.get("kind") == "episode"
                  else "Film")
        line = (f"[{i:>2}] {jenis:<7} {it.get('title', '?')} "
                f"({it.get('year', '-')}) — skor {it.get('score', 0)}")
        say("  " + line)
        listing.append(f"- `{i}` **{it.get('title', '?')}** "
                       f"({it.get('year', '-')}) — skor {it.get('score')}")
    if len(results) > len(relevant):
        say(f"  (+{len(results) - len(relevant)} hasil tak relevan "
            "tidak ditawarkan)")
        listing.append(f"- _{len(results) - len(relevant)} hasil tak relevan "
                       "disembunyikan (skor < 0.6)_")
    summary_line("\n".join(listing))

    if not relevant:
        if from_mb_fallback:
            fail(f"Judul '{TITLE}' TIDAK ditemukan di LK21 maupun MovieBox."
                 "\nCoba kata kunci lain — judul Inggris resmi (TMDB) "
                 "biasanya paling akurat.")
        say("\n🔁 LK21 tidak punya judul ini — lanjut OTOMATIS dengan "
            "sumber MOVIEBOX…")
        summary_line("- LK21: 0 hasil relevan → **fallback MovieBox**")
        return run_moviebox(from_lk_fallback=True)
    if PICK < 0 or PICK >= len(relevant):
        fail(f"Pilihan indeks {PICK} di luar daftar LK21 "
             f"(0..{len(relevant)-1}) — lihat daftar di atas.")
    if PICK == 0:
        # pilihan otomatis: mode bulk (series) → utamakan /tv/ + episode;
        # mode film → utamakan film.
        want_series = bool(EPISODES_SPEC)
        prio = ("tv episode" if want_series else " tv episode").split()
        prio = {k: n for n, k in enumerate(prio)}
        prio.setdefault("", 2 if want_series else 0)
        sel_i = min(
            range(len(relevant)),
            key=lambda i: (-relevant[i].get("score", 0),
                           prio.get(relevant[i].get("kind") or "", 3),
                           relevant[i].get("ep") or 99))
        if sel_i != 0:
            say(f"   pilihan otomatis [0] → indeks {sel_i} "
                f"({relevant[sel_i]['title']} — paling relevan utk mode "
                f"{'series' if want_series else 'film'})")
        PICK_EFF = sel_i
    else:
        PICK_EFF = PICK
    sel = relevant[PICK_EFF]
    say(f"\nTerpilih: {sel['title']} ({sel['year']}) — "
        f"skor {sel.get('score')}")

    # halaman dasar: kalau terpilih post-episode (eps/…), naikkan ke
    # halaman series /tv/ utk daftar episode lengkap; kalau /tv/ tidak
    # ada, pakai halaman eps itu sendiri (biasanya juga memuat link
    # episode lain).
    base_slug = sel["slug"]
    base_det = None
    if sel.get("kind") == "episode":
        cand = re.sub(r"^eps/", "tv/",
                      re.sub(r"-episode-\d+$", "", base_slug))
        try:
            cand_det = lk21.lk_detail(cand, dom)
            if cand_det.get("episode_links"):
                base_slug, base_det = cand, cand_det
                say(f"   halaman series: {cand} "
                    f"({len(cand_det['episode_links'])} episode)")
        except Exception:
            pass
    if base_det is None:
        try:
            base_det = lk21.lk_detail(base_slug, dom)
        except Exception as e:
            fail(f"Halaman detail LK21 gagal dibuka: {repr(e)[:120]}")
    is_tv = bool(base_det.get("episode_links")) or \
        sel.get("kind") in ("tv", "episode")
    eps_n = len(base_det.get("episode_links") or [])
    say("Tipe:", "SERIES" if is_tv else "Film",
        f"({eps_n} episode di LK21)" if is_tv else "")

    if EPISODES_SPEC:
        if not is_tv:
            fail("Hasil terpilih bertipe FILM — input 'episodes' (bulk) "
                 "hanya untuk SERIES. Jalankan ulang TANPA episodes utk "
                 "film, atau pilih indeks hasil bertipe SERIES/EP di atas.")
        try:
            eps = parse_episode_spec(EPISODES_SPEC)
        except ValueError as e:
            fail(f"Input 'episodes' tidak valid: {e}")
        run_bulk_lk21(dom, sel, eps, base_det)
        return

    ep = EPISODE if is_tv else 1
    eps_avail = sorted(int(m.group(1)) for e in base_det["episode_links"]
                       for m in [re.search(r"episode-(\d+)$", e)] if m)
    if is_tv and eps_avail and ep not in eps_avail:
        fail(f"Episode {ep} tidak ada di LK21 "
             f"(tersedia: {eps_avail[:12]}{'…' if len(eps_avail) > 12 else ''}).")
    r = lk21_process_one(dom, sel, ep, is_tv, base_det)
    if not r.get("ok"):
        fail(f"Gagal: {r.get('error')} — LK21 punya batasan host; "
             "coba sumber moviebox untuk judul ini.")
    if r.get("reg_pending"):
        reg = register_one(r["slug"], r["ep"], r["res"], r["size"],
                           r.get("sub_text"), r.get("sub_lan"), is_tv,
                           retries=3)
        r["reg_ok"] = bool(reg.get("ok"))
    say("\n" + "=" * 58)
    if r.get("reg_ok"):
        say("SUKSES! Judul TERSEDIA di Server 3 situs (sumber LK21).")
    else:
        say(f"SELESAI (upload OK). Slug: {r.get('slug')} — register: "
            f"{r.get('reg_error') or 'token kosong/lupa'}")
    say("=" * 58)


# =====================================================================
#  Task 97 — SUMBER MANUAL: link file langsung (GDrive/Mega/URL mp4)
#  =====================================================================
#  LATAR: portal unduh resmi LK21 (dadadidi.de/get/…, identitas
#  INDEXMOVIE.BIZ) menyimpan file 1080p (Google Share / Mega / Cast /
#  Telegram). Gerbang portal ADALAH Cloudflare Turnstile interaktif —
#  headless browser teruji gagal error 600010 berkali-kali (2026-10-30),
#  sedangkan player streaming Byse utk judul yang sama hanya 720p
#  (diverifikasi via API playback: sources = [720p] saja).
#  SOLUSI: user membuka portal di browser ponsel (manusia lolos
#  Turnstile dengan sekali klik), menyalin link final Google Share /
#  Mega, menempelnya di panel Admin → pipeline mengambil file dari
#  host tujuan (GDrive/Mega TIDAK digerbang) → Abyss → register.

_MEGA_ERR = {
    -1: "kesalahan internal Mega", -2: "argumen Mega tidak valid",
    -3: "request Mega ditolak sementara (coba lagi)",
    -4: "kuota transfer Mega habis untuk IP ini (tunggu ±6 jam)",
    -5: "file Mega tidak ditemukan (dihapus / link mati)",
    -6: "akses file Mega ditolak",
    -9: "file Mega melebihi batas",
    -14: "link Mega tidak valid",
    -16: "akses Mega ditolak (kunci salah?)",
    -19: "kuota transfer Mega habis untuk IP ini (tunggu ±6 jam)",
    -21: "login diperlukan (file privat)",
}


def _mega_b64d(s):
    """Base64url Mega (tanpa padding) → bytes."""
    import base64
    s = s.rstrip("=")
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def gdrive_file_id(link):
    """Ekstrak ID file dari semua bentuk link Google Drive umum."""
    m = re.search(r"/file/d/([a-zA-Z0-9_-]{10,})", link)
    if m:
        return m.group(1)
    m = re.search(r"[?&]id=([a-zA-Z0-9_-]{10,})", link)
    if m:
        return m.group(1)
    return None


def gdrive_resolve(link, log=None):
    """Link Google Drive → (url unduh langsung, ukuran). Tangani:
    redirect modern ke drive.usercontent.google.com + form confirm
    "tidak bisa memindai virus" (file > ±100 MB, hidden inputs
    id/export/confirm/uuid), kuota habis, dan file privat."""
    fid = gdrive_file_id(link)
    if not fid:
        raise RuntimeError("bentuk link Google Drive tidak dikenali — "
                           "pakai 'Copy link' dari halaman file Drive")
    url = f"https://drive.google.com/uc?export=download&id={fid}"
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            url, headers={"User-Agent": UA}), timeout=90)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Google Drive HTTP {e.code} — file dihapus / "
                           "link salah / akses ditolak")
    ctype = (r.headers.get("Content-Type") or "").lower()
    if "text/html" not in ctype:
        size = int(r.headers.get("Content-Length") or 0)
        direct, r0 = r.geturl(), r
        r0.close()
        return direct, size
    html = r.read(150000).decode("utf-8", "replace")
    r.close()
    if re.search(r"exceed|melebihi|kuota|quota|terlalu banyak", html, re.I):
        raise RuntimeError("kuota Google Drive file ini habis (file "
                           "populer — coba link Google Share lain / "
                           "tunggu 24 jam)")
    if re.search(r"unable to open|cannot open|akses tidak|permintaan "
                 r"akses|denied", html, re.I):
        raise RuntimeError("file Drive privat / akses ditolak — buka "
                           "dulu di browser dan pastikan bisa diunduh")
    for _round in range(2):
        act = re.search(r'action="([^"]+)"', html)
        params = dict(re.findall(r'name="([^"]+)" value="([^"]*)"', html))
        if not act or not params:
            raise RuntimeError("halaman confirm GDrive berubah — "
                               "laporkan ke admin (screenshot log)")
        dest = act.group(1).replace("&amp;", "&")
        q = urllib.parse.urlencode(params)
        get = urllib.request.Request(
            dest + ("&" if "?" in dest else "?") + q,
            headers={"User-Agent": UA})
        try:
            r2 = urllib.request.urlopen(get, timeout=90)
        except urllib.error.HTTPError as e:
            if e.code not in (400, 405, 501):
                raise RuntimeError(f"GDrive HTTP {e.code} saat confirm")
            post = urllib.request.Request(
                dest, data=q.encode(),
                headers={"User-Agent": UA,
                         "Content-Type": "application/x-www-form-urlencoded"})
            r2 = urllib.request.urlopen(post, timeout=90)
        ctype2 = (r2.headers.get("Content-Type") or "").lower()
        if "text/html" not in ctype2:
            size = int(r2.headers.get("Content-Length") or 0)
            direct, r0 = r2.geturl(), r2
            r0.close()
            if log:
                log("  GDrive confirm lolos → usercontent.google.com")
            return direct, size
        html = r2.read(150000).decode("utf-8", "replace")
        r2.close()
    raise RuntimeError("GDrive tetap meminta confirm setelah 2 putaran — "
                       "buka link di browser, unduh manual sekali, lalu "
                       "pakai provider lain (Mega/Cast)")


def mega_parse(link):
    """Link mega.(nz|io) → (file_id, key_b64). Bentuk:
    /file/<id>#<key> (baru) dan #!<id>!<key> (klasik)."""
    m = re.search(r"mega(?:\.nz|\.io)/file/"
                  r"([a-zA-Z0-9]{8})#([a-zA-Z0-9_-]{43})", link)
    if m:
        return m.group(1), m.group(2)
    m = re.search(r"mega(?:\.nz|\.io)/#!([a-zA-Z0-9]{8})!([a-zA-Z0-9_-]{43})",
                  link)
    if m:
        return m.group(1), m.group(2)
    return None, None


def mega_api(payload, fid=None):
    """Satu panggilan API Mega (/cs) → elemen pertama. Error = int negatif.
    Task 98: endpoint lama /w kini menjawab KOSONG (MEGA menutupnya —
    run user 30 Okt crash JSONDecodeError); endpoint hidup =
    POST /cs?id=1&n={fid}. PENTING: minta URL unduh dgn {"g":1} TANPA
    "v":2 — flag v:2 mengaktifkan mode msd (multi-source: array 6 URL
    segmen ±156 MB yang mustahil dipakai langsung); tanpa v:2 server
    memberi SATU URL file penuh (terverifikasi: CL == size, Range OK,
    dekrip CTR byte-0 = header ZIP 'PK' 0x03 0x04)."""
    url = "https://g.api.mega.co.nz/cs?id=1"
    if fid:
        url += "&n=" + urllib.parse.quote(fid)
    req = urllib.request.Request(
        url, data=json.dumps([payload]).encode(), method="POST",
        headers={"Content-Type": "application/json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            raw = r.read().decode("utf-8", "replace")
    except Exception as e:
        raise RuntimeError(f"API Mega tak terjangkau: {repr(e)[:100]}")
    try:
        d = json.loads(raw)
    except Exception:
        raise RuntimeError("API Mega menjawab bukan-JSON ('"
                           + raw[:60].replace("\n", " ") + "')")
    if isinstance(d, list):
        if d and isinstance(d[0], int) and d[0] < 0:
            return d[0]
        return d[0] if d else {}
    return d


def mega_resolve(link, log=None):
    """Link Mega → {url, size, name, key, nonce}. Uraikan kunci AES dari
    fragmen URL (bentuk 43 char = 32 byte ter-XOR), dekrip atribut nama
    file via AES-ECB, minta URL unduh via API 'g'."""
    fid, key_b64 = mega_parse(link)
    if not fid:
        raise RuntimeError("link Mega tidak dikenali / tanpa kunci #… — "
                           "salin link utuh dari portal (mega.nz/file/…#…)")
    info = mega_api({"a": "g", "g": 1, "p": fid, "ssl": 2}, fid=fid)
    if isinstance(info, int):
        raise RuntimeError(_MEGA_ERR.get(info, f"Mega error {info}"))
    if isinstance(info, dict) and isinstance(info.get("g"), list):
        info["g"] = info["g"][0] if info["g"] else None  # Task 98: g = array
    if not isinstance(info, dict) or not info.get("g"):
        raise RuntimeError("Mega tidak memberi URL unduh (file mati / "
                           "kuota habis — coba link host lain)")
    raw = _mega_b64d(key_b64)
    if len(raw) != 32:
        raise RuntimeError(f"kunci Mega aneh ({len(raw)} byte)")
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    # Task 98 — algoritma resmi (diverifikasi vs megajs + data nyata):
    # kunci file 16 byte = XOR dua half kunci URL; nonce CTR = byte
    # 16-24 kunci URL (half MAC — tak tersentuh unwrap). Kode lama
    # memakai AES-256-ECB self-decrypt → kunci/nonce SALAH → file hasil
    # dekrip berisi sampah (belum pernah tervalidasi dgn file nyata).
    key = bytes(a ^ b for a, b in zip(raw[:16], raw[16:32]))
    nonce = raw[16:24]
    name = None
    try:
        at = _mega_b64d(info.get("at") or "")
        # atribut = AES-128-CBC, IV nol, tanpa padding (bukan ECB —
        # blok-1 memang identik ECB itulah kenapa prefix MEGA{ dulu
        # "kelihatan benar" lalu sisanya rusak)
        dec = Cipher(algorithms.AES(key), modes.CBC(b"\x00" * 16)) \
            .decryptor().update(at)
        blob = dec.split(b"\x00")[0].decode("utf-8", "replace").strip()
        if blob.startswith("MEGA"):
            blob = blob[4:]
        meta = json.loads(blob) if blob.startswith("{") else {}
        name = str(meta.get("n") or meta.get("name") or "") or None
    except Exception:
        pass
    if not name:
        try:
            m = re.search(rb'"n"\s*:\s*"((?:[^"\\]|\\.)*)"', dec)
            if m:
                name = m.group(1).decode("utf-8", "replace")
        except Exception:
            pass
    if log and name:
        log(f"  Mega: file '{name[:60]}' "
            f"({int(info.get('s') or 0)/1e6:.0f} MB)")
    return {"url": info["g"], "size": int(info.get("s") or 0),
            "name": name, "key": key, "nonce": nonce}


def mega_download(link, out_name, log=lambda s: None):
    """Unduh + dekrip CTR Mega → out_name. Stream 1 MB; AES-CTR nonce
    8 byte + counter 8 byte nol (konvensi Mega — increment 128-bit
    identik untuk < 2^64 blok)."""
    r = mega_resolve(link, log=log)
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    dec = Cipher(algorithms.AES(r["key"]),
                 modes.CTR(r["nonce"] + b"\x00" * 8)).decryptor()
    total, done, t0, last = r["size"], 0, time.time(), -1
    log(f"  unduh+dekrip Mega ≈{total/1e6:.0f} MB…")
    try:
        resp = urllib.request.urlopen(urllib.request.Request(
            r["url"], headers={"User-Agent": UA}), timeout=120)
    except Exception as e:
        raise RuntimeError(f"unduh Mega gagal: {repr(e)[:100]}")
    with resp, open(out_name + ".part", "wb") as f:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            f.write(dec.update(chunk))
            done += len(chunk)
            pct = done * 100 // max(1, total)
            if pct != last and (pct % 10 == 0 or done == total):
                last = pct
                el = time.time() - t0
                log(f"      {pct:3d}% ({done/1e6:.0f} MB, "
                    f"{done/1e6/max(el,.1):.0f} MB/s)")
    if total and done < total:
        try:
            os.remove(out_name + ".part")
        except OSError:
            pass
        raise RuntimeError(f"unduh Mega terpotong ({done}/{total} byte)")
    os.replace(out_name + ".part", out_name)
    log(f"   SELESAI [Mega]: {out_name} ({done/1e6:.0f} MB)")
    return r.get("name")


def manual_parse_links(raw):
    """Task 98 — pecah input link JAMAK (newline / spasi / koma) → list
    URL http(s) maks 40 baris (duplikat DIPERTAHANKAN — jumlah baris
    dipakai mode pasangan link↔episode). Semua link portal bisa
    ditempel sekaligus; pipeline mencoba berurutan sampai berhasil."""
    parts = re.split(r"[\s,]+", str(raw or "").strip())
    out = []
    for p in parts:
        p = p.strip().strip('"').strip("'")
        low = p[:8].lower()
        if low == "https://" or p[:7].lower() == "http://":
            out.append(p)
    if len(out) > 40:
        raise ValueError(f"maksimal 40 link per run (terdeteksi {len(out)})")
    return out


def manual_provider(url):
    """Deteksi provider satu link manual (untuk log + prioritas)."""
    low = url.lower()
    if "mega.nz" in low or "mega.io" in low or "mega.co.nz" in low:
        return "MEGA"
    if "drive.google.com" in low or "docs.google.com" in low:
        return "GDRIVE"
    if "gofile." in low:
        return "GOFILE"
    if "t.me/" in low or "telegram." in low:
        return "TELEGRAM"
    return "LANGSUNG"


_MANUAL_RANK = {"LANGSUNG": 0, "MEGA": 1, "GDRIVE": 2, "TELEGRAM": 3,
                "GOFILE": 4}


def manual_order(links):
    """Urutkan kandidat: host langsung (Cast/Vikingfile/dl) dulu, lalu
    Mega, Drive; Telegram/Gofile terakhir (pasti gagal server-side).
    sort stabil → urutan tempel user terjaga dalam tiap kelompok."""
    return sorted(links, key=lambda u: _MANUAL_RANK.get(manual_provider(u), 9))


def manual_try_link(url, out_name, log=lambda s: None):
    """Coba SATU link → (True, nama_asli, None) | (False, None, pesan).
    File sisa dibersihkan sebelum mencoba supaya part lama tak menipu."""
    for f in (out_name, out_name + ".part"):
        try:
            os.remove(f)
        except OSError:
            pass
    prov = manual_provider(url)
    log(f"  [{prov}] {url[:95]}")
    try:
        if prov == "MEGA":
            name = mega_download(url, out_name, log=log)
            return True, name, None
        if prov == "GDRIVE":
            direct, size_est = gdrive_resolve(url, log=log)
            need = max(size_est * 1.15, 200 * 1024 * 1024)
            if need > shutil.disk_usage(".").free:
                return False, None, (f"disk kurang (butuh \u2248"
                                     f"{need/1e9:.1f} GB)")
            got = {"url": direct, "referer": None,
                   "size": size_est, "label": "gdrive"}
            if lk21_download(got, out_name):
                return True, None, None
            return False, None, ("GDrive gagal semua strategi (kuota "
                                "habis / file privat?)")
        if prov == "TELEGRAM":
            return False, None, ("link Telegram bukan file langsung — "
                                 "pakai tombol host file di portal "
                                 "(Cast / Vikingfile / Mega / Google Share)")
        if prov == "GOFILE":
            return False, None, ("Gofile mewajibkan unduhan lewat browser "
                                 "(token JS) — pakai link Cast / Vikingfile "
                                 "/ Mega / Google Share dari portal")
        got = {"url": url, "referer": None, "size": 0, "label": "direct"}
        if lk21_download(got, out_name):
            return True, None, None
        return False, None, "URL langsung gagal (link mati / butuh sesi browser?)"
    except RuntimeError as e:
        return False, None, str(e)[:180]
    except Exception as e:
        return False, None, repr(e)[:180]


def maybe_unzip(out_name, log=lambda s: None):
    """Task 98 — portal LK21 mengemas file Mega sebagai ZIP ('….mp4.zip',
    entri STORED). Bila file hasil unduh = ZIP, bongkar dan ambil anggota
    video terbesar → ganti out_name. Return True bila dibongkar."""
    try:
        with open(out_name, "rb") as f:
            head = f.read(4)
    except OSError:
        return False
    if head != b"PK\x03\x04":
        return False
    import zipfile
    log("  hasil unduh = ZIP — membongkar…")
    try:
        zf = zipfile.ZipFile(out_name)
        best, best_sz = None, -1
        for it in zf.infolist():
            if it.is_dir() or it.file_size <= best_sz:
                continue
            best, best_sz = it, it.file_size
        if best is None:
            raise RuntimeError("zip kosong")
        log(f"  ambil '{(best.filename or '')[:60]}' "
            f"({best_sz/1e6:.0f} MB)…")
        tmp = out_name + ".unzip"
        with zf.open(best) as src, open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"bongkar ZIP gagal: {repr(e)[:120]}")
    os.replace(tmp, out_name)
    return True


def manual_download_and_upload(links, out_name, log=lambda s: None):
    """Coba daftar link BERURUTAN sampai satu berhasil → (slug, size).
    Bila SEMUA link gagal → RuntimeError berisi ringkasan per-link."""
    errs = []
    for i, u in enumerate(links):
        if len(links) > 1:
            log(f"[link {i + 1}/{len(links)}]")
        ok, name, err = manual_try_link(u, out_name, log)
        if not ok:
            errs.append(f"{manual_provider(u)}: {err}")
            log(f"  \u2718 {err}")
            continue
        try:
            maybe_unzip(out_name, log)
        except RuntimeError as ez:
            try:
                os.remove(out_name)
            except OSError:
                pass
            errs.append(f"{manual_provider(u)}: {ez}")
            log(f"  \u2718 {ez}")
            continue
        size = os.path.getsize(out_name)
        if size < 512 * 1024:
            try:
                os.remove(out_name)
            except OSError:
                pass
            errs.append(f"{manual_provider(u)}: file hanya {size} byte "
                        "(bukan video)")
            log("  \u2718 file hasil bukan video — coba link lain")
            continue
        log(f"  unduh OK ({size/1e6:.0f} MB)"
            + (f" — '{(name or '')[:55]}'" if name else ""))
        log("  upload ke Abyss\u2026")
        up = abyss_upload(out_name, log=log)
        try:
            os.remove(out_name)
        except OSError:
            pass
        if not (isinstance(up, dict) and up.get("status")):
            raise RuntimeError(f"upload Abyss ditolak ({up or 'tanpa jawaban'}) "
                               "— jalankan ulang run ini (link sudah "
                               "terbukti hidup)")
        if errs:
            log(f"  (tercapai setelah {len(errs)} link sebelumnya gagal)")
        return up["slug"], size
    raise RuntimeError("SEMUA link gagal:\n  - " + "\n  - ".join(errs[:8])
                       + ("\n  \u2026" if len(errs) > 8 else ""))


def _manual_file_name(judul, is_tv, ep, res):
    base = (f"{judul} S{SEASON:02d}E{ep:02d} [{res}p]" if is_tv
            else f"{judul} [{res}p]")
    return re.sub(r'[<>:"/\\|?*]', "", base).strip() + ".mp4"


def manual_bulk_pairs(links, eps, judul, res):
    """Task 98 — MODE PASANGAN: baris link ke-n \u2194 episode ke-n.
    Loop per episode dengan budget chunk; sisa ditunda via marker
    ZS_BULK_REMAIN + ZS_LINK_REMAIN (b64) dan di-antrekan otomatis."""
    say(f"\nMODE BULK MANUAL: {len(eps)} episode \u00d7 1 link masing-masing "
        f"— budget chunk {BUDGET_MIN} menit")
    summary_line(f"- Mode: **BULK manual** — {len(eps)} episode "
                 f"(baris link ke-n \u2192 episode ke-n)")
    results, t0 = [], time.time()
    rem_links = rem_eps = None
    for i, (u, ep) in enumerate(zip(links, eps)):
        if i > 0 and (time.time() - t0) > BUDGET_MIN * 60:
            rem_links, rem_eps = links[i:], eps[i:]
            say(f"\n\u23f8 Budget chunk tercapai ({BUDGET_MIN} mnt) — "
                f"{len(rem_eps)} episode ditunda ke chunk berikutnya.")
            break
        say(f"\n=== EPISODE {ep} ({i + 1}/{len(eps)}) ===")
        FILE = _manual_file_name(judul, True, ep, res)
        r = {"ep": ep, "ok": False, "error": "", "reg_pending": False}
        try:
            slug, size = manual_download_and_upload([u], FILE, say)
            r.update(ok=True, slug=slug, size=size)
            reg = register_one(slug, ep, res, size, None, None, True,
                               retries=3)
            if reg.get("skip"):
                r["reg_ok"] = None
            elif reg.get("ok"):
                r["reg_ok"] = True
            elif reg.get("retry"):
                r["reg_pending"] = True
            else:
                r["reg_ok"] = False
                r["reg_error"] = str(reg.get("error"))[:160]
        except RuntimeError as e:
            r["error"] = str(e)[:220]
        except Exception as e:
            r["error"] = repr(e)[:220]
        results.append(r)
        say(f"  \u2714 E{ep} OK slug {r.get('slug')}" if r.get("ok")
            else f"  \u2718 E{ep} GAGAL: {r['error'][:130]}")

    # ronde ulang register yang masih "diproses Abyss"
    for r in [x for x in results if x.get("reg_pending")]:
        reg = register_one(r["slug"], r["ep"], res, r["size"], None, None,
                           True, retries=3)
        r["reg_ok"] = bool(reg.get("ok"))
        r.pop("reg_pending", None)
        r["reg_error"] = None if reg.get("ok") else str(reg.get("error"))[:160]
        say(f"  register ulang E{r['ep']}: "
            + ("OK" if r["reg_ok"] else "masih diproses Abyss"))

    ok_n = sum(1 for r in results if r.get("ok"))
    reg_n = sum(1 for r in results if r.get("reg_ok"))
    fail_ep = [r["ep"] for r in results if not r.get("ok")]
    say("\n" + "=" * 58)
    say(f"BULK MANUAL: {ok_n} episode terupload, {reg_n} terdaftar, "
        f"{len(fail_ep)} gagal")
    for r in results:
        if r.get("ok"):
            say(f"  E{r['ep']}: OK slug={r.get('slug')} {res}p"
                + ("  [register TERTUNDA — ulang via slug_lama]"
                   if r.get("reg_ok") is False else ""))
        else:
            say(f"  E{r['ep']}: GAGAL — {r['error'][:110]}")
    if fail_ep:
        spec_f = build_episode_spec(fail_ep)
        say(f"Episode gagal: {spec_f} — tempel link BARU utk episode itu "
            "lalu jalankan lagi (token series masih hidup 72 jam).")
        summary_line(f"- Gagal (link baru + episodes `{spec_f}`): `{spec_f}`")
    if rem_eps:
        spec = build_episode_spec(rem_eps)
        blob = base64.b64encode(json.dumps(rem_links).encode()).decode()
        say(f"\nSISA {len(rem_eps)} episode \u2192 chunk berikutnya otomatis.")
        say(f"ZS_BULK_REMAIN:{spec}")
        say(f"ZS_LINK_REMAIN:{blob}")
        summary_line(f"- Chunk berikutnya (otomatis): episodes `{spec}`")
    say("=" * 58)
    if results and ok_n == 0 and not rem_eps:
        sys.exit(1)


def main_manual():
    """Alur sumber MANUAL (Task 97, dirombak Task 98): tempel link file
    final dari portal LK21 — BOLEH BANYAK SEKALIGUS (satu per baris).
    - Film / 1 episode: semua link = kandidat, dicoba berurutan sampai
      ada yang berhasil (host gagal otomatis dilewati).
    - Series banyak episode: jumlah baris link HARUS = jumlah episode;
      baris ke-n dipasangkan ke episode ke-n (di-chunk otomatis)."""
    egress = mb_ip()
    say(f"ZS pipeline — SUMBER MANUAL multi-link\nIP egress: {egress}\n")
    summary_line(f"# ZS Download (Manual) — {TITLE}")
    if not LINK:
        fail("Input 'link' kosong — tempel link file (Google Share / "
             "Mega / URL mp4; boleh beberapa baris sekaligus).")
    try:
        links = manual_parse_links(LINK)
    except ValueError as e:
        fail(str(e))
    is_tv = TIPE == "tv"
    eps = []
    if EPISODES_SPEC:
        try:
            eps = parse_episode_spec(EPISODES_SPEC)
        except ValueError as e:
            fail(f"input 'episodes' tidak valid: {e}")
    judul = (TITLE or "Film").strip()
    m_res = re.search(r"(\d{3,4})", QUALITY or "")
    res = int(m_res.group(1)) if m_res else 1080
    say(f"Judul: {judul} | tipe: {'SERIES' if is_tv else 'Film'} | "
        f"link ditempel: {len(links)}")
    if eps:
        say(f"Episode diminta: {build_episode_spec(eps)} ({len(eps)})")
    cleanup_workspace()

    # ---- mode pasangan: series banyak episode ----
    if is_tv and len(eps) > 1:
        if len(links) != len(eps):
            fail(f"Jumlah baris link ({len(links)}) TIDAK SAMA dengan "
                 f"jumlah episode ({len(eps)}). Aturan: baris link ke-n "
                 "\u2192 episode ke-n. Pilihan: (1) tempel link sesuai "
                 "jumlah episode, atau (2) pilih 1 episode saja — semua "
                 "link jadi kandidat cadangan.")
        return manual_bulk_pairs(links, eps, judul, res)

    # ---- mode tunggal: film / satu episode; semua link = kandidat ----
    ep = eps[0] if eps else EPISODE
    FILE = _manual_file_name(judul, is_tv, ep, res)
    say(f"File target: {FILE}")
    if len(links) > 1:
        ordered = manual_order(links)
        say("Urutan coba: "
            + " \u2192 ".join(manual_provider(u) for u in ordered))
    else:
        ordered = links
    try:
        slug, size = manual_download_and_upload(ordered, FILE, say)
    except RuntimeError as e:
        fail(str(e))
    say(f"  upload OK: slug {slug} — file lokal dihapus")
    reg = register_one(slug, ep if is_tv else 1, res, size, None, None,
                       is_tv, retries=3)
    say("\n" + "=" * 58)
    if reg.get("skip"):
        say(f"SELESAI (upload OK, [{res}p]). Slug: {slug} — register "
            "dilewati: token kosong. Isi token di panel lalu pakai "
            "mode register-saja (slug_lama) untuk mendaftarkan.")
    elif reg.get("ok"):
        say("SUKSES! Judul TERSEDIA di Server 3 situs (sumber manual "
            f"[{res}p]).")
    else:
        say(f"SELESAI (upload OK). Slug: {slug} — register GAGAL: "
            f"{str(reg.get('error'))[:120]}")
    say("=" * 58)


def mb_rank(results, query):
    """Task 96d: skor relevansi hasil MovieBox vs query — pick [0] tidak
    boleh asal (mis. 'Agent Kim Reactivated' pernah cocok ke 'The Legend
    of Kitchen Soldier'). Kembalikan (ranked, relevant)."""
    for it in results:
        probe = {"title": str(it.get("title") or ""),
                 "slug": str(it.get("detailPath") or ""),
                 "year": (str(it.get("releaseDate") or ""))[:4]}
        it["score"] = round(lk21._score(query, probe), 2)
    ranked = sorted(results, key=lambda x: -x["score"])
    return ranked, [x for x in ranked if x.get("score", 0) >= 0.6]


def run_moviebox(from_lk_fallback=False):
    """Alur sumber MovieBox (dipanggil langsung ATAU sbg fallback LK21).
    Task 96d: hasil diskor relevansi; pick [0] = ter-relevan; 0 hasil
    relevan → fallback OTOMATIS ke LK21.
    Task 99: input judul boleh URL detail MovieBox (moviesDetail/…)
    → LOMPAT pencarian — judul yang tidak terindeks API pencarian
    tetap bisa diunduh; halaman tanpa file video → fallback LK21."""
    egress = mb_ip()
    say(f"ZS pipeline — runner GitHub (Azure)\nIP egress: {egress}\n")
    summary_line(f"# ZS Download — {TITLE}")
    summary_line(f"- IP runner: `{egress}`")

    # Task 99 — input = URL detail MovieBox → LANGSUNG pakai detailPath
    # (judul ada di website tapi tidak muncul di hasil pencarian)
    url_dp = None if from_lk_fallback else mb_url_slug(TITLE)
    if url_dp:
        sel = mb_detail_meta(url_dp)
        if not sel:
            fail(f"Halaman detail MovieBox '{url_dp}' tidak terbaca — "
                 "buka link-nya di browser untuk memastikan masih hidup.")
        say("🔗 Input = URL detail MovieBox → LOMPAT pencarian "
            "(indeks pencarian MovieBox memang tidak punya judul ini)")
        summary_line(f"- Input: URL detail MovieBox `{url_dp}` "
                     "(lompat pencarian)")
    else:
        # 1. cari judul
        results = mb_search(TITLE)
        if not results:
            if from_lk_fallback:
                fail(f"Judul '{TITLE}' tidak ditemukan di LK21 maupun "
                     "MovieBox — coba kata kunci lain.")
            say("MovieBox: 0 hasil — lanjut OTOMATIS dengan sumber LK21…")
            summary_line("- MovieBox: 0 hasil → **fallback LK21**")
            return main_lk21(from_mb_fallback=True)
        results, relevant = mb_rank(results, TITLE)
        say(f"Hasil pencarian MovieBox '{TITLE}': {len(results)} judul — "
            f"{len(relevant)} relevan")
        listing = ["\n## Hasil pencarian\n"]
        for i, it in enumerate(results[:20]):
            year = (it.get("releaseDate") or "")[:4]
            jenis = "SERIAL" if it.get("subjectType") == 2 else "Film"
            flag = "" if it.get("hasResource") is not False else " [belum ada video]"
            line = (f"[{i:>2}] {it.get('score', 0):.2f} {jenis:<6} "
                    f"{it.get('title', '?')} ({year}){flag}")
            say("  " + line)
            listing.append(f"- `{i}` {jenis} **{it.get('title', '?')}** "
                           f"({year}) — skor {it.get('score', 0)}{flag}")
        summary_line("\n".join(listing))

        if not relevant:
            if from_lk_fallback:
                fail(f"Judul '{TITLE}' tidak ditemukan relevan di LK21 maupun "
                     "MovieBox. Hasil pencarian hanya judul lain yang mirip — "
                     "cek ejaan / pakai judul Inggris resmi.")
            say("\n🔁 MovieBox tidak punya judul ini (0 hasil relevan) — "
                "lanjut OTOMATIS dengan sumber LK21…")
            summary_line("- MovieBox: 0 hasil relevan → **fallback LK21**")
            return main_lk21(from_mb_fallback=True)

        if PICK < 0 or PICK >= len(results):
            fail(f"Pilihan indeks {PICK} di luar daftar (0..{len(results)-1}). "
                 "Lihat daftar di atas, jalankan ulang workflow dengan 'pilihan' "
                 "yang benar.")
        if PICK == 0:
            idx = results.index(relevant[0])
            if idx != 0:
                say(f"   pilihan otomatis [0] → indeks {idx} "
                    f"({relevant[0].get('title')} — paling relevan)")
            sel = relevant[0]
        else:
            sel = results[PICK]
            if sel.get("score", 0) < 0.6:
                say(f"   ⚠ indeks {PICK} skor rendah ({sel.get('score')}) — "
                    "dipakai apa adanya karena dipilih eksplisit.")
        say(f"\nTerpilih: {sel.get('title')} "
            f"({(sel.get('releaseDate') or '')[:4]}) — "
            f"{'SERIAL' if sel.get('subjectType') == 2 else 'Film'}")

    DP = str(sel["detailPath"])
    is_tv = (sel.get("subjectType") == 2)
    judul = sel.get("title") or "film"
    tahun = (sel.get("releaseDate") or "")[:4]

    # 2. resolve stream
    DOMAIN = mb_domain()
    say("Domain video:", DOMAIN)

    # Task 92 — MODE BULK: banyak episode dalam satu dispatch (TV saja).
    # Sisa episode di luar budget chunk di-antrekan otomatis oleh runner.
    if EPISODES_SPEC:
        if not is_tv:
            fail("Input 'episodes' (bulk) hanya untuk SERIAL — pilih hasil "
                 "pencarian bertipe SERIAL dengan indeks 'pilihan' yang benar.")
        try:
            eps = parse_episode_spec(EPISODES_SPEC)
        except ValueError as e:
            fail(f"Input 'episodes' tidak valid: {e}")
        run_bulk(DOMAIN, DP, sel, True, eps)
        return

    se = SEASON if is_tv else 0
    ep = EPISODE if is_tv else 0
    try:
        pj = mb_play(DOMAIN, DP, se, ep)
    except urllib.error.HTTPError as e:
        fail(f"play API HTTP {e.code} — judul/episode mungkin tidak ada. "
             f"Cek musim/episode, lalu jalankan ulang.")
    data = pj.get("data") or {}
    streams = [s for s in (data.get("streams") or []) if not s.get("vipLocked")]
    if not data.get("hasResource") or not streams:
        # Task 99: halaman detail ADA tapi file videonya belum diupload
        # MovieBox (placeholder — kejadian 'Wildwood[CAM]'). Jangan asal
        # gagal: coba LK21 dgn judul bersih (tanpa tag [CAM] dsb).
        bersih = re.sub(r"\[[^\]]*\]", "", judul).strip() or judul
        if not from_lk_fallback:
            say(f"\n⚠ Halaman MovieBox judul '{judul}' ADA tapi belum "
                "punya file video (placeholder / masih diproses).")
            say("   Lanjut OTOMATIS dengan sumber LK21…")
            summary_line("- MovieBox: halaman ada, 0 stream → "
                         "**fallback LK21**")
            globals()["TITLE"] = bersih
            return main_lk21(from_mb_fallback=True)
        fail(f"Halaman MovieBox judul '{judul}' belum punya file video "
             "(placeholder) dan LK21 juga tidak menyediakannya. Coba lagi "
             "nanti, atau pakai sumber manual (link portal LK21).")
    say("Kualitas tersedia: " + ", ".join(
        f"{s.get('resolutions')}p" for s in streams))
    stream = _choose_stream(streams, QUALITY)
    res = stream.get("resolutions")

    if is_tv:
        FILE = f"{judul} S{SEASON:02d}E{EPISODE:02d} [{res}p].mp4"
    else:
        FILE = f"{judul} ({tahun}) [{res}p].mp4"
    FILE = re.sub(r'[<>:"/\\|?*]', "", FILE).strip()
    say(f"\nFile tujuan: {FILE}  (kualitas {res}p, "
        f"{int(stream.get('size') or 0)/1e6:.0f} MB)")

    # MODE REGISTER-SAJA (v1.2.3): input slug_lama terisi → film SUDAH
    # ada di Abyss dari run sebelumnya (mis. lupa isi token) → lewati
    # cek disk + unduh; register pakai slug lama. Subtitle tetap diambil.
    reuse = REUSE_SLUG.strip()
    if reuse:
        slug = reuse
        size = int(stream.get("size") or 0)
        up = {"slug": slug,
              "urlIframe": f"https://player.abyssplayer.com/{slug}"}
        say(f"\n🔁 MODE REGISTER-SAJA — slug lama: {slug}")
        say("   unduh + upload DILEWATI: film sudah aman di Abyss dari")
        say("   run sebelumnya. Kualitas/size akan diisi otomatis dari")
        say("   Abyss saat register. Subtitle resmi tetap diambil.")
        summary_line(f"- Mode: register ulang slug `{slug}` (tanpa unduh)")
    else:
        # ruang disk Codespace — bersihkan sisa film run sebelumnya DULU
        # (disk Codespace persisten antar-run; tanpa ini film lama menumpuk
        # sampai cek "Disk tidak cukup" menolak film baru)
        freed = cleanup_workspace(keep=FILE)
        if freed:
            say(f"  🧹 total {freed/1e9:.2f} GB dibebaskan dari sisa run "
                "sebelumnya")
            summary_line(f"- Disk: 🧹 {freed/1e9:.2f} GB sisa lama "
                         "dibersihkan otomatis")
        st_disk = shutil.disk_usage(".")
        need = int(stream.get("size") or 0) * 1.15
        if need > st_disk.free:
            fail(f"Disk Codespace tidak cukup (butuh {need/1e9:.1f} GB, "
                 f"sisa {st_disk.free/1e9:.1f} GB setelah sisa lama "
                 "dibersihkan) — pilih resolusi lebih rendah.")

        # 3. unduh video
        ok = mb_smart_download(DOMAIN, DP, se, ep, res, FILE)
        if not ok:
            fail("Unduhan gagal — lihat jawaban CDN di log. Kalau 426/427, "
                 "jaringan runner GitHub juga diblokir (kabar buruk — "
                 "hubungi Super Z untuk strategi lain).")
        size = os.path.getsize(FILE)
        say(f"\nVideo siap: {FILE} ({size/1e9:.2f} GB)")
        summary_line(f"- File: `{FILE}` ({size/1e9:.2f} GB, {res}p)")

    # 4. subtitle resmi MovieBox (best-effort)
    sub_text, sub_lan = None, None
    if SUB_LANG:
        try:
            caps = ((mb_caption(DOMAIN, DP, stream).get("data") or {})
                    .get("captions") or [])
            cap = mb_pick_caption(caps, prefer=[SUB_LANG])
            if cap and cap.get("url"):
                raw = mb_http(cap["url"], timeout=90).read()
                sub_text = raw.decode("utf-8-sig", "replace")
                sub_lan = cap.get("lanName") or cap.get("lan")
                if "-->" not in sub_text:
                    say(f"Subtitle {sub_lan} didapat tapi format aneh — dilewati.")
                    sub_text = None
                else:
                    say(f"Subtitle {sub_lan}: {len(sub_text)} karakter")
            else:
                say("Subtitle bahasa itu tidak tersedia. Yang ada: "
                    + (", ".join(str(c.get("lan")) for c in caps) or "(tidak ada)"))
        except Exception as e:
            say("Subtitle gagal (dilewati):", repr(e)[:120])
    else:
        say("Unduh subtitle dimatikan.")

    # 5. upload ke Abyss (curl multipart, 3x percobaan) — DILEWATI bila
    #    mode register-saja (slug lama: film sudah ada di Abyss)
    if reuse:
        say("(upload Abyss + hapus file lokal dilewati — mode register-saja)")
    else:
        if not os.path.exists(FILE):
            fail("File hilang setelah unduh — error aneh, cek log.")
        say(f"\nMengunggah {FILE} ({size/1e9:.2f} GB) ke Abyss…")
        up = abyss_upload(FILE, log=say)
        if not (isinstance(up, dict) and up.get("status")):
            fail(f"Upload Abyss ditolak: {up or 'tidak ada jawaban JSON'}")
        slug = up["slug"]
        say("Slug Abyss:", slug, "→", up.get("urlIframe"))
        summary_line(f"- Slug Abyss: `{slug}`")
        summary_line(f"- Player: {up.get('urlIframe', '')}")

        # 5.5 BEBASKAN DISK SEGERA: film sudah aman di Abyss (slug keluar =
        # upload diterima; langkah berikutnya — tunggu transcode, register,
        # pasang subtitle — hanya butuh metadata/teks di memori, BUKAN file
        # lokal). File lokal dihapus SEKARANG supaya storage Codespace tidak
        # pernah menumpuk (permintaan pemilik: hapus begitu upload selesai).
        # Register ulang di kemudian hari: isi input slug_lama — TANPA
        # mengunduh ulang (v1.2.3).
        try:
            os.remove(FILE)
            say(f"\n🧹 File lokal dihapus ({size/1e9:.2f} GB dibebaskan) — "
                "film aman di Abyss.")
            summary_line(f"- Disk: 🧹 file lokal dihapus segera setelah "
                         f"upload ({size/1e9:.2f} GB) — storage Codespace "
                         "tetap ramping")
        except OSError as e:
            say("Gagal menghapus file lokal:", repr(e)[:100])

    # 6. tunggu transcode ready (maks 15 menit)
    say("\nMenunggu Abyss men-transcode (maks 15 menit)…")
    ready = False
    for i in range(45):
        try:
            fi = json.load(urllib.request.urlopen(
                f"https://api.abyss.to/v1/files/{slug}?key={ABYSS_KEY}",
                timeout=60))
        except Exception:
            fi = {}
        if fi.get("status") == "ready":
            say("READY ✔ resolutions:", fi.get("resolutions"))
            ready = True
            break
        if i % 6 == 0:
            say(f"  status: {fi.get('status', '?')}… ({i*20} detik)")
        time.sleep(20)
    if not ready:
        say("⚠ Transcode belum selesai dalam 15 menit — register tetap "
            "dilakukan; cek status nanti di dashboard Abyss.")

    # 7. register ke katalog ZeeStream (kalau token diberikan)
    reg_ok = False
    if TOKEN:
        subs = []
        if sub_text:
            subs = [{"lang": sub_lan or "Indonesia",
                     "filename": f"{lang_slug(sub_lan)}.srt",
                     "content": sub_text, "srt": True}]
        reg_body = {"slug": slug, "subs": subs}
        if not reuse:
            # mode unduh: kualitas/size dari unduhan nyata. Mode
            # register-saja: dibiarkan kosong → diisi otomatis dari
            # Abyss saat register (lebih otoritatatif).
            reg_body["quality"] = f"{res}p"
            reg_body["size"] = size
        body = json.dumps(reg_body).encode()
        req = urllib.request.Request(
            f"{REGISTER}?token={urllib.parse.quote(TOKEN)}", data=body,
            headers={"Content-Type": "application/json",
                     "User-Agent": "zs-actions/1.0"})
        # retry bila Abyss masih mem-proses (jawaban 202/"still
        # processing" — v1.2.3): tunggu 60 dtk lalu ulang, maks 4x
        for reg_try in range(4):
            try:
                reg = json.load(urllib.request.urlopen(req, timeout=120))
            except Exception as e:
                reg = {"ok": False, "error": repr(e)[:200]}
            if reg.get("ok") or not reg.get("retry"):
                break
            say(f"  Abyss masih mem-proses (jawaban: {reg.get('error')}) "
                f"— tunggu 60 dtk, ulang register ({reg_try + 1}/4)…")
            time.sleep(60)
        say("Jawaban situs:", reg)
        reg_ok = bool(reg.get("ok"))
        if reg_ok:
            summary_line("- Register: ✅ BERHASIL — judul masuk Server 3")
        else:
            summary_line(f"- Register: ❌ {reg.get('error')}")
    else:
        say("\nTOKEN register tidak diisi — film SUDAH terupload ke Abyss.")
        say("Slug:", slug)
        say("Untuk memasukkan ke katalog situs (Server 3): generate token di")
        say("Admin → Server 3, lalu jalankan ulang workflow dengan judul")
        say("sama + isi input slug_lama dengan slug di atas (CEPAT —")
        say("tanpa unduh ulang), ATAU daftarkan slug manual di form Admin.")
        say("Player:", str(up.get("urlIframe") or ""))
        summary_line("- Register: ⏭ dilewati (token kosong) — daftarkan slug "
                     "via Admin → Server 3")

    # 8. pasang subtitle ke player Abyss (native CC — best-effort).
    # Dijalankan walau register di-skip: track CC di player.abyssplayer.com
    # berguna juga saat film dibuka langsung dari Abyss.
    # CATATAN PENTING (fix 403, teruji 2026-09-23): api.abyss.to menolak
    # request TANPA User-Agent (Python-urllib) dengan 403 Forbidden —
    # keduanya (login + PUT) HARUS memakai curl + UA browser.
    sub_player_ok = False
    if sub_text and ABYSS_EMAIL and ABYSS_PASSWORD:
        try:
            say("\nMemasang subtitle ke player Abyss…")
            lang = (sub_lan or "Indonesia").strip()
            fname = f"{lang_slug(lang)}.vtt"
            vtt = srt_to_vtt(sub_text)
            jwt = abyss_login(ABYSS_EMAIL, ABYSS_PASSWORD)
            if jwt:
                code = abyss_put_subtitle(jwt, slug, lang, fname, vtt)
                if code == 200:
                    say(f"  subtitle player OK (HTTP 200) — label CC: {lang}")
                    sub_player_ok = True
                else:
                    say(f"  subtitle player HTTP {code} — TIDAK fatal: subtitle "
                        "tetap aktif di halaman film situs via injeksi ZeeStream")
            else:
                say("  login Abyss gagal — TIDAK fatal: subtitle tetap aktif "
                    "di halaman film situs via injeksi ZeeStream")
        except Exception as e:
            say("  subtitle player dilewati:", repr(e)[:120],
                "(TIDAK fatal — subtitle situs tetap aktif via injeksi)")
    elif sub_text:
        say("\nSubtitle tetap terpasang di halaman film situs (injeksi ZeeStream); "
            "track native Abyss dilewati (ABYSS_EMAIL/PASSWORD kosong).")

    say("\n" + "=" * 58)
    if reg_ok:
        say(f"SUKSES! Judul TERSEDIA di Server 3 situs kamu.")
        say(f"Disk Codespace sudah bersih (file lokal dihapus post-upload).")
        if sub_text:
            say(f"Subtitle {sub_lan or 'Indonesia'} aktif: buka halaman film → "
                "Server 3 → CC. (Track native Abyss: "
                + ("terpasang" if sub_player_ok else "best-effort") + ".)")
            say("Kalau timing subtitle kurang pas, atur Sinkron (± detik) "
                "di Admin → Subtitle — langsung terasa tanpa unduh ulang.")
        say("Putar sekarang!")
    else:
        say("SELESAI (upload Abyss OK). Slug:", slug)
        say("Lanjutkan register via Admin / token agar muncul di situs.")
    say("=" * 58)


def lang_slug(name):
    """"English" → "english"; "Indonesia" → "indonesia" (nama file aman)."""
    s = re.sub(r"[^a-z0-9]+", "_", (name or "indonesia").strip().lower()).strip("_")
    return s or "indonesia"


def srt_to_vtt(text):
    """SRT → VTT ringan: koma → titik milidetik + header WEBVTT.
    (Abyss menerima keduanya, tapi VTT teruji 200 pada 2026-09-23.)"""
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"(\d\d:\d\d:\d\d),(\d\d\d)", r"\1.\2", t)
    if not t.lstrip().startswith("WEBVTT"):
        t = "WEBVTT\n\n" + t.lstrip("\ufeff")
    return t


def abyss_login(email, password):
    """Login Abyss via curl + UA browser → JWT (None bila gagal 2x).
    urllib polos DITOLAK 403 oleh Cloudflare di depan api.abyss.to."""
    for attempt in range(2):
        p = subprocess.run(
            ["curl", "-sS", "--max-time", "60", "-A", UA,
             "-X", "POST", "-H", "Content-Type: application/json",
             "-d", json.dumps({"email": email, "password": password}),
             "https://api.abyss.to/auth/login"],
            capture_output=True, timeout=75)
        try:
            tok = json.loads(p.stdout or b"{}").get("token")
            if tok:
                return tok
        except Exception:
            pass
        time.sleep(5)
    return None


def abyss_put_subtitle(jwt, slug, language, filename, vtt_text):
    """PUT subtitle ke player Abyss (CC native) — HTTP code.
    Retry 3x: API ini kadang menjawab 5xx sesaat."""
    url = (f"https://api.abyss.to/v1/upload/subtitles/{slug}"
           f"?language={urllib.parse.quote(language)}"
           f"&filename={urllib.parse.quote(filename)}")
    code = None
    for attempt in range(3):
        p = subprocess.run(
            ["curl", "-sS", "--max-time", "120", "-A", UA,
             "-X", "PUT",
             "-H", f"Authorization: Bearer {jwt}",
             "-H", "Content-Type: text/plain; charset=utf-8",
             "--data-binary", vtt_text,
             "-o", "/dev/null", "-w", "%{http_code}",
             url],
            capture_output=True, timeout=135)
        try:
            code = int(p.stdout.decode().strip() or "0")
        except Exception:
            code = 0
        if code == 200:
            return code
        time.sleep(6)
    return code


def main():
    if not TITLE:
        fail("Input 'judul' kosong — isi kata kunci pencarian.")
    if not ABYSS_KEY:
        fail("Secret ABYSS_KEY belum diset di repo.")
    if EPISODES_SPEC and REUSE_SLUG:
        fail("Input 'episodes' (bulk) tidak bisa dipakai bersama 'slug_lama' ")
    if SOURCE == "manual":
        return main_manual()
    if SOURCE == "lk21":
        return main_lk21()
    return run_moviebox()


if __name__ == "__main__":
    main()
