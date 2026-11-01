#!/usr/bin/env python3
"""zs_media_check — verifikasi integritas file video SEBELUM upload (Task 103).

Latar: encode 1080p MovieBox (CDN hakunaymatata) terbukti memiliki paket
video rusak tersebar ("Invalid NAL unit size" — contoh nyata: Dead-End Job
S1E4 1080p @58.3s, Sense and Sensibility 1080p @32.4s; 480p-nya BERSIH).
Pipeline lama hanya cek ukuran → korupsi lolos ke Abyss → penonton Server 3
mengalami video beku di menit tertentu (audio jalan terus).

Metode (murni Python, tanpa ffmpeg — image codespace tak punya ffmpeg):
  1. Parse box MP4: ftyp/moov → trak video → stbl (stco/co64, stsc, stsz)
     → daftar (offset, ukuran) tiap sampel video + avcC (lengthSize).
  2. Untuk tiap sampel dalam data yang tersedia: validasi rantai NAL
     length-prefixed — panjang NAL harus muat di dalam sampel dan total
     harus pas habis. Struktur rusak = paket korup (persis kelas error
     ffmpeg "Invalid NAL unit size").
  3. Format MPEG-TS (jalur Byse/LK21): cek sync byte 0x47 tiap 188 byte
     pada sampel acak — hitung rasio paket tanpa sync.
Hasil: dict {ok, corrupt, checked, pts_corrupt[], note}.

Ambang: corrupt == 0 → lolos. > 0 → file ditolak (fallback kualitas/sumber).
"""
import struct


# ---------------- parser box MP4 (cukup utk moov klasik non-fragmented) ----------------

def _boxes(data, start, end):
    i = start
    while i + 8 <= end:
        size = struct.unpack(">I", data[i:i + 4])[0]
        typ = data[i + 4:i + 8]
        hdr = 8
        if size == 1:
            if i + 16 > end:
                return
            size = struct.unpack(">Q", data[i + 8:i + 16])[0]
            hdr = 16
        elif size == 0:
            size = end - i
        if size < hdr or i + size > end:
            return
        yield typ, i + hdr, i + size
        i += size


def _find(data, start, end, path):
    """cari box bersarang dgn path [b'moov', b'trak', b'mdia', ...]"""
    cur = [(start, end)]
    for k, name in enumerate(path):
        nxt = []
        for s, e in cur:
            for typ, a, b in _boxes(data, s, e):
                if typ == name:
                    if k == len(path) - 1:
                        return (a, b)
                    nxt.append((a, b))
        if not nxt:
            return None
        cur = nxt
    return None


def _video_samples(data):
    """return (samples, nal_len_size) atau (None, alasan)"""
    moov = _find(data, 0, len(data), [b"moov"])
    if not moov:
        return None, "moov tidak ketemu"
    ms, me = moov
    samples = None
    nal_len = 4
    trak_no = 0
    for typ, a, b in _boxes(data, ms, me):
        if typ != b"trak":
            continue
        trak_no += 1
        stbl = _find(data, a, b, [b"mdia", b"minf", b"stbl"])
        if not stbl:
            continue
        # pastikan trak video: cek hdlr 'vide'
        hdlr = _find(data, a, b, [b"mdia", b"hdlr"])
        if hdlr:
            hs, he = hdlr
            if he - hs > 12 and data[hs + 8:hs + 12] != b"vide":
                continue
        # stsd → avcC (lengthSizeMinusOne)
        stsd = _find(data, stbl[0], stbl[1], [b"stsd"])
        if stsd:
            ss, se = stsd
            avcc = data.find(b"avcC", ss, min(se, ss + 512))
            if avcc > 0:
                nal_len = (data[avcc + 8] & 0x03) + 1
        # stsz: ukuran per sampel
        stsz = _find(data, stbl[0], stbl[1], [b"stsz"])
        if not stsz:
            continue
        zs, ze = stsz
        sample_size, count = struct.unpack(">II", data[zs + 4:zs + 12])
        sizes = []
        if sample_size == 0:
            sizes = list(struct.unpack(">%dI" % count, data[zs + 12:zs + 12 + 4 * count]))
        else:
            sizes = [sample_size] * count
        # stco/co64: offset per chunk
        off = _find(data, stbl[0], stbl[1], [b"stco"])
        co64 = _find(data, stbl[0], stbl[1], [b"co64"])
        chunk_off = []
        if off:
            os_, oe = off
            n = struct.unpack(">I", data[os_ + 4:os_ + 8])[0]
            chunk_off = list(struct.unpack(">%dI" % n, data[os_ + 8:os_ + 8 + 4 * n]))
        elif co64:
            os_, oe = co64
            n = struct.unpack(">I", data[os_ + 4:os_ + 8])[0]
            chunk_off = list(struct.unpack(">%dQ" % n, data[os_ + 8:os_ + 8 + 8 * n]))
        else:
            continue
        # stsc: sampel per chunk
        stsc = _find(data, stbl[0], stbl[1], [b"stsc"])
        if not stsc:
            continue
        cs, ce = stsc
        n = struct.unpack(">I", data[cs + 4:cs + 8])[0]
        entries = [struct.unpack(">III", data[cs + 8 + 12 * i:cs + 20 + 12 * i]) for i in range(n)]
        samples = []
        si = 0
        for ei in range(len(entries)):
            first, spc, _ = entries[ei]
            if ei + 1 < len(entries):
                nchunk = entries[ei + 1][0] - first
            else:
                nchunk = len(chunk_off) - first + 1
            for c in range(nchunk):
                ci = first - 1 + c
                if ci >= len(chunk_off):
                    break
                base = chunk_off[ci]
                for _ in range(spc):
                    if si >= len(sizes):
                        break
                    samples.append((base, sizes[si]))
                    base += sizes[si]
                    si += 1
        if samples:
            return samples, nal_len
    return None, "trak video tidak ketemu"


