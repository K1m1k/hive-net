/* SPDX-License-Identifier: AGPL-3.0-or-later  (commercial licence available: see COMMERCIAL-LICENSE.md) */
/*
 * text_stats v1 - statistics for a packet of texts.
 * Output (text): "records chars words sentences", space-separated.
 * chars counts UTF-8 characters (not bytes); empty records are not counted.
 */
#include "common.h"

EXPORT(run) u8 *run(const u8 *in, u32 n) {
    u32 records = 0, chars = 0, words = 0, sentences = 0;
    int in_word = 0, rec_has = 0, sent_open = 0;
    for (u32 i = 0; i <= n; i++) {
        u8 c = i < n ? in[i] : '\n';
        if (c == '\n') {
            if (rec_has) records++;
            if (sent_open) sentences++;
            in_word = 0; rec_has = 0; sent_open = 0;
            continue;
        }
        if ((c & 0xC0) != 0x80) chars++;
        int sep = is_space(c) || c == ',' || c == ';' || c == ':' || c == '"' || c == '(' || c == ')';
        int end = (c == '.' || c == '!' || c == '?');
        if (sep || end) {
            in_word = 0;
            if (end && sent_open) { sentences++; sent_open = 0; }
        } else {
            rec_has = 1;
            if (!in_word) { words++; in_word = 1; sent_open = 1; }
        }
    }
    Buf b; buf_init(&b, 64);
    buf_u32(&b, records); buf_put(&b, ' ');
    buf_u32(&b, chars); buf_put(&b, ' ');
    buf_u32(&b, words); buf_put(&b, ' ');
    buf_u32(&b, sentences);
    g_out_len = b.len;
    return b.p;
}
