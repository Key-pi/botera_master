// Synthesizes the soundtrack: soft pop loop in F major, 120 BPM, with in-key SFX. Writes track.wav.
import fs from 'fs';
const SR = 44100, DUR = 23, N = SR * DUR;
const L = new Float32Array(N), R = new Float32Array(N);
const fx = new Float32Array(N); // mono send for SFX (goes through delay)
const mtof = m => 440 * Math.pow(2, (m - 69) / 12);
let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647 * 2 - 1;
const add = (buf, i, v) => { if (i >= 0 && i < N) buf[i] += v; };

const BEAT = 0.5, BAR = 2;
// Fmaj7, Dm7, Bbmaj7, C(add9)
const CH = [[53, 57, 60, 64], [50, 53, 57, 60], [46, 50, 53, 57], [48, 52, 55, 62]];
const ROOT = [41, 38, 34, 36];

// Pad: detuned saws through a one-pole lowpass, per bar
function pad(t0, notes, len, gain) {
  const n0 = Math.floor(t0 * SR), n = Math.floor(len * SR);
  let lpL = 0, lpR = 0; const ph = notes.map(() => [Math.random(), Math.random()]);
  for (let i = 0; i < n; i++) {
    const t = i / SR, env = Math.min(1, t / 0.25) * Math.min(1, (len - t) / 0.35);
    let sL = 0, sR = 0;
    notes.forEach((m, k) => { const f = mtof(m + 12);
      ph[k][0] = (ph[k][0] + f * 1.003 / SR) % 1; ph[k][1] = (ph[k][1] + f * 0.997 / SR) % 1;
      sL += 2 * ph[k][0] - 1; sR += 2 * ph[k][1] - 1; });
    const c = 0.045; lpL += c * (sL - lpL); lpR += c * (sR - lpR);
    add(L, n0 + i, lpL * env * gain); add(R, n0 + i, lpR * env * gain);
  }
}
function tone(t0, f, len, gain, dest = null, harm = 0.3, pan = 0) {
  const n0 = Math.floor(t0 * SR), n = Math.floor(len * SR);
  for (let i = 0; i < n; i++) { const t = i / SR;
    const env = Math.min(1, t / 0.004) * Math.exp(-t * 7 / len);
    const s = (Math.sin(2 * Math.PI * f * t) + harm * Math.sin(4 * Math.PI * f * t) * Math.exp(-t * 12)) * env * gain;
    if (dest) add(dest, n0 + i, s);
    add(L, n0 + i, s * (1 - pan) ); add(R, n0 + i, s * (1 + pan));
  }
}
function kick(t0, g) { const n0 = Math.floor(t0 * SR); let ph = 0;
  for (let i = 0; i < SR * 0.35; i++) { const t = i / SR, f = 45 + 80 * Math.exp(-t * 30); ph += 2 * Math.PI * f / SR;
    const s = Math.sin(ph) * Math.exp(-t * 9) * g; add(L, n0 + i, s); add(R, n0 + i, s); } }
function noiseHit(t0, len, g, hp, pan = 0, dest = null, attack = 0.001) { const n0 = Math.floor(t0 * SR); let prev = 0, lp = 0;
  for (let i = 0; i < SR * len; i++) { const t = i / SR, x = rnd(); const h = x - prev * hp; prev = x; lp += 0.5 * (h - lp);
    const env = Math.min(1, t / attack) * Math.exp(-t * 5 / len); const s = lp * env * g;
    if (dest) add(dest, n0 + i, s); add(L, n0 + i, s * (1 - pan)); add(R, n0 + i, s * (1 + pan)); } }
function whoosh(tEnd, len, g) { const n0 = Math.floor((tEnd - len) * SR); let lp = 0;
  for (let i = 0; i < SR * len; i++) { const t = i / SR, env = Math.pow(t / len, 2.2) * (1 - Math.pow(t / len, 8));
    const c = 0.02 + 0.2 * (t / len); lp += c * (rnd() - lp); const s = lp * env * g; add(L, n0 + i, s); add(R, n0 + i, s * 0.9); } }

// --- Music ---
for (let bar = 0; bar < 11; bar++) { const t = bar * BAR, c = bar % 4;
  const last = bar >= 10; pad(t, last ? [53, 57, 60, 64, 67] : CH[c], last ? 3 : BAR + 0.05, last ? 0.05 : 0.042);
  if (bar >= 1 && bar < 10) { // bass from 2s (under the drop at 3s it's full)
    tone(t, mtof(ROOT[c]), 0.45, 0.22, null, 0.5); tone(t + 0.75, mtof(ROOT[c]), 0.35, 0.16, null, 0.5);
    tone(t + 1.0, mtof(ROOT[c] + 12), 0.3, 0.1, null, 0.5); tone(t + 1.5, mtof(ROOT[c]), 0.4, 0.16, null, 0.5);
  }
}
// sparse arpeggio in the hook (0–3s)
[[0, 65], [0.5, 69], [1.0, 72], [1.5, 76], [2.0, 74], [2.5, 72]].forEach(([t, m]) => tone(t, mtof(m), 0.6, 0.07, fx, 0.2, 0.2));
// drums from the drop at 3.0 until 20.0
for (let t = 3; t < 20; t += BEAT) { kick(t, 0.5);
  const b = Math.round((t - 3) / BEAT); if (b % 2 === 1) noiseHit(t, 0.18, 0.12, 0.2, 0, null); // soft clap on 2 & 4
  noiseHit(t + 0.25, 0.05, 0.05, 0.98, 0.3); }
