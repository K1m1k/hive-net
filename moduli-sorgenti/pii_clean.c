/*
 * pii_clean v1 - rimozione dei dati personali riconoscibili da schema.
 * Gira sul nodo di origine o nel browser PRIMA del caricamento.
 * Sostituisce:
 *   email                     -> [EMAIL]
 *   numeri di telefono         -> [TELEFONO]   (7+ cifre con + spazi - . / ( ))
 *   IBAN (2 lettere + 2 cifre + 11..30 alfanumerici) -> [IBAN]
 *   codice fiscale italiano    -> [CF]
 * Limite noto: NON riconosce i nomi di persona (servirebbe un modello IA).
 */
#include "common.h"

static int is_alnum(u8 c) { return is_digit(c) || is_alpha_ascii(c); }
static int is_upper(u8 c) { return c >= 'A' && c <= 'Z'; }
static u8 up(u8 c) { return (c >= 'a' && c <= 'z') ? (u8)(c - 32) : c; }
static int email_local(u8 c) { return is_alnum(c) || c == '.' || c == '_' || c == '-' || c == '+' || c == '%'; }
static int email_dom(u8 c) { return is_alnum(c) || c == '.' || c == '-'; }

/* Email: restituisce la lunghezza del match che inizia in s, 0 se assente. */
static u32 match_email(const u8 *s, u32 n, u32 start_ok) {
    if (!start_ok) return 0;
    u32 i = 0;
    while (i < n && email_local(s[i])) i++;
    if (i == 0 || i >= n || s[i] != '@') return 0;
    u32 j = i + 1;
    while (j < n && email_dom(s[j])) j++;
    while (j > i + 1 && (s[j - 1] == '.' || s[j - 1] == '-')) j--;   /* punto finale della frase */
    u32 dots = 0, last_dot = 0;
    for (u32 k = i + 1; k < j; k++) if (s[k] == '.') { dots++; last_dot = k; }
    if (dots == 0 || last_dot == i + 1 || last_dot + 3 > j) return 0;   /* dominio finale di almeno 2 lettere */
    return j;
}

/* Codice fiscale: 6 lettere, 2 cifre, 1 lettera, 2 cifre, 1 lettera, 3 cifre, 1 lettera. */
static u32 match_cf(const u8 *s, u32 n) {
    static const char pat[] = "LLLLLLDDLDDLDDDL";
    if (n < 16) return 0;
    for (u32 i = 0; i < 16; i++) {
        u8 c = up(s[i]);
        if (pat[i] == 'L' && !is_upper(c)) return 0;
        if (pat[i] == 'D' && !is_digit(c)) return 0;
    }
    if (n > 16 && is_alnum(s[16])) return 0;
    return 16;
}

/* IBAN: 2 lettere + 2 cifre + 11..30 alfanumerici (spazi ammessi a gruppi). */
static u32 match_iban(const u8 *s, u32 n) {
    if (n < 15) return 0;
    if (!is_upper(up(s[0])) || !is_upper(up(s[1])) || !is_digit(s[2]) || !is_digit(s[3])) return 0;
    /* gli spazi sono ammessi solo tra gruppi di 4 caratteri (formato stampato) */
    u32 i = 4, cnt = 0, end = 4, group = 4;
    while (i < n && cnt < 30) {
        if (is_alnum(s[i])) { cnt++; i++; end = i; group++; }
        else if (s[i] == ' ' && group == 4 && i + 1 < n && is_alnum(s[i + 1])) { i++; group = 0; }
        else break;
    }
    if (cnt < 11) return 0;
    /* serve almeno qualche cifra nel corpo per non scambiare parole per IBAN */
    u32 digits = 0;
    for (u32 k = 4; k < end; k++) if (is_digit(s[k])) digits++;
    if (digits < 6) return 0;
    return end;
}

/* Telefono: + opzionale, poi cifre e separatori, almeno 7 cifre, max 15. */
static u32 match_phone(const u8 *s, u32 n) {
    u32 i = 0, digits = 0, end = 0;
    if (i < n && s[i] == '+') i++;
    if (i >= n || !(is_digit(s[i]) || s[i] == '(')) return 0;
    while (i < n) {
        u8 c = s[i];
        if (is_digit(c)) { digits++; i++; end = i; if (digits > 15) return 0; }
        else if ((c == ' ' || c == '-' || c == '.' || c == '/' || c == '(' || c == ')') && i + 1 < n &&
                 (is_digit(s[i + 1]) || s[i + 1] == '(' || s[i+1] == ' ')) i++;
        else break;
    }
    if (digits < 7) return 0;
    if (end < n && is_alpha_ascii(s[end])) return 0;
    return end;
}

EXPORT(run) u8 *run(const u8 *in, u32 n) {
    Buf b; buf_init(&b, n + 64);
    u32 i = 0;
    while (i < n) {
        u8 prev = i ? in[i - 1] : ' ';
        int boundary = !is_alnum(prev) && prev != '.' && prev != '_' && prev != '-' && prev != '+';
        u32 m;
        if ((m = match_email(in + i, n - i, boundary))) { buf_str(&b, "[EMAIL]"); i += m; continue; }
        if (!is_alnum(prev)) {
            if ((m = match_iban(in + i, n - i))) { buf_str(&b, "[IBAN]"); i += m; continue; }
            if ((m = match_cf(in + i, n - i))) { buf_str(&b, "[CF]"); i += m; continue; }
            if ((m = match_phone(in + i, n - i))) { buf_str(&b, "[TELEFONO]"); i += m; continue; }
        }
        buf_put(&b, in[i]); i++;
    }
    g_out_len = b.len;
    return b.p;
}
