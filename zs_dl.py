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
SOURCE = (os.environ.get("ZS_SOURCE") or "moviebox").strip().lower()  # Task 96: moviebox | lk21
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
    ABYSS_KEY = _p.get("abyss_key") or ABYSS_KEY
    ABYSS_EMAIL = _p.get("abyss_email") or ABYSS_EMAIL
    ABYSS_PASSWORD = _p.get("abyss_password") or ABYSS_PASSWORD
    try:
        os.remove(sys.argv[1])  # hapus file param (berisi kredensial)
    except OSError:
        pass

if SUB_LANG in ("none", "no", "off", "tanpa", "-"):
    SUB_LANG = ""
if SOURCE not in ("moviebox", "lk21"):
    SOURCE = "moviebox"
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

    # upload ke Abyss (3x percobaan)
    say(f"  upload ke Abyss…")
    up = None
    for attempt in range(3):
        cmd = ["curl", "-sS", "--max-time", "7200", "--speed-limit",
               "2048", "--speed-time", "120",
               "-F", f"file=@{FILE};type=video/mp4",
               f"https://up.abyss.to/{ABYSS_KEY}"]
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=7300)
        except subprocess.TimeoutExpired:
            p = None
        if p is not None:
            try:
                up = json.loads(p.stdout or b"{}")
            except Exception:
                up = None
        if isinstance(up, dict) and up.get("status"):
            break
        up = None
        time.sleep(10)
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


def lk21_process_one(dom, sel, ep, is_tv):
    """LK21 satu target: resolve → unduh → sub SRT → upload → register.
    Return dict hasil (tidak sys.exit — pemanggil melanjutkan)."""
    judul, tahun = sel.get("title") or "film", sel.get("year") or ""
    slug_page = re.sub(r"-episode-\d+$", "", sel["slug"])
    try:
        if is_tv:
            det = lk21.lk_detail(f"{slug_page}-episode-{ep}", dom)
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

    # upload ke Abyss (3x) — logika sama dgn jalur MovieBox
    say("  upload ke Abyss…")
    up = None
    for attempt in range(3):
        cmd = ["curl", "-sS", "--max-time", "7200", "--speed-limit",
               "2048", "--speed-time", "120",
               "-F", f"file=@{FILE};type=video/mp4",
               f"https://up.abyss.to/{ABYSS_KEY}"]
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=7300)
        except subprocess.TimeoutExpired:
            p = None
        if p is not None:
            try:
                up = json.loads(p.stdout or b"{}")
            except Exception:
                up = None
        if isinstance(up, dict) and up.get("status"):
            break
        up = None
        time.sleep(10)
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


def run_bulk_lk21(dom, sel, eps):
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
            r = lk21_process_one(dom, sel, ep, True)
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


def main_lk21():
    """Alur sumber LK21: cari → pilih → (film | series tunggal/bulk)."""
    if REUSE_SLUG:
        fail("slug_lama (register-saja) hanya untuk sumber moviebox — "
             "LK21 tidak punya koneksi slug lama.")
    egress = mb_ip()
    say(f"ZS pipeline — SUMBER LK21\nIP egress: {egress}\n")
    summary_line(f"# ZS Download (LK21) — {TITLE}")
    summary_line(f"- IP runner: `{egress}` | sumber: LK21")
    dom = lk21.lk_domain()
    results = lk21.lk_search(TITLE, dom)
    if not results:
        fail(f"Tidak ada hasil '{TITLE}' di LK21 — coba kata kunci lain "
             "atau jalankan dengan sumber moviebox.")
    say(f"Hasil pencarian LK21 '{TITLE}': {len(results)} judul")
    listing = ["\n## Hasil pencarian LK21\n"]
    for i, it in enumerate(results[:20]):
        line = f"[{i:>2}] {it.get('title', '?')} ({it.get('year', '-')})"
        say("  " + line)
        listing.append(f"- `{i}` **{it.get('title', '?')}** "
                       f"({it.get('year', '-')})")
    summary_line("\n".join(listing))
    if PICK < 0 or PICK >= len(results):
        fail(f"Pilihan indeks {PICK} di luar daftar LK21 "
             f"(0..{len(results)-1}) — lihat daftar di atas.")
    sel = results[PICK]
    say(f"\nTerpilih: {sel['title']} ({sel['year']})")

    # deteksi series dari link episode di halaman detail
    base_det = lk21.lk_detail(sel["slug"], dom)
    is_tv = bool(base_det["episode_links"])
    say("Tipe:", "SERIES" if is_tv else "Film")

    if EPISODES_SPEC:
        if not is_tv:
            fail("Input 'episodes' (bulk) hanya untuk SERIES — pilih "
                 "hasil bertipe series (punya halaman episode di LK21).")
        try:
            eps = parse_episode_spec(EPISODES_SPEC)
        except ValueError as e:
            fail(f"Input 'episodes' tidak valid: {e}")
        run_bulk_lk21(dom, sel, eps)
        return

    ep = EPISODE if is_tv else 1
    r = lk21_process_one(dom, sel, ep, is_tv)
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


