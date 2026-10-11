// Lo-fi house bed in A minor, 100 BPM, FM electric piano, soft drums, vinyl texture + in-key SFX.
import fs from 'fs';
const SR = 44100, DUR = 55.2, N = Math.floor(SR * DUR);
const L = new Float32Array(N), R = new Float32Array(N), fx = new Float32Array(N);
const mtof = m => 440 * Math.pow(2, (m - 69) / 12);
let seed = 3; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647 * 2 - 1;
const add = (b, i, v) => { if (i >= 0 && i < N) b[i] += v; };
const BEAT = 0.6, BAR = 2.4, DROP = 3.6, END = 51;

function epiano(t0, m, len, g, pan = 0, send = 0) {
  const f = mtof(m), n0 = Math.floor(t0 * SR), n = Math.floor(len * SR);
  for (let i = 0; i < n; i++) { const t = i / SR;
    const I = 0.2 + 1.3 * Math.exp(-t * 6);
    const env = Math.min(1, t / 0.005) * Math.exp(-t * 1.6) * Math.min(1, (len - t) / 0.08);
    const s = Math.sin(2 * Math.PI * f * t + I * Math.sin(2 * Math.PI * f * t)) * env * g;
    const trem = 1 + 0.25 * Math.sin(2 * Math.PI * 4.2 * t0 + 2 * Math.PI * 4.2 * t);
    add(L, n0 + i, s * (1 - pan) * trem); add(R, n0 + i, s * (1 + pan) * (2 - trem));
    if (send) add(fx, n0 + i, s * send);
  }
}
function sub(t0, m, len, g) { const f = mtof(m), n0 = Math.floor(t0 * SR);
  for (let i = 0; i < len * SR; i++) { const t = i / SR, env = Math.min(1, t / 0.01) * Math.exp(-t * 2.2) * Math.min(1, (len - t) / 0.05);
    const s = Math.tanh(1.6 * Math.sin(2 * Math.PI * f * t)) * env * g; add(L, n0 + i, s); add(R, n0 + i, s); } }
function kick(t0, g) { const n0 = Math.floor(t0 * SR); let ph = 0;
  for (let i = 0; i < SR * 0.4; i++) { const t = i / SR, f = 48 + 70 * Math.exp(-t * 28); ph += 2 * Math.PI * f / SR;
    const s = Math.sin(ph) * Math.exp(-t * 7) * g; add(L, n0 + i, s); add(R, n0 + i, s); } }
function noise(t0, len, g, hp, pan = 0, send = 0, lpC = 0.5) { const n0 = Math.floor(t0 * SR); let pr = 0, lp = 0;
  for (let i = 0; i < SR * len; i++) { const t = i / SR, x = rnd(), h = x - pr * hp; pr = x; lp += lpC * (h - lp);
    const s = lp * Math.min(1, t / 0.002) * Math.exp(-t * 5 / len) * g;
    add(L, n0 + i, s * (1 - pan)); add(R, n0 + i, s * (1 + pan)); if (send) add(fx, n0 + i, s * send); } }
function whoosh(tEnd, len, g) { const n0 = Math.floor((tEnd - len) * SR); let lp = 0;
  for (let i = 0; i < SR * len; i++) { const t = i / SR, e = Math.pow(t / len, 2.2) * (1 - Math.pow(t / len, 8));
    lp += (0.015 + 0.12 * t / len) * (rnd() - lp); add(L, n0 + i, lp * e * g); add(R, n0 + i, lp * e * g * 0.9); } }

