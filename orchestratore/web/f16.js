// Conversione float32 -> float16 identica a numpy (arrotondamento al pari piu' vicino).
// Serve alla "prova del nove" per confrontare la mappa dei significati.
(function (g) {
  const f = new Float32Array(1), u = new Uint32Array(f.buffer);
  function toHalf(v) {
    f[0] = v;
    const x = u[0];
    const sign = (x >>> 16) & 0x8000;
    const e = (x >>> 23) & 0xff;
    let m = x & 0x7fffff;
    if (e === 0xff) return sign | 0x7c00 | (m ? 0x200 : 0);
    const ne = e - 127 + 15;
    if (ne >= 0x1f) return sign | 0x7c00;
    if (ne <= 0) {
      if (ne < -10) return sign;
      m |= 0x800000;
      const shift = 14 - ne;
      let hm = m >>> shift;
      const rem = m & ((1 << shift) - 1);
      const half = 1 << (shift - 1);
      if (rem > half || (rem === half && (hm & 1))) hm++;
      return sign | hm;
    }
    const hm = m >>> 13;
    const rem = m & 0x1fff;
    let h = sign | (ne << 10) | hm;
    if (rem > 0x1000 || (rem === 0x1000 && (hm & 1))) h++;
    return h;
  }
  function float32ToF16Bytes(arr) {
    const out = new Uint8Array(arr.length * 2);
    for (let i = 0; i < arr.length; i++) { const h = toHalf(arr[i]); out[2 * i] = h & 0xff; out[2 * i + 1] = h >>> 8; }
    return out;
  }
  g.F16 = { toHalf, float32ToF16Bytes };
  if (typeof module !== 'undefined') module.exports = g.F16;
})(typeof window !== 'undefined' ? window : globalThis);
