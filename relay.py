#!/usr/bin/env python3
# =====================================================================
#  mb-relay.py — ZeeStream MovieBox Relay  (anti blokir HTTP 426/427)
# =====================================================================
#  FUNGSI
#    Server kecil pengganti "IP Colab". CDN file MovieBox (enacdn)
#    menolak jaringan Google Colab (426) & Cloudflare Worker (427),
#    tapi menerima server biasa. Relay ini berjalan di jaringan yang
#    tidak diblokir (GitHub Codespaces / Hugging Face Space / PC mana
#    pun), lalu Colab mengunduh file MELALUI relay — jadi kuota
#    internet pribadi TIDAK terpakai sama sekali.
#
#  GRaTIS, tanpa VPS, tanpa kartu bank, stdlib-only (Python 3.8+).
#
#  ENDPOINT
#    GET /                     bantuan singkat
#    GET /healthz              "ok" (ping penjaga hidup)
#    GET /ip                   JSON {egress} — IP keluar relay
#    GET /probe?url=<file>     uji 1KB dari sisi relay → JSON jawaban CDN
#    GET /dl?url=<file>&ref=<domain video>
#                              streaming file (dukung Range/resume)
#
#  HOST yang DIBOLEHKAN (whitelist — bukan open proxy):
#    *.hakunaymatata.com  (bcdnxw = file video, cacdn = subtitle)
#  Referer otomatis diambil dari get-domain resmi (di-cache 10 menit)
#  bila parameter ref tidak diberikan; host cacdn dikirim tanpa referer.
#
#  JALANKAN
#    python3 relay.py                 # PORT 8080
#    PORT=7860 python3 relay.py       # Hugging Face Space (Docker)
# =====================================================================
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VER = "ZS-MB-RELAY/1.0"
PORT = int(os.environ.get("PORT", "8080"))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
H5 = "https://officialmoviebox.com"           # situs katalog MovieBox
ALLOW_SUFFIXES = ("hakunaymatata.com",)       # host file + subtitle
FALLBACK_REF = "https://mzfi.me/"             # referer darurat
CHUNK = 262144                                # 256 KB
_domain_cache = (None, 0)                     # (nilai, waktu)

# --- paket file uji untuk self-test (film, stream terkecil) ---
SELFTEST_PATH = "inception-e1BOR6f19C7"


def http_get(url, hdrs=None, timeout=45):
    h = {"User-Agent": UA, "accept": "application/json"}
    if hdrs:
        h.update(hdrs)
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=h), timeout=timeout)


def egress_ip():
    for svc in ("https://api.ipify.org", "https://icanhazip.com"):
        try:
            return http_get(svc, timeout=15).read().decode().strip()
        except Exception:
            pass
    return "?"


def video_domain():
    """Domain video MovieBox saat ini (dirotasi berkala) — cache 10 menit."""
    global _domain_cache
    val, ts = _domain_cache
    if val and time.time() - ts < 600:
        return val
    try:
        r = http_get(f"{H5}/wefeed-h5api-bff/media-player/get-domain",
                     hdrs={"X-Client-Type": "h5"})
        val = (json.load(r).get("data") or "").rstrip("/") or FALLBACK_REF
    except Exception:
        val = val or FALLBACK_REF
    _domain_cache = (val, time.time())
    return val


def allowed(url):
    try:
        p = urllib.parse.urlsplit(url)
    except Exception:
        return False
    host = (p.hostname or "").lower()
    return (p.scheme in ("http", "https") and len(url) < 4096
            and any(host.endswith(s) for s in ALLOW_SUFFIXES))


def referer_for(host, ref_param):
    """Referer yang dikirim ke CDN: param ref > auto (non-cacdn) > kosong."""
    if ref_param and ref_param.startswith("http") and len(ref_param) < 300:
        return ref_param
    if host.lower().startswith("cacdn"):
        return None                    # subtitle CDN: tanpa referer
    return video_domain() + "/"


