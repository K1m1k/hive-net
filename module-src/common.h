/* SPDX-License-Identifier: AGPL-3.0-or-later  (commercial licence available: see COMMERCIAL-LICENSE.md) */
/*
 * HiveNet - shared base for the WebAssembly modules.
 *
 * Modules import NOTHING from the outside world: no files, no network,
 * no clock. They receive bytes in memory and return bytes.
 *
 * Interface exported by every module:
 *   alloc(n)      -> pointer to n free bytes
 *   reset()       -> releases all working memory
 *   run(ptr, len) -> processes the input, returns a pointer to the output
 *   out_len()     -> length of the output produced by run
 *   abi_version() -> interface version (1)
 */
#ifndef HIVENET_COMMON_H
#define HIVENET_COMMON_H

typedef unsigned char u8;
typedef unsigned int u32;
typedef int i32;

#define EXPORT(name) __attribute__((export_name(#name)))

extern u8 __heap_base;

static u32 heap_top = 0;
static u32 g_out_len = 0;

static u32 page_bytes(void) { return (u32)__builtin_wasm_memory_size(0) * 65536u; }

EXPORT(reset) void reset(void) { heap_top = (u32)&__heap_base; g_out_len = 0; }

EXPORT(alloc) u8 *alloc(u32 n) {
    if (heap_top == 0) reset();
    u32 start = (heap_top + 7u) & ~7u;
    u32 end = start + n;
    u32 have = page_bytes();
    if (end > have) {
        u32 need = (end - have + 65535u) / 65536u;
        if (__builtin_wasm_memory_grow(0, need) == (unsigned long)-1) return 0;
    }
    heap_top = end;
    return (u8 *)start;
}

EXPORT(out_len) u32 out_len(void) { return g_out_len; }
EXPORT(abi_version) i32 abi_version(void) { return 1; }

static void mem_copy(u8 *d, const u8 *s, u32 n) { for (u32 i = 0; i < n; i++) d[i] = s[i]; }

/* Growable output buffer. */
typedef struct { u8 *p; u32 len; u32 cap; } Buf;

static void buf_init(Buf *b, u32 cap) { b->p = alloc(cap); b->len = 0; b->cap = cap; }
static void buf_put(Buf *b, u8 c) {
    if (b->len == b->cap) {
        u32 nc = b->cap * 2 + 16;
        u8 *np = alloc(nc);
        mem_copy(np, b->p, b->len);
        b->p = np; b->cap = nc;
    }
    b->p[b->len++] = c;
}
static void buf_write(Buf *b, const u8 *s, u32 n) { for (u32 i = 0; i < n; i++) buf_put(b, s[i]); }
static void buf_str(Buf *b, const char *s) { while (*s) buf_put(b, (u8)*s++); }
static void buf_u32(Buf *b, u32 v) {
    char tmp[12]; int n = 0;
    if (v == 0) { buf_put(b, '0'); return; }
    while (v) { tmp[n++] = (char)('0' + v % 10); v /= 10; }
    while (n) buf_put(b, (u8)tmp[--n]);
}

static int is_digit(u8 c) { return c >= '0' && c <= '9'; }
static int is_alpha_ascii(u8 c) { return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z'); }
static int is_space(u8 c) { return c == ' ' || c == '\t' || c == '\r' || c == '\f' || c == '\v'; }

/* 32-bit FNV-1a: fast, deterministic hash. */
static u32 fnv1a(const u8 *s, u32 n, u32 seed) {
    u32 h = 2166136261u ^ seed;
    for (u32 i = 0; i < n; i++) { h ^= s[i]; h *= 16777619u; }
    return h;
}

#endif
