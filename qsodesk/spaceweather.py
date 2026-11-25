"""Space weather for the dashboard, from NOAA's Space Weather Prediction Center
(public-domain data, https://services.swpc.noaa.gov).

The Pi fetches the feeds itself, at most every 10 minutes and only while
someone has a dashboard open, and keeps the last good copy on disk so the
cards still show something (marked as old) when there is no internet.

NOTE: written from the documented feed layouts but not yet run against the
live feeds - the readers below accept more than one layout for that reason.
"""
import calendar
import json
import os
import re
import threading
import time
import urllib.request

BASE = "https://services.swpc.noaa.gov"
FEEDS = {
    "scales": "/products/noaa-scales.json",
    "kp": "/products/noaa-planetary-k-index.json",
    "flux": "/products/summary/10cm-flux.json",
    "flux2": "/json/f107_cm_flux.json",
    "wind": "/products/summary/solar-wind-speed.json",
    "mag": "/products/summary/solar-wind-mag-field.json",
    "alerts": "/products/alerts.json",
}
MAX_AGE = 600
KP_WORDS = ["Quiet", "Quiet", "Quiet", "Unsettled", "Active", "Minor storm (G1)",
            "Moderate storm (G2)", "Strong storm (G3)", "Severe storm (G4)", "Extreme storm (G5)"]
_HEAD = re.compile(r"^(EXTENDED WARNING|CONTINUED ALERT|CANCEL WARNING|CANCEL WATCH|WARNING|ALERT|WATCH|SUMMARY):\s*(.+)$")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pick(d, *names):
    """Value of the first key in `d` matching any name, ignoring case."""
    low = {str(k).lower(): v for k, v in d.items()} if isinstance(d, dict) else {}
    for n in names:
        if n.lower() in low:
            return low[n.lower()]
    return None


def _rows(data):
    """NOAA tables come either as a list of dicts or as a header row + rows."""
    if isinstance(data, dict):
        return [data]
    if not isinstance(data, list) or not data:
        return []
    if isinstance(data[0], dict):
        return data
    if isinstance(data[0], list):
        head = [str(h) for h in data[0]]
        return [dict(zip(head, r)) for r in data[1:] if isinstance(r, list)]
    return []


def _latest(data):
    """The newest record of a feed, whatever shape it arrives in."""
    rows = _rows(data)
    if not rows:
        return {}
    stamped = [r for r in rows if _pick(r, "time_tag", "TimeStamp")]
    if len(stamped) == len(rows) and len(rows) > 1:
        return max(rows, key=lambda r: str(_pick(r, "time_tag", "TimeStamp")))
    return rows[-1]


def find_num(data, words, lo=None, hi=None):
    """First sensible number stored under a key containing one of `words`,
    looking in the newest record first and then inside nested objects."""
    seen = [_latest(data)] if not isinstance(data, dict) else [data]
    for _ in range(4):
        nxt = []
        for d in seen:
            if not isinstance(d, dict):
                continue
            for k, v in d.items():
                if any(w in str(k).lower() for w in words) and not isinstance(v, (dict, list)):
                    n = _num(v)
                    if n is not None and (lo is None or n >= lo) and (hi is None or n <= hi):
                        return n
            nxt += [v if isinstance(v, dict) else _latest(v) for v in d.values()
                    if isinstance(v, (dict, list))]
        seen = nxt
    return None


def shape(data, depth=0):
    """A short description of a feed's layout, for troubleshooting."""
    if isinstance(data, dict):
        return {str(k): shape(v, depth + 1) for k, v in list(data.items())[:12]} if depth < 3 else "{...}"
    if isinstance(data, list):
        return ["%d items" % len(data)] + [shape(x, depth + 1) for x in data[:2] + data[-1:]] if depth < 3 else "[...]"
    return data if not isinstance(data, str) else data[:60]


def parse_kp(data):
    out = []
    for r in _rows(data):
        kp = _num(_pick(r, "Kp", "kp_index", "estimated_kp"))
        if kp is not None:
            out.append({"time": str(_pick(r, "time_tag", "TimeStamp") or ""), "kp": kp,
                        "a": _num(_pick(r, "a_running", "a_index"))})
    return out[-16:]


def parse_scales(data):
    def one(day):
        if not isinstance(day, dict):
            return None
        r, s, g = (_pick(day, k) or {} for k in ("R", "S", "G"))
        return {"date": str(_pick(day, "DateStamp") or ""),
                "R": int(_num(_pick(r, "Scale")) or 0), "S": int(_num(_pick(s, "Scale")) or 0),
                "G": int(_num(_pick(g, "Scale")) or 0),
                "R_minor": _num(_pick(r, "MinorProb")), "R_major": _num(_pick(r, "MajorProb")),
                "S_prob": _num(_pick(s, "Prob")), "G_text": str(_pick(g, "Text") or "")}
    if not isinstance(data, dict):
        return None
    return {"now": one(data.get("0")), "forecast": [x for x in (one(data.get(k)) for k in ("1", "2", "3")) if x]}


