"""Audio for QSODesk: one stream from the radio's USB sound card, shared by
the waterfall, the CW decoder and FT8.

* On a Pi the radio's sound card (ALSA "CODEC") is read with `arecord` and
  transmit audio is played with `aplay` - both part of alsa-utils.
* With no sound card, a simulator generates band noise plus CW or FT8-like
  signals, so every page can be tried without a radio.

Audio is 12 kHz, 16-bit mono throughout (the native rate for FT8).
NOTE: the sound-card path has not yet been tested against real hardware.
"""
import array
import math
import os
import queue
import random
import shutil
import subprocess
import tempfile
import threading
import time
import wave

RATE = 12000
CHUNK = 1200            # 0.1 s

MORSE = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.",
    "G": "--.", "H": "....", "I": "..", "J": ".---", "K": "-.-", "L": ".-..",
    "M": "--", "N": "-.", "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.",
    "S": "...", "T": "-", "U": "..-", "V": "...-", "W": ".--", "X": "-..-",
    "Y": "-.--", "Z": "--..", "0": "-----", "1": ".----", "2": "..---",
    "3": "...--", "4": "....-", "5": ".....", "6": "-....", "7": "--...",
    "8": "---..", "9": "----.", "/": "-..-.", "?": "..--..", ".": ".-.-.-",
    ",": "--..--", "=": "-...-", "+": ".-.-.", "-": "-....-",
}


def cw_samples(text, wpm=22, freq=650, amp=6000):
    """Morse audio for `text` (used by the simulator)."""
    dot = int(RATE * 1.2 / max(5, wpm))
    ramp = max(1, int(RATE * 0.005))
    w = 2 * math.pi * freq / RATE
    out = array.array("h")
    k = 0
    for word in text.upper().split():
        for ch in word:
            code = MORSE.get(ch)
            if not code:
                continue
            for sym in code:
                n = dot if sym == "." else 3 * dot
                for i in range(n):
                    env = min(1.0, i / ramp, (n - 1 - i) / ramp)
                    out.append(int(amp * env * math.sin(w * k)))
                    k += 1
                out.extend(array.array("h", bytes(2 * dot)))
                k += dot
            out.extend(array.array("h", bytes(4 * dot)))      # 3 dots between letters
            k += 2 * dot
        out.extend(array.array("h", bytes(8 * dot)))          # 7 dots between words
        k += 4 * dot
    return out


_COSTAS = [3, 1, 4, 0, 6, 5, 2]


def ft8_lookalike(message, freq, amp=2500):
    """An FT8-shaped signal for the SIMULATOR's waterfall: correct timing, tone
    spacing and sync pattern, but the data symbols are random - it is not a
    decodable transmission. Real transmit audio comes from the FT8 engine."""
    rnd = random.Random(message)
    sps = int(RATE * 0.16)
    out = array.array("h", bytes(2 * int(RATE * 0.5)))
    phase = 0.0
    for s in range(79):
        blk = s if s < 7 else s - 36 if 36 <= s < 43 else s - 72 if s >= 72 else -1
        tone = _COSTAS[blk] if blk >= 0 else rnd.randrange(8)
        dphi = 2 * math.pi * (freq + 6.25 * tone) / RATE
        for _ in range(sps):
            phase += dphi
            out.append(int(amp * math.sin(phase)))
    return out


def write_wav(path, samples):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(samples if isinstance(samples, (bytes, bytearray)) else samples.tobytes())


SIM_CW = [("CQ FD DE ZL2SIM ZL2SIM K", 22, 650), ("CQ TEST VK3SIM VK3SIM", 17, 930),
          ("ZL4TEST DE ZL2SIM 5NN 014 27 K", 22, 650), ("TU ZL1DX 73", 26, 480)]