def _pts_list(data, samples):
    """stts → pts per sampel (utk laporan detik-keberapa)."""
    out = None
    moov = _find(data, 0, len(data), [b"moov"])
    if not moov:
        return None
    # cari stts di trak video — pakai heuristic: trak pertama dgn stts
    # (urutan samples kita = urutan dekode trak video)
    for typ, a, b in _boxes(data, moov[0], moov[1]):
        if typ != b"trak":
            continue
        stbl = _find(data, a, b, [b"mdia", b"minf", b"stbl"])
        hdlr = _find(data, a, b, [b"mdia", b"hdlr"])
        if hdlr and data[hdlr[0] + 8:hdlr[0] + 12] != b"vide":
            continue
        mdhd = _find(data, a, b, [b"mdia", b"mdhd"])
        if not mdhd or not stbl:
            continue
        timescale = struct.unpack(">I", data[mdhd[0] + 12:mdhd[0] + 16])[0]
        stts = _find(data, stbl[0], stbl[1], [b"stts"])
        if not stts:
            continue
        ss, se = stts
        n = struct.unpack(">I", data[ss + 4:ss + 8])[0]
        pts = []
        t = 0
        total = 0
        for i in range(n):
            cnt, dur = struct.unpack(">II", data[ss + 8 + 8 * i:ss + 16 + 8 * i])
            for _ in range(cnt):
                if total < len(samples):
                    pts.append(t / max(1, timescale))
                t += dur
                total += 1
        if len(pts) == len(samples):
            return pts
    return None


# ---------------- pemeriksaan NAL ----------------

def check_mp4(data, max_report=8):
    """validasi struktur NAL tiap sampel video MP4."""
    samples, nal_len = _video_samples(data)
    if not samples:
        return {"ok": False, "error": "parse-gagal: %s" % nal_len,
                "corrupt": -1, "checked": 0, "pts_corrupt": []}
    pts = _pts_list(data, samples) or [0.0] * len(samples)
    corrupt, checked = [], 0
    for (off, size), t in zip(samples, pts):
        if off + size > len(data):
            break  # data terpotong (Range) — periksa sebatas yang ada
        checked += 1
        pkt = data[off:off + size]
        i, ok = 0, True
        while i + nal_len <= len(pkt):
            ln = int.from_bytes(pkt[i:i + nal_len], "big")
            if ln < 1 or i + nal_len + ln > len(pkt):
                ok = False
                break
            i += nal_len + ln
        if ok and i != len(pkt):
            ok = False
        if not ok:
            corrupt.append(round(t, 1))
    return {
        "ok": len(corrupt) == 0,
        "corrupt": len(corrupt),
        "checked": checked,
        "pts_corrupt": corrupt[:max_report],
        "format": "mp4",
    }


def check_ts(data, sample_step=188 * 97):
    """sinkronisasi paket TS: 0x47 tiap 188 byte (sampel merata)."""
    n = len(data)
    if n < 188 * 10:
        return {"ok": True, "corrupt": 0, "checked": 0, "format": "ts"}
    bad = tot = 0
    pos = 0
    while pos + 188 <= n:
        tot += 1
        if data[pos] != 0x47:
            bad += 1
        pos += sample_step
    ratio = bad / max(1, tot)
    return {"ok": ratio < 0.02, "corrupt": bad, "checked": tot,
            "note": "sync-ratio %.3f" % ratio, "format": "ts"}


def check_file(path):
    """entry point: deteksi format lalu periksa."""
    with open(path, "rb") as f:
        data = f.read(64 * 1024 * 1024)  # periksa maks 64MB pertama (cepat, cukup)
    if len(data) > 12 and data[4:8] in (b"ftyp",) :
        return check_mp4(data)
    if len(data) > 188 and data[0] == 0x47:
        return check_ts(data)
    return {"ok": True, "corrupt": 0, "checked": 0,
            "note": "format tak dikenal — dilewati", "format": "?"}


if __name__ == "__main__":
    import sys, json
    for p in sys.argv[1:]:
        print(p, "->", json.dumps(check_file(p), ensure_ascii=False))
