/* Shared code for the QSODesk radio pages: page header, the audio feed from
   the radio, the FFT, the waterfall display and the CW decoder. */
"use strict";
const QD = (() => {
  const RATE = 12000;
  const $ = id => document.getElementById(id);
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const pad = (n, w) => String(n).padStart(w || 2, "0");

  async function api(path, body, timeout) {
    const opt = body ? {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)} : {};
    if (timeout) { const ac = new AbortController(); setTimeout(() => ac.abort(), timeout); opt.signal = ac.signal; }
    const r = await fetch(path, opt);
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || "Request failed");
    return j;
  }

  /* ---------- page header: navigation, radio read-out, clock ---------- */
  const state = {radio: {}, radios: {}, listen: "", handlers: {}};
  function on(name, cb) { (state.handlers[name] = state.handlers[name] || []).push(cb); }
  function emit(name, data) { (state.handlers[name] || []).forEach(cb => cb(data)); }

  function header(active) {
    const links = [["/", "Logger", "log"], ["/dashboard", "Dashboard", "dashboard"], ["/transceivers", "Radios", "radios"], ["/waterfall", "Waterfall", "waterfall"], ["/cw", "CW", "cw"], ["/ft8", "FT8", "ft8"]];
    $("hdr").innerHTML = `<a class="logo" href="/">QSO<span>Desk</span></a><nav>` +
      links.map(l => `<a href="${l[0]}" class="${l[2] === active ? "on" : ""}">${l[1]}</a>`).join("") +
      `</nav><div class="spacer"></div><span class="hfreq" id="h-freq"></span><span class="tag" id="h-mode"></span>` +
      `<span class="tag" id="h-rig"></span>` +
      `<select id="h-listen" title="Transceiver used by the waterfall, CW and FT8 pages" style="display:none;padding:3px 8px;font-size:13px"></select>` +
      `<span class="tag" id="h-audio"></span>` +
      `<div class="clock"><span id="h-clock"></span><small id="h-utc"></small></div>`;
    const tick = () => {
      const d = new Date();
      $("h-clock").textContent = pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
      $("h-utc").textContent = pad(d.getUTCHours()) + ":" + pad(d.getUTCMinutes()) + ":" + pad(d.getUTCSeconds()) + " UTC";
    };
    setInterval(tick, 500); tick();
    const es = new EventSource("/api/events");
    es.addEventListener("radio", e => {
      const r = JSON.parse(e.data);
      state.radios[r.id] = r; emit("anyradio", r);
      if (!state.listen || r.id === state.listen) { state.radio = r; showRadio(); emit("radio", r); }
    });
    es.addEventListener("radios", () => { refreshAudioTag(); emit("radios"); });
    $("h-listen").addEventListener("change", async e => { await api("/api/radios/listen", {id: e.target.value}); refreshAudioTag(); });
    es.addEventListener("log", () => emit("log"));
    es.addEventListener("ft8", () => emit("ft8"));
    refreshAudioTag();
    setInterval(refreshAudioTag, 10000);
  }
  function showRadio() {
    const r = state.radio;
    if (r.freq == null) return;
    $("h-freq").textContent = (r.freq / 1e6).toFixed(4) + " MHz";
    $("h-mode").textContent = (r.band || "?") + " " + (r.rmode || "");
    const t = $("h-rig");
    const many = state.app && state.app.radios.length > 1;      // the selector already names it
    t.textContent = (r.label && !many ? r.label + ": " : "") + (!r.connected ? "NO RADIO" : r.simulated ? "SIMULATED" : r.name);
    t.className = "tag " + (!r.connected ? "bad" : r.simulated ? "sim" : "live");
  }
  async function refreshAudioTag() {
    try {
      const s = await api("/api/state");
      state.app = s;
      if (state.listen !== s.audio_radio) {
        state.listen = s.audio_radio; state.radio = state.radios[s.audio_radio] || s.radio; showRadio(); emit("radio", state.radio);
      }
      const sel = $("h-listen");
      sel.style.display = s.radios.length > 1 ? "" : "none";
      sel.innerHTML = s.radios.map(r => `<option value="${esc(r.id)}" ${r.id === s.audio_radio ? "selected" : ""}>${esc(r.name)}</option>`).join("");
      const t = $("h-audio");
      t.textContent = s.audio.source === "sim" ? "SIM AUDIO" : "LIVE AUDIO";
      t.className = "tag " + (s.audio.source === "sim" ? "sim" : "live");
      t.title = s.audio.note || s.audio.device || "";
      emit("app", s);
    } catch (e) { /* server away - the page keeps trying */ }
  }

  /* ---------- audio feed: 12 kHz 16-bit samples from the Pi ---------- */
  class AudioFeed {
    constructor() { this.subs = []; this.alive = false; }
    on(cb) { this.subs.push(cb); }
    async start() {
      for (;;) {
        try {
          const r = await fetch("/api/audio");
          const reader = r.body.getReader();
          let odd = null;
          this.alive = true;
          for (;;) {
            const {done, value} = await reader.read();
            if (done) break;
            let bytes = value;
            if (odd !== null) { const j = new Uint8Array(bytes.length + 1); j[0] = odd; j.set(bytes, 1); bytes = j; odd = null; }
            if (bytes.length & 1) { odd = bytes[bytes.length - 1]; bytes = bytes.subarray(0, bytes.length - 1); }
            const n = bytes.length >> 1, out = new Int16Array(n);
            const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.length);
            for (let i = 0; i < n; i++) out[i] = dv.getInt16(i * 2, true);
            for (const cb of this.subs) cb(out);
          }
        } catch (e) { /* fall through and reconnect */ }
        this.alive = false;
        await new Promise(res => setTimeout(res, 2000));
      }
    }
  }

  /* ---------- FFT ---------- */
  class FFT {
    constructor(n) {
      this.n = n; this.rev = new Uint32Array(n);
      this.cos = new Float32Array(n / 2); this.sin = new Float32Array(n / 2);
      const bits = Math.log2(n);
      for (let i = 0; i < n; i++) { let r = 0; for (let b = 0; b < bits; b++) r |= ((i >> b) & 1) << (bits - 1 - b); this.rev[i] = r; }
      for (let i = 0; i < n / 2; i++) { this.cos[i] = Math.cos(2 * Math.PI * i / n); this.sin[i] = Math.sin(2 * Math.PI * i / n); }
    }
    forward(re, im) {
      const n = this.n;
      for (let i = 0; i < n; i++) { const j = this.rev[i]; if (j > i) { let t = re[i]; re[i] = re[j]; re[j] = t; t = im[i]; im[i] = im[j]; im[j] = t; } }
      for (let size = 2; size <= n; size <<= 1) {
        const half = size >> 1, step = n / size;
        for (let i = 0; i < n; i += size) {
          for (let j = 0, k = 0; j < half; j++, k += step) {
            const c = this.cos[k], s = this.sin[k], a = i + j, b = a + half;
            const tr = re[b] * c + im[b] * s, ti = im[b] * c - re[b] * s;
            re[b] = re[a] - tr; im[b] = im[a] - ti; re[a] += tr; im[a] += ti;
          }
        }
      }
    }
  }

  /* Turns the sample stream into spectrum rows (dB per bin). */
  class Spectrum {
    constructor(n, hop) {
      this.n = n || 2048; this.hop = hop || 600; this.fft = new FFT(this.n);
      this.buf = new Float32Array(this.n); this.fill = 0; this.since = 0; this.seen = 0;
      this.win = new Float32Array(this.n);
      for (let i = 0; i < this.n; i++) this.win[i] = 0.5 - 0.5 * Math.cos(2 * Math.PI * i / (this.n - 1));
      this.re = new Float32Array(this.n); this.im = new Float32Array(this.n);
      this.db = new Float32Array(this.n / 2); this.subs = [];
      this.binHz = RATE / this.n;
    }
    on(cb) { this.subs.push(cb); }
    push(samples) {
      for (let i = 0; i < samples.length; i++) {
        this.buf[this.fill] = samples[i]; this.fill = (this.fill + 1) % this.n;
        if (this.seen < this.n) this.seen++;
        if (++this.since >= this.hop) { this.since = 0; if (this.seen >= this.n) this.row(); }
      }
    }
    row() {
      const n = this.n;
      for (let i = 0; i < n; i++) { this.re[i] = this.buf[(this.fill + i) % n] * this.win[i]; this.im[i] = 0; }
      this.fft.forward(this.re, this.im);
      for (let i = 0; i < n / 2; i++) this.db[i] = 10 * Math.log10(this.re[i] * this.re[i] + this.im[i] * this.im[i] + 1e-3);
      for (const cb of this.subs) cb(this.db, this.binHz);
    }
  }

  /* ---------- waterfall ---------- */
  const PALETTE = (() => {
    const stops = [[0, 0, 0, 12], [0.22, 10, 34, 120], [0.45, 0, 150, 190], [0.65, 240, 224, 64], [0.85, 255, 70, 32], [1, 255, 255, 255]];
    const lut = new Uint8Array(256 * 3);
    for (let i = 0; i < 256; i++) {
      const v = i / 255; let k = 0;
      while (k < stops.length - 2 && v > stops[k + 1][0]) k++;
      const a = stops[k], b = stops[k + 1], t = Math.max(0, Math.min(1, (v - a[0]) / (b[0] - a[0])));
      for (let c = 0; c < 3; c++) lut[i * 3 + c] = a[c + 1] + (b[c + 1] - a[c + 1]) * t;
    }
    return lut;
  })();

  class Waterfall {
    /* el: a container div. opts: {fmin, fmax, height, label(f)} */
    constructor(el, opts) {
      this.o = Object.assign({fmin: 0, fmax: 3000, height: 300, label: f => Math.round(f)}, opts || {});
      this.axis = document.createElement("div"); this.axis.className = "wf-axis";
      this.box = document.createElement("div"); this.box.className = "wf"; this.box.style.height = this.o.height + "px";
      this.canvas = document.createElement("canvas");
      this.read = document.createElement("div"); this.read.className = "wf-read";
      this.box.append(this.canvas, this.read); el.append(this.axis, this.box);
      this.ctx = this.canvas.getContext("2d", {willReadFrequently: false});
      this.floor = null; this.marks = []; this.onclick = null;
      this.resize(); window.addEventListener("resize", () => this.resize());
      const f = e => { const r = this.box.getBoundingClientRect(); return this.o.fmin + (e.clientX - r.left) / r.width * (this.o.fmax - this.o.fmin); };
      this.box.addEventListener("mousemove", e => { this.read.textContent = this.o.readout ? this.o.readout(f(e)) : Math.round(f(e)) + " Hz"; });
      this.box.addEventListener("mouseleave", () => { this.read.textContent = ""; });
      this.box.addEventListener("click", e => { if (this.onclick) this.onclick(f(e), e); });
    }
    setRange(fmin, fmax) { this.o.fmin = fmin; this.o.fmax = fmax; this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height); this.drawAxis(); this.drawMarks(); }
    resize() {
      const w = Math.max(200, Math.floor(this.box.clientWidth));
      if (w !== this.canvas.width) { this.canvas.width = w; this.canvas.height = this.o.height; this.rowImg = this.ctx.createImageData(w, 1); }
      this.drawAxis(); this.drawMarks();
    }
    drawAxis() {
      const span = this.o.fmax - this.o.fmin;
      const step = span <= 1200 ? 100 : span <= 3500 ? 500 : 1000;
      let h = "";
      for (let f = Math.ceil(this.o.fmin / step) * step; f <= this.o.fmax; f += step) {
        const x = (f - this.o.fmin) / span * 100;
        if (x < 2 || x > 98) continue;
        h += `<span style="left:${x}%">${esc(this.o.label(f))}</span><i style="left:${x}%"></i>`;
      }
      this.axis.innerHTML = h;
    }
    /* marks: [{f, label, color, width}] - width in Hz draws a shaded band */
    setMarks(marks) { this.marks = marks; this.drawMarks(); }
    drawMarks() {
      this.box.querySelectorAll(".wf-mark").forEach(m => m.remove());
      const span = this.o.fmax - this.o.fmin;
      for (const m of this.marks) {
        if (m.f < this.o.fmin || m.f > this.o.fmax) continue;
        const d = document.createElement("div"); d.className = "wf-mark";
        d.style.left = ((m.f - this.o.fmin) / span * 100) + "%";
        d.style.borderColor = m.color || "";
        if (m.width) { d.classList.add("band"); d.style.width = (m.width / span * 100) + "%"; d.style.background = m.color || "#ffb224"; }
        if (m.label) { const b = document.createElement("b"); b.textContent = m.label; b.style.color = m.color || "#ffb224"; d.append(b); }
        this.box.append(d);
      }
    }
    row(db, binHz) {
      const w = this.canvas.width, hgt = this.canvas.height, img = this.rowImg.data;
      const span = this.o.fmax - this.o.fmin;
      // noise floor: a low percentile of this row, smoothed over time
      let lo = Infinity, acc = 0, cnt = 0;
      const b0 = Math.max(1, Math.floor(this.o.fmin / binHz)), b1 = Math.min(db.length - 1, Math.ceil(this.o.fmax / binHz));
      for (let b = b0; b <= b1; b += 3) { acc += db[b]; cnt++; if (db[b] < lo) lo = db[b]; }
      const est = (acc / cnt + lo) / 2;
      this.floor = this.floor === null ? est : this.floor + 0.05 * (est - this.floor);
      for (let x = 0; x < w; x++) {
        const fa = this.o.fmin + x / w * span, fb = this.o.fmin + (x + 1) / w * span;
        let i0 = Math.floor(fa / binHz), i1 = Math.max(i0, Math.ceil(fb / binHz) - 1), v = -Infinity;
        for (let b = i0; b <= i1 && b < db.length; b++) if (db[b] > v) v = db[b];
        let k = Math.round((v - this.floor - 7) / 40 * 255);
        k = k < 0 ? 0 : k > 255 ? 255 : k;
        img[x * 4] = PALETTE[k * 3]; img[x * 4 + 1] = PALETTE[k * 3 + 1]; img[x * 4 + 2] = PALETTE[k * 3 + 2]; img[x * 4 + 3] = 255;
      }
      this.ctx.drawImage(this.canvas, 0, 0, w, hgt - 1, 0, 1, w, hgt - 1);
      this.ctx.putImageData(this.rowImg, 0, 0);
    }
  }

  /* ---------- CW decoder ----------
     Listens at one audio tone, turns it into key-down / key-up times, works
     out the sender's speed from the mix of short and long elements, and
     prints characters. Works on samples only, so it can be tested anywhere. */
  const MORSE = {".-": "A", "-...": "B", "-.-.": "C", "-..": "D", ".": "E", "..-.": "F", "--.": "G", "....": "H", "..": "I", ".---": "J", "-.-": "K", ".-..": "L", "--": "M", "-.": "N", "---": "O", ".--.": "P", "--.-": "Q", ".-.": "R", "...": "S", "-": "T", "..-": "U", "...-": "V", ".--": "W", "-..-": "X", "-.--": "Y", "--..": "Z", "-----": "0", ".----": "1", "..---": "2", "...--": "3", "....-": "4", ".....": "5", "-....": "6", "--...": "7", "---..": "8", "----.": "9", "-..-.": "/", "..--..": "?", ".-.-.-": ".", "--..--": ",", "-...-": "=", ".-.-.": "+", "-....-": "-"};

  class CWDecoder {
    constructor(onText, rate) {
      this.rate = rate || RATE; this.onText = onText || (() => {});
      this.freq = 650; this.block = Math.round(this.rate * 0.012); this.hop = Math.round(this.rate * 0.004);
      this.ring = new Float32Array(this.block); this.pos = 0; this.since = 0;
      this.hopMs = 1000 * this.hop / this.rate;
      this.reset();
    }
    reset() {
      this.peak = 0; this.floor = 0; this.warm = 0; this.key = false; this.pending = 0; this.run = 0;
      this.dot = 60; this.marks = []; this.sym = []; this.spaced = true; this.signal = false;
    }
    get wpm() { return Math.round(1200 / this.dot); }
    setFreq(f) { if (Math.abs(f - this.freq) > 1) { this.freq = f; } }
    push(samples) {
      for (let i = 0; i < samples.length; i++) {
        this.ring[this.pos] = samples[i]; this.pos = (this.pos + 1) % this.block;
        if (++this.since >= this.hop) { this.since = 0; this.step(this.goertzel()); }
      }
    }
    goertzel() {
      const w = 2 * Math.PI * this.freq / this.rate, c = 2 * Math.cos(w);
      let s1 = 0, s2 = 0;
      for (let i = 0; i < this.block; i++) { const s0 = this.ring[(this.pos + i) % this.block] + c * s1 - s2; s2 = s1; s1 = s0; }
      return Math.sqrt(Math.max(0, s1 * s1 + s2 * s2 - c * s1 * s2)) / this.block;
    }
    step(mag) {
      // track the signal peak (slow decay) and the noise floor
      if (this.warm < 100) {                                  // first 0.4 s: just learn the noise level
        this.warm++; this.floor += 0.1 * (mag - this.floor); return;
      }
      this.peak = mag > this.peak ? mag : this.peak * 0.998;
      const thr = this.floor + (this.peak - this.floor) * 0.42;
      this.signal = this.peak > this.floor * 7 + 6;
      const down = this.signal && mag > thr;
      if (!down) this.floor += 0.02 * (mag - this.floor);     // noise is measured between elements
      // debounce: a change must last two steps (8 ms) to count
      if (down !== this.key) {
        if (++this.pending >= 2) {
          if (this.key) this.markEnd(this.run * this.hopMs);
          this.key = down; this.run = this.pending; this.pending = 0;
        }
      } else { this.run += this.pending + 1; this.pending = 0; }
      if (!this.key) this.spaceTick(this.run * this.hopMs);
    }
    markEnd(ms) {
      if (ms < Math.max(12, this.dot * 0.3)) return;            // a click, not an element
      this.sym.push(ms); this.spaced = false;
      this.marks.push(ms); if (this.marks.length > 30) this.marks.shift();
      if (this.marks.length >= 4) {
        const s = this.marks.slice().sort((a, b) => a - b);
        const lo = s[Math.floor(s.length * 0.1)], hi = s[Math.ceil(s.length * 0.9) - 1];
        if (hi >= lo * 1.8) {                                   // both dots and dashes seen
          const split = (lo + hi) / 2, shorts = s.filter(v => v < split), longs = s.filter(v => v >= split);
          const d1 = shorts.reduce((a, b) => a + b, 0) / shorts.length;
          const d2 = longs.reduce((a, b) => a + b, 0) / longs.length / 3;
          this.dot = Math.max(20, Math.min(240, (d1 + d2) / 2));
        }
      }
    }
    spaceTick(ms) {
      if (this.sym.length && ms >= this.dot * 2) {
        const code = this.sym.map(v => v < this.dot * 2 ? "." : "-").join("");
        this.sym = [];
        this.onText(MORSE[code] || "□");
      } else if (!this.sym.length && !this.spaced && ms >= this.dot * 5) {
        this.spaced = true; this.onText(" ");
      }
    }
  }

  return {RATE, $, esc, pad, api, header, on, state, AudioFeed, FFT, Spectrum, Waterfall, CWDecoder, MORSE};
})();
if (typeof module !== "undefined") module.exports = QD;