class AudioEngine(threading.Thread):
    def __init__(self, get_settings, radio_state):
        super().__init__(daemon=True)
        self.get_settings = get_settings
        self.radio_state = radio_state
        self.subs = []                  # one queue per browser listening
        self.listeners = []             # callables(chunk_bytes, time)
        self.lock = threading.Lock()
        self.injects = []               # simulator: [samples, position]
        self.status = {"source": "sim", "device": "", "note": ""}
        self.proc = None
        self._reconf = True
        rnd = random.Random(7)
        self.pool = [rnd.gauss(0, 260) for _ in range(RATE * 2)]
        self.next_cw = 0
        self.cw_i = 0

    def reconfigure(self):
        self._reconf = True

    # ---- device handling --------------------------------------------------
    @staticmethod
    def detect():
        """Find the radio's USB sound card (Icom rigs appear as 'CODEC')."""
        if not shutil.which("arecord"):
            return None
        try:
            out = subprocess.run(["arecord", "-L"], capture_output=True, text=True, timeout=3).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        for line in out.splitlines():
            if line.startswith("plughw:") and "CODEC" in line:
                return line.strip()
        return None

    def _close(self):
        if self.proc:
            self.proc.kill()
            self.proc = None

    def _open(self):
        s = self.get_settings()
        src = s.get("audio_source", "auto")
        dev = s.get("audio_device", "").strip()
        self._close()
        self.status = {"source": "sim", "device": "", "note": ""}
        if src == "sim":
            return
        if not dev:
            dev = self.detect()
        if dev and shutil.which("arecord"):
            self.proc = subprocess.Popen(
                ["arecord", "-q", "-D", dev, "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self.status = {"source": "radio", "device": dev, "note": ""}
        elif src == "device":
            self.status["note"] = "No sound device found - showing simulated audio."

    # ---- simulator --------------------------------------------------------
    def inject(self, samples):
        with self.lock:
            self.injects.append([samples, 0])

    def _sim_background(self, now):
        if self.radio_state().get("mode") != "CW" or now < self.next_cw:
            return
        text, wpm, freq = SIM_CW[self.cw_i % len(SIM_CW)]
        self.cw_i += 1
        s = cw_samples(text, wpm, freq, 3500)
        self.inject(s)
        self.next_cw = now + len(s) / RATE + random.uniform(3, 7)

    def _sim_chunk(self):
        start = random.randrange(len(self.pool) - CHUNK)
        buf = self.pool[start:start + CHUNK]
        with self.lock:
            for inj in self.injects:
                a, pos = inj
                n = min(CHUNK, len(a) - pos)
                for i in range(n):
                    buf[i] += a[pos + i]
                inj[1] += n
            self.injects = [x for x in self.injects if x[1] < len(x[0])]
        return array.array("h", [max(-32000, min(32000, int(v))) for v in buf])

    # ---- transmit audio ---------------------------------------------------
    def play(self, samples=None, wav_path=None, timeout=15):
        """Send audio to the radio (blocks until finished)."""
        if self.status["source"] == "sim":
            if samples is None:
                with wave.open(wav_path, "rb") as w:
                    samples = array.array("h", w.readframes(w.getnframes()))
            self.inject(samples)
            time.sleep(len(samples) / RATE)
            return
        tmp = None
        if wav_path is None:
            fd, tmp = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            write_wav(tmp, samples)
            wav_path = tmp
        try:
            subprocess.run(["aplay", "-q", "-D", self.status["device"], wav_path], timeout=timeout)
        finally:
            if tmp:
                os.remove(tmp)

    # ---- main loop --------------------------------------------------------
    def run(self):
        next_t = time.time()
        while True:
            try:
                if self._reconf:
                    self._reconf = False
                    self._open()
                    next_t = time.time()
                if self.proc:
                    data = self.proc.stdout.read(CHUNK * 2)
                    if len(data) < CHUNK * 2:
                        raise IOError("the sound device stopped")
                else:
                    self._sim_background(time.time())
                    data = self._sim_chunk().tobytes()
                    next_t += CHUNK / RATE
                    delay = next_t - time.time()
                    if delay > 0:
                        time.sleep(delay)
                    elif delay < -1:
                        next_t = time.time()
                now = time.time()
                with self.lock:
                    subs = list(self.subs)
                for q in subs:
                    try:
                        q.put_nowait(data)
                    except queue.Full:
                        pass
                for fn in self.listeners:
                    fn(data, now)
            except Exception as exc:
                self._close()
                self.status = {"source": "sim", "device": "", "note": "Audio problem: %s" % exc}
                time.sleep(3)
                self._reconf = True
