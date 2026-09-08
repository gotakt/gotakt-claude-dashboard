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
    fd, pfad = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        for z in zeilen:
            fh.write((z if isinstance(z, str) else json.dumps(z)) + "\n")
    return pfad


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


class Nebenlaeufigkeit(unittest.TestCase):
    """Die Eskalationsschleife darf parallele Aenderungen nicht ueberschreiben."""

    def setUp(self):
        self.alt = d.STATE
        d.STATE = tempfile.mktemp(suffix=".json")

    def tearDown(self):
        d.STATE = self.alt

    def test_eskalation_ueberschreibt_erledigt_nicht(self):
        # Ausgangslage wie sie die Schleife zu Beginn liest
        d.erledigt_setzen("alt", True)
        veraltet = d.zustand_laden()
        # waehrenddessen archiviert jemand ueber HTTP
        d.erledigt_setzen("neu", True)
        # jetzt merkt sich die Schleife ihre Eskalation
        d.eskalation_merken({"sitzung-1": 1234.0})
        jetzt = d.zustand_laden()
        self.assertIn("neu", jetzt["erledigt"], "parallele Archivierung ging verloren")
        self.assertIn("alt", jetzt["erledigt"])
        self.assertEqual(jetzt["eskaliert"]["sitzung-1"], 1234.0)
        self.assertNotIn("sitzung-1", veraltet["eskaliert"])

    def test_leere_eskalation_schreibt_nicht(self):
        d.erledigt_setzen("x", True)
        d.eskalation_merken({})
        self.assertIn("x", d.zustand_laden()["erledigt"])


class Kopfzeilen(unittest.TestCase):
    def test_seite_meldet_keinen_dauerspeicher(self):
        html = d.build([], 0)
        self.assertNotIn("localStorage.setItem", html)
        self.assertIn("sessionStorage", html)


class NurLesen(unittest.TestCase):
    def test_statischer_bau_ohne_mutierende_knoepfe(self):
        alt = d.NUR_LESEN
        d.NUR_LESEN = True
        try:
            html = d.build([], 0)
            self.assertNotIn("data-neu='1'", html)
            self.assertNotIn("id='b_speichern'", html)
            self.assertIn("data-nurlesen='1'", html)
        finally:
            d.NUR_LESEN = alt


class Zeit(unittest.TestCase):
    def test_utc_wird_ortszeit(self):
        self.assertEqual(len(d._ortstag("2026-08-27T23:30:00.000Z")), 10)
        self.assertIsNone(d._ortstag(""))


if __name__ == "__main__":
    unittest.main(verbosity=2)


# ---------------------------------------------------------------- Lifecycle
import subprocess as _sp

HOOK = os.path.join(WURZEL, "hooks", "lifecycle.py")


def hook(ereignis, ordner, **rest):
    """Ein Hook-Ereignis abfeuern, mit umgebogenem Zielordner."""
    nutzlast = dict(rest)
    nutzlast["hook_event_name"] = ereignis
    umgebung = dict(os.environ, HOME=ordner)
    return _sp.run([sys.executable, HOOK], input=json.dumps(nutzlast),
                   text=True, capture_output=True, env=umgebung)


