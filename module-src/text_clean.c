/* SPDX-License-Identifier: AGPL-3.0-or-later  (commercial licence available: see COMMERCIAL-LICENSE.md) */
/*
 * text_clean v1 - text cleaning.
 * Input: records separated by '\n' (UTF-8).
 * For each record: removes control characters, collapses whitespace,
 * trims leading/trailing spaces, lowercases ASCII letters and accented
 * Latin capitals (À..Þ). The number of records is preserved.
 */
#include "common.h"

EXPORT(run) u8 *run(const u8 *in, u32 n) {
    Buf b; buf_init(&b, n + 16);
    int pending_space = 0, at_start = 1;
    for (u32 i = 0; i < n; i++) {
        u8 c = in[i];
        if (c == '\n') { buf_put(&b, '\n'); pending_space = 0; at_start = 1; continue; }
        if (is_space(c) || c < 0x20 || c == 0x7f) { if (!at_start) pending_space = 1; continue; }
        if (pending_space) { buf_put(&b, ' '); pending_space = 0; }
        at_start = 0;
        if (c >= 'A' && c <= 'Z') { buf_put(&b, (u8)(c + 32)); continue; }
        /* UTF-8: 0xC3 0x80..0x9E are À..Þ (except 0x97 = ×) */
        if (c == 0xC3 && i + 1 < n) {
            u8 d = in[i + 1];
            if (d >= 0x80 && d <= 0x9E && d != 0x97) d = (u8)(d + 0x20);
            buf_put(&b, c); buf_put(&b, d); i++; continue;
        }
        buf_put(&b, c);
    }
    g_out_len = b.len;
    return b.p;
}
