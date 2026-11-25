"""Crash-proof storage for QSODesk.

Every contact is written to three places before the entry line is cleared:

1. the SQLite database (WAL mode, fully synced on every commit)
2. a plain-text ADIF journal, appended and fsync'd - readable in any editor
3. an optional mirror of that journal in another folder (e.g. a USB stick)

If the database is missing, empty or damaged at start-up, it is rebuilt from
the journal automatically. Nothing is ever truly deleted: deletes are a flag
and every edit keeps the previous version in the history table.
"""
import json
import os
import re
import shutil
import sqlite3
import threading
import time

DEFAULTS = {
    "callsign": "", "operator": "", "branch": "", "station": "Station 1",
    "current_event": "", "radio_source": "auto",
    "rigctld_host": "127.0.0.1", "rigctld_port": "4532",
    "mirror_dir": "", "tt_serial_per_band": "0",
    "grid": "", "audio_source": "auto", "audio_device": "", "cw_wpm": "22",
    "dashboard": "", "radios": "", "audio_radio": "", "keymap": "", "net_name": "qsodesk",
}

FIELDS = ["uuid", "event", "contest", "ts", "call", "band", "mode", "rmode",
          "freq", "rst_sent", "rst_rcvd", "serial_sent", "serial_rcvd", "exch",
          "name", "notes", "station", "operator", "deleted"]