def satz(ordner, sid):
    p = os.path.join(ordner, ".claude", "dashboard", "lifecycle", sid + ".json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


class LifecycleHook(unittest.TestCase):
    def setUp(self):
        self.heim = tempfile.mkdtemp()
        self.sid = "sitzung-1"

    def _feuer(self, ereignis, **rest):
        r = hook(ereignis, self.heim, session_id=self.sid, cwd="/tmp/p", **rest)
        self.assertEqual(r.returncode, 0, r.stderr)
        return satz(self.heim, self.sid)

    def test_sessionstart_legt_an(self):
        e = self._feuer("SessionStart", source="startup")
        self.assertEqual(e["state"], "bereit")
        self.assertEqual(e["source"], "startup")

    def test_prompt_bedeutet_arbeit(self):
        e = self._feuer("UserPromptSubmit", prompt="mach mal was")
        self.assertEqual(e["state"], "arbeitet")

    def test_pretooluse_merkt_werkzeug(self):
        e = self._feuer("PreToolUse", tool_name="Bash",
                        tool_input={"command": "rm -rf /"})
        self.assertEqual(e["state"], "arbeitet")
        self.assertEqual(e["current_tool"], "Bash")
        self.assertIsNotNone(e["tool_started_at"])

    def test_posttooluse_raeumt_werkzeug_weg(self):
        self._feuer("PreToolUse", tool_name="Edit")
        e = self._feuer("PostToolUse", tool_name="Edit", tool_response={"x": 1})
        self.assertIsNone(e["current_tool"])
        self.assertTrue(e["last_tool_ok"])

    def test_werkzeugfehler_wird_vermerkt(self):
        self._feuer("PreToolUse", tool_name="Bash")
        e = self._feuer("PostToolUseFailure", tool_name="Bash")
        self.assertFalse(e["last_tool_ok"])
        self.assertIsNone(e["current_tool"])

    def test_permissionrequest_eigener_zustand(self):
        e = self._feuer("PermissionRequest", tool_name="Bash")
        self.assertEqual(e["state"], "freigabe")

    def test_stop_bedeutet_wartet(self):
        e = self._feuer("Stop")
        self.assertEqual(e["state"], "wartet")

    def test_stopfailure(self):
        e = self._feuer("StopFailure")
        self.assertEqual(e["state"], "fehler")

    def test_sessionend_mit_grund(self):
        e = self._feuer("SessionEnd", reason="clear")
        self.assertEqual(e["state"], "beendet")
        self.assertEqual(e["end_reason"], "clear")
        self.assertIsNotNone(e["ended_at"])

    def test_keine_inhalte_im_zustand(self):
        e = self._feuer("PreToolUse", tool_name="Bash",
                        tool_input={"command": "echo geheim"},
                        prompt="auch geheim")
        roh = json.dumps(e)
        self.assertNotIn("geheim", roh)
        for k in ("tool_input", "prompt", "tool_response", "command", "message"):
            self.assertNotIn(k, e)

    def test_unbekanntes_ereignis_aendert_nichts(self):
        self._feuer("Stop")
        e = self._feuer("Quatsch")
        self.assertEqual(e["state"], "wartet")

    def test_ohne_sitzungskennung_kein_schreiben(self):
        r = hook("Stop", self.heim, cwd="/tmp/p")
        self.assertEqual(r.returncode, 0)
        self.assertIsNone(satz(self.heim, ""))

    def test_kaputte_eingabe_bricht_nicht(self):
        umgebung = dict(os.environ, HOME=self.heim)
        r = _sp.run([sys.executable, HOOK], input="kein json",
                    text=True, capture_output=True, env=umgebung)
        self.assertEqual(r.returncode, 0)

    def test_parallele_schreibvorgaenge(self):
        import threading
        def feuern(i):
            hook("PreToolUse", self.heim, session_id=self.sid,
                 cwd="/tmp/p", tool_name="W%d" % i)
        faeden = [threading.Thread(target=feuern, args=(i,)) for i in range(12)]
        for f in faeden:
            f.start()
        for f in faeden:
            f.join()
        e = satz(self.heim, self.sid)
        self.assertIsNotNone(e, "Zustandsdatei fehlt nach paralleler Last")
        self.assertEqual(e["state"], "arbeitet")


class LifecycleVorrang(unittest.TestCase):
    def _inf(self, rolle, alter, sid="s1"):
        return {"id": sid, "last_role": rolle,
                "mtime": datetime.datetime.now().timestamp() - alter}

    def _leben(self, zustand, alter, sid="s1"):
        wann = datetime.datetime.now().astimezone() - datetime.timedelta(seconds=alter)
        return {sid: {"session_id": sid, "state": zustand,
                      "last_event_at": wann.isoformat(timespec="seconds")}}

    def test_frischer_hook_schlaegt_herleitung(self):
        # Transkript sagt "wartet", Hook sagt "arbeitet"
        z = d.status(self._inf("assistant", 600), self._leben("arbeitet", 30))
        self.assertEqual(z[0], "live")
        self.assertEqual(z[3], "hook")

    def test_freigabe_wird_eigene_spalte(self):
        z = d.status(self._inf("assistant", 600), self._leben("freigabe", 60))
        self.assertEqual(z[0], "freigabe")
        self.assertEqual(z[3], "hook")

    def test_veralteter_arbeitet_faellt_zurueck(self):
        z = d.status(self._inf("assistant", 600), self._leben("arbeitet", 3 * 3600))
        self.assertEqual(z[0], "warte")
        self.assertEqual(z[3], "hergeleitet")

    def test_ohne_hook_wie_bisher(self):
        z = d.status(self._inf("user", 600), {})
        self.assertEqual(z[0], "tot")
        self.assertEqual(z[3], "hergeleitet")

    def test_bereit_wird_als_hook_angezeigt(self):
        # Ein frisches SessionStart soll sichtbar vom Hook kommen und nicht
        # sofort wieder auf die Herleitung zurueckfallen.
        z = d.status(self._inf("assistant", 600), self._leben("bereit", 30))
        self.assertEqual(z[0], "warte")
        self.assertEqual(z[1], "BEREIT")
        self.assertEqual(z[3], "hook")

    def test_bereit_veraltet_nach_zehn_minuten(self):
        z = d.status(self._inf("user", 600), self._leben("bereit", 20 * 60))
        self.assertEqual(z[3], "hergeleitet")
        self.assertEqual(z[0], "tot")

    def test_jeder_haltbare_zustand_hat_eine_spalte(self):
        ohne = set(d.HALTBAR) - set(d.LEBEN_ZU_SPALTE)
        self.assertEqual(ohne, set(), "Zustand ohne Spalte waere ein toter Pfad")

    def test_beendet_veraltet_nie(self):
        z = d.status(self._inf("assistant", 600), self._leben("beendet", 30 * 86400))
        self.assertEqual(z[0], "kalt")
        self.assertEqual(z[3], "hook")

    def test_kaputter_zeitstempel_faellt_zurueck(self):
        z = d.status(self._inf("assistant", 600),
                     {"s1": {"session_id": "s1", "state": "arbeitet",
                             "last_event_at": "voellig kaputt"}})
        self.assertEqual(z[3], "hergeleitet")


class HookInstallation(unittest.TestCase):
    """Die Installation darf fremde Einstellungen nicht anfassen."""

    def setUp(self):
        self.heim = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.heim, ".claude"))
        self.datei = os.path.join(self.heim, ".claude", "settings.json")

    def _lauf(self):
        return _sp.run([sys.executable, os.path.join(WURZEL, "hooks", "install-hooks.py")],
                       text=True, capture_output=True,
                       env=dict(os.environ, HOME=self.heim))

    def _cfg(self):
        with open(self.datei, encoding="utf-8") as fh:
            return json.load(fh)

    def _zaehle(self, cfg, teil):
        n = 0
        for liste in (cfg.get("hooks") or {}).values():
            for g in liste:
                for h in g.get("hooks", []):
                    if teil in h.get("command", ""):
                        n += 1
        return n

    def test_idempotent_und_fremdes_bleibt(self):
        with open(self.datei, "w", encoding="utf-8") as fh:
            json.dump({"model": "opus", "theme": "dark",
                       "hooks": {"Stop": [{"hooks": [{"type": "command",
                                                      "command": "python3 stop-notify.py"}]}]}}, fh)
        self.assertEqual(self._lauf().returncode, 0)
        eins = self._cfg()
        self.assertEqual(self._lauf().returncode, 0)
        zwei = self._cfg()

        self.assertEqual(self._zaehle(eins, "lifecycle.py"), 9)
        self.assertEqual(self._zaehle(zwei, "lifecycle.py"), 9, "zweiter Lauf hat doppelt eingetragen")
        self.assertEqual(self._zaehle(zwei, "stop-notify"), 1, "fremder Hook verloren")
        self.assertEqual(zwei["model"], "opus")
        self.assertEqual(zwei["theme"], "dark")

    def test_pfade_mit_leerzeichen_werden_zitiert(self):
        """Ein Pfad wie /Users/x/My Projects/ darf den Befehl nicht zerlegen."""
        import shlex, importlib.util
        spec = importlib.util.spec_from_file_location(
            "ih", os.path.join(WURZEL, "hooks", "install-hooks.py"))
        ih = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ih)
        cmd = ih.befehl("/usr/bin/python3", "lifecycle.py")
        teile = shlex.split(cmd)
        self.assertEqual(len(teile), 2, "Befehl zerfaellt in zu viele Argumente: %r" % cmd)
        self.assertTrue(teile[1].endswith("lifecycle.py"))

        alt = ih.HOOKS
        try:
            ih.HOOKS = "/Users/x/My Projects/gotakt's tools"
            cmd = ih.befehl("/usr/bin/python3", "lifecycle.py")
            teile = shlex.split(cmd)
            self.assertEqual(len(teile), 2, "Leerzeichen zerlegen den Befehl: %r" % cmd)
            self.assertEqual(teile[1], "/Users/x/My Projects/gotakt's tools/lifecycle.py")
        finally:
            ih.HOOKS = alt

    def test_kaputte_datei_wird_nicht_angefasst(self):
        with open(self.datei, "w", encoding="utf-8") as fh:
            fh.write("{kein json")
        r = self._lauf()
        self.assertEqual(r.returncode, 1)
        with open(self.datei, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "{kein json")


