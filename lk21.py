#!/usr/bin/env python3
# =====================================================================
#  lk21.py — Sumber LK21 (LayarKaca21) untuk pipeline ZeeStream
# =====================================================================
#  Rekon live 29 Okt 2026 (tv.nontonfilm.red — penerima alias lk21.fyi):
#
#  STRUKTUR SITUS
#    - WordPress tema LK21 (gmr). Pencarian: GET /?s=<kata-kunci>
#    - Detail: https://tv.nontonfilm.red/<judul-tahun>/
#    - Blok unduh: <div id="download"> → <a> per host/kualitas, SEMUA
#      dibungkus shortlink monetisasi ouo.io
#    - Player utama: iframe embed (kadang drive.google.com/file/d/<id>)
#
#  POLA SHORTLINK ouo.io (kunci):
#    (a) ouo.io/qs/<hash>?s=<URL-ASLI>   ← URL ASLI BOCOR di parameter s
#        → cukup di-extract TANPA menyentuh ouo sama sekali.
#    (b) ouo.io/<kode> (pola murni)      ← kini di-balut Cloudflare
#        challenge ("Just a moment") → TIDAK bisa dilewati dari server
#        script → item seperti ini DILEWATI (dicatat di log).
#
#  HOST FILE yang muncul (bervariasi per judul):
#    file.drakor.club  → mirror file (perlu Referer situs, kadang 503)
#    dood family       → dood.so/d/... (redirect ds2play/dsvplay dst) —
#                        ekstraksi pass_md5 klasik
#    streamtape        → ekstraksi get_video (token dari robotlink)
#    mixdrop           → MDCore.wurl (obfuscated eval, regex defensif)
#    vid21 / one-lak   → gate JS → tak bisa server-side (dilewati)
#    gdrive embed      → jalur uc?export=download + tarian confirm
#
#  SUBTITLE: item "Sub Indo" sering .srt LANGSUNG (lewat pola (a)) —
#  diambil bila ter-ekstrak. Banyak file LK21 juga hardsub.
# =====================================================================
import base64
import http.cookiejar
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

LK_DOMAINS = [
    "https://tv.nontonfilm.red",     # utama (terverifikasi 29 Okt 2026)
    "https://tv1.nontonfilm.red",    # mirror cadangan (belum teruji)
    "https://tv2.nontonfilm.red",    # mirror cadangan (belum teruji)
]
_domain_cache = (None, 0.0)

# cookie jar global (dood/streamtape kadang minta cookie dari halaman
# embed sebelum pass_md5; gdrive butuh jar utk confirm)
JAR = http.cookiejar.CookieJar()
OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(JAR))


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
            if "gmr-" in body or "id=\"download\"" in body or \
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
    """Cari judul → daftar {title, year, slug}. Hasil = posting film/serial
    saja (slug dengan tanda hubung, dibersihkan dari halaman statis)."""
    domain = domain or lk_domain()
    r = lk_http(f"{domain}/?s={urllib.parse.quote(kw)}")
    html = r.read().decode("utf-8", "replace")
    r.close()
    root = urllib.parse.urlsplit(domain).netloc
    out, seen = [], set()
    for m in re.finditer(
            rf'href="https?://{re.escape(root)}/([a-z0-9][a-z0-9-]{{3,90}})/"',
            html):
        slug = m.group(1)
        if slug in seen or any(s in slug for s in (
                "film-semi", "blog-category", "cast/", "genre/", "country/",
                "director/", "contact-us", "dmca", "faq", "best-rating",
                "order-by", "report-link", "iklan", "index-movie", "blog/",
                "tag/", "season/")):
            continue
        seen.add(slug)
        # judul dari slug: "cosmic-sin-2021" → "Cosmic Sin" (2021)
        parts = slug.rstrip("/").split("-")
        year = parts[-1] if parts and parts[-1].isdigit() and \
            len(parts[-1]) == 4 else ""
        title = " ".join(parts[:-1] if year else parts).title()
        out.append({"slug": slug, "title": title or slug, "year": year})
    return out


