// pow-solve.js — port persis solver PoW Byse (dari assets/pow-DEJGtdh2.js)
// Pemakaian: node pow-solve.js <nonce> <difficulty> [timeoutMs]
// Output: solution (string counter), atau "TIMEOUT".
const be = 512, lt = be - 1, dr = 2, lr = 2654435761, hr = 2246822519;
const re = (t, e) => (t << e | t >>> 32 - e) >>> 0;
const ht = (t, e) => Math.imul(t, e) >>> 0;

function ye(t) {
  t[0] = t[0] + t[1] >>> 0;
  t[3] = re(t[3] ^ t[0], 16);
  t[2] = t[2] + t[3] >>> 0;
  t[1] = re(t[1] ^ t[2], 12);
  t[0] = t[0] + t[1] >>> 0;
  t[3] = re(t[3] ^ t[0], 8);
  t[2] = t[2] + t[3] >>> 0;
  t[1] = re(t[1] ^ t[2], 7);
}

function gr(t) {
  const e = new Uint32Array([1779033703, 3144134277, 1013904242, 2773480762]);
  for (let i = 0; i < t.length; i++) {
    e[0] = e[0] + t[i] >>> 0;
    e[0] = re(e[0], 7);
    ye(e);
  }
  for (let i = 0; i < 8; i++) ye(e);
  const r = new Uint32Array(be);
  for (let i = 0; i < be; i++) { ye(e); r[i] = (e[0] ^ e[2]) >>> 0; }
  for (let i = 0; i < dr; i++)
    for (let s = 0; s < be; s++) {
      const a = r[s] & lt;
      let c = r[s] + r[a] >>> 0;
      c = re(c, 13);
      c = (c ^ ht(r[(s + 1) & lt], lr)) >>> 0;
      r[s] = c;
      e[0] = (e[0] ^ c) >>> 0;
      ye(e);
    }
  const n = new Uint32Array(8), o = be / 8;
  for (let i = 0; i < 8; i++) {
    ye(e);
    let s = e[0];
    const a = i * o;
    for (let c = 0; c < o; c++) {
      const d = r[a + c];
      s = s + d >>> 0;
      s = re(s, 5);
      s = (s ^ ht(d, hr)) >>> 0;
    }
    n[i] = (s ^ e[2]) >>> 0;
  }
  return n;
}

function wr(t) {
  let e = 0;
  for (let r = 0; r < t.length; r++) {
    const n = t[r];
    if (n === 0) { e += 32; continue; }
    return e + Math.clz32(n);
  }
  return e;
}

function yr(t) {
  const e = new Uint8Array(t.length);
  for (let r = 0; r < t.length; r++) e[r] = t.charCodeAt(r) & 255;
  return e;
}

async function solve(nonce, difficulty, timeoutMs = 20000) {
  if (difficulty <= 0) return "0";
  const o = nonce + ":";
  const t0 = Date.now();
  let s = 0;
  for (;;) {
    for (let c = 0; c < 1024; c++) {
      const d = gr(yr(o + s));
      if (wr(d) >= difficulty) return String(s);
      s++;
    }
    if (Date.now() - t0 > timeoutMs) return null;
    await new Promise(r => setTimeout(r, 0));
  }
}

const [nonce, diff, tmo] = process.argv.slice(2);
solve(nonce, parseInt(diff, 10), parseInt(tmo || "20000", 10))
  .then(s => { process.stdout.write(s === null ? "TIMEOUT" : s); process.exit(0); })
  .catch(e => { process.stdout.write("ERROR:" + e.message); process.exit(1); });
