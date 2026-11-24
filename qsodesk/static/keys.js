/* Keypad support for QSODesk.
   A macro keypad (or any spare keys) plugged into the logging computer shows
   up as an ordinary keyboard. This file watches for the keys you have mapped
   on the Keypad page and runs the matching action, on every QSODesk page.
   The map is kept on the Pi, so it follows you to every device. */
"use strict";
const QDKeys = (() => {
  const ACTIONS = {
    cw:        {name: "Send CW text", arg: "Text ({MYCALL} {CALL} {SERIAL} {BRANCH} allowed)", def: "CQ TEST DE {MYCALL} {MYCALL}"},
    cw_stop:   {name: "Stop sending CW"},
    log:       {name: "Log the contact", page: "Logger"},
    clear:     {name: "Clear the entry line", page: "Logger"},
    focus:     {name: "Jump to the callsign box", page: "Logger"},
    tune_up:   {name: "Tune up", arg: "Step in Hz", def: "100", repeat: true},
    tune_down: {name: "Tune down", arg: "Step in Hz", def: "100", repeat: true},
    band_up:   {name: "Next band"},
    band_down: {name: "Previous band"},
    mode_next: {name: "Next mode"},
    wpm_up:    {name: "CW speed up", arg: "WPM step", def: "2", repeat: true},
    wpm_down:  {name: "CW speed down", arg: "WPM step", def: "2", repeat: true},
    radio_next:{name: "Switch to the next transceiver"},
    ft8_cq:    {name: "FT8: call CQ"},
    ft8_halt:  {name: "FT8: halt transmit"},
    go:        {name: "Open a page", arg: "logger, dashboard, radios, waterfall, cw or ft8", def: "logger"},
  };
  let map = {}, paused = false, listeners = [], pendingTune = 0, tuneTimer = null;

  async function api(path, body) {
    const r = await fetch(path, body ? {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)} : undefined);
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || "Request failed");
    return j;
  }
  const radioId = () => { try { return localStorage.getItem("qsodesk_radio") || ""; } catch (e) { return ""; } };
  const state = () => api("/api/state?radio=" + encodeURIComponent(radioId()));

  /* "Ctrl+Alt+F13" style name for a key press */
  function combo(e) {
    if (["Control", "Shift", "Alt", "Meta"].includes(e.key)) return "";
    let k = e.key.length === 1 ? e.key.toUpperCase() : e.key;
    if (k === " ") k = "Space";
    if (e.code && e.code.startsWith("Numpad")) k = e.code;         // tell the number pad from the top row
    return (e.ctrlKey ? "Ctrl+" : "") + (e.altKey ? "Alt+" : "") + (e.shiftKey && k.length > 1 ? "Shift+" : "") + (e.metaKey ? "Meta+" : "") + k;
  }
  const isPlain = c => /^(.|Space|Enter|Tab|Backspace|Escape|Arrow\w+)$/.test(c);

  function toast(text, bad) {
    let t = document.getElementById("qdk-toast");
    if (!t) {
      t = document.createElement("div"); t.id = "qdk-toast";
      t.style.cssText = "position:fixed;left:50%;bottom:22px;transform:translateX(-50%);background:#1b2430;border:1px solid #ffb224;color:#e8edf3;padding:8px 16px;border-radius:10px;font:600 14px system-ui,sans-serif;z-index:9999;pointer-events:none;transition:opacity .3s";
      document.body.append(t);
    }
    t.textContent = text; t.style.borderColor = bad ? "#ff5f5f" : "#ffb224"; t.style.opacity = "1";
    clearTimeout(t._h); t._h = setTimeout(() => { t.style.opacity = "0"; }, 1400);
  }
  const page = name => window.dispatchEvent(new CustomEvent("qsodesk-action", {detail: name}));

  async function run(action, arg) {
    const A = ACTIONS[action]; if (!A) return;
    try {
      if (action === "tune_up" || action === "tune_down") {
        // a knob sends a burst of presses: add them up and send one move
        pendingTune += (action === "tune_up" ? 1 : -1) * (Number(arg) || 100);
        if (!tuneTimer) tuneTimer = setTimeout(async () => {
          const d = pendingTune; pendingTune = 0; tuneTimer = null;
          try { await api("/api/radio/step", {radio: radioId(), delta: d}); } catch (e) { toast(e.message, true); }
        }, 60);
        return;
      }
      if (action === "log" || action === "clear" || action === "focus") {
        if (!window.QSODeskLogger) return toast(A.name + " works on the Logger page", true);
        return page(action);
      }
      if (action === "go") { const p = (arg || "logger").toLowerCase().trim(); location.href = p === "logger" ? "/" : p === "radios" ? "/transceivers" : "/" + p; return; }
      if (action === "cw_stop") { await api("/api/cw/stop", {}); return toast("CW stopped"); }
      if (action === "ft8_cq") { await api("/api/ft8/cq", {}); return toast("FT8: calling CQ"); }
      if (action === "ft8_halt") { await api("/api/ft8/halt", {}); return toast("FT8: transmit halted"); }
      const s = await state();
      if (action === "cw") {
        const ctx = window.QSODeskContext ? window.QSODeskContext() : {};
        const text = String(arg || A.def).split("{MYCALL}").join(s.settings.callsign || "").replace("{CALL}", (ctx.call || "").toUpperCase())
          .replace("{SERIAL}", s.serial != null ? String(s.serial).padStart(3, "0") : "")
          .replace("{BRANCH}", s.contest.fields.includes("exch") ? (s.settings.branch || "00") : "").replace(/\s+/g, " ").trim();
        if (!text) return;
        const r = await api("/api/cw/send", {text: text, wpm: Number(s.settings.cw_wpm) || 22});
        return toast("CW: " + r.text);
      }
      if (action === "wpm_up" || action === "wpm_down") {
        const w = Math.max(5, Math.min(50, (Number(s.settings.cw_wpm) || 22) + (action === "wpm_up" ? 1 : -1) * (Number(arg) || 2)));
        await api("/api/settings", {cw_wpm: String(w)}); return toast("CW speed " + w + " wpm");
      }
      if (action === "band_up" || action === "band_down") {
        const b = s.contest.bands, i = b.indexOf(s.radio.band);
        const next = b[(i + (action === "band_up" ? 1 : b.length - 1) + b.length) % b.length] || b[0];
        await api("/api/radio", {band: next, radio: s.radio.id}); return toast(next);
      }
      if (action === "mode_next") {
        const m = s.contest.modes, next = m[(m.indexOf(s.radio.mode) + 1) % m.length];
        await api("/api/radio", {mode: next, band: s.radio.band, radio: s.radio.id}); return toast(next);
      }
      if (action === "radio_next") {
        const i = s.radios.findIndex(r => r.id === s.radio.id), next = s.radios[(i + 1) % s.radios.length];
        try { localStorage.setItem("qsodesk_radio", next.id); } catch (e) {}
        toast("Now using " + next.name); page("radio"); return;
      }
    } catch (e) { toast(e.message, true); }
  }

  async function load() {
    try { const s = await api("/api/state"); map = JSON.parse(s.settings.keymap || "{}") || {}; } catch (e) { /* keep the old map */ }
    return map;
  }
  async function save(m) { map = m; await api("/api/settings", {keymap: JSON.stringify(m)}); }

  window.addEventListener("keydown", e => {
    const c = combo(e); if (!c) return;
    for (const fn of listeners) fn(c, e);
    if (paused) return;
    const hit = map[c]; if (!hit) return;
    e.preventDefault(); e.stopPropagation();
    if (e.repeat && !(ACTIONS[hit.action] || {}).repeat) return;
    run(hit.action, hit.arg);
  }, true);

  load();
  window.addEventListener("focus", load);          // pick up changes made on another device
  setInterval(load, 30000);
  return {ACTIONS, combo, isPlain, run, load, save, get map() { return map; },
    pause(v) { paused = v; }, onKey(fn) { listeners.push(fn); }};
})();