// harmony: Am9 – Fmaj9 – Cmaj7 – G6
const CH = [[53, 57, 60, 64, 67], [50, 53, 57, 60, 64], [46, 50, 53, 57, 60], [48, 52, 55, 57, 62]];
const RT = [41, 38, 34, 36];
const nBars = Math.ceil(DUR / BAR);
for (let b = 0; b < nBars; b++) { const t = b * BAR, c = b % 4, outro = t >= END;
  if (outro) { if (t < END + 0.01 || b === Math.floor(END / BAR) + 1 && false) {} }
  if (t >= END) break;
  CH[c].forEach((m, k) => epiano(t + k * 0.012, m, 1.5, 0.05, (k - 2) * 0.12, 0.15));
  CH[c].slice(1, 4).forEach((m, k) => epiano(t + 1.5 + k * 0.01, m + 12, 0.8, 0.022, (k - 1) * 0.2, 0.2));
  if (t >= DROP - 0.01) { sub(t, RT[c], 1.0, 0.3); sub(t + 1.5, RT[c], 0.5, 0.2); sub(t + 2.1, RT[c] + 7, 0.3, 0.14); }
}
// outro chord rings
[41, 53, 57, 60, 64, 67, 72].forEach((m, k) => epiano(END + k * 0.03, m, 4.2, 0.05, (k - 3) * 0.1, 0.3));
sub(END, 41, 3.0, 0.3);
// drums: half-time kick, soft clap on 2 & 4, swung shaker
for (let t = DROP; t < END - 0.01; t += BEAT) { const b = Math.round((t - DROP) / BEAT) % 4;
  if (b === 0) kick(t, 0.55); if (b === 2) kick(t + 0.45, 0.3);
  if (b === 1 || b === 3) noise(t, 0.22, 0.13, 0.3, 0, 0.35, 0.35);
  for (let q = 0; q < 4; q++) { const sw = q % 2 ? 0.035 : 0; noise(t + q * 0.15 + sw, 0.04, q % 2 ? 0.03 : 0.045, 0.97, 0.25); }
}
kick(END, 0.5);
// vinyl texture
for (let i = 0; i < N; i++) { const s = rnd() * 0.0035; L[i] += s; R[i] += s; }
for (let k = 0; k < 140; k++) { const t = Math.abs(rnd()) * DUR; noise(t, 0.004, 0.08 * Math.abs(rnd()), 0.9, rnd() * 0.5); }
whoosh(DROP, 0.8, 0.3); whoosh(END, 0.9, 0.25);
for (const tt of [8.4, 14.4, 19.8, 25.8, 31.2, 36.6, 42, 46.8]) whoosh(tt, 0.45, 0.12);

// SFX (F major pentatonic)
const PEN = [65, 67, 69, 72, 74, 77, 79, 81, 84, 86];
const pl = (t, m, g = 0.05) => epiano(t, m, 0.6, g, -0.1, 0.45);
const tap = t => noise(t, 0.012, 0.05, 0.9, 0.2);
const chord = (t, ms, g = 0.035) => ms.forEach((m, i) => epiano(t + i * 0.045, m, 1.4, g, (i - 1) * 0.2, 0.5));
for (let i = 0; i < 7; i++) pl(1.1 + i * 0.09, PEN[i + 1], 0.022);
for (let i = 0; i < 4; i++) pl(4.15 + i * 0.12, PEN[i + 2], 0.03);
for (let i = 0; i < 14; i++) noise(5.0 + i * 0.07, 0.006, 0.02, 0.95, 0.3);
tap(6.3); chord(6.45, [77, 81, 84]);
[8.7, 9.6, 9.9, 11.2, 12.1].forEach((t, i) => pl(t, PEN[i + 2], 0.045)); [9.4, 10.4, 10.6, 10.9, 11.9].forEach(t => tap(t)); chord(12.4, [77, 81, 84]);
pl(14.9, 74, 0.045); tap(15.8); pl(16.0, 77, 0.04); tap(16.3); tap(16.5); pl(16.8, 79, 0.03); pl(17.2, 72, 0.04); tap(18.2); chord(18.5, [77, 81, 84]);
pl(20.4, 77, 0.04); pl(20.47, 81, 0.03); tap(21.6); pl(21.8, 74, 0.035); tap(23.2); chord(23.25, [79, 84, 88], 0.03);
tap(27.0); pl(27.2, 74, 0.04); pl(28.2, 77, 0.045); pl(29.2, 81, 0.04); pl(29.6, 84, 0.025);
for (let i = 0; i < 4; i++) pl(31.55 + i * 0.15, PEN[i + 1], 0.025); [31.9, 32.6, 33.3].forEach((t, i) => { pl(t, PEN[6 + i], 0.04); pl(t + 0.08, PEN[4 + i], 0.03); });
for (let i = 0; i < 4; i++) pl(37.2 + i * 0.15, PEN[i + 2], 0.03); pl(37.4, 79, 0.03); pl(38.0, 81, 0.03);
for (let i = 0; i < 3; i++) pl(42.4 + i * 0.25, PEN[i + 3], 0.035);
for (let i = 0; i < 18; i++) noise(43.3 + i * 0.066, 0.006, 0.018, 0.95, 0.3);
tap(44.8); chord(44.9, [77, 81, 84]);
for (let i = 0; i < 4; i++) chord(47.2 + i * 0.2, [PEN[i + 2], PEN[i + 4], PEN[i + 6]], 0.022);
chord(51.05, [77, 81, 84, 89], 0.03);

