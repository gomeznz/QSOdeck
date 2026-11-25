"""Radio control for QSODesk.

Two radio types share one small interface:

* SimRadio      - a pretend radio, so the logger can be tried with no rig.
* RigctldRadio  - a real radio through Hamlib's rigctld daemon.

In "auto" mode QSODesk looks for an Icom IC-7300 or IC-705 on USB, starts
rigctld for it, and falls back to the simulator if nothing is found.
NOTE: the Hamlib path has not yet been tested against real hardware.
"""
import glob
import json
import random
import shutil
import socket
import subprocess
import threading
import time

from . import contests

# USB device-name fragment -> (display name, Hamlib model number)
KNOWN_RIGS = [("IC-7300", "Icom IC-7300", 3073), ("IC-705", "Icom IC-705", 3085)]


def detect_usb_rigs():
    """Every supported radio plugged in by USB, one entry per radio."""
    found, seen = [], set()
    for path in sorted(glob.glob("/dev/serial/by-id/*")):
        for frag, name, model in KNOWN_RIGS:
            unit = path.split("-if")[0]             # an IC-705 shows two ports; use the first
            if frag in path and unit not in seen:
                seen.add(unit)
                serial = unit.rsplit("_", 1)[-1][-6:]
                found.append({"path": path, "name": name, "model": model,
                              "label": "%s (serial ...%s)" % (name, serial)})
    return found


FILTERS = {"PH": [1800, 2400, 3000], "CW": [250, 500, 1200], "DIG": [500, 1200, 3000]}
FILTER_DEFAULT = {"PH": 2400, "CW": 500, "DIG": 3000}

# What each pretend radio is modelled on. Ranges are receive coverage in Hz.
SIM_MODELS = {
    "generic": {"name": "Simulated radio", "max_power": 100, "tuner": True, "amps": 0.17,
                "idle": 1.0, "ranges": [(100000, 470000000)]},
    "ic7300": {"name": "IC-7300 (simulated)", "max_power": 100, "tuner": True, "amps": 0.17,
               "idle": 0.9, "ranges": [(30000, 74800000)]},
    "ic705": {"name": "IC-705 (simulated)", "max_power": 10, "tuner": False, "amps": 0.26,
              "idle": 0.4, "ranges": [(30000, 199999999), (400000000, 470000000)]},
}


def _group(rmode):
    return contests.MODE_GROUP.get((rmode or "").upper(), "PH")