class HookEntfernung(unittest.TestCase):
    """Nach der dokumentierten Deinstallation darf kein Dashboard-Hook
    zurueckbleiben — und kein fremder darf dabei mitgehen.

    Der Zustand vor dieser Klasse: `install.sh` trug neun Hooks ein, und der
    im README beschriebene Entfernungsweg (LaunchAgent, plist) liess alle
    neun stehen. Wer danach das Repository loeschte, hatte in jeder
    Claude-Sitzung neun Hooks, die ins Leere zeigten.
    """

    FREMD = "/usr/local/bin/mein-eigener-hook.sh"

    def setUp(self):
        self.heim = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.heim, ".claude"))
        self.datei = os.path.join(self.heim, ".claude", "settings.json")

    def _lauf(self, *args):
        return _sp.run([sys.executable, os.path.join(WURZEL, "hooks", "install-hooks.py")] + list(args),
                       text=True, capture_output=True,
                       env=dict(os.environ, HOME=self.heim))

    def _cfg(self):
        with open(self.datei, encoding="utf-8") as fh:
            return json.load(fh)

    def _zaehle(self, cfg, teil):
        n = 0
        for liste in (cfg.get("hooks") or {}).values():
            if not isinstance(liste, list):
                continue
            for g in liste:
                for h in (g or {}).get("hooks", []) or []:
                    if teil in (h.get("command") or ""):
                        n += 1
        return n

    def _schreibe(self, cfg):
        with open(self.datei, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)

    # 1 ---------------------------------------------------------------
    def test_leere_einstellungen_installieren(self):
        self._schreibe({})
        self.assertEqual(self._lauf().returncode, 0)
        self.assertEqual(self._zaehle(self._cfg(), "lifecycle.py"), 9)

    # 2 ---------------------------------------------------------------
    def test_zweites_installieren_erzeugt_keine_duplikate(self):
        self._schreibe({})
        self.assertEqual(self._lauf().returncode, 0)
        r = self._lauf()
        self.assertEqual(r.returncode, 0)
        self.assertIn("eingetragen: nichts", r.stdout)
        self.assertEqual(self._zaehle(self._cfg(), "lifecycle.py"), 9)

    # 3 ---------------------------------------------------------------
    def test_fremder_hook_bleibt_beim_entfernen(self):
        """Der Kernvertrag. Wird er verletzt, nimmt eine Deinstallation
        jemandem seine eigene Einrichtung weg."""
        self._schreibe({
            "model": "opus",
            "hooks": {"Stop": [{"hooks": [{"type": "command", "command": self.FREMD}]}]},
        })
        self.assertEqual(self._lauf().returncode, 0)
        self.assertEqual(self._zaehle(self._cfg(), "lifecycle.py"), 9)

        r = self._lauf("--entfernen")
        self.assertEqual(r.returncode, 0, r.stderr)
        nachher = self._cfg()
        self.assertEqual(self._zaehle(nachher, "lifecycle.py"), 0,
                         "Dashboard-Hooks sind zurueckgeblieben")
        self.assertEqual(self._zaehle(nachher, "mein-eigener-hook"), 1,
                         "fremder Hook wurde mitentfernt")
        self.assertEqual(nachher["model"], "opus", "fremde Einstellung verloren")

    def test_fremder_hook_im_selben_ereignis_ueberlebt(self):
        """Fremd und eigen in EINER Gruppe — der schwierigere Fall."""
        self._schreibe({"hooks": {}})
        self.assertEqual(self._lauf().returncode, 0)
        cfg = self._cfg()
        cfg["hooks"]["Stop"][0]["hooks"].append({"type": "command", "command": self.FREMD})
        self._schreibe(cfg)

        self.assertEqual(self._lauf("--entfernen").returncode, 0)
        nachher = self._cfg()
        self.assertEqual(self._zaehle(nachher, "lifecycle.py"), 0)
        self.assertEqual(self._zaehle(nachher, "mein-eigener-hook"), 1,
                         "fremder Hook in gemeinsamer Gruppe verloren")

    def test_gruppe_mit_matcher_bleibt_stehen(self):
        """Eine Gruppe, die ausser `hooks` noch etwas traegt, wird nicht
        weggeworfen — sonst ginge eine fremde Angabe verloren."""
        self._schreibe({"hooks": {}})
        self.assertEqual(self._lauf().returncode, 0)
        cfg = self._cfg()
        cfg["hooks"]["PreToolUse"][0]["matcher"] = "Bash"
        self._schreibe(cfg)

        self.assertEqual(self._lauf("--entfernen").returncode, 0)
        nachher = self._cfg()
        self.assertEqual(self._zaehle(nachher, "lifecycle.py"), 0)
        self.assertEqual(nachher["hooks"]["PreToolUse"][0]["matcher"], "Bash",
                         "matcher einer fremden Gruppe verloren")

    def test_leer_gewordenes_ereignis_verschwindet(self):
        self._schreibe({"hooks": {}})
        self.assertEqual(self._lauf().returncode, 0)
        self.assertEqual(self._lauf("--entfernen").returncode, 0)
        self.assertEqual(self._cfg().get("hooks"), {},
                         "leer gewordene Ereignisse blieben stehen")

    def test_vorher_leeres_ereignis_bleibt(self):
        """Was schon vorher leer dastand, ist nicht unseres."""
        self._schreibe({"hooks": {"Notification": []}})
        self.assertEqual(self._lauf().returncode, 0)
        self.assertEqual(self._lauf("--entfernen").returncode, 0)
        self.assertIn("Notification", self._cfg()["hooks"],
                      "fremdes leeres Ereignis wurde aufgeraeumt")

    # 4 ---------------------------------------------------------------
    def test_entfernen_ohne_eigene_hooks_ist_folgenlos(self):
        vorher = json.dumps({"model": "opus",
                             "hooks": {"Stop": [{"hooks": [{"type": "command",
                                                            "command": self.FREMD}]}]}},
                            indent=2)
        with open(self.datei, "w", encoding="utf-8") as fh:
            fh.write(vorher)
        r = self._lauf("--entfernen")
        self.assertEqual(r.returncode, 0)
        self.assertIn("entfernt: nichts", r.stdout)
        with open(self.datei, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), vorher, "Datei wurde ohne Not angefasst")

    def test_entfernen_ohne_settings_datei(self):
        r = self._lauf("--entfernen")
        self.assertEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(self.datei), "Datei wurde angelegt")

    # 5 ---------------------------------------------------------------
    def test_ungueltiges_json_bleibt_byteweise_unveraendert(self):
        roh = b'{"model": "opus", kaputt\n'
        with open(self.datei, "wb") as fh:
            fh.write(roh)
        for args in ([], ["--entfernen"]):
            r = self._lauf(*args)
            self.assertEqual(r.returncode, 1, "Abbruch erwartet bei %s" % (args or ["(einbauen)"],))
            with open(self.datei, "rb") as fh:
                self.assertEqual(fh.read(), roh, "kaputte Datei wurde veraendert")

    def test_unbekanntes_argument_aendert_nichts(self):
        self._schreibe({})
        r = self._lauf("--alles-weg")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self._cfg(), {})

    # 6 ---------------------------------------------------------------
    def test_fehlgeschlagenes_ersetzen_laesst_das_original_intakt(self):
        """Der Grund fuer das atomare Schreiben. settings.json gehoert Claude
        Code; eine halb geschriebene Fassung waere ein fremder Schaden."""
        import importlib.util
        vorher = json.dumps({"model": "opus"}, indent=2)
        with open(self.datei, "w", encoding="utf-8") as fh:
            fh.write(vorher)

        alt_heim = os.environ.get("HOME")
        os.environ["HOME"] = self.heim
        try:
            spec = importlib.util.spec_from_file_location(
                "ih_fehler", os.path.join(WURZEL, "hooks", "install-hooks.py"))
            ih = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(ih)
            self.assertEqual(ih.SETTINGS, self.datei)

            echtes_replace = ih.os.replace
            ih.os.replace = lambda *a, **k: (_ for _ in ()).throw(OSError("Platte voll"))
            try:
                with self.assertRaises(OSError):
                    ih.eintragen()
            finally:
                ih.os.replace = echtes_replace
        finally:
            if alt_heim is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = alt_heim

        with open(self.datei, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), vorher, "Original wurde beschaedigt")
        rest = [f for f in os.listdir(os.path.dirname(self.datei)) if ".neu-" in f]
        self.assertEqual(rest, [], "Nachbardatei blieb liegen: %s" % rest)
