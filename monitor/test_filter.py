"""Opt-in-Filter `schwellen.als_filter` (22.10.2026). `python -m unittest test_filter`.

Kern: Ohne den Schalter verhält sich der Monitor genau wie vorher (kalibrierte
Configs). Mit dem Schalter verwerfen Höchstwert, Ausschlüsse und Vorlaufzeit.
"""

from __future__ import annotations

import datetime as dt
import unittest

import vergabe_monitor as vm

HEUTE = dt.date(2026, 10, 22)


def kandidat(**anders) -> dict:
    c = {"title": "Softwareentwicklung Fachverfahren", "description": "",
         "form_type": "competition", "procedure_type": "open", "nuts": "DE300",
         "cpv": ["72262000"], "value": "50000", "currency": "EUR",
         "sme_suitable": "", "buyer": "", "buyer_nuts": ""}
    c.update(anders)
    return c


def config(als_filter: bool, **schwellen) -> dict:
    s = {"auftragswert_min": 0, "auftragswert_max": None, "vorlaufzeit_min_tage": 7}
    s.update(schwellen)
    if als_filter:
        s["als_filter"] = True
    return {"suchraum": {"regionen": ["DE3"], "cpv_codes": ["72262000"]},
            "schwellen": s, "ausschluss": ["generalunternehmer_pflicht"]}


class Hoechstwert(unittest.TestCase):
    def test_ohne_schalter_bleibt_treffer(self):
        m = vm.match(kandidat(value="900000"), config(False, auftragswert_max=100000))
        self.assertTrue(m["hit"])

    def test_mit_schalter_verworfen(self):
        m = vm.match(kandidat(value="900000"), config(True, auftragswert_max=100000))
        self.assertFalse(m["hit"])
        self.assertIn("Max", m["reject"])

    def test_unter_max_bleibt(self):
        self.assertTrue(vm.match(kandidat(value="90000"), config(True, auftragswert_max=100000))["hit"])

    def test_ohne_wert_bleibt(self):
        self.assertTrue(vm.match(kandidat(value=""), config(True, auftragswert_max=100000))["hit"])


class Ausschluss(unittest.TestCase):
    GU = "Vergabe an einen Generalunternehmer"

    def test_ohne_schalter_nur_flag(self):
        m = vm.match(kandidat(description=self.GU), config(False))
        self.assertTrue(m["hit"])
        self.assertTrue(any("Ausschluss-Verdacht" in f for f in m["flags"]))

    def test_mit_schalter_verworfen(self):
        m = vm.match(kandidat(description=self.GU), config(True))
        self.assertFalse(m["hit"])
        self.assertIn("generalunternehmer_pflicht", m["reject"])

    def test_nicht_konfigurierter_ausschluss_greift_nicht(self):
        m = vm.match(kandidat(description="Rahmenvereinbarung über 4 Jahre"), config(True))
        self.assertTrue(m["hit"])


class Vorlaufzeit(unittest.TestCase):
    def test_zu_knapp_nur_mit_schalter(self):
        c = kandidat(angebotsfrist="2026-10-25T10:00:00")
        self.assertTrue(vm.zu_knapp(c, config(True), HEUTE))
        self.assertFalse(vm.zu_knapp(c, config(False), HEUTE))

    def test_genug_vorlauf(self):
        self.assertFalse(vm.zu_knapp(kandidat(angebotsfrist="2026-10-29"), config(True), HEUTE))
        self.assertTrue(vm.zu_knapp(kandidat(angebotsfrist="2026-10-28"), config(True), HEUTE))

    def test_ohne_frist_nie_verworfen(self):
        self.assertFalse(vm.zu_knapp(kandidat(), config(True), HEUTE))
        self.assertFalse(vm.zu_knapp(kandidat(angebotsfrist="unbekannt"), config(True), HEUTE))

    def test_oeffnung_als_rueckfall(self):
        self.assertTrue(vm.zu_knapp(kandidat(opening="2026-10-23 10:00"), config(True), HEUTE))


class VmpOhneFormType(unittest.TestCase):
    def test_ex_post_ohne_form_type_kein_keyerror(self):
        # 21.10.2026: KeyError 'form_type' liess den ganzen VMP-Kanal scheitern
        c = kandidat(procedure_type="Ex post Veröffentlichung")
        del c["form_type"]
        m = vm.match(c, config(False))
        self.assertFalse(m["hit"])
        self.assertIn("schon vergeben", m["reject"])


if __name__ == "__main__":
    unittest.main()
