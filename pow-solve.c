/* pow-solve.c — port C dari solver PoW Byse (pow-solve.js / pow-DEJGtdh2.js).
 * Kompile: gcc -O2 -o pow-solve pow-solve.c
 * Pakai:   ./pow-solve <nonce> <difficulty> [timeout_ms]
 * Output:  solution (angka) di stdout, atau "TIMEOUT".
 * Identik bit-per-bit dgn versi JS (uint32 ops, urutan loop sama). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>

#define BE 512u
#define LT (BE - 1)
#define DR 2u
#define LR 2654435761u
#define HR 2246822519u

static inline uint32_t rotl(uint32_t t, uint32_t e) {
    e &= 31u;
    if (e == 0) return t;
    return (t << e) | (t >> (32u - e));
}
static inline uint32_t mul32(uint32_t a, uint32_t b) {
    return a * b; /* uint32 * uint32 = low 32 bit (cukup, unsigned wrap) */
}
static inline uint32_t clz32(uint32_t x) {
    if (x == 0) return 32;
    uint32_t n = 0;
    if (x <= 0x0000FFFF) { n += 16; x <<= 16; }
    if (x <= 0x00FFFFFF) { n += 8;  x <<= 8;  }
    if (x <= 0x0FFFFFFF) { n += 4;  x <<= 4;  }
    if (x <= 0x3FFFFFFF) { n += 2;  x <<= 2;  }
    if (x <= 0x7FFFFFFF) { n += 1; }
    return n;
}

/* ye — campur state 4 word */
static inline void ye(uint32_t t[4]) {
    t[0] = t[0] + t[1];
    t[3] = rotl(t[3] ^ t[0], 16);
    t[2] = t[2] + t[3];
    t[1] = rotl(t[1] ^ t[2], 12);
    t[0] = t[0] + t[1];
    t[3] = rotl(t[3] ^ t[0], 8);
    t[2] = t[2] + t[3];
    t[1] = rotl(t[1] ^ t[2], 7);
}

/* gr — hash custom, input byte, keluar 8 word */
static void gr(const uint8_t *in, size_t len, uint32_t out[8]) {
    uint32_t e[4] = {1779033703u, 3144134277u, 1013904242u, 2773480762u};
    uint32_t r[BE];
    for (size_t i = 0; i < len; i++) {
        e[0] = e[0] + in[i];
        e[0] = rotl(e[0], 7);
        ye(e);
    }
    for (int i = 0; i < 8; i++) ye(e);
    for (uint32_t i = 0; i < BE; i++) { ye(e); r[i] = e[0] ^ e[2]; }
    for (uint32_t i = 0; i < DR; i++) {
        for (uint32_t s = 0; s < BE; s++) {
            uint32_t a = r[s] & LT;
            uint32_t c = r[s] + r[a];
            c = rotl(c, 13);
            c = c ^ mul32(r[(s + 1) & LT], LR);
            r[s] = c;
            e[0] = e[0] ^ c;
            ye(e);
        }
    }
    uint32_t o = BE / 8;
    for (uint32_t i = 0; i < 8; i++) {
        ye(e);
        uint32_t s = e[0];
        uint32_t a = i * o;
        for (uint32_t c = 0; c < o; c++) {
            uint32_t d = r[a + c];
            s = s + d;
            s = rotl(s, 5);
            s = s ^ mul32(d, HR);
        }
        out[i] = s ^ e[2];
    }
}

/* wr — hitung bit nol di depan array 8 word */
static inline uint32_t wr(const uint32_t t[8]) {
    uint32_t bits = 0;
    for (int i = 0; i < 8; i++) {
        if (t[i] == 0) { bits += 32; continue; }
        return bits + clz32(t[i]);
    }
    return bits;
}

/* yr — string → byte (charCode & 255) */
static size_t yr(const char *s, uint8_t *buf) {
    size_t n = strlen(s);
    for (size_t i = 0; i < n; i++) buf[i] = (uint8_t)(s[i] & 0xFF);
    return n;
}

int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "pemakaian: %s nonce difficulty [timeout_ms]\n", argv[0]); return 2; }
    const char *nonce = argv[1];
    uint32_t difficulty = (uint32_t)strtoul(argv[2], NULL, 10);
    uint64_t timeout_ms = (argc > 3) ? strtoull(argv[3], NULL, 10) : 20000ull;
    if (difficulty == 0) { printf("0"); return 0; }

    char buf[256];
    uint8_t bytes[256];
    uint32_t h[8];
    struct timespec ts0, ts1;
    clock_gettime(CLOCK_MONOTONIC, &ts0);
    uint64_t t0_ms = (uint64_t)ts0.tv_sec * 1000ull + ts0.tv_nsec / 1000000ull;

    for (uint64_t s = 0; ; s++) {
        int n = snprintf(buf, sizeof buf, "%s:%llu", nonce, (unsigned long long)s);
        size_t len = yr(buf, bytes);
        gr(bytes, len, h);
        if (wr(h) >= difficulty) { printf("%llu", (unsigned long long)s); return 0; }
        if ((s & 1023u) == 1023u) {
            clock_gettime(CLOCK_MONOTONIC, &ts1);
            uint64_t t1_ms = (uint64_t)ts1.tv_sec * 1000ull + ts1.tv_nsec / 1000000ull;
            if (t1_ms - t0_ms > timeout_ms) { printf("TIMEOUT"); return 1; }
        }
    }
}