// fx bus: ping-pong dotted-eighth delay + comb reverb on master
const D = Math.floor(0.45 * SR), dl = new Float32Array(N), dr = new Float32Array(N);
for (let i = 0; i < N; i++) { dl[i] = fx[i] * 0.5 + (i >= D ? dr[i - D] * 0.4 : 0); dr[i] = i >= D ? dl[i - D] * 0.4 : 0; }
let a = 0, b = 0; for (let i = 0; i < N; i++) { a += 0.2 * (dl[i] - a); b += 0.2 * (dr[i] - b); L[i] += a * 0.4; R[i] += b * 0.4; }
const combs = [1687, 1601, 2053, 2251].map(n => ({ n, buf: new Float32Array(n), i: 0 }));
for (let i = 0; i < N; i++) { const x = (L[i] + R[i]) * 0.5; let y = 0;
  for (const c of combs) { const o = c.buf[c.i]; c.buf[c.i] = x + o * 0.78; c.i = (c.i + 1) % c.n; y += o; }
  L[i] += y * 0.03; R[i] += y * 0.026; }
// master: gentle lowpass warmth, fade, soft clip, normalize
let lpL = 0, lpR = 0, pk = 0;
for (let i = 0; i < N; i++) { const t = i / SR, f = Math.min(1, t / 0.3) * (t > DUR - 1.2 ? Math.max(0, (DUR - t) / 1.2) : 1);
  lpL += 0.55 * (L[i] - lpL); lpR += 0.55 * (R[i] - lpR);
  L[i] = Math.tanh(lpL * 1.5 * f); R[i] = Math.tanh(lpR * 1.5 * f); pk = Math.max(pk, Math.abs(L[i]), Math.abs(R[i])); }
const g = 0.89 / pk, out = Buffer.alloc(44 + N * 4);
out.write('RIFF', 0); out.writeUInt32LE(36 + N * 4, 4); out.write('WAVEfmt ', 8); out.writeUInt32LE(16, 16); out.writeUInt16LE(1, 20);
out.writeUInt16LE(2, 22); out.writeUInt32LE(SR, 24); out.writeUInt32LE(SR * 4, 28); out.writeUInt16LE(4, 32); out.writeUInt16LE(16, 34);
out.write('data', 36); out.writeUInt32LE(N * 4, 40);
for (let i = 0; i < N; i++) { out.writeInt16LE(Math.round(L[i] * g * 32767), 44 + i * 4); out.writeInt16LE(Math.round(R[i] * g * 32767), 46 + i * 4); }
fs.writeFileSync('track-v2.wav', out); console.log('ok peak', pk.toFixed(2));