def lk_detail(slug, domain=None):
    """Bedah halaman detail → blok unduh + embed gdrive + subtitle +
    link episode (utk series)."""
    domain = domain or lk_domain()
    if not slug.startswith("http"):
        url = f"{domain}/{slug.strip('/')}/"
    else:
        url = slug
    r = lk_http(url)
    html = r.read().decode("utf-8", "replace")
    r.close()

    # --- link episode utk SERIES: <judul>-episode-<N>/ ---
    episode_links = []
    for m in re.finditer(
            rf'href="https?://[^"]*?/([a-z0-9-]+-episode-\d+)/?"', html):
        ep_slug = m.group(1)
        if ep_slug not in episode_links:
            episode_links.append(ep_slug)

    # --- blok unduh ---
    # Link unduhan SELALU eksternal (file hosts / shortlink), sedangkan
    # tag a internal (genre/cast/related) memakai domain situs → filter
    # host eksternal memangkas derau dgn akurat.
    root_host = urllib.parse.urlsplit(domain).netloc
    items, subs = [], []
    i = html.find('id="download"')
    seg = html[i:i + 12000] if i >= 0 else ""
    for m in re.finditer(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', seg,
                         re.S):
        href, label = m.group(1), re.sub(r"<[^>]+>|\s+", " ",
                                         m.group(2)).strip()
        real = ouo_unwrap(href)
        gate = "" if real else "ouo-challenge"
        low = (real or href).lower()
        # subtitle boleh dari domain mana pun (biasanya situs saudara)
        is_sub = low.endswith(".srt") or low.endswith(".vtt") or \
            "/subtitle" in label.lower() or "sub indo" in label.lower()
        host = urllib.parse.urlsplit(real or href).hostname or ""
        if real is None and is_sub:
            subs.append("")  # sub dibalut ouo murni — tandai hadir
            continue
        if real is None:
            # ouo murni (CF-gated) → tandai & lewati, tapi log
            items.append({"label": label, "quality": "",
                          "url": None, "gate": gate})
            continue
        if is_sub:
            subs.append(real)
            continue
        if host.endswith(root_host) or not host:
            continue  # link internal situs — bukan item unduhan
        if not label:
            continue
        q = re.search(r"(\d{3,4})\s*p", label.lower())
        items.append({"label": label, "quality": q.group(1) if q else "",
                      "url": real, "gate": gate})

    # --- embed gdrive di area player ---
    gdrive = []
    for m in re.finditer(r'drive\.google\.com/file/d/([a-zA-Z0-9_-]{10,})',
                         html):
        if m.group(1) not in gdrive:
            gdrive.append(m.group(1))

    # --- judul & tahun dari halaman ---
    t = re.search(r"<h1[^>]*>([^<]{2,150})</h1>", html)
    title = t.group(1).strip() if t else slug
    return {"url": url, "title": title, "items": items, "subs": subs,
            "gdrive": gdrive, "episode_links": episode_links}


# ---------------------------------------------------------------------
#  shortlink & extractor per-host
# ---------------------------------------------------------------------

def ouo_unwrap(url):
    """Bocorkan URL asli dari ouo.io/qs/<h>?s=<ASLI>. Return None bila
    pola murni (CF-challenge) — TIDAK dicoba dibypass."""
    if not url:
        return None
    m = re.search(r"[?&]s=(https?://.+)$", url)
    if m and "ouo.io" in url:
        return urllib.parse.unquote(m.group(1).strip())
    if "ouo.io" in url or "ouo.press" in url or "shrinkme" in url:
        return None
    return url.strip()


def extract_dood(url, referer=None):
    """Doodstream family (dood.so/wf/to → ds2play/dsvplay/d000d dst):
    halaman /e/<id> → JS memanggil /pass_md5/<tok>/<code> → URL mp4 +
    10 char acak. Return (url_langsung, referer_untuk_download) atau None."""
    try:
        # normalisasi /d/<id> → /e/<id>
        m = re.search(r"(https?://[^/]+)/[de]/([a-z0-9]+)", url)
        if not m:
            return None
        base, code = m.group(1), m.group(2)
        r = lk_http(f"{base}/e/{code}", referer=referer or "https://tv.nontonfilm.red/", timeout=15, retry=1)
        html = r.read().decode("utf-8", "replace")
        r.close()
        fm = re.search(r"/pass_md5/([a-f0-9]{16,})/" + re.escape(code), html)
        host = re.search(r"(https?://[a-z0-9.-]+/pass_md5)", html)
        if not fm:
            return None
        pm = (host.group(1) if host else f"{base}/pass_md5") + \
            fm.group(0)[len("/pass_md5"):]
        r2 = lk_http(pm, referer=f"{base}/e/{code}", timeout=15, retry=1, hdrs={
            "Accept": "*/*"})
        dest = r2.read(400).decode("utf-8", "replace").strip()
        r2.close()
        if not dest.startswith("http"):
            return None
        final = dest + "".join(
            __import__("random").choice("abcdefghijklmnopqrstuvwxyz0123456789")
            for _ in range(10)) + ("?direct=1" if "?" not in dest else "")
        return final, pm
    except Exception:
        return None


