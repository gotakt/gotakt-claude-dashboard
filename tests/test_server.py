"""Integrationstests fuer den HTTP-Server.

Die Schutzmassnahmen sehen beim Lesen richtig aus, aber genau das ist die Sorte
Code, die ein Umbau still zerbricht. Deshalb hier gegen einen echten laufenden
Server geprueft, nicht gegen den Quelltext.
"""
import json, os, re, sys, socket, threading, time, unittest, importlib.util
import urllib.request, urllib.error

WURZEL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("dash", os.path.join(WURZEL, "dash.py"))
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


def freier_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = freier_port()
        cls.faden = threading.Thread(target=d.serve, args=(cls.port,), daemon=True)
        cls.faden.start()
        for _ in range(60):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/" % cls.port, timeout=2).read()
                break
            except Exception:
                time.sleep(0.25)

    def _anfrage(self, weg, methode="POST", token=None, kopf=None, koerper=None):
        url = "http://127.0.0.1:%d%s" % (self.port, weg)
        r = urllib.request.Request(url, method=methode,
                                   data=(koerper or b"") if methode == "POST" else None)
        if token is not None:
            r.add_header("X-Dashboard-Token", token)
        for k, v in (kopf or {}).items():
            r.add_header(k, v)
        try:
            with urllib.request.urlopen(r, timeout=8) as a:
                return a.status, a.read(), dict(a.headers)
        except urllib.error.HTTPError as ex:
            return ex.code, ex.read(), dict(ex.headers)

    # ---------- Zugriffsschutz ----------
    def test_ohne_token_abgelehnt(self):
        self.assertEqual(self._anfrage("/erledigt?id=x")[0], 403)

    def test_falscher_token_abgelehnt(self):
        self.assertEqual(self._anfrage("/erledigt?id=x", token="falsch")[0], 403)

    def test_richtiger_token_kommt_durch(self):
        code, _, _ = self._anfrage("/erledigt?id=", token=d.TOKEN)
        self.assertEqual(code, 400, "richtiger Token muss bis zur Pruefung der id kommen")

    def test_fremde_herkunft_abgelehnt(self):
        code, _, _ = self._anfrage("/erledigt?id=x", token=d.TOKEN,
                                   kopf={"Origin": "https://boese.example"})
        self.assertEqual(code, 403)

    def test_fremder_host_abgelehnt(self):
        code, _, _ = self._anfrage("/erledigt?id=x", token=d.TOKEN,
                                   kopf={"Host": "boese.example"})
        self.assertEqual(code, 403)

    def test_eigene_herkunft_erlaubt(self):
        code, _, _ = self._anfrage("/erledigt?id=", token=d.TOKEN,
                                   kopf={"Origin": "http://127.0.0.1:%d" % self.port})
        self.assertEqual(code, 400)

    # ---------- Methoden ----------
    def test_get_auf_aktion_gibt_es_nicht(self):
        self.assertEqual(self._anfrage("/erledigt?id=x", methode="GET")[0], 404)

    def test_get_auf_antwort_gibt_es_nicht(self):
        self.assertEqual(self._anfrage("/antwort?id=x", methode="GET")[0], 404)

    def test_unbekannter_weg(self):
        self.assertEqual(self._anfrage("/gibtsnicht", token=d.TOKEN)[0], 404)

    # ---------- Nutzlast ----------
    def test_zu_grosse_antwort(self):
        code, _, _ = self._anfrage("/antwort?id=x", token=d.TOKEN,
                                   koerper=b"a" * 9000)
        self.assertEqual(code, 413)

    def test_unbekannte_sitzung(self):
        code, _, _ = self._anfrage("/antwort?id=gibtsnicht", token=d.TOKEN, koerper=b"hi")
        self.assertEqual(code, 404)

    def test_papierkorb_lehnt_pfadwechsel_ab(self):
        code, roh, _ = self._anfrage("/zurueckholen?datei=../../boese.jsonl", token=d.TOKEN)
        self.assertFalse(json.loads(roh)["ok"])

    # ---------- Kopfzeilen der Seite ----------
    def test_schutzkopfzeilen(self):
        code, roh, kopf = self._anfrage("/", methode="GET")
        self.assertEqual(code, 200)
        self.assertEqual(kopf.get("X-Frame-Options"), "DENY")
        self.assertEqual(kopf.get("Referrer-Policy"), "no-referrer")
        self.assertEqual(kopf.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(kopf.get("Cache-Control"), "no-store")
        csp = kopf.get("Content-Security-Policy") or ""
        for teil in ("default-src 'none'", "frame-ancestors 'none'",
                     "connect-src 'self'", "base-uri 'none'", "form-action 'none'"):
            self.assertIn(teil, csp)

    def test_seite_traegt_den_token(self):
        _, roh, _ = self._anfrage("/", methode="GET")
        self.assertIn(d.TOKEN.encode(), roh)

    def test_fremder_host_auch_bei_der_seite(self):
        code, _, _ = self._anfrage("/", methode="GET", kopf={"Host": "boese.example"})
        self.assertEqual(code, 403)


class KeineExternenVerweise(unittest.TestCase):
    """Die Zusage lautet: die Seite laedt nichts aus dem Netz nach.

    Geprueft wird das erzeugte HTML, nicht der Quelltext. Nur das ist die
    Eigenschaft, die tatsaechlich versprochen wird.
    """

    def test_erzeugtes_html_ohne_fremde_adressen(self):
        html = d.build([], 0)
        adressen = re.findall(r'(?:src|href|action)\s*=\s*["\']([^"\']+)', html)
        adressen += re.findall(r'url\(([^)]+)\)', html)
        fremd = []
        for a in adressen:
            a = a.strip().strip('"\'')
            if a.startswith(("http://", "https://", "//")):
                if not re.match(r'https?://(localhost|127\.0\.0\.1)(:|/|$)', a):
                    fremd.append(a)
        self.assertEqual(fremd, [], "die Seite wuerde von aussen nachladen")

    def test_keine_bekannten_schriftdienste(self):
        html = d.build([], 0)
        for dienst in ("googleapis", "gstatic", "cdn.", "unpkg", "jsdelivr"):
            self.assertNotIn(dienst, html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
