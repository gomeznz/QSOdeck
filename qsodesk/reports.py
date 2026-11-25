"""Reports for QSODesk: one for any contest event, and one for personal
(non-contest) contacts. Everything is worked out from the log on request."""
import collections
import time

from . import contests

MODE_NAMES = {"PH": "Phone", "CW": "CW", "DIG": "Digital"}


def _count(items):
    c = collections.Counter(i for i in items if i)
    return [{"label": k, "count": v} for k, v in c.most_common()]


def _band_order(rows):
    order = {b: i for i, b in enumerate(contests.ALL_BANDS)}
    return sorted(rows, key=lambda r: order.get(r["label"], 99))


def _basics(qsos):
    calls = [contests.base_call(q["call"]) for q in qsos]
    matrix = collections.Counter((q["band"], q["mode"]) for q in qsos)
    return {
        "count": len(qsos), "unique_calls": len(set(calls)),
        "first": min((q["ts"] for q in qsos), default=None),
        "last": max((q["ts"] for q in qsos), default=None),
        "bands": _band_order(_count(q["band"] for q in qsos)),
        "modes": [{"label": MODE_NAMES.get(r["label"], r["label"]), "count": r["count"]}
                  for r in _count(q["mode"] for q in qsos)],
        "matrix": [{"band": b, "mode": m, "count": n} for (b, m), n in sorted(matrix.items())],
        "operators": _count(q.get("operator") for q in qsos),
        "stations": _count(q.get("station") for q in qsos),
    }


def _best_hour(qsos):
    """Most contacts in any 60 minutes."""
    ts = sorted(q["ts"] for q in qsos)
    best, j = 0, 0
    for i in range(len(ts)):
        while ts[i] - ts[j] > 3600:
            j += 1
        best = max(best, i - j + 1)
    return best


def contest_report(ev, c, qsos, settings):
    sc = c.score(qsos, settings)
    marks = sc.pop("marks")
    out = _basics(qsos)
    step = c.period_seconds or 3600
    buckets = collections.Counter(int(q["ts"] // step) for q in qsos)
    periods = []
    if buckets:
        for k in range(min(buckets), max(buckets) + 1):
            if buckets.get(k) or max(buckets) - min(buckets) < 60:
                periods.append({"start": k * step, "count": buckets.get(k, 0)})
    mults = []
    for q in qsos:
        if marks.get(q["id"], {}).get("mult"):
            what = q.get("exch") if c.branch_exchange else contests.prefix_of(q["call"])
            mults.append({"ts": q["ts"], "call": q["call"], "band": q["band"], "mode": q["mode"],
                          "what": ("Branch %s" % what) if c.branch_exchange else what})
    dupes = sum(1 for q in qsos if marks.get(q["id"], {}).get("dupe"))
    out.update({
        "kind": "contest", "title": ev["name"], "contest": c.describe(), "score": sc,
        "periods": periods, "period_seconds": step, "mults": mults, "dupes": dupes,
        "no_points": sum(1 for q in qsos if not marks.get(q["id"], {}).get("pts")) - dupes,
        "best_hour": _best_hour(qsos),
        "station": {"callsign": settings.get("callsign", ""), "branch": settings.get("branch", ""),
                    "operator": settings.get("operator", "")},
    })
    return out


def personal_report(qsos, title, year=None):
    years = sorted(set(time.localtime(q["ts"]).tm_year for q in qsos), reverse=True)
    if year:
        qsos = [q for q in qsos if time.localtime(q["ts"]).tm_year == year]
    out = _basics(qsos)
    months = collections.Counter(time.strftime("%Y-%m", time.localtime(q["ts"])) for q in qsos)
    calls = collections.Counter(contests.base_call(q["call"]) for q in qsos)
    last = {}
    for q in qsos:
        last[contests.base_call(q["call"])] = max(last.get(contests.base_call(q["call"]), 0), q["ts"])
    prefixes = collections.Counter(contests.prefix_of(q["call"]) for q in qsos)
    areas = collections.Counter(contests.prefix_of(q["call"]) for q in qsos if contests.is_vk_zl(q["call"]))
    recent = sorted(qsos, key=lambda q: -q["ts"])[:25]
    out.update({
        "kind": "personal", "title": title, "years": years, "year": year,
        "months": [{"label": k, "count": months[k]} for k in sorted(months)][-36:],
        "top_calls": [{"label": k, "count": v, "last": last[k]} for k, v in calls.most_common(15)],
        "prefixes": [{"label": k, "count": v} for k, v in prefixes.most_common(40) if k],
        "prefix_total": len([p for p in prefixes if p]),
        "areas": sorted(({"label": k, "count": v} for k, v in areas.items()), key=lambda r: r["label"]),
        "events": _count(q.get("event") for q in qsos),
        "recent": [{"ts": q["ts"], "call": q["call"], "band": q["band"], "mode": q["mode"],
                    "rst_sent": q["rst_sent"], "rst_rcvd": q["rst_rcvd"], "name": q.get("name") or "",
                    "notes": q.get("notes") or "", "event": q.get("event")} for q in recent],
    })
    return out


def callsign_history(qsos, call):
    call = contests.base_call(call)
    rows = [q for q in qsos if contests.base_call(q["call"]) == call]
    rows.sort(key=lambda q: -q["ts"])
    return [{"ts": q["ts"], "call": q["call"], "band": q["band"], "mode": q["mode"],
             "rst_sent": q["rst_sent"], "rst_rcvd": q["rst_rcvd"], "exch": q.get("exch") or "",
             "serial_rcvd": q.get("serial_rcvd") or "", "name": q.get("name") or "",
             "notes": q.get("notes") or "", "event": q.get("event")} for q in rows[:200]]