class SimRadio:
    """A pretend transceiver. Besides frequency and mode it mimics the
    controls and meters a real rig reports through Hamlib: RF power, filter
    width, noise blanker and reduction, preamp, attenuator, tuner, and on
    transmit the power, SWR, ALC, supply voltage and current meters.

    Its pretend antenna is resonant in the middle of each band, so SWR climbs
    towards the band edges, power folds back when SWR is high, and transmit
    is inhibited outside the amateur bands - as on a real radio."""
    simulated = True

    def __init__(self, model="generic"):
        self.model = model if model in SIM_MODELS else "generic"
        self.spec = SIM_MODELS[self.model]
        self.name = self.spec["name"]
        self.freq = 3650000
        self.rmode = "LSB"
        self._s = 5.0
        self.ptt = False
        self.tx_until = 0
        self.c = {"rfpower": self.spec["max_power"], "filter": 2400, "nb": False, "nr": False,
                  "preamp": 0, "att": False, "tuner": False}

    # ---- what the meters would read --------------------------------------
    def _swr(self):
        for name, lo, hi in contests.BANDS:
            if lo <= self.freq <= hi:
                off = abs(self.freq - (lo + hi) / 2.0) / ((hi - lo) / 2.0)
                swr = 1.15 + 2.3 * off * off
                return min(swr, 1.25) if self.c["tuner"] and self.spec["tuner"] else swr
        return None                                   # outside the amateur bands

    def transmitting(self):
        return self.ptt or time.time() < self.tx_until

    def poll(self):
        self._s = max(1.0, min(9.0, self._s + random.uniform(-0.6, 0.6)))
        s = self._s + self.c["preamp"] - (3 if self.c["att"] else 0)
        tx = self.transmitting()
        swr = self._swr()
        meters = {"power": 0, "swr": None, "alc": 0}
        note = ""
        if tx and swr is None:
            note = "Outside the amateur bands - transmit inhibited"
            tx = False
        if tx:
            fold = 1.0 if swr < 2.0 else 2.0 / swr              # protection folds power back
            power = self.c["rfpower"] * fold * random.uniform(0.96, 1.0)
            meters = {"power": round(power, 1), "swr": round(swr + random.uniform(-0.03, 0.03), 2),
                      "alc": int(random.uniform(35, 55) if fold == 1.0 else random.uniform(70, 95))}
        amps = self.spec["idle"] + meters["power"] * self.spec["amps"]
        meters["id"] = round(amps, 1)
        meters["vd"] = round(13.8 - amps * 0.03, 1)
        caps = {"model": self.spec["name"], "max_power": self.spec["max_power"],
                "tuner": self.spec["tuner"], "filters": FILTERS[_group(self.rmode)],
                "can": ["rfpower", "filter", "nb", "nr", "preamp", "att"] + (["tuner"] if self.spec["tuner"] else [])}
        return {"freq": self.freq, "rmode": self.rmode, "smeter": 0 if tx else max(0, min(9, round(s))),
                "tx": tx, "ext": {"controls": dict(self.c), "meters": meters, "caps": caps, "note": note}}

    # ---- commands ---------------------------------------------------------
    def set_freq(self, hz):
        hz = int(hz)
        if not any(lo <= hz <= hi for lo, hi in self.spec["ranges"]):
            raise IOError("%s does not cover %.3f MHz" % (self.spec["name"], hz / 1e6))
        self.freq = hz

    def set_mode(self, rmode):
        if _group(rmode) != _group(self.rmode):
            self.c["filter"] = FILTER_DEFAULT[_group(rmode)]
        self.rmode = rmode

    def set_control(self, name, value):
        if name == "rfpower":
            self.c["rfpower"] = int(max(1, min(self.spec["max_power"], round(float(value)))))
        elif name == "filter":
            self.c["filter"] = int(value)
        elif name == "preamp":
            self.c["preamp"] = int(value) % 3
        elif name in ("nb", "nr", "att", "tuner"):
            self.c[name] = bool(value)
        else:
            raise IOError("unknown control %s" % name)

    def set_ptt(self, on):
        self.ptt = bool(on)

    def tx_for(self, seconds):
        self.tx_until = time.time() + seconds

    def tune_carrier(self):
        self.tx_for(3)

    def send_morse(self, text, wpm):
        pass                    # the simulator plays the Morse as audio instead

    def stop_morse(self):
        self.tx_until = 0

    def close(self):
        pass


