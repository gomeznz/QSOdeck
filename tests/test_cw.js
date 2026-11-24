// Run with: node tests/test_cw.js   - checks the CW decoder against generated Morse
const QD = require("../qsodesk/static/qd.js");
const CODE = Object.fromEntries(Object.entries(QD.MORSE).map(([k, v]) => [v, k]));
function gauss() { let u = 0, v = 0; while (!u) u = Math.random(); v = Math.random(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); }
function make(text, wpm, freq, amp, noise) {
  const R = 12000, dot = Math.round(R * 1.2 / wpm), out = [];
  let k = 0;
  const tone = n => { for (let i = 0; i < n; i++) { const env = Math.min(1, i / 60, (n - 1 - i) / 60); out.push(amp * env * Math.sin(2 * Math.PI * freq * k++ / R)); } };
  const gap = n => { for (let i = 0; i < n; i++) { out.push(0); k++; } };
  gap(R / 2);
  for (const word of text.split(" ")) {
    for (const ch of word) { for (const s of CODE[ch]) { tone(s === "." ? dot : 3 * dot); gap(dot); } gap(2 * dot); }
    gap(4 * dot);
  }
  gap(R);
  return Int16Array.from(out, v => Math.max(-32000, Math.min(32000, v + noise * gauss())));
}
function run(samples, freq) {
  let text = "";
  const d = new QD.CWDecoder(c => { text += c; });
  d.setFreq(freq);
  for (let i = 0; i < samples.length; i += 1200) d.push(samples.subarray(i, i + 1200));
  return [text.trim(), d.wpm];
}
let fail = 0;
const msg = "CQ FD DE ZL4TEST ZL4TEST 5NN 014 27 K";
for (const [wpm, amp, noise] of [[12, 4000, 260], [18, 4000, 260], [22, 3500, 260], [28, 3500, 260], [35, 4000, 260], [22, 1500, 260], [22, 900, 260]]) {
  const [got, est] = run(make(msg, wpm, 650, amp, noise), 650);
  const ok = got.endsWith(msg.slice(6));          // allow the first letters while it finds the speed
  if (!ok) fail++;
  console.log((ok ? "ok  " : "FAIL") + ` ${wpm} wpm amp ${amp}: "${got}" (est ${est} wpm)`);
}
const [junk] = run(make("", 20, 650, 0, 260), 650);
const [junk2] = run(Int16Array.from({length: 12000 * 20}, () => 260 * gauss()), 650);
console.log(`noise only, 20 s: "${junk2}" (${junk2.length} chars)`);
if (junk2.length > 3) fail++;
process.exit(fail ? 1 : 0);