def extract_streamtape(url, referer=None):
    """Streamtape: halaman embed berisi robotlink: '…get_video?id=' + a
    + '&expires=' + b + '&ip=' + c + '&token=' + d (4 bagian split)."""
    try:
        r = lk_http(url.replace("/v/", "/e/"), referer=referer or "https://tv.nontonfilm.red/", timeout=15, retry=1)
        html = r.read().decode("utf-8", "replace")
        r.close()
        # pola JS terobfuscate; tangkap semua token bagian per bagian
        parts = re.findall(r"fetch\('|/get_video\?id=", html)
        if not parts:
            return None
        seg = re.search(r"/get_video\?id=(.*)", html)
        if not seg:
            return None
        tail = seg.group(1)[:600]
        ids = re.findall(r"'([^']{3,64})'", tail) or \
            re.findall(r'\+([^+]+?)\+', tail)
        if len(ids) < 4:
            return None
        host_m = re.search(r"https?://([a-z0-9.-]+)/(?:v|e)/", url)
        host = host_m.group(1) if host_m else "streamtape.com"
        return (f"https://{host}/get_video?id={ids[0]}&expires={ids[1]}"
                f"&ip={ids[2]}&token={ids[3]}"), None
    except Exception:
        return None


def extract_mixdrop(url, referer=None):
    """Mixdrop: cari MDCore.wurl di dalam string eval (regex defensif)."""
    try:
        r = lk_http(url.replace("/f/", "/e/"), referer=referer or "https://tv.nontonfilm.red/", timeout=15, retry=1)
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


def extract_clicknupload(url, referer=None):
    """Clicknupload: halaman file punya form POST; versi ringan — bila ada
    direct link (file/download) di HTML, pakai; selain itu None."""
    try:
        r = lk_http(url, referer=referer or "https://tv.nontonfilm.red/", timeout=15, retry=1)
        html = r.read().decode("utf-8", "replace")
        r.close()
        m = re.search(r'href="(https?://[^"]*(?:files/|/d/)[^"]{8,200})"', html)
        if m:
            return m.group(1), None
        return None
    except Exception:
        return None


EXTRACTORS = {
    "dood": extract_dood,
    "streamtape": extract_streamtape,
    "mixdrop": extract_mixdrop,
    "clickn": extract_clicknupload,
}

# host yang JELAS bukan file unduhan (iklan/trailer/sosial) — jangan diprobe
SKIP_HOSTS = ("youtube.com", "youtu.be", "imdb.com", "facebook.com",
              "t.me", "telegram", "twitter.com", "instagram.com",
              "whatsapp", "ouo.io", "ouo.press", "shrinkme", "ouo")


def host_of(url):
    try:
        return urllib.parse.urlsplit(url).hostname or ""
    except Exception:
        return ""


def probe_video(url, referer=None, timeout=15):
    """Range 0-1023 → (ok, code, ctype, size_total)."""
    h = {"User-Agent": UA, "Range": "bytes=0-1023",
         "Accept": "*/*"}
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
    """Kaskade item terurut → (url_langsung, referer, quality_label, size).
    Urutkan: kualitas terpilih/ketinggian dulu; lewati gate ouo-challenge."""
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
        exact = [it for it in items if qval(it) == w]
        lower = sorted([it for it in items if 0 < qval(it) < w],
                       key=qval, reverse=True)
        higher = sorted([it for it in items if qval(it) > w > 0], key=qval)
        other = [it for it in items if qval(it) == 0]
        cand = exact + lower + higher + other

    for it in cand:
        if not it.get("url"):
            say(f"   ⏭ {it['label']}: dibalut shortlink Cloudflare "
                "(ouo murni) — dilewati otomatis")
            continue
        url, ref = it["url"], None
        host = host_of(url)
        if any(s in host for s in SKIP_HOSTS):
            continue  # bukan host file
        label = it["label"]
        # host extractor
        if any(host.endswith(s) for s in ("drakor.club",)):
            ref = "https://tv.nontonfilm.red/"
            ok, code, ctype, size = probe_video(url, ref)
            if ok:
                return {"url": url, "referer": ref,
                        "quality": it.get("quality") or "", "size": size}
            say(f"   ✘ {label}: HTTP {code} ({host}) — coba host lain")
            continue
        for key, fn in EXTRACTORS.items():
            if key in host or (key == "dood" and
                               any(s in host for s in ("dood", "ds2", "dsv",
                                                       "d000", "playmogo",
                                                       "doo"))):
                got = fn(url, referer="https://tv.nontonfilm.red/")
                if not got:
                    say(f"   ✘ {label}: ekstraksi {key} gagal")
                    break
                durl, dref = got
                ok, code, ctype, size = probe_video(durl, dref)
                if ok:
                    return {"url": durl, "referer": dref,
                            "quality": it.get("quality") or "",
                            "size": size}
                say(f"   ✘ {label}: {key} probe HTTP {code}")
                break
        else:
            # host tak dikenal — coba langsung (bila langsung video)
            ok, code, ctype, size = probe_video(url, ref)
            if ok:
                return {"url": url, "referer": ref,
                        "quality": it.get("quality") or "", "size": size}
            say(f"   ✘ {label}: host {host or '?'} tidak terekspos "
                f"langsung ({code})")
    return None


