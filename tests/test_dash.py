"""Tests fuer die Teile, bei denen ein Fehler wirklich weh tut."""
import json, os, sys, tempfile, unittest, importlib.util, datetime

WURZEL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def lade():
    spec = importlib.util.spec_from_file_location("dash", os.path.join(WURZEL, "dash.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


d = lade()


def schreibe(zeilen):
    fh = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    for z in zeilen:
        fh.write((z if isinstance(z, str) else json.dumps(z)) + "\n")
    fh.close()
    return fh.name


def grund(rolle, text, ts="2026-08-28T10:00:00.000Z"):
    return {"type": rolle, "timestamp": ts, "cwd": "/tmp/p", "sessionId": "s1",
            "message": {"role": rolle, "content": [{"type": "text", "text": text}]}}


class Robustheit(unittest.TestCase):
    def test_kaputte_zeile_wirft_nicht(self):
        p = schreibe(["{kein json", grund("assistant", "hallo")])
        self.assertIsNone(d._loads("{kein json"))
        self.assertIsNotNone(d.read_session(p, {}))

    def test_abgeschnittene_zeile(self):
        p = schreibe([grund("assistant", "hallo")])
        with open(p, "a", encoding="utf-8") as fh:
            fh.write('{"type":"assistant","mess')
        self.assertIsNotNone(d.read_session(p, {}))

    def test_cache_liest_nur_neues(self):
        p = schreibe([grund("assistant", "eins")])
        cache = {}
        d.verbrauch(p, cache)
        vorher = cache[p]["offset"]
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(grund("assistant", "zwei")) + "\n")
        d.verbrauch(p, cache)
        self.assertGreater(cache[p]["offset"], vorher)


class Zustand(unittest.TestCase):
    def _inf(self, rolle, alter):
        return {"last_role": rolle, "mtime": datetime.datetime.now().timestamp() - alter}

    def test_nutzer_zuletzt_ist_abgebrochen(self):
        self.assertEqual(d.status(self._inf("user", 600))[0], "tot")

    def test_claude_zuletzt_wartet(self):
        self.assertEqual(d.status(self._inf("assistant", 600))[0], "warte")

    def test_frisch_ist_live(self):
        self.assertEqual(d.status(self._inf("assistant", 10))[0], "live")

    def test_alt_ist_kalt(self):
        self.assertEqual(d.status(self._inf("assistant", 8 * 86400))[0], "kalt")


class Ausgabe(unittest.TestCase):
    def test_titel_wird_entschaerft(self):
        s = {"id": "x", "title": "<script>alert(1)</script>", "first_prompt": None,
             "cwd": "/tmp/p", "last_role": "assistant", "last_user": None,
             "last_asst": "<img onerror=alert(1)>", "mtime": datetime.datetime.now().timestamp(),
             "modelle": {}, "kosten": 0.0, "token": 0, "prompts": 0, "size": 1,
             "branch": None, "dauer": 0.0, "taeglich": {}, "erledigt": False}
        html = d.build([s], 0)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("<img onerror=", html)
        self.assertIn("&lt;script&gt;", html)

    def test_befehl_ist_zitiert(self):
        s = {"id": "a b", "cwd": "/Users/me/Marwan's App"}
        befehl = d.fortsetzen_befehl(s)
        self.assertNotIn("Marwan's App &&", befehl)
        self.assertIn("'/Users/me/Marwan'\"'\"'s App'", befehl)


class Budget(unittest.TestCase):
    def test_text_wird_abgelehnt(self):
        alt = d.STATE
        d.STATE = tempfile.mktemp(suffix=".json")
        try:
            self.assertFalse(d.budget_setzen("keine zahl", "0")["ok"])
            self.assertTrue(d.budget_setzen("10", "50")["ok"])
        finally:
            d.STATE = alt

    def test_negativ_wird_zu_null(self):
        alt = d.STATE
        d.STATE = tempfile.mktemp(suffix=".json")
        try:
            self.assertEqual(d.budget_setzen("-5", "-9")["tag"], 0.0)
        finally:
            d.STATE = alt


class Preise(unittest.TestCase):
    def test_modellzuordnung(self):
        self.assertEqual(d.preis_fuer("claude-opus-5"), d.PREISE["opus"])
        self.assertEqual(d.preis_fuer("unbekannt"), d.STANDARD)

    def test_kosten_rechnen(self):
        k = d.kosten({"claude-opus-5": {"in": 1_000_000, "out": 0,
                                        "cache_read": 0, "cache_write": 0}})
        self.assertAlmostEqual(k, d.PREISE["opus"]["in"], places=6)


class Zeit(unittest.TestCase):
    def test_utc_wird_ortszeit(self):
        self.assertEqual(len(d._ortstag("2026-08-27T23:30:00.000Z")), 10)
        self.assertIsNone(d._ortstag(""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
