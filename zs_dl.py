#!/usr/bin/env python3
# =====================================================================
#  zs_dl.py — ZeeStream pipeline: MovieBox → Abyss  (GitHub Actions)
# =====================================================================
#  Berjalan di runner GitHub (jaringan Azure — diterima CDN MovieBox,
#  terbukti dari Codespaces). TANPA Colab, TANPA relay, TANPA kuota
#  pribadi. Gratis (repo private: 2000 menit runner/bulan).
#
#  ENV (dari workflow_dispatch inputs + secrets):
#    ZS_TITLE    (wajib) kata kunci pencarian judul
#    ZS_PICK     indeks hasil pencarian (default "0")
#    ZS_SE       musim, serial saja (default "1")
#    ZS_EP       episode, serial saja (default "1")
#    ZS_QUALITY  auto / 1080 / 720 / 360 (default "auto")
#    ZS_SUB      in_id / ms / en / none (default "in_id")
#    ZS_TOKEN    token register dari Admin → Server 3 (opsional)
#    ABYSS_KEY / ABYSS_EMAIL / ABYSS_PASSWORD  (repo secrets)
# =====================================================================
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request

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
QUALITY = (os.environ.get("ZS_QUALITY") or "auto").strip().lower()
SUB_LANG = (os.environ.get("ZS_SUB") or "in_id").strip().lower()
TOKEN = (os.environ.get("ZS_TOKEN") or "").strip()
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
    QUALITY = str(_p.get("quality") or QUALITY).strip().lower()
    SUB_LANG = str(_p.get("sub") or SUB_LANG).strip().lower()
    TOKEN = str(_p.get("token") or TOKEN).strip()
    ABYSS_KEY = _p.get("abyss_key") or ABYSS_KEY
    ABYSS_EMAIL = _p.get("abyss_email") or ABYSS_EMAIL
    ABYSS_PASSWORD = _p.get("abyss_password") or ABYSS_PASSWORD
    try:
        os.remove(sys.argv[1])  # hapus file param (berisi kredensial)
    except OSError:
        pass

if SUB_LANG in ("none", "no", "off", "tanpa", "-"):
    SUB_LANG = ""
MB_UUID = str(__import__("uuid").uuid4())
MB_STRAT_OK = None
MB_DIAG = []


def say(msg):
    print(msg, flush=True)


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
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=h), timeout=timeout)


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


def main():
    if not TITLE:
        fail("Input 'judul' kosong — isi kata kunci pencarian.")
    if not ABYSS_KEY:
        fail("Secret ABYSS_KEY belum diset di repo.")

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

    # ruang disk runner
    st_disk = shutil.disk_usage(".")
    need = int(stream.get("size") or 0) * 1.15
    if need > st_disk.free:
        fail(f"Disk runner tidak cukup (butuh {need/1e9:.1f} GB, "
             f"sisa {st_disk.free/1e9:.1f} GB) — pilih resolusi lebih rendah.")

    # 3. unduh video
    ok = mb_smart_download(DOMAIN, DP, se, ep, res, FILE)
    if not ok:
        fail("Unduhan gagal — lihat jawaban CDN di log. Kalau 426/427, "
             "jaringan runner GitHub juga diblokir (kabar buruk — hubungi "
             "Super Z untuk strategi lain).")
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

    # 5. upload ke Abyss (curl multipart, 3x percobaan)
    if not os.path.exists(FILE):
        fail("File hilang setelah unduh — error aneh, cek log.")
    say(f"\nMengunggah {FILE} ({size/1e9:.2f} GB) ke Abyss…")
    up = None
    for attempt in range(3):
        cmd = ["curl", "-sS", "--max-time", "7200", "--speed-limit", "2048",
               "--speed-time", "120",
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
                     "filename": "indonesia.srt",
                     "content": sub_text, "srt": True}]
        body = json.dumps({"slug": slug, "quality": f"{res}p",
                           "size": size, "subs": subs}).encode()
        req = urllib.request.Request(
            f"{REGISTER}?token={urllib.parse.quote(TOKEN)}", data=body,
            headers={"Content-Type": "application/json",
                     "User-Agent": "zs-actions/1.0"})
        try:
            reg = json.load(urllib.request.urlopen(req, timeout=120))
        except Exception as e:
            reg = {"ok": False, "error": repr(e)[:200]}
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
        say("Admin → Server 3, lalu jalankan ulang workflow dengan judul sama")
        say("(unduh ulang ~beberapa menit saja), ATAU daftarkan slug manual")
        say("di form Admin. Player:", str(up.get("urlIframe") or ""))
        summary_line("- Register: ⏭ dilewati (token kosong) — daftarkan slug "
                     "via Admin → Server 3")

    # 8. pasang subtitle ke player Abyss (kalau ada + register sukses)
    if sub_text and reg_ok and ABYSS_EMAIL and ABYSS_PASSWORD:
        try:
            say("\nMemasang subtitle ke player Abyss…")
            login = json.load(urllib.request.urlopen(
                urllib.request.Request(
                    "https://api.abyss.to/auth/login",
                    data=json.dumps({"email": ABYSS_EMAIL,
                                     "password": ABYSS_PASSWORD}).encode(),
                    headers={"Content-Type": "application/json"}), timeout=60))
            jwt = login.get("token")
            if jwt:
                rr = urllib.request.urlopen(
                    urllib.request.Request(
                        f"https://api.abyss.to/v1/upload/subtitles/{slug}"
                        f"?language={urllib.parse.quote(sub_lan or 'Indonesia')}"
                        f"&filename=indonesia.srt",
                        data=sub_text.encode("utf-8"), method="PUT",
                        headers={"Authorization": "Bearer " + jwt}), timeout=120)
                say("  subtitle → HTTP", rr.status)
            else:
                say("  login Abyss gagal — subtitle hanya di arsip CMS")
        except Exception as e:
            say("  subtitle player dilewati:", repr(e)[:120])

    say("\n" + "=" * 58)
    if reg_ok:
        say("SUKSES! Judul TERSEDIA di Server 3 situs kamu.")
        say("Buka halaman filmnya → pilih Server 3 → putar!")
    else:
        say("SELESAI (upload Abyss OK). Slug:", slug)
        say("Lanjutkan register via Admin / token agar muncul di situs.")
    say("=" * 58)


if __name__ == "__main__":
    main()
