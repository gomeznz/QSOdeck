"""FT8 for QSODesk.

QSODesk does the timing, the band-activity list, the QSO sequencing, the
transmit keying and the logging. The FT8 signal processing itself is done by
the open-source ft8_lib programs `decode_ft8` and `gen_ft8` (see
tools/install-ft8.sh). Without them the page still runs against the simulator.

IMPORTANT: in simulator mode the decodes are scripted, not decoded from audio,
and the real-engine path has not yet been tested. See README.
"""
import collections
import os
import random
import re
import shutil
import subprocess
import tempfile
import threading
import time

from . import contests
from .audio import ft8_lookalike, write_wav

BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin")
SLOT = 15
MAX_TRIES = 6

FT8_FREQS = [("160m", 1840000), ("80m", 3573000), ("40m", 7074000), ("30m", 10136000),
             ("20m", 14074000), ("17m", 18100000), ("15m", 21074000), ("12m", 24915000),
             ("10m", 28074000), ("6m", 50313000)]

_GRID = re.compile(r"^[A-R]{2}\d{2}$")
_RPT = re.compile(r"^(R?)([+-]\d{2})$")
# "000000 -07 +0.5 1234 ~  CQ K1ABC FN42" (ft8_lib and WSJT-X's jt9 both fit)
_LINE = re.compile(r"^\s*(?:\d{6}\s+)?(-?\d+(?:\.\d+)?)\s+([+-]?\d+(?:\.\d+)?)\s+"
                   r"(\d+(?:\.\d+)?)\s+~\s+(.+?)\s*$")


def find_tool(name):
    local = os.path.join(BIN, name)
    return local if os.access(local, os.X_OK) else shutil.which(name)


def fmt_report(snr):
    return "%+03d" % max(-30, min(30, int(round(snr))))


def parse_msg(text):
    """Split a standard FT8 message into who, to whom, and what."""
    t = text.upper().split()
    out = {"cq": False, "to": "", "de": "", "grid": "", "report": "", "r": False, "end": ""}
    if not t:
        return out
    if t[0] == "CQ":
        out["cq"] = True
        rest = t[1:]
        if rest and _GRID.match(rest[-1]) and len(rest) >= 2:
            out["grid"] = rest.pop()
        out["de"] = rest[-1] if rest else ""
        return out
    if len(t) < 2:
        return out
    out["to"], out["de"] = t[0], t[1]
    x = t[2] if len(t) > 2 else ""
    m = _RPT.match(x)
    if x in ("RRR", "RR73", "73"):
        out["end"] = x
    elif m:
        out["r"], out["report"] = bool(m.group(1)), m.group(2)
    elif _GRID.match(x):
        out["grid"] = x
    return out


def parse_decodes(text):
    out = []
    for line in text.splitlines():
        m = _LINE.match(line)
        if m:
            out.append({"snr": int(round(float(m.group(1)))), "dt": float(m.group(2)),
                        "freq": int(round(float(m.group(3)))), "msg": m.group(4).strip()})
    return out


class SimBand:
    """Pretend stations for the simulator, including ones that answer you."""
    CALLERS = [("ZL2SIM", "RF70", 620, 0), ("VK3SIM", "QF22", 1040, 1),
               ("ZL1DX", "RF73", 1480, 0), ("JA1SIM", "PM95", 2250, 1)]
    ANSWERER = ("VK4ANS", "QG62", 1800)

    def __init__(self):
        self.replies = collections.defaultdict(list)      # slot -> [(freq, msg, snr)]
        self.busy = {}                                    # call -> slot until which it is in QSO

    def messages(self, slot):
        out = list(self.replies.pop(slot, []))
        for call, grid, freq, parity in self.CALLERS:
            if slot % 2 == parity and self.busy.get(call, 0) < slot and random.random() < 0.85:
                out.append((freq, "CQ %s %s" % (call, grid), random.randint(-16, 4)))
        if slot % 2 == 0 and random.random() < 0.6:       # someone else's QSO in progress
            out.append((2640, random.choice(["VK2XYZ ZL3QSO -11", "VK2XYZ ZL3QSO RR73",
                                             "VK2XYZ ZL3QSO RE57"]), -9))
        return out

    def heard(self, slot, text, mycall):
        """I transmitted `text` in `slot`; work out who answers in the next one."""
        p = parse_msg(text)
        known = {c[0]: c for c in self.CALLERS}
        known[self.ANSWERER[0]] = self.ANSWERER + (1,)
        snr = "%+03d" % random.randint(-14, -3)
        if p["cq"]:
            call, grid, freq = self.ANSWERER
            reply = "%s %s %s" % (mycall, call, grid)
        elif p["to"] in known:
            call, freq = p["to"], known[p["to"]][2]
            if p["grid"]:
                reply = "%s %s %s" % (mycall, call, snr)
            elif p["report"] and not p["r"]:
                reply = "%s %s R%s" % (mycall, call, snr)
            elif p["report"]:
                reply = "%s %s RR73" % (mycall, call)
            elif p["end"] in ("RR73", "RRR"):
                reply = "%s %s 73" % (mycall, call)
            else:
                return
        else:
            return
        self.busy[call] = slot + 3
        self.replies[slot + 1].append((freq, reply, int(snr)))


