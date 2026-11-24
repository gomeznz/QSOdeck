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
