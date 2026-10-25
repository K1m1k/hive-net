/* SPDX-License-Identifier: AGPL-3.0-or-later  (commercial licence available: see COMMERCIAL-LICENSE.md) */
/* HOSTILE modules, used only by the bee's sandbox tests. They are never signed or distributed.
 * Build (from bee-node/test/hostile):  sh build.sh
 *   LOOP     -> never ends: the sandbox must stop it when time runs out
 *   (default)-> keeps asking for memory: it must stop at its declared limit
 *   IMPORTS  -> asks the system for a function: it must be rejected
 */
#define EXPORT(n) __attribute__((export_name(n)))
static unsigned char buf[16];
EXPORT("abi_version") int abi_version(void) { return 1; }
EXPORT("reset") void reset(void) {}
EXPORT("alloc") unsigned char *alloc(unsigned n) { (void)n; return buf; }
EXPORT("out_len") unsigned out_len(void) { return 0; }
#if defined(LOOP)
EXPORT("run") unsigned char *run(unsigned char *p, unsigned n) { volatile unsigned i = 0; for (;;) i++; return p; }
#elif defined(IMPORTS)
__attribute__((import_module("env"), import_name("system_call"))) void system_call(void);
EXPORT("run") unsigned char *run(unsigned char *p, unsigned n) { system_call(); return p; }
#else
EXPORT("run") unsigned char *run(unsigned char *p, unsigned n) {
  for (;;) {
    int prev = __builtin_wasm_memory_grow(0, 1);
    if (prev < 0) break;
    ((volatile unsigned char *)0)[prev * 65536u + 100] = 1;
  }
  return p;
}
#endif
