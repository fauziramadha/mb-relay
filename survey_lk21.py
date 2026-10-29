#!/usr/bin/env python3
# Survei hit-rate bocoran LK21: ambil judul dari beberapa pencarian,
# bedah blok unduh tiap judul, catat host yang TERBUKA (gate kosong).
import collections
import re
import urllib.parse

import lk21

dom = lk21.lk_domain()
queries = ["horror", "the", "love", "man", "dead", "korea", "dragon",
           "night", "world", "home"]
slugs, seen = [], set()
for q in queries:
    try:
        for r in lk21.lk_search(q, dom):
            if r["slug"] not in seen:
                seen.add(r["slug"])
                slugs.append(r["slug"])
    except Exception:
        pass
    if len(slugs) >= 24:
        break

print(f"judul disurvei: {len(slugs)}\n")
stat = collections.Counter()
leaked_hosts = collections.Counter()
healthy = []
for n, slug in enumerate(slugs[:24]):
    try:
        det = lk21.lk_detail(slug, dom)
    except Exception as e:
        print(f"[{n:>2}] {slug[:40]:<42} ERR {repr(e)[:50]}")
        continue
    items = det["items"]
    if not items and not det["gdrive"]:
        print(f"[{n:>2}] {slug[:40]:<42} (blok unduh kosong)")
        continue
    open_items = [it for it in items if it["url"]]
    for it in items:
        stat["ouo_murni" if not it["url"] else "terbuka"] += 1
        if it["url"]:
            h = urllib.parse.urlsplit(it["url"]).hostname or "?"
            leaked_hosts[h] += 1
    print(f"[{n:>2}] {slug[:40]:<42} "
          f"terbuka={len(open_items)} ouo={len(items)-len(open_items)} "
          f"gdrive={len(det['gdrive'])} sub={'ya' if det['subs'] else '-'}")
    if open_items:
        healthy.append((slug, open_items))

print("\nTotal item:", dict(stat))
print("Host terbuka:", dict(leaked_hosts.most_common(12)))

# probe satu host file terbuka yang menarik
print("\n== probe contoh link terbuka ==")
for slug, items in healthy[:6]:
    for it in items[:2]:
        h = urllib.parse.urlsplit(it["url"]).hostname or ""
        if any(s in h for s in lk21.SKIP_HOSTS):
            continue
        ok, code, ctype, size = lk21.probe_video(it["url"],
                                                 "https://tv.nontonfilm.red/")
        print(f"  {slug[:30]:<32} {it['label'][:18]:<20} {h[:28]:<30} "
              f"HTTP {code} {'VIDEO!' if ok else ''} {ctype[:24]}")
        break
