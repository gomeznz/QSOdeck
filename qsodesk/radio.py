"""Radio control for QSODesk.

Two radio types share one small interface:

* SimRadio      - a pretend radio, so the logger can be tried with no rig.
* RigctldRadio  - a real radio through Hamlib's rigctld daemon.

In "auto" mode QSODesk looks for an Icom IC-7300 or IC-705 on USB, starts
rigctld for it, and falls back to the simulator if nothing is found.
NOTE: the Hamlib path has not yet been tested against real hardware.
"""
import glob
import random
import shutil
import socket
import subprocess
import threading
import time

from . import contests

# USB device-name fragment -> (display name, Hamlib model number)
KNOWN_RIGS = [("IC-7300", "Icom IC-7300", 3073), ("IC-705", "Icom IC-705", 3085)]


def detect_usb_rig():
    for path in sorted(glob.glob("/dev/serial/by-id/*")):
        for frag, name, model in KNOWN_RIGS:
            if frag in path:
                return {"path": path, "name": name, "model": model}
    return None


class SimRadio:
    simulated = True

    def __init__(self):
        self.name = "Simulated radio"
        self.freq = 3650000
        self.rmode = "LSB"
        self._s = 5.0

    def poll(self):
        self._s = max(1.0, min(9.0, self._s + random.uniform(-0.6, 0.6)))
        return {"freq": self.freq, "rmode": self.rmode, "smeter": round(self._s)}

    def set_freq(self, hz):
        self.freq = int(hz)

    def set_mode(self, rmode):
        self.rmode = rmode

    def set_ptt(self, on):
        self.ptt = bool(on)

    def send_morse(self, text, wpm):
        pass                    # the simulator plays the Morse as audio instead

    def stop_morse(self):
        pass

    def close(self):
        pass


class RigctldRadio:
    simulated = False

    def __init__(self, host, port, name="Radio (rigctld)"):
        self.name = name
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
        return {"freq": freq, "rmode": rmode, "smeter": smeter}

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
    """Keeps one radio connected and publishes its state when it changes."""

    def __init__(self, get_settings, on_change):
        super().__init__(daemon=True)
        self.get_settings = get_settings
        self.on_change = on_change
        self.radio = None
        self.proc = None
        self.note = ""
        self.state = {"freq": 0, "rmode": "", "smeter": None, "band": "", "mode": "PH",
                      "name": "", "simulated": True, "connected": False, "note": ""}
        self._reconfigure = True
        self.lock = threading.Lock()

    def reconfigure(self):
        self._reconfigure = True

    def _stop_proc(self):
        if self.proc:
            self.proc.terminate()
            self.proc = None

    def _connect(self):
        s = self.get_settings()
        source = s.get("radio_source", "auto")
        self.note = ""
        self._stop_proc()
        if source == "rigctld":
            return RigctldRadio(s["rigctld_host"], s["rigctld_port"])
        if source == "auto":
            rig = detect_usb_rig()
            if rig and not shutil.which("rigctld"):
                self.note = ("%s found on USB, but Hamlib is not installed "
                             "(sudo apt install libhamlib-utils). Using the simulator."
                             % rig["name"])
            elif rig:
                self.proc = subprocess.Popen(
                    ["rigctld", "-m", str(rig["model"]), "-r", rig["path"], "-t", "4532"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(1.5)
                return RigctldRadio("127.0.0.1", 4532, rig["name"])
        return SimRadio()

    def run(self):
        last = None
        while True:
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
                time.sleep(3)
            self.state = st
            if st != last:
                last = dict(st)
                self.on_change(st)
            time.sleep(0.4)

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

    def send_morse(self, text, wpm):
        if self.radio:
            self.radio.send_morse(text, wpm)

    def stop_morse(self):
        if self.radio:
            self.radio.stop_morse()
