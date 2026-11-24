"""Contest rules for QSODesk.

Each contest is a small class: which fields the exchange has, how long an
operating period is, what counts as a dupe, and how the score is worked out.
Adding a contest means adding a class here - nothing else changes.

A "period key" is simply time // period_seconds. Jock White periods are clock
hours and Trans-Tasman blocks start on even UTC hours, so both line up with
that arithmetic and the rules also work for practice sessions at any time.
"""
import re
import time

BANDS = [
    ("160m", 1800000, 2000000), ("80m", 3500000, 3900000),
    ("60m", 5351500, 5366500), ("40m", 7000000, 7300000),
    ("30m", 10100000, 10150000), ("20m", 14000000, 14350000),
    ("17m", 18068000, 18168000), ("15m", 21000000, 21450000),
    ("12m", 24890000, 24990000), ("10m", 28000000, 29700000),
    ("6m", 50000000, 54000000), ("2m", 144000000, 148000000),
    ("70cm", 430000000, 440000000),
]
ALL_BANDS = [b[0] for b in BANDS]

MODE_GROUP = {
    "USB": "PH", "LSB": "PH", "AM": "PH", "FM": "PH", "WFM": "PH",
    "CW": "CW", "CWR": "CW",
    "RTTY": "DIG", "RTTYR": "DIG", "PKTUSB": "DIG", "PKTLSB": "DIG",
    "PKTFM": "DIG", "PSK": "DIG", "PSKR": "DIG",
}


def band_of(hz):
    for name, lo, hi in BANDS:
        if lo <= hz <= hi:
            return name
    return ""