def main():
    if not TITLE:
        fail("Input 'judul' kosong — isi kata kunci pencarian.")
    if not ABYSS_KEY:
        fail("Secret ABYSS_KEY belum diset di repo.")
    if EPISODES_SPEC and REUSE_SLUG:
        fail("Input 'episodes' (bulk) tidak bisa dipakai bersama 'slug_lama' ")
    if SOURCE == "lk21":
        return main_lk21()

    egress = mb_ip()
    say(f"ZS pipeline — runner GitHub (Azure)\nIP egress: {egress}\n")
    summary_line(f"# ZS Download — {TITLE}")
    summary_line(f"- IP runner: `{egress}`")

    # 1. cari judul
    results = mb_search(TITLE)
    if not results:
        fail(f"Tidak ada hasil untuk '{TITLE}' — coba kata kunci lain "
             "(judul Inggris biasanya lebih akurat).")
    say(f"Hasil pencarian '{TITLE}': {len(results)} judul")
    listing = ["\n## Hasil pencarian\n"]
    for i, it in enumerate(results[:20]):
        year = (it.get("releaseDate") or "")[:4]
        jenis = "SERIAL" if it.get("subjectType") == 2 else "Film"
        flag = "" if it.get("hasResource") is not False else " [belum ada video]"
        line = f"[{i:>2}] {jenis:<6} {it.get('title', '?')} ({year}){flag}"
        say("  " + line)
        listing.append(f"- `{i}` {jenis} **{it.get('title', '?')}** ({year}){flag}")
    summary_line("\n".join(listing))

    if PICK < 0 or PICK >= len(results):
        fail(f"Pilihan indeks {PICK} di luar daftar (0..{len(results)-1}). "
             "Lihat daftar di atas, jalankan ulang workflow dengan 'pilihan' "
             "yang benar.")
    sel = results[PICK]
    DP = str(sel["detailPath"])
    is_tv = (sel.get("subjectType") == 2)
    judul = sel.get("title") or "film"
    tahun = (sel.get("releaseDate") or "")[:4]
    say(f"\nTerpilih: {judul} ({tahun}) — "
        f"{'SERIAL' if is_tv else 'Film'}")

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
        fail("Tidak ada stream free — mungkin salah musim/episode atau judul "
             "belum punya video. Lihat daftar hasil pencarian di atas.")
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
        up = None
        for attempt in range(3):
            cmd = ["curl", "-sS", "--max-time", "7200", "--speed-limit",
                   "2048", "--speed-time", "120",
                   "-F", f"file=@{FILE};type=video/mp4",
                   f"https://up.abyss.to/{ABYSS_KEY}"]
            try:
                p = subprocess.run(cmd, capture_output=True, timeout=7300)
            except subprocess.TimeoutExpired:
                p = None
            if p is not None:
                try:
                    up = json.loads(p.stdout or b"{}")
                except Exception:
                    up = None
            if isinstance(up, dict) and up.get("status"):
                break
            say(f"  percobaan {attempt + 1}/3 gagal: "
                f"exit {p.returncode if p else 'timeout'} "
                f"{((p.stderr or b'')[:150]) if p else b''}")
            up = None
            time.sleep(10)
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


if __name__ == "__main__":
    main()
