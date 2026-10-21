"""Copia in Python del modulo embed_hash, usata dal server solo per le ricerche.

Deve restare allineata a moduli-sorgenti/embed_hash.c (stesso hashing, stesse
caratteristiche). Un test controlla che i due diano gli stessi vettori.
"""
import numpy as np

DIM = 256


def _fnv1a(b: bytes, seed: int) -> int:
    h = (2166136261 ^ seed) & 0xFFFFFFFF
    for c in b:
        h ^= c
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def _is_word_byte(c: int) -> bool:
    return c >= 0x80 or 48 <= c <= 57 or 65 <= c <= 90 or 97 <= c <= 122


def embed_record(text_bytes: bytes) -> np.ndarray:
    v = np.zeros(DIM, dtype=np.float32)
    s = text_bytes
    n = len(s)
    i = 0
    while i < n:
        while i < n and (s[i] == 32 or not _is_word_byte(s[i])):
            i += 1
        st = i
        while i < n and s[i] != 32 and _is_word_byte(s[i]):
            i += 1
        if i > st:
            h = _fnv1a(s[st:i], 0x9E3779B9)
            v[h % DIM] += np.float32(-1.0 if h & 0x80000000 else 1.0)
    if n >= 3:
        for j in range(n - 2):
            h = _fnv1a(s[j:j + 3], 0x85EBCA6B)
            v[h % DIM] += np.float32(-0.5 if h & 0x80000000 else 0.5)
    norm = float(np.dot(v, v))
    if norm > 0:
        v *= np.float32(1.0 / np.sqrt(np.float32(norm)))
    return v


def embed_query(text: str) -> np.ndarray:
    """Il testo di ricerca viene pulito come fa text_clean (minuscole, spazi)."""
    t = " ".join(text.lower().split())
    return embed_record(t.encode("utf-8"))