def default_freq(band, group):
    """A sensible spot to land on when a band button is pressed."""
    table = {
        "160m": (1850000, 1820000), "80m": (3650000, 3525000),
        "40m": (7100000, 7020000), "20m": (14200000, 14030000),
        "15m": (21250000, 21030000), "10m": (28500000, 28030000),
    }
    for name, lo, hi in BANDS:
        if name == band:
            ph, cw = table.get(band, (lo + (hi - lo) // 2, lo + 10000))
            return ph if group == "PH" else cw
    return 0


def default_rmode(group, hz):
    if group == "CW":
        return "CW"
    if group == "DIG":
        return "RTTY"
    return "LSB" if hz < 10000000 else "USB"


_FULL_CALL = re.compile(r"^\d?[A-Z]{1,2}\d+[A-Z]+$")


def base_call(call):
    """ZL4AB/P -> ZL4AB. Used for dupe checking."""
    parts = [p for p in call.upper().strip().split("/") if p]
    if not parts:
        return ""
    for p in parts:
        if _FULL_CALL.match(p):
            return p
    return max(parts, key=len)


def prefix_of(call):
    """ZL4AB -> ZL4, VK2XYZ/4 -> VK4, ZM90DX -> ZM90."""
    parts = [p for p in call.upper().strip().split("/") if p]
    base = base_call(call)
    m = re.match(r"^(\d?[A-Z]+)(\d+)", base)
    if not m:
        return ""
    letters, digits = m.group(1), m.group(2)
    for p in parts:
        if p != base and re.match(r"^\d$", p):
            digits = p                      # VK2XYZ/4 operates from VK4
        elif p != base and re.match(r"^\d?[A-Z]+\d+$", p):
            return p                        # VK9/ZL1ABC style
    return letters + digits


def is_vk_zl(call):
    return bool(re.match(r"^(VK|VI|VJ|VL|VM|VN|VH|AX|ZL|ZM)", prefix_of(call)))


def is_zl(call):
    return bool(re.match(r"^(ZL|ZM)", prefix_of(call)))


_OCEANIA = re.compile(r"^(ZL|ZM|ZK|VK|VI|VJ|VL|VM|VN|VH|AX|P2|YJ|FK|FO|FW|3D2|5W|A3|C2|"
                      r"T2|T3|E5|E6|H4|V6|V7|T8|KH)")


def is_oceania(call):
    """ZL, VK and the commoner Pacific prefixes. The list is not complete."""
    return bool(_OCEANIA.match(prefix_of(call)))


def norm_branch(text):
    t = (text or "").strip().upper()
    if t in ("HS", "H"):
        return "00"
    if t.isdigit():
        return t.zfill(2)
    return t


class Contest:
    key = "general"
    name = "General logging"
    period_seconds = 0
    bands = ALL_BANDS
    modes = ["PH", "CW", "DIG"]
    fields = ["name"]             # exchange fields after RST
    exch_label = ""
    has_serial = False
    exch_required = "no"          # "always", "zl" (NZ stations only) or "no"
    branch_exchange = False       # the exchange field holds an NZART branch number
    exports = []                  # extra exports beyond ADIF
    rules_url = ""
    when = ""
    submit = ""

    def last_contact(self, qsos):
        return max(qsos, key=lambda q: (q["ts"], q["id"])) if qsos else None

    def serial_scope(self, band, settings):
        return None               # None = no serial numbers

    def period_key(self, ts):
        return int(ts // self.period_seconds) if self.period_seconds else 0

    def period(self, ts=None):
        ts = time.time() if ts is None else ts
        if not self.period_seconds:
            return None
        k = self.period_key(ts)
        return {"key": k, "start": k * self.period_seconds,
                "end": (k + 1) * self.period_seconds}

    def same_slot(self, q, call, band, mode, pk):
        return (base_call(q["call"]) == call and q["band"] == band
                and q["mode"] == mode and self.period_key(q["ts"]) == pk)

    def history(self, qsos, call):
        prev = [q for q in qsos if base_call(q["call"]) == call]
        if not prev:
            return None
        return max(prev, key=lambda q: q["ts"])

    def check(self, qsos, call, band, mode, ts, settings):
        """Called on every keystroke. Returns status + a line of info."""
        call = base_call(call)
        last = self.history(qsos, call)
        out = {"status": "ok", "msg": "", "exch": "", "name": ""}
        if last:
            out["msg"] = "Worked before"
            out["name"] = last.get("name") or ""
            out["last"] = {"ts": last["ts"], "band": last["band"], "mode": last["mode"]}
        elif call:
            out["status"] = "new"
            out["msg"] = "New station"
        return out

    def score(self, qsos, settings):
        marks = {q["id"]: {"pts": 1, "mult": False, "dupe": False} for q in qsos}
        return {"total": len(qsos), "points": len(qsos), "mults": 0,
                "count": len(qsos), "lines": [], "marks": marks}

    def available(self, qsos, band, mode, ts):
        """Stations worked last period on this band/mode, not yet this period."""
        if not self.period_seconds:
            return []
        pk = self.period_key(ts)
        now_calls = set(base_call(q["call"]) for q in qsos
                        if q["band"] == band and q["mode"] == mode
                        and self.period_key(q["ts"]) == pk)
        out, seen = [], set()
        for q in sorted(qsos, key=lambda q: -q["ts"]):
            c = base_call(q["call"])
            if (q["band"] == band and q["mode"] == mode
                    and self.period_key(q["ts"]) == pk - 1
                    and c not in now_calls and c not in seen):
                seen.add(c)
                out.append({"call": q["call"], "exch": q.get("exch") or ""})
        return out

    def describe(self):
        return {"key": self.key, "name": self.name, "bands": self.bands,
                "modes": self.modes, "fields": self.fields,
                "exch_label": self.exch_label, "has_serial": self.has_serial,
                "exch_required": self.exch_required, "exports": self.exports,
                "rules_url": self.rules_url, "when": self.when, "submit": self.submit,
                "period_seconds": self.period_seconds}


class JockWhite(Contest):
    """NZART Jock White Memorial Field Day.

    RS(T) + serial + branch number. One-hour periods. A station may be worked
    once per period, per mode, per band. Phone 3 points, CW 5. Each branch
    counts as a multiplier once per band and mode. Separate serial series per
    band, shared between phone and CW.
    """
    key = "jwfd"
    name = "Jock White Field Day"
    period_seconds = 3600
    bands = ["80m", "40m"]
    modes = ["PH", "CW"]
    fields = ["serial", "exch"]
    exch_label = "Branch"
    has_serial = True
    exch_required = "always"
    branch_exchange = True
    exports = ["sheets", "summary"]
    rules_url = "https://nzart.org.nz/activities/contests/jwfd/"
    when = "Last full weekend of February: Saturday 1500-2400 and Sunday 0600-1500 NZDT"
    submit = "Log sheets for each band plus the summary sheet, posted to the Contest Manager within four weeks."
    points = {"PH": 3, "CW": 5}

    def serial_scope(self, band, settings):
        return band

    def _mult_ok(self, branch, settings):
        own = norm_branch(settings.get("branch", ""))
        return bool(branch) and branch != "00" and branch != own

    def _walk(self, qsos, settings):
        seen, mults, marks = set(), set(), {}
        points = 0
        lines = {}
        for q in sorted(qsos, key=lambda q: (q["ts"], q["id"])):
            slot = (base_call(q["call"]), q["band"], q["mode"], self.period_key(q["ts"]))
            mark = {"pts": 0, "mult": False, "dupe": False}
            valid = q["band"] in self.bands and q["mode"] in self.modes
            if slot in seen or not valid:
                mark["dupe"] = slot in seen
            else:
                seen.add(slot)
                mark["pts"] = self.points[q["mode"]]
                points += mark["pts"]
                br = norm_branch(q.get("exch"))
                mk = (br, q["band"], q["mode"])
                if self._mult_ok(br, settings) and mk not in mults:
                    mults.add(mk)
                    mark["mult"] = True
                ln = lines.setdefault((q["band"], q["mode"]), [0, 0, 0])
                ln[0] += 1
                ln[1] += mark["pts"]
                ln[2] += 1 if mark["mult"] else 0
            marks[q["id"]] = mark
        return seen, mults, marks, points, lines

    def check(self, qsos, call, band, mode, ts, settings):
        call = base_call(call)
        out = {"status": "ok", "msg": "", "exch": "", "name": ""}
        if not call:
            return out
        last = self.history(qsos, call)
        if last:
            out["exch"] = norm_branch(last.get("exch"))
            out["last"] = {"ts": last["ts"], "band": last["band"], "mode": last["mode"]}
            out["msg"] = "Worked before - branch %s" % (out["exch"] or "?")
        if band not in self.bands or mode not in self.modes:
            out["status"] = "invalid"
            out["msg"] = "Jock White is 80 m and 40 m, phone or CW only"
            return out
        pk = self.period_key(ts)
        if any(self.same_slot(q, call, band, mode, pk) for q in qsos):
            out["status"] = "dupe"
            out["msg"] = "Already worked this hour on %s %s" % (band, mode)
            return out
        if out["exch"]:
            _, mults, _, _, _ = self._walk(qsos, settings)
            if self._mult_ok(out["exch"], settings) and (out["exch"], band, mode) not in mults:
                out["status"] = "mult"
                out["msg"] = "New multiplier - branch %s on %s %s" % (out["exch"], band, mode)
        elif not last:
            out["status"] = "new"
            out["msg"] = "New station"
        return out

    def is_new_mult(self, qsos, branch, band, mode, settings):
        br = norm_branch(branch)
        _, mults, _, _, _ = self._walk(qsos, settings)
        return self._mult_ok(br, settings) and (br, band, mode) not in mults

    def score(self, qsos, settings):
        seen, mults, marks, points, lines = self._walk(qsos, settings)
        out_lines = []
        for band in self.bands:
            for mode in self.modes:
                n = lines.get((band, mode), [0, 0, 0])
                out_lines.append({"label": "%s %s" % (band, mode), "count": n[0],
                                  "points": n[1], "mults": n[2]})
        return {"total": points * len(mults), "points": points,
                "mults": len(mults), "count": len(seen),
                "lines": out_lines, "marks": marks}


class TransTasman(Contest):
    """WIA Trans-Tasman Low-Band Contest.

    RS(T) + serial. Three two-hour blocks. Work each station once per band
    and mode in each block. One point per contact, VK and ZL stations only.
    Multiplier: each VK/ZL prefix, once per band per block.
    Block score = contacts x prefixes; final score = sum of the blocks.
    """
    key = "tt"
    name = "Trans-Tasman Low-Band"
    period_seconds = 7200
    bands = ["160m", "80m", "40m"]
    modes = ["PH", "CW", "DIG"]
    fields = ["serial"]
    has_serial = True
    exports = ["cabrillo", "summary"]
    rules_url = "https://www.wia.org.au/members/contests/transtasman/"
    when = "Saturday of the third full weekend of July, 0800-1400 UTC"
    submit = "Cabrillo log uploaded at vklogchecker.com within a week."

    def serial_scope(self, band, settings):
        return band if settings.get("tt_serial_per_band") == "1" else ""

    def _walk(self, qsos):
        seen, marks, blocks = set(), {}, {}
        for q in sorted(qsos, key=lambda q: (q["ts"], q["id"])):
            pk = self.period_key(q["ts"])
            slot = (base_call(q["call"]), q["band"], q["mode"], pk)
            mark = {"pts": 0, "mult": False, "dupe": False}
            valid = (q["band"] in self.bands and q["mode"] in self.modes
                     and is_vk_zl(q["call"]))
            if slot in seen or not valid:
                mark["dupe"] = slot in seen
            else:
                seen.add(slot)
                mark["pts"] = 1
                blk = blocks.setdefault(pk, {"count": 0, "mults": set()})
                blk["count"] += 1
                mk = (prefix_of(q["call"]), q["band"])
                if mk not in blk["mults"]:
                    blk["mults"].add(mk)
                    mark["mult"] = True
            marks[q["id"]] = mark
        return seen, marks, blocks

    def check(self, qsos, call, band, mode, ts, settings):
        call_in = call
        call = base_call(call)
        out = {"status": "ok", "msg": "", "exch": "", "name": ""}
        if not call:
            return out
        last = self.history(qsos, call)
        if last:
            out["last"] = {"ts": last["ts"], "band": last["band"], "mode": last["mode"]}
            out["msg"] = "Worked before"
        if band not in self.bands:
            out["status"] = "invalid"
            out["msg"] = "Trans-Tasman is 160 m, 80 m and 40 m only"
            return out
        pfx = prefix_of(call_in)
        if len(call) >= 3 and not is_vk_zl(call_in):
            out["status"] = "invalid"
            out["msg"] = "Not a VK or ZL station - no points"
            return out
        pk = self.period_key(ts)
        if any(self.same_slot(q, call, band, mode, pk) for q in qsos):
            out["status"] = "dupe"
            out["msg"] = "Already worked this block on %s %s" % (band, mode)
            return out
        _, _, blocks = self._walk(qsos)
        have = blocks.get(pk, {"mults": set()})["mults"]
        if pfx and len(call) >= 4 and (pfx, band) not in have:
            out["status"] = "mult"
            out["msg"] = "New multiplier - %s on %s this block" % (pfx, band)
        elif not last:
            out["status"] = "new"
            out["msg"] = "New station"
        return out

    def score(self, qsos, settings):
        seen, marks, blocks = self._walk(qsos)
        lines, total, points, mults = [], 0, 0, 0
        for pk in sorted(blocks):
            b = blocks[pk]
            s = b["count"] * len(b["mults"])
            total += s
            points += b["count"]
            mults += len(b["mults"])
            lines.append({"label": "block", "start": pk * self.period_seconds,
                          "count": b["count"], "points": s, "mults": len(b["mults"])})
        return {"total": total, "points": points, "mults": mults,
                "count": len(seen), "lines": lines, "marks": marks}


class SangsterShield(Contest):
    """NZART Sangster Shield: 80 m CW, QRP. Six half-hour periods, each station
    once per period. NZ-NZ 5 points, NZ-overseas 10. Multiplier: each different
    NZART branch worked (not your own). Score = points x branches."""
    key = "sangster"
    name = "Sangster Shield"
    period_seconds = 1800
    bands = ["80m"]
    modes = ["CW"]
    fields = ["serial", "exch"]
    exch_label = "Branch"
    has_serial = True
    exch_required = "zl"
    branch_exchange = True
    exports = ["sheets", "summary", "cabrillo"]
    rules_url = "https://nzart.org.nz/activities/contests/sangster-shield/"
    when = "Third Saturday of May, 0800-1100 UTC (2000-2300 NZST)"
    submit = ("Log and summary sheet to sangster@nzart.org.nz (text, Cabrillo, Word or Excel) "
              "by the fourth Saturday after the contest.")

    def serial_scope(self, band, settings):
        return ""

    def _mult_ok(self, branch, settings):
        return bool(branch) and branch != norm_branch(settings.get("branch", ""))

    def _walk(self, qsos, settings):
        seen, mults, marks, points = set(), set(), {}, 0
        nz = dx = 0
        for q in sorted(qsos, key=lambda q: (q["ts"], q["id"])):
            slot = (base_call(q["call"]), self.period_key(q["ts"]))
            mark = {"pts": 0, "mult": False, "dupe": False}
            if slot in seen or q["band"] not in self.bands or q["mode"] != "CW":
                mark["dupe"] = slot in seen
            else:
                seen.add(slot)
                home = is_zl(q["call"])
                mark["pts"] = 5 if home else 10
                points += mark["pts"]
                nz, dx = nz + (1 if home else 0), dx + (0 if home else 1)
                br = norm_branch(q.get("exch"))
                if home and self._mult_ok(br, settings) and br not in mults:
                    mults.add(br)
                    mark["mult"] = True
            marks[q["id"]] = mark
        return seen, mults, marks, points, nz, dx

    def is_new_mult(self, qsos, branch, band, mode, settings):
        br = norm_branch(branch)
        return self._mult_ok(br, settings) and br not in self._walk(qsos, settings)[1]

    def check(self, qsos, call, band, mode, ts, settings):
        call = base_call(call)
        out = {"status": "ok", "msg": "", "exch": "", "name": ""}
        if not call:
            return out
        last = self.history(qsos, call)
        if last:
            out["exch"] = norm_branch(last.get("exch"))
            out["msg"] = "Worked before - branch %s" % (out["exch"] or "?")
        if band not in self.bands or mode != "CW":
            return dict(out, status="invalid", msg="The Sangster Shield is 80 m CW only")
        pk = self.period_key(ts)
        if any(base_call(q["call"]) == call and self.period_key(q["ts"]) == pk for q in qsos):
            return dict(out, status="dupe", msg="Already worked this half hour")
        prev = self.last_contact(qsos)
        if prev and base_call(prev["call"]) == call and ts - prev["ts"] < 300:
            wait = int((300 - (ts - prev["ts"])) // 60) + 1
            return dict(out, status="invalid",
                        msg="Twice running - work another station first, or wait %d min" % wait)
        if out["exch"] and self.is_new_mult(qsos, out["exch"], band, mode, settings):
            out.update(status="mult", msg="New multiplier - branch %s" % out["exch"])
        elif not last:
            out.update(status="new", msg="New station")
        return out

    def score(self, qsos, settings):
        seen, mults, marks, points, nz, dx = self._walk(qsos, settings)
        lines = [{"label": "NZ stations", "count": nz, "points": nz * 5, "mults": len(mults)},
                 {"label": "Overseas", "count": dx, "points": dx * 10, "mults": 0}]
        return {"total": points * len(mults), "points": points, "mults": len(mults),
                "count": len(seen), "lines": lines, "marks": marks}


class Memorial(Contest):
    """NZART Memorial Contest: 80 m, SSB and CW. Three one-hour periods; a
    station may be worked once on each mode per period, not consecutively.
    SSB 1 point, CW 2. Multiplier: each ZL/VK/Oceania prefix, per mode.
    Score = points x multipliers (SSB and CW multipliers added together)."""
    key = "memorial"
    name = "NZART Memorial Contest"
    period_seconds = 3600
    bands = ["80m"]
    modes = ["PH", "CW"]
    fields = ["serial"]
    has_serial = True
    exports = ["cabrillo", "summary"]
    rules_url = "https://nzart.org.nz/activities/contests/memorial-contest/"
    when = "First Saturday of July, 0800-1100 UTC (2000-2300 NZST)"
    submit = "Cabrillo log emailed to contests@nzart.org.nz, to arrive by 31 July."
    points = {"PH": 1, "CW": 2}

    def serial_scope(self, band, settings):
        return ""

    def _walk(self, qsos):
        seen, mults, marks, lines = set(), set(), {}, {}
        for q in sorted(qsos, key=lambda q: (q["ts"], q["id"])):
            slot = (base_call(q["call"]), q["mode"], self.period_key(q["ts"]))
            mark = {"pts": 0, "mult": False, "dupe": False}
            if slot in seen or q["band"] not in self.bands or q["mode"] not in self.modes:
                mark["dupe"] = slot in seen
            else:
                seen.add(slot)
                mark["pts"] = self.points[q["mode"]]
                mk = (prefix_of(q["call"]), q["mode"])
                if is_oceania(q["call"]) and mk not in mults:
                    mults.add(mk)
                    mark["mult"] = True
                ln = lines.setdefault(q["mode"], [0, 0, 0])
                ln[0] += 1
                ln[1] += mark["pts"]
                ln[2] += 1 if mark["mult"] else 0
            marks[q["id"]] = mark
        return seen, mults, marks, lines

    def check(self, qsos, call, band, mode, ts, settings):
        full = call
        call = base_call(call)
        out = {"status": "ok", "msg": "", "exch": "", "name": ""}
        if not call:
            return out
        last = self.history(qsos, call)
        if last:
            out["msg"] = "Worked before"
        if band not in self.bands or mode not in self.modes:
            return dict(out, status="invalid", msg="The Memorial Contest is 80 m, SSB or CW")
        pk = self.period_key(ts)
        if any(self.same_slot(q, call, band, mode, pk) for q in qsos):
            return dict(out, status="dupe", msg="Already worked this hour on %s" % mode)
        prev = self.last_contact(qsos)
        if prev and base_call(prev["call"]) == call and self.period_key(prev["ts"]) == pk:
            return dict(out, status="invalid",
                        msg="Not straight after their %s contact - work another station first" % prev["mode"])
        mults = self._walk(qsos)[1]
        if len(call) >= 4 and is_oceania(full) and (prefix_of(full), mode) not in mults:
            out.update(status="mult", msg="New multiplier - %s on %s" % (prefix_of(full), mode))
        elif not last:
            out.update(status="new", msg="New station")
        return out

    def score(self, qsos, settings):
        seen, mults, marks, lines = self._walk(qsos)
        points = sum(v[1] for v in lines.values())
        out = [{"label": m, "count": lines.get(m, [0, 0, 0])[0], "points": lines.get(m, [0, 0, 0])[1],
                "mults": lines.get(m, [0, 0, 0])[2]} for m in self.modes]
        return {"total": points * len(mults), "points": points, "mults": len(mults),
                "count": len(seen), "lines": out, "marks": marks}


class Sprint(Contest):
    """ZL Autumn and Winter Sprints: 80 m, half an hour each of CW, SSB and
    FT4. Exchange is RS(T) and the outside temperature (grid square on FT4).
    Each station once per mode. One point per contact, no multipliers."""
    key = "sprint"
    name = "ZL Sprint (Autumn / Winter)"
    bands = ["80m"]
    modes = ["CW", "PH", "DIG"]
    fields = ["exch"]
    exch_label = "Temp / grid"
    exch_required = "always"
    exports = ["cabrillo", "summary"]
    rules_url = "https://nzart.org.nz/activities/contests/sprints/"
    when = "Tuesdays in April and August: CW 0800, SSB 0830, FT4 0900 UTC, half an hour each"
    submit = "Log emailed to the organiser within 24 hours, with your claimed score and branch number."

    def _walk(self, qsos):
        seen, marks, lines = set(), {}, {}
        for q in sorted(qsos, key=lambda q: (q["ts"], q["id"])):
            slot = (base_call(q["call"]), q["mode"])
            mark = {"pts": 0, "mult": False, "dupe": False}
            if slot in seen or q["band"] not in self.bands:
                mark["dupe"] = slot in seen
            else:
                seen.add(slot)
                mark["pts"] = 1
                lines[q["mode"]] = lines.get(q["mode"], 0) + 1
            marks[q["id"]] = mark
        return seen, marks, lines

    def check(self, qsos, call, band, mode, ts, settings):
        call = base_call(call)
        out = {"status": "ok", "msg": "", "exch": "", "name": ""}
        if not call:
            return out
        if band not in self.bands:
            return dict(out, status="invalid", msg="The Sprints are on 80 m")
        if any(base_call(q["call"]) == call and q["mode"] == mode for q in qsos):
            return dict(out, status="dupe", msg="Already worked on %s tonight" % mode)
        if any(base_call(q["call"]) == call for q in qsos):
            out["msg"] = "Worked on another mode tonight"
        else:
            out.update(status="new", msg="New station")
        return out

    def score(self, qsos, settings):
        seen, marks, lines = self._walk(qsos)
        names = {"CW": "CW", "PH": "SSB", "DIG": "FT4"}
        out = [{"label": names[m], "count": lines.get(m, 0), "points": lines.get(m, 0), "mults": 0}
               for m in self.modes]
        return {"total": len(seen), "points": len(seen), "mults": 0, "count": len(seen),
                "lines": out, "marks": marks}


CONTESTS = {c.key: c() for c in (JockWhite, SangsterShield, Memorial, Sprint, TransTasman, Contest)}

# The NZART contest calendar, with where to find the rules. "key" is set for
# contests QSODesk scores; the rest can be logged as General logging.
_N = "https://nzart.org.nz/activities/contests/"
CATALOGUE = [
    {"name": "NZART Portable Activity Day", "key": "", "when": "1 January (an activity day, not a contest)",
     "url": _N + "nzart-portable-activity-day/"},
    {"name": "NZART Jock White Memorial Field Day", "key": "jwfd", "url": _N + "jwfd/"},
    {"name": "Commonwealth Contest", "key": "", "when": "March (14-15 March in 2026)",
     "url": _N + "commonwealth-contest/"},
    {"name": "NZART Autumn and Winter Sprints", "key": "sprint", "url": _N + "sprints/"},
    {"name": "NZART Sangster Shield", "key": "sangster", "url": _N + "sangster-shield/"},
    {"name": "NZART Memorial Contest", "key": "memorial", "url": _N + "memorial-contest/"},
    {"name": "Trans-Tasman Low-Band Contest (WIA)", "key": "tt",
     "url": "https://www.wia.org.au/members/contests/transtasman/"},
    {"name": "VHF/UHF Contests", "key": "", "when": "Several through the year; see the calendar",
     "url": _N + "vhf/"},
    {"name": "H-Night Contest", "key": "", "when": "December (8 December in 2026)",
     "url": _N + "h-night-contest/"},
    {"name": "Straight Key Night", "key": "", "when": "Winter and summer (14 June and 6 December in 2026)",
     "url": "https://zl1.nz/new-zealand-straight-key-night/"},
]
CALENDAR_URL = _N + "nzart-contest-calendar/"


def catalogue():
    out = []
    for c in CATALOGUE:
        row = dict(c)
        if c["key"]:
            row["when"] = CONTESTS[c["key"]].when
        out.append(row)
    return out


def get(key):
    return CONTESTS.get(key) or CONTESTS["general"]
