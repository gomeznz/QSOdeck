"""QSODesk web server. Standard library only - nothing to pip install."""
import csv
import io
import json
import os
import queue
import threading
import time
import uuid as uuidlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import contests
from .audio import AudioEngine, cw_samples
from .ft8 import FT8
from .radio import RadioHub
from .spaceweather import SpaceWeather
from .store import Store, adif_record

STATIC = os.path.join(os.path.dirname(__file__), "static")
VERSION = "0.4"
PAGES = {"/": "index.html", "/index.html": "index.html", "/dashboard": "dashboard.html",
         "/transceivers": "transceivers.html", "/keypad": "keypad.html",
         "/waterfall": "waterfall.html",
         "/cw": "cw.html", "/ft8": "ft8.html"}
TYPES = {".html": "text/html; charset=utf-8", ".js": "application/javascript",
         ".css": "text/css"}


class App:
    def __init__(self, data_dir):
        self.store = Store(data_dir)
        self.clients = []
        self.clients_lock = threading.Lock()
        self.hub = RadioHub(self.store, lambda st: self.broadcast("radio", st))
        self.audio = AudioEngine(self.audio_settings, lambda: self.radio.state)
        self.ft8 = FT8(self)
        self.audio.start()
        self.spacewx = SpaceWeather(data_dir)

    # ---- transceivers -----------------------------------------------------
    @property
    def radio(self):
        """The transceiver the waterfall, CW and FT8 pages are working with."""
        return self.hub.get(self.store.settings().get("audio_radio"))

    def rig(self, rid=None):
        """A transceiver by id; with no id, the one the radio pages use."""
        return self.hub.get(rid) if rid else self.radio

    def audio_settings(self):
        cfg = self.radio.cfg
        return {"audio_source": "sim" if cfg.get("source") == "sim" else "auto",
                "audio_device": cfg.get("audio", "")}

    def radios(self):
        return {"radios": self.hub.list(), "detected": self.hub.detected(),
                "audio_radio": self.radio.id}

    # ---- CW ---------------------------------------------------------------
    def cw_send(self, b):
        text = " ".join((b.get("text") or "").upper().split())[:200]
        wpm = max(5, min(50, int(b.get("wpm") or self.store.settings().get("cw_wpm") or 22)))
        if not text:
            return {"error": "Nothing to send"}
        if self.radio.state.get("simulated", True):
            self.audio.inject(cw_samples(text, wpm, 700, 9000))   # hear it on the simulator
        else:
            self.radio.send_morse(text, wpm)
        return {"ok": True, "text": text, "wpm": wpm}

    # ---- live updates -----------------------------------------------------
    def broadcast(self, kind, data):
        with self.clients_lock:
            for q in self.clients:
                try:
                    q.put_nowait((kind, data))
                except queue.Full:
                    pass

    # ---- helpers ----------------------------------------------------------
    def ctx(self, event_name=None):
        s = self.store.settings()
        ev = self.store.event(event_name) or self.store.event()
        return s, ev, contests.get(ev["contest"])

    def snapshot(self, rid=None):
        s, ev, c = self.ctx()
        rs = self.rig(rid).state
        now = time.time()
        live = self.store.qsos(ev["name"])
        everything = self.store.qsos(ev["name"], include_deleted=True)
        score = c.score(live, s)
        marks = score.pop("marks")
        rows = []
        for q in reversed(everything[-400:]):
            q["mark"] = marks.get(q["id"], {"pts": 0, "mult": False, "dupe": False})
            rows.append(q)
        scope = c.serial_scope(rs["band"], s)
        return {
            "version": VERSION, "now": now, "settings": s,
            "events": self.store.events(), "event": ev,
            "contest": c.describe(),
            "contests": [x.describe() for x in contests.CONTESTS.values()],
            "radio": rs, "serial": self.store.next_serial(ev["name"], scope),
            "serial_scope": scope, "score": score, "period": c.period(now),
            "available": c.available(live, rs["band"], rs["mode"], now),
            "qsos": rows, "total_logged": len(live),
            "recovered": self.store.recovered, "audio": self.audio.status,
            "radios": [{"id": r["id"], "name": r["name"]} for r in self.hub.list()],
            "audio_radio": self.radio.id,
        }

    def check(self, p):
        s, ev, c = self.ctx()
        rs = self.rig(p.get("radio")).state
        band = p.get("band") or rs["band"]
        mode = p.get("mode") or rs["mode"]
        live = self.store.qsos(ev["name"])
        out = c.check(live, p.get("call", ""), band, mode, time.time(), s)
        typed = p.get("exch", "")
        if typed and c.key == "jwfd" and out["status"] in ("ok", "new"):
            if c.is_new_mult(live, typed, band, mode, s):
                out["status"] = "mult"
                out["msg"] = "New multiplier - branch %s on %s %s" % (
                    contests.norm_branch(typed), band, mode)
        return out

    def log(self, b):
        call = (b.get("call") or "").upper().strip()
        if not call:
            return {"error": "No callsign"}
        s, ev, c = self.ctx(b.get("event"))
        rig = self.rig(b.get("radio"))
        rs = rig.state
        now = time.time()
        ts = float(b.get("ts") or now)
        if ts > now + 300 or ts < now - 14 * 86400:
            ts = now
        band = b.get("band") or rs["band"]
        exch = (b.get("exch") or "").strip()
        if c.key == "jwfd":
            exch = contests.norm_branch(exch)
        sent = b.get("serial_sent")
        q = {
            "uuid": b.get("uuid") or str(uuidlib.uuid4()), "event": ev["name"],
            "contest": c.key, "ts": ts, "call": call, "band": band,
            "mode": b.get("mode") or rs["mode"], "rmode": b.get("rmode") or rs["rmode"],
            "freq": int(b.get("freq") or rs["freq"] or 0),
            "rst_sent": (b.get("rst_sent") or "").strip(),
            "rst_rcvd": (b.get("rst_rcvd") or "").strip(),
            "serial_sent": int(sent) if str(sent or "").isdigit() else None,
            "serial_rcvd": (b.get("serial_rcvd") or "").strip(), "exch": exch,
            "name": (b.get("name") or "").strip(), "notes": (b.get("notes") or "").strip(),
            "station": rig.cfg.get("name") or s.get("station", ""),
            "operator": (b.get("operator") or s.get("operator") or s.get("callsign") or "").upper(),
        }
        row, saved, changed = self.store.add(q, c.serial_scope(band, s))
        self.broadcast("log", {})
        return {"qso": row, "saved": saved, "serial_changed": changed}

    # ---- exports ----------------------------------------------------------
    def export_adif(self, event):
        s, ev, c = self.ctx(event)
        out = b"QSODesk export - %s\n<ADIF_VER:5>3.1.4 <PROGRAMID:7>QSODesk <EOH>\n" % ev["name"].encode()
        for q in self.store.qsos(ev["name"]):
            out += adif_record(q, s)
        return out

    def export_sheets(self, event):
        """Jock White log sheets: one section per band, in time order, with the
        column order the rules ask for and multipliers flagged."""
        s, ev, c = self.ctx(event)
        live = self.store.qsos(ev["name"])
        marks = c.score(live, s)["marks"]
        own = contests.norm_branch(s.get("branch", "")) or "HS"
        buf = io.StringIO()
        w = csv.writer(buf)
        for band in sorted(set(q["band"] for q in live), reverse=True):
            w.writerow(["Station", s.get("callsign", ""), "Branch", own, "Band", band,
                        "Operator", s.get("operator", "")])
            w.writerow(["Date (NZ)", "Time (NZ)", "Station worked", "Mode", "Band",
                        "Exchange sent", "Exchange received", "Points", "Multiplier", "Dupe"])
            for q in [x for x in live if x["band"] == band]:
                m = marks.get(q["id"], {})
                t = time.localtime(q["ts"])
                w.writerow([
                    time.strftime("%d/%m/%Y", t), time.strftime("%H:%M", t), q["call"],
                    q["mode"], band,
                    "%s %03d %s" % (q["rst_sent"], q["serial_sent"] or 0, own),
                    "%s %s %s" % (q["rst_rcvd"], q["serial_rcvd"], q["exch"]),
                    m.get("pts", 0), "MULT" if m.get("mult") else "",
                    "DUPE" if m.get("dupe") else ""])
            w.writerow([])
        return buf.getvalue().encode()

    def export_summary(self, event):
        s, ev, c = self.ctx(event)
        sc = c.score(self.store.qsos(ev["name"]), s)
        lines = ["QSODesk summary - %s (%s)" % (ev["name"], c.name),
                 "Station: %s   Branch: %s   Operator(s): %s" % (
                     s.get("callsign", ""), s.get("branch", "") or "HS", s.get("operator", "")), ""]
        for ln in sc["lines"]:
            label = ln["label"]
            if label == "block":
                label = "Block from " + time.strftime("%H:%M UTC", time.gmtime(ln["start"]))
            lines.append("%-22s contacts %4d   points %5d   multipliers %3d" % (
                label, ln["count"], ln["points"], ln["mults"]))
        lines += ["", "Valid contacts: %d" % sc["count"], "Contact points: %d" % sc["points"],
                  "Multipliers: %d" % sc["mults"], "CLAIMED SCORE: %d" % sc["total"], ""]
        return "\n".join(lines).encode()

    def export_cabrillo(self, event):
        s, ev, c = self.ctx(event)
        live = self.store.qsos(ev["name"])
        sc = c.score(live, s)
        me = s.get("callsign", "") or "NOCALL"
        modes = {"PH": "PH", "CW": "CW", "DIG": "RY"}
        out = ["START-OF-LOG: 3.0", "CREATED-BY: QSODesk " + VERSION,
               "CONTEST: " + ("TRANS-TASMAN" if c.key == "tt" else c.key.upper()),
               "CALLSIGN: " + me, "CATEGORY-OPERATOR: ", "CATEGORY-BAND: ALL",
               "CATEGORY-MODE: ", "CATEGORY-POWER: ", "CATEGORY-TRANSMITTER: ",
               "CLAIMED-SCORE: %d" % sc["total"],
               "OPERATORS: " + (s.get("operator", "") or me), "NAME: ", "EMAIL: "]
        for q in live:
            t = time.gmtime(q["ts"])
            out.append("QSO: %5d %s %s %s %-10s %-3s %03d %-10s %-3s %s" % (
                (q["freq"] or 0) // 1000, modes.get(q["mode"], "PH"),
                time.strftime("%Y-%m-%d", t), time.strftime("%H%M", t), me,
                q["rst_sent"], q["serial_sent"] or 0, q["call"], q["rst_rcvd"],
                q["serial_rcvd"]))
        out.append("END-OF-LOG:")
        return ("\n".join(out) + "\n").encode()


