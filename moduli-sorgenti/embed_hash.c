/*
 * embed_hash v1 - embedding leggeri per hashing delle caratteristiche.
 * Per ogni record: parole e trigrammi di caratteri vengono proiettati in
 * DIM dimensioni con segno pseudo-casuale, poi il vettore viene normalizzato.
 * Testi simili -> vettori vicini (somiglianza coseno).
 * Uscita: per ogni record DIM float32 little-endian (record vuoto = zeri).
 * Sara' sostituito da un modello neurale multilingue (stessa interfaccia).
 */
#include "common.h"

#define DIM 256

static void add_feat(float *v, const u8 *s, u32 len, u32 seed, float w) {
    u32 h = fnv1a(s, len, seed);
    float sign = (h & 0x80000000u) ? -w : w;
    v[h % DIM] += sign;
}

static void embed_record(const u8 *s, u32 n, float *v) {
    for (u32 k = 0; k < DIM; k++) v[k] = 0.0f;
    /* parole */
    u32 i = 0;
    while (i < n) {
        while (i < n && (s[i] == ' ' || (s[i] < 0x80 && !is_alpha_ascii(s[i]) && !is_digit(s[i])))) i++;
        u32 st = i;
        while (i < n && s[i] != ' ' && (s[i] >= 0x80 || is_alpha_ascii(s[i]) || is_digit(s[i]))) i++;
        if (i > st) add_feat(v, s + st, i - st, 0x9e3779b9u, 1.0f);
    }
    /* trigrammi di byte (robusti a piccoli errori di battitura) */
    if (n >= 3) for (u32 j = 0; j + 3 <= n; j++) add_feat(v, s + j, 3, 0x85ebca6bu, 0.5f);
    float norm = 0.0f;
    for (u32 k = 0; k < DIM; k++) norm += v[k] * v[k];
    if (norm > 0.0f) {
        float inv = 1.0f / __builtin_sqrtf(norm);
        for (u32 k = 0; k < DIM; k++) v[k] *= inv;
    }
}

EXPORT(dim) i32 dim(void) { return DIM; }

EXPORT(run) u8 *run(const u8 *in, u32 n) {
    u32 records = 1;
    for (u32 i = 0; i < n; i++) if (in[i] == '\n') records++;
    if (n == 0) records = 0;
    float *out = (float *)alloc(records * DIM * 4);
    u32 r = 0, st = 0;
    for (u32 i = 0; i <= n && r < records; i++) {
        if (i == n || in[i] == '\n') { embed_record(in + st, i - st, out + r * DIM); r++; st = i + 1; }
    }
    g_out_len = records * DIM * 4;
    return (u8 *)out;
}