def parse_alerts(data, now=None, hours=72):
    """Recent geomagnetic warnings, watches and alerts, newest first."""
    now = now or time.time()
    out = []
    for a in data if isinstance(data, list) else []:
        msg = str(_pick(a, "message") or "")
        if not re.search(r"geomagnetic|k-index", msg, re.I):
            continue
        issued = str(_pick(a, "issue_datetime") or "")
        try:
            ts = calendar.timegm(time.strptime(issued[:19], "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            ts = None
        if ts and now - ts > hours * 3600:
            continue
        kind, text = "NOTICE", ""
        for line in msg.replace("\r", "").split("\n"):
            m = _HEAD.match(line.strip())
            if m:
                kind, text = m.group(1), m.group(2).strip()
                break
        if not text or kind.startswith("CANCEL") or kind == "SUMMARY":
            continue
        out.append({"time": ts, "issued": issued[:16], "kind": kind, "text": text,
                    "id": str(_pick(a, "product_id") or "")})
    out.sort(key=lambda a: -(a["time"] or 0))
    return out[:6]


def band_guide(sfi, kp):
    """A rough HF guide from solar flux and Kp. Not a path prediction."""
    if sfi is None:
        return []
    steps = ["Poor", "Fair", "Good"]

    def lvl(good, fair):
        return 2 if sfi >= good else 1 if sfi >= fair else 0
    rows = [("80 m - 40 m", 1 if sfi < 100 else 0, 2),
            ("30 m - 20 m", lvl(90, 0), lvl(120, 90)),
            ("17 m - 15 m", lvl(120, 90), 1 if sfi >= 150 else 0),
            ("12 m - 10 m", lvl(150, 110), 0)]
    drop = 0 if kp is None or kp < 5 else 1 if kp < 7 else 2
    return [{"band": b, "day": steps[max(0, d - drop)], "night": steps[max(0, n - drop)]}
            for b, d, n in rows]


def build(raw, now=None):
    kp = parse_kp(raw.get("kp"))
    latest = kp[-1] if kp else None
    f2 = _rows(raw.get("flux2"))
    f2 = [r for r in f2 if _num(_pick(r, "frequency")) in (None, 2800.0)]     # the 10.7 cm reading
    sfi = (find_num(raw.get("flux"), ("flux", "f10"), 40, 500)
           or find_num(f2, ("flux",), 40, 500))
    scales = parse_scales(raw.get("scales")) or {"now": None, "forecast": []}
    k = latest["kp"] if latest else None
    return {
        "sfi": sfi, "kp": k, "kp_time": latest["time"] if latest else "",
        "a": latest["a"] if latest else None,
        "kp_word": KP_WORDS[max(0, min(9, int(round(k))))] if k is not None else "",
        "kp_history": [x["kp"] for x in kp[-8:]],
        "wind": find_num(raw.get("wind"), ("speed",), 100, 3000),
        "bt": find_num(raw.get("mag"), ("bt",), 0, 200),
        "bz": find_num(raw.get("mag"), ("bz",), -200, 200),
        "scales": scales, "alerts": parse_alerts(raw.get("alerts"), now),
        "bands": band_guide(sfi, k),
    }


class SpaceWeather:
    def __init__(self, data_dir):
        self.path = os.path.join(data_dir, "spaceweather-cache.json")
        self.lock = threading.Lock()
        self.busy = False
        self.cache = {"fetched": 0, "data": None, "error": ""}
        self.shapes = {}                    # layout of each feed as last fetched
        try:
            with open(self.path) as fh:
                self.cache.update(json.load(fh))
        except (OSError, ValueError):
            pass

    def _fetch(self):
        raw, errors = {}, []
        for key, path in FEEDS.items():
            try:
                req = urllib.request.Request(BASE + path, headers={"User-Agent": "QSODesk"})
                with urllib.request.urlopen(req, timeout=8) as r:
                    raw[key] = json.loads(r.read().decode("utf-8", "replace"))
            except Exception as exc:
                errors.append("%s: %s" % (key, exc))
        self.shapes = {k: shape(v) for k, v in raw.items()}
        self.shapes["errors"] = errors
        with self.lock:
            if raw.get("kp") or raw.get("flux") or raw.get("scales"):
                self.cache = {"fetched": time.time(), "data": build(raw), "error": "",
                              "tried": time.time()}
                try:
                    with open(self.path, "w") as fh:
                        json.dump(self.cache, fh)
                except OSError:
                    pass
            else:
                self.cache["error"] = "Could not reach NOAA (no internet?)"
                self.cache["tried"] = time.time()
            self.busy = False

    def get(self):
        """Return what we have straight away; refresh in the background if old."""
        with self.lock:
            age = time.time() - self.cache.get("fetched", 0)
            tried = time.time() - self.cache.get("tried", 0)
            incomplete = bool(self.cache.get("data")) and self.cache["data"].get("sfi") is None
            if (age > MAX_AGE or incomplete) and tried > 60 and not self.busy:
                self.busy = True
                threading.Thread(target=self._fetch, daemon=True).start()
            out = dict(self.cache)
        out["age"] = age if out.get("fetched") else None
        out["stale"] = age > MAX_AGE * 3
        out["loading"] = self.busy and not out.get("data")
        return out