class FT8:
    def __init__(self, app):
        self.app = app
        self.lock = threading.RLock()
        self.slots = collections.deque(maxlen=10)
        self.buf = bytearray()
        self.slot = None
        self.decoded_slot = None
        self.sim = SimBand()
        self.sim_now = {}                 # slot -> messages on the simulated band
        self.qso = None
        self.tx_enabled = False
        self.transmitting = False
        self.tx_freq = 1500
        self.note = ""
        app.audio.listeners.append(self.feed)

    # ---- helpers ----------------------------------------------------------
    def simulated(self):
        return self.app.audio.status["source"] == "sim"

    def active(self):
        return self.app.radio.state.get("mode") == "DIG"

    def me(self):
        s = self.app.store.settings()
        return (s.get("callsign") or "").upper(), (s.get("grid") or "").upper()[:4]

    def engine(self):
        if self.simulated():
            return "simulated"
        return "ft8_lib" if find_tool("decode_ft8") else "missing"

    def _slot_entry(self, slot):
        for e in self.slots:
            if e["slot"] == slot:
                return e
        e = {"slot": slot, "t": slot * SLOT, "decodes": [], "tx": ""}
        self.slots.append(e)
        return e

    def changed(self):
        self.app.broadcast("ft8", {})

    # ---- audio in ---------------------------------------------------------
    def feed(self, data, now):
        slot = int(now // SLOT)
        if slot != self.slot:
            self.slot = slot
            self.buf = bytearray()
            if self.active():
                threading.Thread(target=self._slot_start, args=(slot,), daemon=True).start()
        self.buf += data
        if self.active() and self.decoded_slot != slot and now - slot * SLOT >= 13.4:
            self.decoded_slot = slot
            threading.Thread(target=self._decode, args=(slot, bytes(self.buf)), daemon=True).start()

    def _slot_start(self, slot):
        with self.lock:
            q = self.qso
            send = self.tx_enabled and q and slot % 2 == q["parity"]
            msg = q["tx"] if send else None
        if self.simulated():
            msgs = self.sim.messages(slot)
            self.sim_now[slot] = msgs
            for freq, text, snr in msgs:
                self.app.audio.inject(ft8_lookalike(text, freq, int(900 * 10 ** (snr / 40.0)) + 600))
        if send:
            self._transmit(slot, msg, q)

    # ---- transmit ---------------------------------------------------------
    def _transmit(self, slot, msg, q):
        mycall, _ = self.me()
        with self.lock:
            self.transmitting = True
            self._slot_entry(slot)["tx"] = msg
        self.changed()
        try:
            if self.simulated():
                self.sim.heard(slot, msg, mycall)
                self.app.radio.tx_for(13.1)                       # show it on the meters
                self.app.audio.play(samples=ft8_lookalike(msg, self.tx_freq, 5000))
            else:
                gen = find_tool("gen_ft8")
                if not gen:
                    raise RuntimeError("gen_ft8 is not installed, so QSODesk cannot transmit FT8")
                fd, wav = tempfile.mkstemp(suffix=".wav")
                os.close(fd)
                try:
                    subprocess.run([gen, msg, wav, str(self.tx_freq)], check=True, timeout=5,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    self.app.radio.set_ptt(True)
                    try:
                        self.app.audio.play(wav_path=wav, timeout=14)
                    finally:
                        self.app.radio.set_ptt(False)     # never leave the radio keyed
                finally:
                    os.remove(wav)
        except Exception as exc:
            with self.lock:
                self.tx_enabled = False
                self.note = "Transmit stopped: %s" % exc
        with self.lock:
            self.transmitting = False
            if self.qso is q:
                q["tries"] += 1
                if q.get("final"):                        # 73 or RR73 has gone out
                    self._log(q)
                    self.tx_enabled = False
                    self.note = "QSO with %s complete and logged." % q["dx"]
                    self.qso = None
                elif q["tries"] >= MAX_TRIES:
                    self.tx_enabled = False
                    self.note = "No reply after %d calls - transmit stopped." % MAX_TRIES
        self.changed()

    # ---- receive ----------------------------------------------------------
    def _decode(self, slot, pcm):
        decodes = []
        if self.simulated():
            if not (self._slot_entry(slot)["tx"]):        # you can't hear while transmitting
                for freq, text, snr in self.sim_now.pop(slot, []):
                    decodes.append({"snr": snr, "dt": round(random.uniform(-0.2, 0.4), 1),
                                    "freq": freq, "msg": text})
            self.sim_now.pop(slot, None)
        else:
            tool = find_tool("decode_ft8")
            if not tool:
                self.note = ("The FT8 decoder is not installed yet. Run tools/install-ft8.sh "
                             "on this computer, then reload.")
                self.changed()
                return
            fd, wav = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            try:
                write_wav(wav, pcm)
                res = subprocess.run([tool, wav], capture_output=True, text=True, timeout=12)
                decodes = parse_decodes(res.stdout)
            except (OSError, subprocess.SubprocessError) as exc:
                self.note = "FT8 decoder problem: %s" % exc
            finally:
                os.remove(wav)
        mycall, _ = self.me()
        s, ev, c = self.app.ctx()
        worked = set(contests.base_call(q["call"]) for q in self.app.store.qsos(ev["name"]))
        for d in decodes:
            p = parse_msg(d["msg"])
            d.update(cq=p["cq"], de=p["de"], to=p["to"], grid=p["grid"] if p["cq"] else "",
                     mine=bool(mycall) and p["to"] == mycall,
                     worked=contests.base_call(p["de"]) in worked,
                     vkzl=contests.is_vk_zl(p["de"]) if p["de"] else False)
        decodes.sort(key=lambda d: d["freq"])
        with self.lock:
            self._slot_entry(slot)["decodes"] = decodes
            self._sequence(decodes)
        self.changed()

    def _sequence(self, decodes):
        """Advance the QSO from what the other station just sent."""
        q = self.qso
        mycall, _ = self.me()
        if not q or not mycall:
            return
        for d in decodes:
            p = parse_msg(d["msg"])
            if p["to"] != mycall:
                continue
            if q["cq"] and not q["dx"]:
                q["dx"] = p["de"]                         # first answer to my CQ
                q["freq"] = d["freq"]
            if p["de"] != q["dx"]:
                continue
            q["tries"] = 0
            head = "%s %s " % (q["dx"], mycall)
            if p["grid"]:
                q["grid"] = p["grid"]
                q["sent"] = fmt_report(d["snr"])
                q["tx"] = head + q["sent"]
            elif p["report"] and not p["r"]:
                q["rcvd"] = p["report"]
                q["sent"] = fmt_report(d["snr"])
                q["tx"] = head + "R" + q["sent"]
            elif p["report"]:
                q["rcvd"] = p["report"]
                q["tx"] = head + "RR73"
                q["final"] = True
            elif p["end"] in ("RRR", "RR73"):
                q["tx"] = head + "73"
                q["final"] = True
            elif p["end"] == "73":
                self._log(q)
                self.tx_enabled = False
                self.note = "QSO with %s complete and logged." % q["dx"]
                self.qso = None
            return

    def _log(self, q):
        if q.get("logged") or not q.get("dx"):
            return
        q["logged"] = True
        rs = self.app.radio.state
        self.app.log({"call": q["dx"], "mode": "DIG", "rmode": "FT8",
                      "freq": (rs.get("freq") or 0) + self.tx_freq,
                      "rst_sent": q.get("sent", ""), "rst_rcvd": q.get("rcvd", ""),
                      "notes": ("Grid " + q["grid"]) if q.get("grid") else ""})

    # ---- commands from the page ------------------------------------------
    def _ready(self):
        mycall, grid = self.me()
        if not mycall or not _GRID.match(grid):
            return "Set your callsign and 4-character grid locator in Settings first."
        if not self.simulated() and not find_tool("gen_ft8"):
            return "gen_ft8 is not installed, so QSODesk cannot transmit FT8 yet."
        return ""

    def call(self, dx, snr, slot, grid=""):
        """Answer a station heard in `slot`."""
        err = self._ready()
        if err:
            return err
        mycall, grid = self.me()
        with self.lock:
            self.qso = {"dx": dx.upper(), "cq": False, "grid": grid or "", "sent": fmt_report(snr),
                        "rcvd": "", "tries": 0, "parity": (int(slot) + 1) % 2,
                        "tx": "%s %s %s" % (dx.upper(), mycall, grid)}
            self.tx_enabled = True
            self.note = ""
        self.changed()
        return ""

    def cq(self):
        err = self._ready()
        if err:
            return err
        mycall, grid = self.me()
        with self.lock:
            self.qso = {"dx": "", "cq": True, "grid": "", "sent": "", "rcvd": "", "tries": 0,
                        "parity": (int(time.time() // SLOT) + 1) % 2,
                        "tx": "CQ %s %s" % (mycall, grid)}
            self.tx_enabled = True
            self.note = ""
        self.changed()
        return ""

    def halt(self):
        with self.lock:
            self.tx_enabled = False
            self.qso = None
            self.note = "Transmit halted."
        if not self.simulated():
            try:
                self.app.radio.set_ptt(False)
            except Exception:
                pass
        self.changed()

    def status(self):
        mycall, grid = self.me()
        with self.lock:
            q = dict(self.qso) if self.qso else None
            return {"engine": self.engine(), "active": self.active(), "mycall": mycall,
                    "grid": grid, "tx_enabled": self.tx_enabled,
                    "transmitting": self.transmitting, "tx_freq": self.tx_freq,
                    "qso": q, "note": self.note, "now": time.time(),
                    "slots": [dict(e) for e in self.slots], "freqs": FT8_FREQS}