class RigctldRadio:
    simulated = False

    def __init__(self, host, port, name="Radio (rigctld)"):
        self.name = name
        self.max_power = 10 if "705" in name else 100
        self._n = 0
        self._ext = None
        self.sock = socket.create_connection((host, int(port)), timeout=2)
        self.fh = self.sock.makefile("rwb")
        self.lock = threading.Lock()

    def _cmd(self, line, replies):
        with self.lock:
            self.fh.write(line.encode() + b"\n")
            self.fh.flush()
            out = []
            for _ in range(replies):
                reply = self.fh.readline().decode().strip()
                if reply.startswith("RPRT"):
                    if reply != "RPRT 0":
                        raise IOError("rigctld: %s for '%s'" % (reply, line))
                    break
                out.append(reply)
            return out

    def poll(self):
        freq = int(float(self._cmd("f", 1)[0]))
        rmode = self._cmd("m", 2)[0]
        smeter = None
        try:
            db = float(self._cmd("l STRENGTH", 1)[0])       # dB relative to S9
            smeter = max(0, min(9, int(round(9 + db / 6.0))))
        except (IOError, IndexError, ValueError):
            pass
        out = {"freq": freq, "rmode": rmode, "smeter": smeter}
        self._n += 1
        try:
            out["tx"] = self._cmd("t", 1)[0] == "1"
        except (IOError, IndexError):
            out["tx"] = False
        if self._ext is None or out["tx"] or self._n % 5 == 0:
            self._ext = self._extras(out["tx"], rmode)        # the slower extras, now and then
        out["ext"] = self._ext
        return out

    # Extra controls and meters. NOTE: untested against real radios; anything
    # a radio or Hamlib version does not support is simply left out.
    def _level(self, name):
        try:
            return float(self._cmd("l " + name, 1)[0])
        except (IOError, IndexError, ValueError):
            return None

    def _func(self, name):
        try:
            return self._cmd("u " + name, 1)[0] == "1"
        except (IOError, IndexError):
            return None

    def _extras(self, tx, rmode):
        c, can = {}, []
        p = self._level("RFPOWER")
        if p is not None:
            c["rfpower"] = int(round(p * self.max_power))
            can.append("rfpower")
        for f in ("nb", "nr", "tuner"):
            v = self._func(f.upper())
            if v is not None:
                c[f] = v
                can.append(f)
        try:
            c["filter"] = int(float(self._cmd("m", 2)[1]))
            can.append("filter")
        except (IOError, IndexError, ValueError):
            pass
        meters = {"power": 0, "swr": None, "alc": 0}
        if tx:
            pm, swr, alc = self._level("RFPOWER_METER"), self._level("SWR"), self._level("ALC")
            meters = {"power": round((pm or 0) * self.max_power, 1), "swr": swr,
                      "alc": int((alc or 0) * 100)}
        caps = {"model": self.name, "max_power": self.max_power, "tuner": "tuner" in can,
                "filters": FILTERS[_group(rmode)], "can": can}
        return {"controls": c, "meters": meters, "caps": caps, "note": ""}

    def set_control(self, name, value):
        if name == "rfpower":
            self._cmd("L RFPOWER %.3f" % (max(1.0, float(value)) / self.max_power), 1)
        elif name in ("nb", "nr", "tuner"):
            self._cmd("U %s %d" % (name.upper(), 1 if value else 0), 1)
        elif name == "filter":
            self._cmd("M %s %d" % (self._cmd("m", 2)[0], int(value)), 1)
        else:
            raise IOError("%s cannot be set on this radio" % name)
        self._ext = None

    def tune_carrier(self):
        self._cmd("U TUNER 2", 1)                 # start the radio's own antenna tuner

    def tx_for(self, seconds):
        pass

    def set_freq(self, hz):
        self._cmd("F %d" % int(hz), 1)

    def set_mode(self, rmode):
        self._cmd("M %s 0" % rmode, 1)

    def set_ptt(self, on):
        self._cmd("T %d" % (1 if on else 0), 1)

    def send_morse(self, text, wpm):
        """Key the radio's own CW keyer with this text."""
        self._cmd("L KEYSPD %d" % int(wpm), 1)
        self._cmd("b %s" % text, 1)

    def stop_morse(self):
        self._cmd("\\stop_morse", 1)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class RadioManager(threading.Thread):
    """Keeps one radio connected and publishes its state when it changes.

    cfg: {"id", "name", "source": "auto" | "usb" | "rigctld" | "sim",
          "path", "model", "host", "port", "audio"}
    """

    def __init__(self, cfg, on_change, rig_port=4532, claimed=lambda: set()):
        super().__init__(daemon=True)
        self.cfg = dict(cfg)
        self.id = cfg["id"]
        self.on_change = on_change
        self.rig_port = rig_port
        self.claimed = claimed              # USB paths other radios are set to use
        self.radio = None
        self.proc = None
        self.note = ""
        self.state = {"id": self.id, "label": cfg.get("name", ""), "freq": 0, "rmode": "",
                      "smeter": None, "band": "", "mode": "PH", "name": "",
                      "simulated": True, "connected": False, "note": ""}
        self._reconfigure = True
        self._stop = False
        self.lock = threading.Lock()

    def reconfigure(self, cfg=None):
        if cfg is not None:
            self.cfg = dict(cfg)
        self._reconfigure = True

    def stop(self):
        self._stop = True

    def _stop_proc(self):
        if self.proc:
            self.proc.terminate()
            self.proc = None

    def _connect(self):
        c = self.cfg
        source = c.get("source", "auto")
        self.note = ""
        self._stop_proc()
        if source == "rigctld":
            return RigctldRadio(c.get("host") or "127.0.0.1", c.get("port") or 4532)
        if source in ("auto", "usb"):
            rigs = detect_usb_rigs()
            if source == "usb":
                rig = next((r for r in rigs if r["path"] == c.get("path")), None)
                if not rig and c.get("path") and c.get("model"):
                    raise IOError("not plugged in")
            else:                                   # auto: first radio nobody else is using
                rig = next((r for r in rigs if r["path"] not in self.claimed()), None)
            if rig and not shutil.which("rigctld"):
                self.note = ("%s found on USB, but Hamlib is not installed "
                             "(sudo apt install libhamlib-utils). Using the simulator."
                             % rig["name"])
            elif rig:
                self.proc = subprocess.Popen(
                    ["rigctld", "-m", str(rig["model"]), "-r", rig["path"], "-t", str(self.rig_port)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(1.5)
                return RigctldRadio("127.0.0.1", self.rig_port, rig["name"])
        return SimRadio(c.get("sim_model") or "generic")

    def run(self):
        last = None
        while not self._stop:
            try:
                if self._reconfigure or self.radio is None:
                    self._reconfigure = False
                    if self.radio:
                        self.radio.close()
                    self.radio = None
                    self.radio = self._connect()
                with self.lock:
                    st = self.radio.poll()
                st["band"] = contests.band_of(st["freq"])
                st["mode"] = contests.MODE_GROUP.get(st["rmode"].upper(), "PH")
                st["name"] = self.radio.name
                st["simulated"] = self.radio.simulated
                st["connected"] = True
                st["note"] = self.note
            except Exception as exc:                # radio unplugged, rigctld down...
                if self.radio:
                    self.radio.close()
                self.radio = None
                st = dict(self.state)
                st["connected"] = False
                st["note"] = "Radio not responding: %s" % exc
                for _ in range(30):
                    if self._stop or self._reconfigure:
                        break
                    time.sleep(0.1)
            st["id"] = self.id
            st["label"] = self.cfg.get("name") or self.id
            self.state = st
            if st != last:
                last = dict(st)
                self.on_change(st)
            time.sleep(0.4)
        if self.radio:
            self.radio.close()
        self._stop_proc()

    def tune(self, band=None, mode=None, freq=None, rmode=None):
        """Band/mode buttons and click-to-tune. Works on real and pretend rigs."""
        if not self.radio:
            return
        with self.lock:
            cur = self.state
            group = mode or cur.get("mode", "PH")
            if band and not freq:
                freq = contests.default_freq(band, group)
            if freq:
                self.radio.set_freq(freq)
            if rmode:
                self.radio.set_mode(rmode)
            elif mode or band:
                self.radio.set_mode(contests.default_rmode(group, freq or cur.get("freq", 0)))

    def set_ptt(self, on):
        if self.radio:
            self.radio.set_ptt(on)

    def tx_for(self, seconds):
        if self.radio:
            self.radio.tx_for(seconds)

    def tune_carrier(self):
        if self.radio:
            self.radio.tune_carrier()

    def adjust(self, name, value=None, delta=None):
        """Set a control, nudge it by `delta`, or (with neither) toggle/step it."""
        if not self.radio:
            raise IOError("radio not connected")
        ext = self.state.get("ext") or {}
        cur = (ext.get("controls") or {}).get(name)
        caps = ext.get("caps") or {}
        if name not in (caps.get("can") or []):
            raise IOError("This radio does not offer %s" % name)
        if value is None:
            if delta is not None:
                value = (cur or 0) + delta
            elif name == "filter":
                opts = caps.get("filters") or [cur]
                value = opts[(opts.index(cur) + 1) % len(opts)] if cur in opts else opts[0]
            elif name == "preamp":
                value = ((cur or 0) + 1) % 3
            else:
                value = not cur
        with self.lock:
            self.radio.set_control(name, value)
        return value

    def send_morse(self, text, wpm):
        if self.radio:
            self.radio.send_morse(text, wpm)

    def stop_morse(self):
        if self.radio:
            self.radio.stop_morse()


class RadioHub:
    """The transceiver manager: any number of radios, each with its own
    connection, kept in the "radios" setting and edited from the web page."""

    def __init__(self, store, on_change):
        self.store = store
        self.on_change = on_change
        self.lock = threading.RLock()
        self.managers = {}
        self.order = []
        self.sync()

    def configs(self):
        try:
            cfgs = json.loads(self.store.settings().get("radios") or "[]")
        except ValueError:
            cfgs = []
        cfgs = [c for c in cfgs if isinstance(c, dict) and c.get("id")]
        if not cfgs:                                # first run: carry over the old single radio
            s = self.store.settings()
            cfgs = [{"id": "r1", "name": s.get("station") or "Radio 1",
                     "source": s.get("radio_source") or "auto",
                     "host": s.get("rigctld_host"), "port": s.get("rigctld_port"),
                     "audio": s.get("audio_device", "")}]
        return cfgs

    def save(self, cfgs):
        clean, used = [], set()
        for c in cfgs:
            rid = str(c.get("id") or "")
            if not rid or rid in used:
                n = 1
                while "r%d" % n in used or any(x.get("id") == "r%d" % n for x in cfgs):
                    n += 1
                rid = "r%d" % n
            used.add(rid)
            src = c.get("source") if c.get("source") in ("auto", "usb", "rigctld", "sim") else "sim"
            clean.append({"id": rid, "name": (c.get("name") or "Radio").strip()[:30], "source": src,
                          "path": c.get("path") or "", "model": c.get("model") or 0,
                          "host": (c.get("host") or "127.0.0.1").strip(),
                          "port": int(c.get("port") or 4532), "audio": (c.get("audio") or "").strip(),
                          "sim_model": c.get("sim_model") if c.get("sim_model") in SIM_MODELS else "generic"})
        if not clean:
            raise ValueError("Keep at least one transceiver")
        self.store.set_settings({"radios": json.dumps(clean)})
        self.sync()
        return clean

    def sync(self):
        """Start, stop or reconfigure radio threads to match the saved list."""
        with self.lock:
            cfgs = self.configs()
            ids = [c["id"] for c in cfgs]
            for rid in list(self.managers):
                if rid not in ids:
                    self.managers.pop(rid).stop()
            claimed = lambda: set(c.get("path") for c in self.configs() if c.get("source") == "usb")
            for i, c in enumerate(cfgs):
                m = self.managers.get(c["id"])
                if m is None:
                    m = RadioManager(c, self.on_change, 4532 + i, claimed)
                    self.managers[c["id"]] = m
                    m.start()
                elif m.cfg != c:
                    m.reconfigure(c)
            self.order = ids

    def get(self, rid=None):
        with self.lock:
            return self.managers.get(rid) or self.managers[self.order[0]]

    def list(self):
        with self.lock:
            return [dict(self.managers[i].cfg, state=self.managers[i].state) for i in self.order]

    def detected(self):
        """USB radios plugged in but not yet added."""
        have = set(c.get("path") for c in self.configs())
        return [r for r in detect_usb_rigs() if r["path"] not in have]