def resolve_gdrive(file_id, quality="", log=None):
    """Jalur gdrive: uc?export=download → bila HTML confirm (file besar),
    ikuti form action+confirm+uuid. Return dict atau None."""
    def say(*a):
        if log:
            log(*a)
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
                say("   ✘ gdrive: file tidak dapat diakses "
                    "(404/private/quota)")
                return None
            params = dict(re.findall(
                r'name="(\w+)" value="([^"]*)"', html))
            q = urllib.parse.urlencode(params)
            dest = act.group(1).replace("&amp;", "&")
            if "?" not in dest:
                dest += "?" + q
            else:
                dest += "&" + q
            ok, code, ctype2, size = probe_video(dest)
            if ok:
                return {"url": dest, "referer": None,
                        "quality": quality, "size": size}
            say(f"   ✘ gdrive: probe HTTP {code}")
            return None
        # bukan HTML → kemungkinan langsung file
        size = int(r.headers.get("Content-Length") or 0)
        r.close()
        return {"url": u, "referer": None, "quality": quality, "size": size}
    except Exception as e:
        if log:
            log("   ✘ gdrive:", repr(e)[:90])
        return None


# ---------------------------------------------------------------------
#  resolver tingkat judul (dipakai zs_dl.py)
# ---------------------------------------------------------------------

def resolve_from_detail(det, quality="auto", log=None):
    """Resolve URL unduh dari HASIL lk_detail (tanpa pencarian ulang) —
    dipakai mode bulk zs_dl agar 100 episode ≠ 100 pencarian."""
    got = resolve_direct(det["items"], quality, log)
    if got:
        got["sub_url"] = next((s for s in det["subs"] if s), None)
        got["source_page"] = det["url"]
        return got
    for fid in det["gdrive"]:
        if log:
            log("  mencoba embed GDrive:", fid)
        g = resolve_gdrive(fid, quality, log)
        if g:
            g["sub_url"] = next((s for s in det["subs"] if s), None)
            g["source_page"] = det["url"]
            return g
    return None


def resolve_from_detail(det, quality="auto", log=None):
    """Resolve URL unduh dari HASIL lk_detail (tanpa pencarian ulang) —
    dipakai mode bulk zs_dl agar 100 episode ≠ 100 pencarian."""
    got = resolve_direct(det["items"], quality, log)
    if got:
        got["sub_url"] = next((s for s in det["subs"] if s), None)
        got["source_page"] = det["url"]
        return got
    for fid in det["gdrive"]:
        if log:
            log("  mencoba embed GDrive:", fid)
        g = resolve_gdrive(fid, quality, log)
        if g:
            g["sub_url"] = next((s for s in det["subs"] if s), None)
            g["source_page"] = det["url"]
            return g
    return None


def resolve_title(title, pick=0, quality="auto", season=1, episode=1,
                  log=None):
    """Cari + pilih + resolve SATU target (film atau SxE series).
    Return dict: {kind: 'movie'|'tv', title, year, url, referer, size,
    quality, sub_url, source_page} — atau raise RuntimeError dgn pesan."""
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
    # buat step summary bila jalan di Actions
    try:
        import os
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
        f"({len(det['episode_links'])} link episode)"
        if is_tv else f"({len(det['items'])} item unduh)")

    target_slug = sel["slug"]
    if is_tv:
        # halaman episode: <slug-film>-episode-<N>
        eps = sorted(
            set(int(re.search(r"episode-(\d+)$", e).group(1))
                for e in det["episode_links"]
                if re.search(r"episode-(\d+)$", e)))
        if episode not in eps:
            raise RuntimeError(f"Episode {episode} tidak ada di LK21 "
                               f"(tersedia: {eps[:20]}…)")
        base_slug = re.sub(r"-episode-\d+$", "", target_slug)
        target_slug = f"{base_slug}-episode-{episode}"
        det = lk_detail(target_slug, dom)

    got = resolve_from_detail(det, quality, log)
    if got:
        got.update({"kind": "tv" if is_tv else "movie",
                    "title": det["title"], "year": sel["year"]})
        return got
    raise RuntimeError(
        "Semua host unduh LK21 judul ini tidak terekspos server-side "
        "(shortlink ouo murni / host JS-gated) — pakai sumber MovieBox "
        "untuk judul ini, atau coba judul lain.")
