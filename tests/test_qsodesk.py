"""Run with:  python3 -m unittest discover tests"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from qsodesk import contests            # noqa: E402
from qsodesk.store import Store         # noqa: E402

H = 3600
T0 = 1_800_000_000 - (1_800_000_000 % 7200)      # start of an hour and a 2 h block


def q(i, call, band, mode, ts, exch="", serial=None):
    return {"id": i, "call": call, "band": band, "mode": mode, "ts": ts, "exch": exch,
            "serial_sent": serial}


class Calls(unittest.TestCase):
    def test_prefix(self):
        self.assertEqual(contests.prefix_of("ZL4AB"), "ZL4")
        self.assertEqual(contests.prefix_of("VK2XYZ/4"), "VK4")
        self.assertEqual(contests.prefix_of("ZL4AB/P"), "ZL4")
        self.assertEqual(contests.prefix_of("ZM90DX"), "ZM90")
        self.assertEqual(contests.base_call("ZL4AB/P"), "ZL4AB")
        self.assertTrue(contests.is_vk_zl("VK3ABC"))
        self.assertFalse(contests.is_vk_zl("W1AW"))


class JockWhite(unittest.TestCase):
    c = contests.get("jwfd")
    s = {"branch": "27"}

    def test_dupe_resets_each_hour(self):
        log = [q(1, "ZL2AB", "80m", "PH", T0 + 60, "12")]
        chk = lambda band, mode, ts: self.c.check(log, "ZL2AB", band, mode, ts, self.s)["status"]
        self.assertEqual(chk("80m", "PH", T0 + 120), "dupe")
        self.assertNotEqual(chk("80m", "CW", T0 + 120), "dupe")     # other mode
        self.assertNotEqual(chk("40m", "PH", T0 + 120), "dupe")     # other band
        self.assertNotEqual(chk("80m", "PH", T0 + H + 5), "dupe")   # next hour
        self.assertEqual(chk("20m", "PH", T0 + 120), "invalid")

    def test_branch_remembered_and_new_mult(self):
        log = [q(1, "ZL2AB", "80m", "PH", T0 + 60, "12")]
        r = self.c.check(log, "ZL2AB", "40m", "PH", T0 + 120, self.s)
        self.assertEqual(r["exch"], "12")
        self.assertEqual(r["status"], "mult")                        # branch 12 new on 40 m

    def test_score(self):
        log = [
            q(1, "ZL2AB", "80m", "PH", T0 + 60, "12"),       # 3 pts, mult
            q(2, "ZL2CD", "80m", "PH", T0 + 90, "12"),       # 3 pts, same branch: no mult
            q(3, "ZL2AB", "80m", "CW", T0 + 120, "12"),      # 5 pts, mult (new mode)
            q(4, "ZL2AB", "80m", "PH", T0 + 150, "12"),      # dupe: 0
            q(5, "ZL2AB", "80m", "PH", T0 + H + 10, "12"),   # next hour: 3 pts
            q(6, "ZL4HS", "40m", "PH", T0 + 200, "00"),      # home station: 3 pts, no mult
            q(7, "ZL4XY", "40m", "PH", T0 + 220, "27"),      # own branch: 3 pts, no mult
        ]
        sc = self.c.score(log, self.s)
        self.assertEqual(sc["points"], 3 + 3 + 5 + 3 + 3 + 3)
        self.assertEqual(sc["mults"], 2)
        self.assertEqual(sc["total"], 20 * 2)
        self.assertTrue(sc["marks"][4]["dupe"])

    def test_available_again(self):
        log = [q(1, "ZL2AB", "80m", "PH", T0 + 60, "12"), q(2, "ZL2CD", "80m", "PH", T0 + 90, "12"),
               q(3, "ZL2CD", "80m", "PH", T0 + H + 5, "12")]
        av = self.c.available(log, "80m", "PH", T0 + H + 30)
        self.assertEqual([a["call"] for a in av], ["ZL2AB"])


class TransTasman(unittest.TestCase):
    c = contests.get("tt")

    def test_blocks_and_score(self):
        log = [
            q(1, "VK2AB", "80m", "PH", T0 + 60),             # block 1: VK2/80
            q(2, "VK2CD", "80m", "PH", T0 + 90),             # same prefix
            q(3, "ZL1AA", "80m", "PH", T0 + 100),            # ZL1/80
            q(4, "VK2AB", "40m", "PH", T0 + 110),            # VK2/40
            q(5, "W1AW", "40m", "PH", T0 + 120),             # not VK/ZL
            q(6, "VK2AB", "80m", "PH", T0 + 7200 + 5),       # block 2
        ]
        sc = self.c.score(log, {})
        self.assertEqual(sc["total"], 4 * 3 + 1 * 1)
        self.assertEqual(self.c.check(log, "VK2AB", "80m", "PH", T0 + H, {})["status"], "dupe")
        self.assertNotEqual(self.c.check(log, "VK2AB", "80m", "PH", T0 + 7300, {})["status"], "ok")
        self.assertEqual(self.c.check(log, "W1AW", "80m", "PH", T0 + H, {})["status"], "invalid")

    def test_serial_scope(self):
        self.assertEqual(self.c.serial_scope("80m", {}), "")
        self.assertEqual(self.c.serial_scope("80m", {"tt_serial_per_band": "1"}), "80m")


class MoreContests(unittest.TestCase):
    def test_sangster(self):
        c, s = contests.get("sangster"), {"branch": "27"}
        log = [q(1, "ZL2AB", "80m", "CW", T0 + 60, "12"),          # 5 pts, branch 12
               q(2, "ZL2CD", "80m", "CW", T0 + 90, "12"),          # 5 pts
               q(3, "VK3XX", "80m", "CW", T0 + 120, ""),           # overseas: 10 pts
               q(4, "ZL4ME", "80m", "CW", T0 + 150, "27"),         # own branch: points only
               q(5, "ZL2AB", "80m", "CW", T0 + 200, "12"),         # dupe this half hour
               q(6, "ZL2AB", "80m", "CW", T0 + 1800 + 10, "12")]   # next half hour
        sc = c.score(log, s)
        self.assertEqual((sc["points"], sc["mults"], sc["total"]), (30, 1, 30))
        self.assertEqual(c.check(log, "ZL2AB", "80m", "CW", T0 + 1800 + 60, s)["status"], "dupe")
        self.assertEqual(c.check(log, "ZL2AB", "80m", "PH", T0 + 3700, s)["status"], "invalid")
        two = [q(1, "ZL2AB", "80m", "CW", T0 + 1790, "12")]          # end of one period...
        self.assertEqual(c.check(two, "ZL2AB", "80m", "CW", T0 + 1810, s)["status"], "invalid")
        self.assertNotEqual(c.check(two, "ZL2AB", "80m", "CW", T0 + 1790 + 301, s)["status"], "invalid")
        self.assertEqual(c.check(log, "ZL3NEW", "80m", "CW", T0 + 4000, s)["status"], "new")

    def test_memorial(self):
        c = contests.get("memorial")
        log = [q(1, "ZL2AB", "80m", "PH", T0 + 60), q(2, "ZL2CD", "80m", "PH", T0 + 90),
               q(3, "ZL2AB", "80m", "CW", T0 + 120), q(4, "VK3XX", "80m", "CW", T0 + 150),
               q(5, "W1AW", "80m", "CW", T0 + 180)]
        sc = c.score(log, {})
        self.assertEqual(sc["points"], 1 + 1 + 2 + 2 + 2)
        self.assertEqual(sc["mults"], 3)                           # ZL2 on PH, ZL2 on CW, VK3 on CW
        self.assertEqual(sc["total"], 8 * 3)
        self.assertEqual(c.check(log, "ZL2AB", "80m", "PH", T0 + 200, {})["status"], "dupe")
        self.assertEqual(c.check(log[:1], "ZL2AB", "80m", "CW", T0 + 100, {})["status"], "invalid")  # consecutive

    def test_sprint(self):
        c = contests.get("sprint")
        log = [q(1, "ZL2AB", "80m", "CW", T0 + 60, "12"), q(2, "ZL2AB", "80m", "PH", T0 + 2000, "12")]
        self.assertEqual(c.score(log, {})["total"], 2)
        self.assertEqual(c.check(log, "ZL2AB", "80m", "CW", T0 + 70, {})["status"], "dupe")
        self.assertEqual(c.check(log, "ZL2AB", "80m", "DIG", T0 + 4000, {})["status"], "ok")
        self.assertIsNone(c.period(T0))

    def test_reports(self):
        from qsodesk import reports
        c = contests.get("jwfd")
        log = [dict(q(i, "ZL2A%s" % chr(65 + i % 5), "80m" if i % 2 else "40m", "PH", T0 + i * 400, "%02d" % (i % 4 + 1)),
                    operator="ZL4XX", station="S1", rst_sent="59", rst_rcvd="59", name="", notes="",
                    serial_rcvd="001", event="E") for i in range(12)]
        r = reports.contest_report({"name": "E", "contest": "jwfd"}, c, log, {"branch": "27"})
        self.assertEqual(r["count"], 12)
        self.assertEqual(r["score"]["total"], c.score(log, {"branch": "27"})["total"])
        self.assertEqual(sum(p["count"] for p in r["periods"]), 12)
        self.assertEqual(len(r["mults"]), r["score"]["mults"])
        self.assertEqual(r["best_hour"], 10)
        p = reports.personal_report(log, "All")
        self.assertEqual((p["count"], p["unique_calls"], p["prefix_total"]), (12, 5, 1))
        self.assertEqual(p["top_calls"][0]["count"], 3)
        self.assertEqual(len(reports.callsign_history(log, "zl2aa/p")), 3)
        self.assertEqual(len(contests.catalogue()), 10)


class MDNSPackets(unittest.TestCase):
    def test_packets(self):
        import struct
        from qsodesk import mdns
        self.assertEqual(mdns.clean_name("QSO Desk 80m!"), "qso-desk-80m")
        self.assertEqual(mdns.clean_name(""), "qsodesk")
        q = struct.pack("!HHHHHH", 0, 0, 2, 0, 0, 0) + mdns.encode_name("QSOdesk.local") + \
            struct.pack("!HH", 1, 1) + b"\x05other\xc0\x14" + struct.pack("!HH", 28, 1)
        self.assertEqual(mdns.questions(q), [("qsodesk.local", 1), ("other.local", 28)])
        a = mdns.answer("qsodesk.local", "192.168.1.50", 7)
        self.assertEqual(mdns.questions(a), [])                       # a response is not a question
        self.assertEqual(struct.unpack("!HHHH", a[:8]), (7, 0x8400, 0, 1))
        self.assertTrue(a.endswith(bytes([192, 168, 1, 50])))
        name, pos = mdns.read_name(a, 12)
        self.assertEqual(name, "qsodesk.local")
        self.assertEqual(mdns.questions(b"\x00\x01"), [])             # junk is ignored


class FT8Messages(unittest.TestCase):
    def test_parse(self):
        from qsodesk import ft8
        p = ft8.parse_msg("CQ DX ZL2AB RF70")
        self.assertEqual((p["cq"], p["de"], p["grid"]), (True, "ZL2AB", "RF70"))
        p = ft8.parse_msg("ZL4XX VK2AB R-05")
        self.assertEqual((p["to"], p["de"], p["report"], p["r"]), ("ZL4XX", "VK2AB", "-05", True))
        self.assertEqual(ft8.parse_msg("ZL4XX VK2AB RR73")["end"], "RR73")
        self.assertEqual(ft8.parse_msg("ZL4XX VK2AB QF56")["grid"], "QF56")
        d = ft8.parse_decodes("000000 -07 +0.5 1234 ~  CQ K1ABC FN42\nnoise line\n")
        self.assertEqual(d, [{"snr": -7, "dt": 0.5, "freq": 1234, "msg": "CQ K1ABC FN42"}])
        self.assertEqual(ft8.fmt_report(-7.4), "-07")
        self.assertEqual(ft8.fmt_report(3), "+03")

    def test_cw_audio(self):
        from qsodesk import audio
        s = audio.cw_samples("E T", 20, 600)                  # dot, 7-dot gap, dash
        dot = int(audio.RATE * 1.2 / 20)
        self.assertEqual(len(s), dot * (1 + 1 + 2 + 4) + dot * (3 + 1 + 2 + 4))


class SpaceWx(unittest.TestCase):
    RAW = {
        "kp": [["time_tag", "Kp", "a_running", "station_count"],
               ["2026-11-24 03:00:00.000", "2.33", "9", "8"], ["2026-11-24 06:00:00.000", "5.00", "48", "8"]],
        "flux": {"Flux": "142", "TimeStamp": "2026-11-23 20:00:00"},
        "wind": {"WindSpeed": "510", "TimeStamp": "x"}, "mag": {"Bt": "9", "Bz": "-6", "TimeStamp": "x"},
        "scales": {"0": {"DateStamp": "2026-11-24", "R": {"Scale": "1", "Text": "minor"},
                         "S": {"Scale": "0", "Text": "none"}, "G": {"Scale": "1", "Text": "minor"}},
                   "1": {"DateStamp": "2026-11-25", "R": {"Scale": None, "MinorProb": "40", "MajorProb": "10"},
                         "S": {"Scale": None, "Prob": "5"}, "G": {"Scale": "2", "Text": "moderate"}}},
        "alerts": [
            {"product_id": "K05W", "issue_datetime": "2026-11-24 05:12:33.000",
             "message": "Space Weather Message Code: WARK05\r\nSerial Number: 1\r\n\r\n"
                        "WARNING: Geomagnetic K-index of 5 expected\r\nValid From: ..."},
            {"product_id": "XM5A", "issue_datetime": "2026-11-24 04:00:00.000",
             "message": "ALERT: X-Ray Flux exceeded M5\r\n"},
            {"product_id": "K04W", "issue_datetime": "2026-10-01 05:00:00.000",
             "message": "WARNING: Geomagnetic K-index of 4 expected\r\n"}],
    }

    def test_build(self):
        import calendar
        from qsodesk import spaceweather as sw
        now = calendar.timegm((2026, 11, 24, 8, 0, 0))
        d = sw.build(self.RAW, now)
        self.assertEqual((d["sfi"], d["kp"], d["a"], d["wind"], d["bz"]), (142, 5, 48, 510, -6))
        self.assertEqual(d["kp_word"], "Minor storm (G1)")
        self.assertEqual(d["scales"]["now"]["G"], 1)
        self.assertEqual(d["scales"]["forecast"][0]["G"], 2)
        self.assertEqual(len(d["alerts"]), 1)                 # X-ray and old ones left out
        self.assertEqual(d["alerts"][0]["text"], "Geomagnetic K-index of 5 expected")
        self.assertEqual(d["bands"][0], {"band": "80 m - 40 m", "day": "Poor", "night": "Fair"})

    def test_other_layout(self):
        from qsodesk import spaceweather as sw
        kp = sw.parse_kp([{"time_tag": "2026-11-24T06:00:00", "kp_index": 3}])
        self.assertEqual(kp[0]["kp"], 3)
        self.assertEqual(sw.build({})["kp"], None)            # nothing fetched: no crash
        # solar flux in whichever layout NOAA uses
        for flux in ({"Flux": "142"}, [{"time_tag": "2026-11-23", "flux": 150}, {"time_tag": "2026-11-24", "flux": 142}],
                     [["time_tag", "flux"], ["2026-11-24", "142"]], {"summary": {"observed_flux": 142.0}},
                     [{"time_tag": "2026-11-24T20:00", "frequency": 2800, "flux": 142}]):
            self.assertEqual(sw.build({"flux": flux})["sfi"], 142, flux)
        self.assertEqual(sw.build({"flux": {"odd": 1}, "flux2": [{"time_tag": "x", "frequency": 2800, "flux": 139}]})["sfi"], 139)
        many = [{"time_tag": "2026-11-24", "frequency": 2800, "flux": 141}, {"time_tag": "2026-11-24", "frequency": 8800, "flux": 260}]
        self.assertEqual(sw.build({"flux2": many})["sfi"], 141)   # picks the 2800 MHz (10.7 cm) row
        self.assertEqual(sw.build({"wind": [{"time_tag": "a", "proton_speed": 480}]})["wind"], 480)
        self.assertEqual(sw.build({"mag": {"Bt": "9", "Bz": "-6"}})["bz"], -6)
        self.assertEqual(sw.shape([{"a": 1}, {"a": 2}])[0], "2 items")


class Transceivers(unittest.TestCase):
    def test_hub(self):
        import time
        from qsodesk.radio import RadioHub
        with tempfile.TemporaryDirectory() as d:
            st = Store(d)
            seen = []
            hub = RadioHub(st, seen.append)
            self.assertEqual([r["id"] for r in hub.list()], ["r1"])          # old single radio carried over
            hub.save([{"id": "r1", "name": "80 m station", "source": "sim"},
                      {"name": "40 m station", "source": "sim"}])
            self.assertEqual([(r["id"], r["name"]) for r in hub.list()],
                             [("r1", "80 m station"), ("r2", "40 m station")])
            time.sleep(1.2)
            hub.get("r2").tune(band="40m", mode="CW")
            time.sleep(1.0)
            self.assertEqual(hub.get("r1").state["band"], "80m")              # independent radios
            self.assertEqual((hub.get("r2").state["band"], hub.get("r2").state["mode"]), ("40m", "CW"))
            self.assertEqual(hub.get("r2").state["label"], "40 m station")
            self.assertEqual(hub.get("nope").id, "r1")
            hub.save([{"id": "r2", "name": "40 m station", "source": "sim"}])
            self.assertEqual([r["id"] for r in hub.list()], ["r2"])
            with self.assertRaises(ValueError):
                hub.save([])
            self.assertEqual(RadioHub(st, seen.append).list()[0]["name"], "40 m station")   # survives restart


class Storage(unittest.TestCase):
    def entry(self, n, band="80m"):
        return {"uuid": "u%d" % n, "event": "Practice - Jock White", "contest": "jwfd",
                "ts": T0 + n, "call": "ZL2A%s" % chr(65 + n), "band": band, "mode": "PH",
                "rmode": "LSB", "freq": 3650000, "rst_sent": "59", "rst_rcvd": "59",
                "serial_sent": None, "serial_rcvd": "%03d" % n, "exch": "12",
                "name": "", "notes": "", "station": "S1", "operator": "ZL4XX"}

    def test_serials_per_band_and_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            st = Store(d)
            a, _, _ = st.add(self.entry(1), "80m")
            b, _, _ = st.add(self.entry(2), "80m")
            c, _, _ = st.add(self.entry(3, "40m"), "40m")
            again, _, _ = st.add(self.entry(2), "80m")               # browser retry
            self.assertEqual([a["serial_sent"], b["serial_sent"], c["serial_sent"]], [1, 2, 1])
            self.assertEqual(again["serial_sent"], 2)
            self.assertEqual(len(st.qsos("Practice - Jock White")), 3)
            st.set_deleted("u2", True)                               # number stays used
            self.assertEqual(st.next_serial("Practice - Jock White", "80m"), 3)

    def test_rebuild_from_journal(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as usb:
            st = Store(d)
            st.set_settings({"mirror_dir": usb, "callsign": "ZL4XX"})
            for n in range(1, 6):
                row, saved, _ = st.add(self.entry(n), "80m")
                self.assertEqual(saved, {"db": True, "journal": True, "mirror": True})
            st.edit("u3", {"call": "ZL2FIX"})
            st.set_deleted("u5", True)
            before = st.qsos("Practice - Jock White", include_deleted=True)
            st.db.close()
            for ext in ("", "-wal", "-shm"):                         # the card dies
                if os.path.exists(st.db_path + ext):
                    os.remove(st.db_path + ext)
            st2 = Store(d)
            after = st2.qsos("Practice - Jock White", include_deleted=True)
            strip = lambda rows: [{k: v for k, v in r.items() if k != "id"} for r in rows]
            self.assertEqual(strip(before), strip(after))
            self.assertEqual(st2.recovered, 5)
            self.assertEqual(st2.settings()["callsign"], "ZL4XX")
            with open(os.path.join(usb, "qsodesk-journal.adi"), "rb") as m, \
                    open(st.journal_path, "rb") as j:
                self.assertEqual(m.read(), j.read())

    def test_damaged_database(self):
        with tempfile.TemporaryDirectory() as d:
            st = Store(d)
            st.add(self.entry(1), "80m")
            st.db.close()
            for ext in ("-wal", "-shm"):
                if os.path.exists(st.db_path + ext):
                    os.remove(st.db_path + ext)
            with open(st.db_path, "wb") as fh:
                fh.write(b"this is not a database" * 200)
            st2 = Store(d)
            self.assertEqual(len(st2.qsos("Practice - Jock White")), 1)


if __name__ == "__main__":
    unittest.main()