class Handler(BaseHTTPRequestHandler):
    app = None
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def send(self, body, ctype="application/json", status=200, filename=None):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if filename:
            self.send_header("Content-Disposition", 'attachment; filename="%s"' % filename)
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        app = self.app
        url = urlparse(self.path)
        p = {k: v[0] for k, v in parse_qs(url.query).items()}
        path = url.path
        if path in PAGES or path.startswith("/static/"):
            name = PAGES.get(path) or os.path.basename(path)
            full = os.path.join(STATIC, name)
            ext = os.path.splitext(name)[1]
            if ext not in TYPES or not os.path.isfile(full):
                return self.send({"error": "not found"}, status=404)
            with open(full, "rb") as fh:
                return self.send(fh.read(), TYPES[ext])
        if path == "/api/audio":
            return self.audio_stream()
        if path == "/api/spaceweather":
            return self.send(app.spacewx.get())
        if path == "/api/ft8":
            return self.send(app.ft8.status())
        if path == "/api/state":
            return self.send(app.snapshot(p.get("radio")))
        if path == "/api/radios":
            return self.send(app.radios())
        if path == "/api/check":
            return self.send(app.check(p))
        if path == "/api/events":
            return self.sse()
        if path == "/api/history":
            return self.send(app.store.history(p.get("uuid", "")))
        ev = p.get("event")
        safe = "".join(ch if ch.isalnum() else "-" for ch in (ev or "log")).strip("-")
        if path == "/export/adif":
            return self.send(app.export_adif(ev), "text/plain", filename=safe + ".adi")
        if path == "/export/sheets":
            return self.send(app.export_sheets(ev), "text/csv", filename=safe + "-log-sheets.csv")
        if path == "/export/summary":
            return self.send(app.export_summary(ev), "text/plain; charset=utf-8",
                             filename=safe + "-summary.txt")
        if path == "/export/cabrillo":
            return self.send(app.export_cabrillo(ev), "text/plain", filename=safe + ".cbr")
        if path == "/export/journal":
            with open(app.store.journal_path, "rb") as fh:
                return self.send(fh.read(), "text/plain", filename="qsodesk-journal.adi")
        self.send({"error": "not found"}, status=404)

    def do_POST(self):
        app = self.app
        path = urlparse(self.path).path
        try:
            b = self.body()
        except ValueError:
            return self.send({"error": "bad request"}, status=400)
        if path == "/api/qso":
            out = app.log(b)
            return self.send(out, status=400 if "error" in out else 200)
        if path == "/api/qso/edit":
            vals = dict(b.get("values") or {})
            if "call" in vals:
                vals["call"] = vals["call"].upper().strip()
            if str(vals.get("serial_sent", "")).isdigit():
                vals["serial_sent"] = int(vals["serial_sent"])
            elif "serial_sent" in vals:
                vals.pop("serial_sent")
            row, saved = app.store.edit(b.get("uuid", ""), vals)
            app.broadcast("log", {})
            return self.send({"qso": row, "saved": saved}, status=200 if row else 404)
        if path == "/api/qso/delete":
            row, saved = app.store.set_deleted(b.get("uuid", ""), bool(b.get("deleted", True)))
            app.broadcast("log", {})
            return self.send({"qso": row, "saved": saved}, status=200 if row else 404)
        if path == "/api/settings":
            app.store.set_settings(b)
            app.broadcast("log", {})
            return self.send({"ok": True})
        if path == "/api/event":
            name = (b.get("name") or "").strip()
            if not name:
                return self.send({"error": "Give the event a name"}, status=400)
            app.store.create_event(name, b.get("contest") or "general")
            app.broadcast("log", {})
            return self.send({"ok": True})
        if path == "/api/radio/step":
            try:
                rig = app.rig(b.get("radio"))
                rig.tune(freq=max(100000, int(rig.state.get("freq") or 0) + int(b.get("delta") or 0)))
            except Exception as exc:
                return self.send({"error": str(exc)}, status=502)
            return self.send({"ok": True})
        if path == "/api/radio":
            try:
                app.rig(b.get("radio")).tune(b.get("band"), b.get("mode"), b.get("freq"),
                                             b.get("rmode"))
            except Exception as exc:
                return self.send({"error": str(exc)}, status=502)
            return self.send({"ok": True})
        try:
            if path == "/api/radios/save":
                app.hub.save(b.get("radios") or [])
                app.audio.reconfigure()
                app.broadcast("radios", {})
                app.broadcast("log", {})
                return self.send(app.radios())
            if path == "/api/radios/listen":
                app.store.set_settings({"audio_radio": b.get("id", "")})
                app.audio.reconfigure()
                app.broadcast("radios", {})
                app.broadcast("radio", app.radio.state)
                return self.send({"ok": True})
            if path == "/api/cw/send":
                out = app.cw_send(b)
                return self.send(out, status=400 if "error" in out else 200)
            if path == "/api/cw/stop":
                with app.audio.lock:
                    app.audio.injects = []
                app.radio.stop_morse()
                return self.send({"ok": True})
            if path == "/api/ft8/call":
                err = app.ft8.call(b.get("call", ""), b.get("snr", -10), b.get("slot", 0),
                                   b.get("grid", ""))
                return self.send({"error": err} if err else {"ok": True}, status=400 if err else 200)
            if path == "/api/ft8/cq":
                err = app.ft8.cq()
                return self.send({"error": err} if err else {"ok": True}, status=400 if err else 200)
            if path == "/api/ft8/halt":
                app.ft8.halt()
                return self.send({"ok": True})
            if path == "/api/ft8/txfreq":
                app.ft8.tx_freq = max(200, min(2900, int(b.get("freq") or 1500)))
                app.ft8.changed()
                return self.send({"ok": True})
        except Exception as exc:
            return self.send({"error": str(exc)}, status=502)
        self.send({"error": "not found"}, status=404)

    def audio_stream(self):
        """Raw 12 kHz 16-bit mono audio, as a never-ending download."""
        app = self.app
        q = queue.Queue(maxsize=50)
        with app.audio.lock:
            app.audio.subs.append(q)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            while True:
                self.wfile.write(q.get(timeout=10))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError, queue.Empty):
            pass
        finally:
            self.close_connection = True
            with app.audio.lock:
                if q in app.audio.subs:
                    app.audio.subs.remove(q)

    def sse(self):
        app = self.app
        q = queue.Queue(maxsize=200)
        with app.clients_lock:
            app.clients.append(q)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            for r in app.hub.list():
                q.put(("radio", r["state"]))
            while True:
                try:
                    kind, data = q.get(timeout=15)
                    msg = "event: %s\ndata: %s\n\n" % (kind, json.dumps(data))
                except queue.Empty:
                    msg = ": ping\n\n"
                self.wfile.write(msg.encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.close_connection = True
            with app.clients_lock:
                if q in app.clients:
                    app.clients.remove(q)


def serve(data_dir, port=8073, host="0.0.0.0"):
    Handler.app = App(data_dir)
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    print("QSODesk %s" % VERSION)
    print("Log and journal folder: %s" % os.path.abspath(data_dir))
    if Handler.app.store.recovered:
        print("Recovered %d contacts from the journal." % Handler.app.store.recovered)
    print("Open  http://localhost:%d  in your browser (Ctrl+C to stop)" % port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped. Your log is safe.")