class Relay(BaseHTTPRequestHandler):
    server_version = VER
    timeout = 120                      # baca request dari klien

    # ---------- util ----------
    def _q(self):
        p = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(p.query)
        return p.path.rstrip("/") or "/", {k: v[0] for k, v in q.items()}

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8",
              extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body and self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
        self.log_line(code, len(body))

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(),
                   "application/json")

    def log_line(self, code=0, n=0):
        sys.stderr.write(f"[relay] {self.command} {self.path.split('?')[0]:<10} "
                         f"→ {code} ({n} B) {self.client_address[0]}\n")

    def log_message(self, *a):         # matikan log default yang panjang
        pass

    # ---------- rute ----------
    def do_GET(self):
        try:
            self.route()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            try:
                self._send(500, f"relay error: {e!r}".encode())
            except Exception:
                pass

    do_HEAD = do_GET

    def route(self):
        path, q = self._q()
        if path in ("", "/", "/help"):
            self._send(200, (
                f"{VER} — relay unduhan MovieBox untuk Colab ZeeStream\n"
                f"GET /ip | /healthz | /probe?url=<file> | "
                f"/dl?url=<file>&ref=<domain-video>\n"
                f"whitelist: *{' *'.join(ALLOW_SUFFIXES)}\n").encode())
        elif path == "/healthz":
            self._send(200, b"ok")
        elif path == "/ip":
            self._json({"ver": VER, "egress": egress_ip(),
                        "domain": video_domain()})
        elif path == "/probe":
            self.api_probe(q)
        elif path == "/dl":
            self.api_dl(q)
        else:
            self._send(404, "endpoint tidak ada — lihat /help".encode())

    # ---------- /probe ----------
    def api_probe(self, q):
        url = q.get("url", "")
        if not url or not allowed(url):
            self._json({"error": "url kosong / host tidak di-whitelist"}, 400)
            return
        host = urllib.parse.urlsplit(url).hostname
        ref = referer_for(host, q.get("ref"))
        try:
            r = http_get(url, hdrs={"Referer": ref or "",
                                    "Range": "bytes=0-1023"}, timeout=40)
            code, ctype, body = r.getcode(), \
                r.headers.get("Content-Type", ""), r.read(1024)
            r.close()
        except urllib.error.HTTPError as e:
            try:
                body = e.read(1024)
            except Exception:
                body = b""
            code, ctype = e.code, e.headers.get("Content-Type", "")
        except Exception as e:
            self._json({"egress": egress_ip(), "code": None,
                        "error": repr(e)[:200]})
            return
        ok = code in (200, 206)
        self._json({
            "ver": VER, "egress": egress_ip(), "domain": video_domain(),
            "code": code, "ctype": ctype, "ok": ok,
            "head": body[:12].decode("latin-1", "replace"),
            "verdict": "LOLOS" if ok else "DITOLAK/BLOKIR",
        })

    # ---------- /dl (streaming) ----------
    def api_dl(self, q):
        url = q.get("url", "")
        if not url or not allowed(url):
            self._send(403, "host tidak di-whitelist (relay khusus MovieBox)"
                       .encode())
            return
        host = urllib.parse.urlsplit(url).hostname
        ref = referer_for(host, q.get("ref"))
        rng = self.headers.get("Range")            # teruskan resume klien
        hdrs = {"User-Agent": UA, "Accept": "*/*"}
        if ref:
            hdrs["Referer"] = ref
        if rng:
            hdrs["Range"] = rng
        try:
            r = urllib.request.urlopen(
                urllib.request.Request(url, headers=hdrs), timeout=120)
        except urllib.error.HTTPError as e:         # jawaban CDN diteruskan
            try:
                body = e.read(4096)
            except Exception:
                body = b""
            self._send(e.code, body or str(e.reason).encode(),
                       e.headers.get("Content-Type", "text/plain"))
            return
        except Exception as e:
            self._send(502, f"CDN tidak terjangkau: {e!r}".encode())
            return

        code = getattr(r, "status", None) or r.getcode()
        sent = 0
        try:
            self.send_response(code)
            passthrough = ("Content-Type", "Content-Length", "Content-Range",
                           "Accept-Ranges", "ETag", "Last-Modified")
            for k in passthrough:
                v = r.headers.get(k)
                if v:
                    self.send_header(k, v)
            if not r.headers.get("Content-Length"):
                self.send_header("Connection", "close")
            self.end_headers()
            while True:
                chunk = r.read(CHUNK)
                if not chunk:
                    break
                self.wfile.write(chunk)
                sent += len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            sys.stderr.write(f"[relay] klien putus di {sent/1e6:.1f} MB "
                             "(resume Range nanti) — normal\n")
        finally:
            try:
                r.close()
            except Exception:
                pass
            self.log_line(code, sent)


# ---------------------------------------------------------------------
#  SELF-TEST saat startup: jawaban CDN thd jaringan relay dicetak jelas
# ---------------------------------------------------------------------
def self_test():
    time.sleep(0.5)
    print("=" * 60)
    print("mb-relay self-test — mengecek jaringan server ini…")
    ip = egress_ip()
    print("  IP egress relay :", ip)
    dom = video_domain()
    print("  Domain video    :", dom)
    try:
        pj = json.load(http_get(
            f"{dom}/wefeed-h5api-bff/subject/play?subjectId=0&se=0&ep=0"
            f"&detailPath={urllib.parse.quote(SELFTEST_PATH)}",
            hdrs={"Referer": f"{dom}/spa/videoPlayPage/movies/{SELFTEST_PATH}",
                  "x-client-info": '{"timezone":"Asia/Jakarta"}'}))
        st = min([s for s in pj["data"]["streams"]
                  if not s.get("vipLocked")],
                 key=lambda s: int(s.get("size") or 0))
        try:
            r = http_get(st["url"], hdrs={"Referer": dom + "/",
                                          "Range": "bytes=0-1023"}, timeout=40)
            code = r.getcode()
            r.close()
        except urllib.error.HTTPError as e:
            code = e.code
        if code in (200, 206):
            print(f"  Jawaban CDN     : HTTP {code} — jaringan relay LOLOS ✔")
            print("  STATUS          : SIAP DIPAKAI oleh notebook CELL 4R")
        else:
            print(f"  Jawaban CDN     : HTTP {code} — jaringan ini DIBLOKIR ✘")
            print("  STATUS          : pindah relay ke platform lain")
            print("                     (mis. Codespaces ↔ Hugging Face).")
    except Exception as e:
        print("  self-test file  : gagal (", repr(e)[:100], ") —",
              "API MovieBox mungkin sedang sibuk; coba /probe?url=… nanti")
    print("=" * 60)
    print(f"mb-relay berjalan di port {PORT}")
    print(f"contoh   : http://<host-publik>:{PORT}/dl?url=<file-MovieBox>")
    print("Colab → CELL 4R (RELAY_URL) → CELL 4 seperti biasa.")
    print("=" * 60, flush=True)


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Relay)
    srv.daemon_threads = True
    threading.Thread(target=self_test, daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[relay] berhenti.")


if __name__ == "__main__":
    main()