EDITABLE = ["call", "band", "mode", "rst_sent", "rst_rcvd", "serial_sent",
            "serial_rcvd", "exch", "name", "notes", "ts"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS qsos (
  id INTEGER PRIMARY KEY, uuid TEXT UNIQUE, event TEXT, contest TEXT,
  ts REAL, call TEXT, band TEXT, mode TEXT, rmode TEXT, freq INTEGER,
  rst_sent TEXT, rst_rcvd TEXT, serial_sent INTEGER, serial_rcvd TEXT,
  exch TEXT, name TEXT, notes TEXT, station TEXT, operator TEXT,
  deleted INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS qsos_event ON qsos(event);
CREATE TABLE IF NOT EXISTS history (
  id INTEGER PRIMARY KEY, uuid TEXT, ts REAL, action TEXT, snapshot TEXT);
CREATE TABLE IF NOT EXISTS events (
  name TEXT PRIMARY KEY, contest TEXT, created REAL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""

JOURNAL_HEADER = (b"QSODesk journal. Every logged, edited or deleted contact is "
                  b"appended here.\n<ADIF_VER:5>3.1.4 <PROGRAMID:7>QSODesk <EOH>\n")

_TAG = re.compile(rb"<([A-Za-z0-9_]+)(?::(\d+))?(?::[^>]*)?>")


def parse_adif(data):
    """Minimal ADIF reader (lengths are byte counts, as the spec says)."""
    out, rec, pos = [], {}, 0
    while True:
        m = _TAG.search(data, pos)
        if not m:
            break
        tag = m.group(1).upper()
        if tag == b"EOR":
            out.append(rec)
            rec, pos = {}, m.end()
        elif tag == b"EOH":
            rec, pos = {}, m.end()
        else:
            n = int(m.group(2) or 0)
            rec[tag.decode()] = data[m.end():m.end() + n].decode("utf-8", "replace")
            pos = m.end() + n
    return out


def _f(tag, value):
    b = str(value).encode("utf-8")
    return b"<" + tag.encode() + b":" + str(len(b)).encode() + b">" + b + b" "


def adif_mode(q):
    rm = (q.get("rmode") or "").upper()
    if rm in ("FT8", "FT4"):
        return rm, ""
    if q.get("mode") == "CW":
        return "CW", ""
    if q.get("mode") == "DIG":
        return ("PSK", "PSK31") if rm.startswith("PSK") else ("RTTY", "")
    if rm in ("AM", "FM"):
        return rm, ""
    return "SSB", rm if rm in ("USB", "LSB") else ""


def adif_record(q, settings, action=None):
    t = time.gmtime(q["ts"])
    mode, sub = adif_mode(q)
    b = _f("CALL", q["call"])
    b += _f("QSO_DATE", time.strftime("%Y%m%d", t)) + _f("TIME_ON", time.strftime("%H%M%S", t))
    b += _f("BAND", q.get("band") or "") + _f("MODE", mode)
    if sub:
        b += _f("SUBMODE", sub)
    if q.get("freq"):
        b += _f("FREQ", "%.6f" % (q["freq"] / 1e6))
    b += _f("RST_SENT", q.get("rst_sent") or "") + _f("RST_RCVD", q.get("rst_rcvd") or "")
    if q.get("serial_sent"):
        b += _f("STX", q["serial_sent"])
    if q.get("serial_rcvd"):
        b += _f("SRX_STRING", q["serial_rcvd"])
        if str(q["serial_rcvd"]).isdigit():
            b += _f("SRX", int(q["serial_rcvd"]))
    if q.get("name"):
        b += _f("NAME", q["name"])
    if q.get("notes"):
        b += _f("COMMENT", q["notes"])
    if settings.get("callsign"):
        b += _f("STATION_CALLSIGN", settings["callsign"])
    if q.get("operator"):
        b += _f("OPERATOR", q["operator"])
    if q.get("contest") and q["contest"] != "general":
        b += _f("CONTEST_ID", q["contest"].upper())
    if action:                       # journal-only fields, used for recovery
        b += _f("APP_QSODESK_ACTION", action) + _f("APP_QSODESK_UUID", q["uuid"])
        b += _f("APP_QSODESK_TS", repr(float(q["ts"])))
        b += _f("APP_QSODESK_EVENT", q.get("event") or "")
        b += _f("APP_QSODESK_CONTEST", q.get("contest") or "")
        b += _f("APP_QSODESK_MODE", q.get("mode") or "")
        b += _f("APP_QSODESK_RMODE", q.get("rmode") or "")
        b += _f("APP_QSODESK_EXCH", q.get("exch") or "")
        b += _f("APP_QSODESK_STATION", q.get("station") or "")
        b += _f("APP_QSODESK_DELETED", int(q.get("deleted") or 0))
    return b + b"<EOR>\n"


class Store:
    def __init__(self, data_dir):
        self.dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.db_path = os.path.join(data_dir, "qsodesk.db")
        self.journal_path = os.path.join(data_dir, "journal.adi")
        self.settings_path = os.path.join(data_dir, "settings-backup.json")
        self.lock = threading.RLock()
        self.recovered = 0
        self._open()

    # ---- opening and recovery -------------------------------------------
    def _connect(self):
        db = sqlite3.connect(self.db_path, check_same_thread=False, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.executescript(SCHEMA)
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise sqlite3.DatabaseError("integrity check failed")
        return db

    def _open(self):
        try:
            self.db = self._connect()
        except sqlite3.DatabaseError:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            for ext in ("", "-wal", "-shm"):
                if os.path.exists(self.db_path + ext):
                    os.replace(self.db_path + ext, "%s.damaged-%s%s" % (self.db_path, stamp, ext))
            self.db = self._connect()
        if not os.path.exists(self.journal_path):
            with open(self.journal_path, "wb") as fh:
                fh.write(JOURNAL_HEADER)
                fh.flush()
                os.fsync(fh.fileno())
        if (self.db.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0
                and os.path.exists(self.settings_path)):
            try:                                    # settings survive a lost database too
                with open(self.settings_path) as fh:
                    self.set_settings(json.load(fh))
            except (OSError, ValueError):
                pass
        if self.db.execute("SELECT COUNT(*) FROM qsos").fetchone()[0] == 0:
            self.recovered = self._replay()
        if not self.settings().get("current_event"):
            self.create_event("Practice - Jock White", "jwfd")

    def _replay(self):
        """Rebuild the database from the journal."""
        with open(self.journal_path, "rb") as fh:
            records = parse_adif(fh.read())
        n = 0
        for r in records:
            uuid = r.get("APP_QSODESK_UUID")
            if not uuid:
                continue
            q = {
                "uuid": uuid, "event": r.get("APP_QSODESK_EVENT", ""),
                "contest": r.get("APP_QSODESK_CONTEST", "general"),
                "ts": float(r.get("APP_QSODESK_TS", "0") or 0),
                "call": r.get("CALL", ""), "band": r.get("BAND", ""),
                "mode": r.get("APP_QSODESK_MODE", "PH"),
                "rmode": r.get("APP_QSODESK_RMODE", ""),
                "freq": int(round(float(r.get("FREQ", "0") or 0) * 1e6)),
                "rst_sent": r.get("RST_SENT", ""), "rst_rcvd": r.get("RST_RCVD", ""),
                "serial_sent": int(r["STX"]) if r.get("STX", "").isdigit() else None,
                "serial_rcvd": r.get("SRX_STRING", ""),
                "exch": r.get("APP_QSODESK_EXCH", ""), "name": r.get("NAME", ""),
                "notes": r.get("COMMENT", ""), "station": r.get("APP_QSODESK_STATION", ""),
                "operator": r.get("OPERATOR", ""),
                "deleted": int(r.get("APP_QSODESK_DELETED", "0") or 0),
            }
            if q["event"]:
                self.db.execute("INSERT OR IGNORE INTO events VALUES (?,?,?)",
                                (q["event"], q["contest"], q["ts"]))
            cols = ",".join(FIELDS)
            marks = ",".join("?" for _ in FIELDS)
            sets = ",".join("%s=excluded.%s" % (c, c) for c in FIELDS if c != "uuid")
            self.db.execute("INSERT INTO qsos (%s) VALUES (%s) ON CONFLICT(uuid) DO UPDATE SET %s"
                            % (cols, marks, sets), [q[c] for c in FIELDS])
            n += 1
        if n:
            last = self.db.execute("SELECT event FROM qsos ORDER BY ts DESC LIMIT 1").fetchone()
            if last and last[0]:
                self.set_settings({"current_event": last[0]})
        return self.db.execute("SELECT COUNT(*) FROM qsos").fetchone()[0] if n else 0

    # ---- journal ----------------------------------------------------------
    def _journal(self, q, action):
        """Append to the journal (and mirror). Returns what was saved where."""
        rec = adif_record(q, self.settings(), action)
        saved = {"db": True, "journal": False, "mirror": None}
        with open(self.journal_path, "ab") as fh:
            before = fh.tell()
            fh.write(rec)
            fh.flush()
            os.fsync(fh.fileno())
        saved["journal"] = True
        mdir = self.settings().get("mirror_dir", "").strip()
        if mdir:
            try:
                mpath = os.path.join(mdir, "qsodesk-journal.adi")
                if not os.path.exists(mpath) or os.path.getsize(mpath) != before:
                    shutil.copyfile(self.journal_path, mpath)   # catch the mirror up
                    with open(mpath, "ab") as fh:
                        os.fsync(fh.fileno())
                else:
                    with open(mpath, "ab") as fh:
                        fh.write(rec)
                        fh.flush()
                        os.fsync(fh.fileno())
                saved["mirror"] = True
            except OSError:
                saved["mirror"] = False
        return saved

    # ---- settings and events ---------------------------------------------
    def settings(self):
        out = dict(DEFAULTS)
        for row in self.db.execute("SELECT key, value FROM settings"):
            out[row["key"]] = row["value"]
        return out

    def set_settings(self, values):
        with self.lock:
            for k, v in values.items():
                if k in DEFAULTS:
                    self.db.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) "
                                    "DO UPDATE SET value=excluded.value", (k, str(v)))
            try:
                with open(self.settings_path, "w") as fh:
                    json.dump(self.settings(), fh, indent=1)
                    fh.flush()
                    os.fsync(fh.fileno())
            except OSError:
                pass

    def events(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM events ORDER BY created DESC")]

    def event(self, name=None):
        name = name or self.settings().get("current_event")
        row = self.db.execute("SELECT * FROM events WHERE name=?", (name,)).fetchone()
        return dict(row) if row else None

    def create_event(self, name, contest):
        name = name.strip()
        with self.lock:
            self.db.execute("INSERT OR IGNORE INTO events VALUES (?,?,?)",
                            (name, contest, time.time()))
            self.set_settings({"current_event": name})

    # ---- contacts ---------------------------------------------------------
    def qsos(self, event, include_deleted=False):
        sql = "SELECT * FROM qsos WHERE event=?"
        if not include_deleted:
            sql += " AND deleted=0"
        return [dict(r) for r in self.db.execute(sql + " ORDER BY ts, id", (event,))]

    def all_qsos(self, contest=None):
        """Every live contact, optionally only those logged under one contest type."""
        sql, args = "SELECT * FROM qsos WHERE deleted=0", []
        if contest:
            sql += " AND contest=?"
            args.append(contest)
        return [dict(r) for r in self.db.execute(sql + " ORDER BY ts, id", args)]

    def get(self, uuid):
        row = self.db.execute("SELECT * FROM qsos WHERE uuid=?", (uuid,)).fetchone()
        return dict(row) if row else None

    def next_serial(self, event, scope):
        """Next serial to send. Deleted contacts still use up their number,
        because it has already been given out on air."""
        if scope is None:
            return None
        sql, args = "SELECT MAX(serial_sent) FROM qsos WHERE event=?", [event]
        if scope:
            sql += " AND band=?"
            args.append(scope)
        return (self.db.execute(sql, args).fetchone()[0] or 0) + 1

    def serial_used(self, event, scope, serial):
        sql, args = "SELECT 1 FROM qsos WHERE event=? AND serial_sent=?", [event, serial]
        if scope:
            sql += " AND band=?"
            args.append(scope)
        return self.db.execute(sql, args).fetchone() is not None

    def add(self, q, scope):
        """Log a contact. Safe to call twice with the same uuid."""
        with self.lock:
            old = self.get(q["uuid"])
            if old:
                return old, {"db": True, "journal": True, "mirror": None}, False
            changed = False
            if scope is None:
                q["serial_sent"] = None
            else:
                want = q.get("serial_sent")
                if not want or self.serial_used(q["event"], scope, want):
                    q["serial_sent"] = self.next_serial(q["event"], scope)
                    changed = bool(want)
            q["deleted"] = 0
            cols = ",".join(FIELDS)
            marks = ",".join("?" for _ in FIELDS)
            self.db.execute("INSERT INTO qsos (%s) VALUES (%s)" % (cols, marks),
                            [q.get(c) for c in FIELDS])
            row = self.get(q["uuid"])
            saved = self._journal(row, "ADD")
            return row, saved, changed

    def _snapshot(self, row, action):
        self.db.execute("INSERT INTO history (uuid, ts, action, snapshot) VALUES (?,?,?,?)",
                        (row["uuid"], time.time(), action, json.dumps(row)))

    def edit(self, uuid, values):
        with self.lock:
            row = self.get(uuid)
            if not row:
                return None, None
            self._snapshot(row, "EDIT")
            for k in EDITABLE:
                if k in values:
                    self.db.execute("UPDATE qsos SET %s=? WHERE uuid=?" % k, (values[k], uuid))
            row = self.get(uuid)
            return row, self._journal(row, "EDIT")

    def set_deleted(self, uuid, deleted):
        with self.lock:
            row = self.get(uuid)
            if not row:
                return None, None
            self._snapshot(row, "DELETE" if deleted else "RESTORE")
            self.db.execute("UPDATE qsos SET deleted=? WHERE uuid=?", (1 if deleted else 0, uuid))
            row = self.get(uuid)
            return row, self._journal(row, "DELETE" if deleted else "RESTORE")

    def history(self, uuid):
        return [dict(r) for r in self.db.execute(
            "SELECT ts, action, snapshot FROM history WHERE uuid=? ORDER BY id", (uuid,))]