kick(20, 0.55); noiseHit(20, 1.4, 0.06, 0.95, 0, fx, 0.002); // outro hit
whoosh(3.0, 0.7, 0.35); whoosh(20.0, 0.8, 0.3);
for (const tt of [7, 12.5, 16.5]) whoosh(tt, 0.35, 0.14);

// --- SFX in F major pentatonic ---
const pl = (t, m, g = 0.08) => tone(t, mtof(m), 0.35, g, fx, 0.35, -0.15);
const tap = t => { tone(t, mtof(84), 0.08, 0.035, fx, 0, 0.2); };
// S1 chips
for (let i = 0; i < 7; i++) tone(1.0 + i * 0.08, mtof([77, 79, 81, 84, 86, 89, 91][i]), 0.2, 0.025, fx, 0, (i - 3) * 0.1);
// S2 checks + launch
[0.55, 0.67, 0.79, 0.91].forEach((d, i) => pl(3 + d, [72, 74, 77, 79][i], 0.04));
for (let i = 0; i < 12; i++) tone(4.2 + i * 0.066, mtof(96), 0.02, 0.012, null, 0, 0.3); // typing
[65, 69, 72, 77].forEach((m, i) => tone(5.35 + i * 0.03, mtof(m + 12), 0.9, 0.05, fx, 0.3)); // launched chord
// S3 messages & taps
[0.3, 1.5, 2.8, 4.0].forEach((d, i) => pl(7 + d, [72, 74, 77, 81][i], 0.06));
[1.2, 3.7].forEach(d => pl(7 + d, 79, 0.035));
[1.0, 2.0, 2.2, 2.5, 3.5].forEach(d => tap(7 + d));
[77, 81, 84].forEach((m, i) => tone(11.0 + i * 0.05, mtof(m), 1.0, 0.045, fx, 0.3)); // booked!
// S4 new appointment + confirm
[72, 77].forEach((m, i) => pl(13.2 + i * 0.07, m, 0.055));
tap(14.8); [74, 79, 83].forEach((m, i) => tone(14.95 + i * 0.05, mtof(m + (m === 83 ? 1 : 0)), 0.7, 0.04, fx, 0.3));
// S5 tiles + notifications
[0, 1, 2, 3].forEach(i => pl(16.85 + i * 0.12, [69, 72, 74, 77][i], 0.03));
[17.0, 17.6].forEach((t, i) => { pl(t, 84, 0.04); pl(t + 0.08, 81, 0.03); });
// S6 logo
[65, 72, 77, 81, 84].forEach((m, i) => tone(20.05 + i * 0.06, mtof(m), 2.2, 0.04, fx, 0.25));

// --- FX bus: stereo ping-pong delay (dotted eighth) + small diffusion ---
const D = Math.floor(0.375 * SR); const dl = new Float32Array(N), dr = new Float32Array(N);
for (let i = 0; i < N; i++) { const inL = fx[i];
  const fbL = i >= D ? dr[i - D] * 0.38 : 0, fbR = i >= D ? dl[i - D] * 0.38 : 0;
  dl[i] = inL * 0.5 + fbL; dr[i] = fbR; }
let lpl = 0, lpr = 0;
for (let i = 0; i < N; i++) { lpl += 0.25 * (dl[i] - lpl); lpr += 0.25 * (dr[i] - lpr); L[i] += lpl * 0.35; R[i] += lpr * 0.35; }
// short reverb-ish comb on the master
const combs = [1557, 1617, 1491, 1422].map(n => ({ n, b: new Float32Array(n), i: 0 }));
for (let i = 0; i < N; i++) { const x = (L[i] + R[i]) * 0.5; let y = 0;
  for (const c of combs) { const o = c.b[c.i]; c.b[c.i] = x + o * 0.72; c.i = (c.i + 1) % c.n; y += o; }
  L[i] += y * 0.035; R[i] += y * 0.03; }

// master: fade tail, soft clip, normalize
let pk = 0;
for (let i = 0; i < N; i++) { const t = i / SR, f = t > 22.2 ? Math.max(0, (23 - t) / 0.8) : 1;
  L[i] = Math.tanh(L[i] * 1.4 * f); R[i] = Math.tanh(R[i] * 1.4 * f); pk = Math.max(pk, Math.abs(L[i]), Math.abs(R[i])); }
const g = 0.89 / pk; const buf = Buffer.alloc(44 + N * 4);
buf.write('RIFF', 0); buf.writeUInt32LE(36 + N * 4, 4); buf.write('WAVEfmt ', 8); buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20);
buf.writeUInt16LE(2, 22); buf.writeUInt32LE(SR, 24); buf.writeUInt32LE(SR * 4, 28); buf.writeUInt16LE(4, 32); buf.writeUInt16LE(16, 34);
buf.write('data', 36); buf.writeUInt32LE(N * 4, 40);
for (let i = 0; i < N; i++) { buf.writeInt16LE(Math.round(L[i] * g * 32767), 44 + i * 4); buf.writeInt16LE(Math.round(R[i] * g * 32767), 46 + i * 4); }
fs.writeFileSync('track.wav', buf); console.log('ok peak', pk.toFixed(2));
