#!/usr/bin/env python3
# =====================================================================
#  test_lk21.py — uji modul LK21 (jalankan di mana saja; tidak mengunduh)
#  Uji: domain → pencarian → detail → ouo unwrap → resolve ladder
# =====================================================================
import sys
import time

import lk21


def main():
    t0 = time.time()
    print("== 1. domain aktif ==")
    dom = lk21.lk_domain()
    print("   domain:", dom)

    print("\n== 2. pencarian 'cosmic sin' ==")
    res = lk21.lk_search("cosmic sin", dom)
    for i, r in enumerate(res[:6]):
        print(f"   [{i}] {r['title']} ({r['year']}) → /{r['slug']}/")
    assert res, "pencarian kosong!"

    print("\n== 3. detail cosmic-sin-2021 (bocoran ?s= tersedia) ==")
    det = lk21.lk_detail("cosmic-sin-2021", dom)
    print("   judul halaman:", det["title"][:60])
    for it in det["items"]:
        print(f"   - {it['label']:<26} q={it['quality'] or '-':<5} "
              f"gate={it['gate'] or 'LANGSUNG'} → "
              f"{(it['url'] or '')[:70]}")
    for s in det["subs"]:
        print("   sub:", s[:80])
    print("   gdrive embeds:", det["gdrive"])
    print("   episode links:", det["episode_links"][:4])

    print("\n== 4. ouo unwrap ==")
    assert lk21.ouo_unwrap(
        "http://ouo.io/qs/wNU7Vskq?s=https://file.drakor.club/file/x"
    ) == "https://file.drakor.club/file/x", "unwrap ?s= gagal"
    assert lk21.ouo_unwrap("https://ouo.io/hYqwNOf") is None
    print("   ?s= bocor OK; pola murni = None OK")

    print("\n== 5. resolve ladder judul terpilih (probe 1KB, tanpa unduh) ==")
    try:
        got = lk21.resolve_title("cosmic sin", 0, "auto", 1, 1,
                                 log=lambda *a: print("   ", *a))
        print("\n   RESOLVED:", got["url"][:90])
        print("   quality:", got["quality"], "| size:",
              f"{got['size']/1e6:.0f} MB" if got.get("size") else "?",
              "| referer:", (got.get("referer") or "-")[:50])
        print("   sub_url:", (got.get("sub_url") or "-")[:80])
    except RuntimeError as e:
        print("\n   KASKADE GAGAL (peserta host mungkin mati — "
              "bukan bug modul):", e)

    print(f"\nSELESAI dalam {time.time()-t0:.1f} dtk")
    return 0


if __name__ == "__main__":
    sys.exit(main())
