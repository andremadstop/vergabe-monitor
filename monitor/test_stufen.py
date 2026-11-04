"""Stufen, Kontextregel, Vergebenes am Titel, Frist-Flag (03.11.2026, Profil BMP).

Kern: Profile ohne `stufen` und ohne `vorlaufzeit_markieren` verhalten sich wie
vorher. Gemessen über den Halbjahreskorpus 01.05.-02.11.2026; hier die Regeln im Kleinen.
"""

from __future__ import annotations

import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path

import store
import vergabe_monitor as vm


def kand(**x) -> dict:
    k = {"title": "Leistung", "description": "", "nuts": "DE300", "cpv": set(), "value": "",
         "currency": "", "sme_suitable": "", "form_type": "competition", "notice_type": "cn-standard",
         "procedure_type": "open", "buyer": "Bezirksamt", "buyer_id": "", "buyer_nuts": "DE300",
         "notice_id": "n1", "lot_id": "LOT-0001", "pub_date": "2026-09-18", "opening": "",
         "buyer_url": "", "city": "Berlin", "plz": "10115", "legal_basis": "de-vob"}
    k.update(x)
    return k


# Nachbau der Struktur von bmp-restaurierung-v3.yaml, verkleinert.
PROFIL = {
    "mandant": "test-stufen",
    "suchraum": {
        "regionen": ["DE3", "DE4"],
        "cpv_codes": ["45262510"],
        "freitext_muster": ["bildhauer", "natursteinarbeit"],
        "auftraggeber": [{"name": "Stiftung Preußische Schlösser und Gärten", "kennung": "DE138408360",
                          "kurz": "SPSG", "cpv_codes": ["71000000"]}],
    },
    "kontext": {
        "fenster_zeichen": 40,
        "objekt": ["skulptur", "bauzier", " figur"],
        "handwerk": ["restaur", "kopie"],
        "material": ["naturstein", " sandstein"],
        "kunstgut": ["exponat", "skulptur"],
    },
    "stufen": [
        {"stufe": 1, "name": "Kern", "muster": ["bildhauer"],
         "regeln": [{"woerter": ["kopie", "restaurierung"], "kontext": "objekt"},
                    {"woerter": ["skulptur"], "kontext": "handwerk"}]},
        {"stufe": 2, "name": "Lückenfüller", "cpv": ["45262510"], "cpv_nur_mit_kontext": "material",
         "muster": ["natursteinarbeit"],
         "regeln": [{"woerter": ["fassade"], "kontext": "material"}]},
        {"stufe": 3, "name": "Rand", "regeln": [{"woerter": ["transport"], "kontext": ["kunstgut"]}],
         "auftraggeber_ohne_muster": True},
    ],
    "schwellen": {"auftragswert_min": 0, "vorlaufzeit_min_tage": 7, "vorlaufzeit_markieren": True},
}


class Stufen(unittest.TestCase):
    def test_kern_ueber_muster(self):
        m = vm.match(kand(title="Bildhauerarbeiten am Portal"), PROFIL)
        self.assertTrue(m["hit"])
        self.assertEqual(m["stufe"], 1)
        self.assertEqual(m["stufe_name"], "Kern")
        self.assertIn("Muster bildhauer", m["stufe_grund"])
        self.assertTrue(m["reasons"][0].startswith("Stufe 1:"))

    def test_erste_stufe_gewinnt(self):
        m = vm.match(kand(title="Natursteinarbeiten und Bildhauerarbeiten"), PROFIL)
        self.assertEqual(m["stufe"], 1)

    def test_lueckenfueller_ueber_regel(self):
        m = vm.match(kand(title="Sanierung Fassade aus Naturstein"), PROFIL)
        self.assertTrue(m["hit"])
        self.assertEqual(m["stufe"], 2)
        self.assertIn("fassade + Kontext 'naturstein'", m["stufe_grund"])

    def test_rand_nur_ueber_auftraggeber(self):
        m = vm.match(kand(title="Bauaufnahme", cpv={"71000000"}, buyer_id="DE138408360"), PROFIL)
        self.assertTrue(m["hit"])
        self.assertEqual(m["stufe"], 3)
        self.assertIn("Auftraggeber", m["stufe_grund"])

    def test_auftraggeber_ohne_stufe_mit_schalter_kein_treffer(self):
        p = copy.deepcopy(PROFIL)
        del p["stufen"][2]["auftraggeber_ohne_muster"]
        m = vm.match(kand(title="Bauaufnahme", cpv={"71000000"}, buyer_id="DE138408360"), p)
        self.assertFalse(m["hit"])
        self.assertIn("keine Stufe", m["reject"])

    def test_stufe_landet_im_kandidaten_und_im_speicher(self):
        c = kand(title="Bildhauerarbeiten am Portal")
        vm.treffer_uebernehmen(c, vm.match(c, PROFIL))
        self.assertEqual((c["stufe"], c["stufe_name"]), (1, "Kern"))
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "hits.db"
            sid = store.record_hits(db, "test-stufen", "2026-09-18", [c], "2026-09-19 06:00")[0][0]
            gespeichert = store.candidate_of(store.get_hit(db, sid, "test-stufen"))
        self.assertEqual(gespeichert["stufe"], 1)
        self.assertIn("bildhauer", gespeichert["stufe_grund"])

    def test_bericht_zeigt_stufe_und_sortiert(self):
        hits = []
        for t in ("Sanierung Fassade aus Naturstein", "Bildhauerarbeiten am Portal"):
            c = kand(title=t)
            vm.treffer_uebernehmen(c, vm.match(c, PROFIL))
            hits.append(c)
        hits[0]["_m"]["score"] = 999      # höherer Score, aber Stufe 2: steht trotzdem hinten
        r = vm.render_report(hits, ["2026-09-18"], {"now": "x", "candidates": 2, "days_with_data": 1},
                             PROFIL)
        self.assertIn("- Stufe 1 (Kern): **1**", r)
        self.assertIn("- **Stufe:** 2 · Lückenfüller", r)
        self.assertLess(r.index("[Stufe 1] Bildhauerarbeiten"), r.index("[Stufe 2] Sanierung"))

    def test_ohne_stufen_wie_bisher(self):
        p = {"suchraum": {"regionen": ["DE3"], "freitext_muster": ["bildhauer"]}}
        c = kand(title="Bildhauerarbeiten am Portal")
        m = vm.match(c, p)
        self.assertEqual(m, {"hit": True, "reasons": ["Region DE300", "Muster: bildhauer"],
                             "flags": ["Wert nicht angegeben"], "score": 50})
        vm.treffer_uebernehmen(c, m)
        self.assertNotIn("stufe", c)
        r = vm.render_report([c], ["d"], {"now": "x", "candidates": 1, "days_with_data": 1}, p)
        self.assertNotIn("Stufe", r)

    def test_profil_wird_nicht_veraendert(self):
        # sbund_feed schickt freitext_muster als Suchbegriffe; die Regelwörter dürfen nicht hinein.
        p = copy.deepcopy(PROFIL)
        vm.match(kand(title="Kopie einer Skulptur"), p)
        self.assertEqual(p, PROFIL)


class Kontextregel(unittest.TestCase):
    def test_kopie_ohne_kontext_kein_treffer(self):
        for t in ("Lieferung Kopierpapier", "Kopiergeräte für die Verwaltung", "Restaurierung von Akten"):
            m = vm.match(kand(title=t), PROFIL)
            self.assertFalse(m["hit"], t)
            self.assertIn("keine Stufe", m["reject"])

    def test_kopie_mit_kontext_kern(self):
        m = vm.match(kand(title="Herstellung einer Kopie der Skulptur am Brunnen"), PROFIL)
        self.assertTrue(m["hit"])
        self.assertEqual(m["stufe"], 1)
        self.assertIn("kopie + Kontext 'skulptur'", m["stufe_grund"])
        # Das kontextpflichtige Wort erscheint nicht als „Muster: …“
        self.assertFalse(any(r.startswith("Muster:") for r in m["reasons"]))

    def test_kontext_ausserhalb_des_fensters_zaehlt_nicht(self):
        weit = "Kopie " + "x" * 60 + " Skulptur"
        self.assertFalse(vm.match(kand(title=weit), PROFIL)["hit"])

    def test_fundstelle_ist_nicht_ihr_eigener_kontext(self):
        # „skulptur“ braucht Handwerk daneben; das Wort allein trägt sich nicht selbst.
        self.assertFalse(vm.match(kand(title="Vogelskulpturen in der Ausstellung"), PROFIL)["hit"])
        self.assertTrue(vm.match(kand(title="Restaurierung einer Skulptur"), PROFIL)["hit"])

    def test_cpv_nur_mit_kontextwort(self):
        ohne = vm.match(kand(title="Lieferung Kissensteine", cpv={"45262511"}), PROFIL)
        self.assertFalse(ohne["hit"])
        mit = vm.match(kand(title="Treppenanlage", description="Stufen aus Naturstein",
                            cpv={"45262511"}), PROFIL)
        self.assertTrue(mit["hit"])
        self.assertEqual(mit["stufe"], 2)
        self.assertIn("CPV 45262511", mit["stufe_grund"])

    def test_kontext_als_liste(self):
        m = vm.match(kand(title="Transport der Exponate"), PROFIL)
        self.assertEqual(m["stufe"], 3)

    def test_unbekannte_kontextliste_ist_laut(self):
        p = copy.deepcopy(PROFIL)
        p["stufen"][0]["regeln"][0]["kontext"] = "gibtsnicht"
        with self.assertRaises(ValueError):
            vm.match(kand(title="Kopie der Skulptur"), p)


class VergebenAmTitel(unittest.TestCase):
    CFG = {"suchraum": {"regionen": ["DE3"], "freitext_muster": ["skulptur", "auftrag"]}}

    def test_vergeben_im_titel_wird_aussortiert(self):
        for t in ("Vergebener Auftrag: Ertüchtigung der Eingangsbereiche - Restaurierung Skulptur",
                  "Vergebener Auftrag Wartung Skulptur",
                  "Vergebener Auftrag_Skulptur",
                  "Bekanntmachung vergebener Auftrag gem. § 135 GWB Skulptur",
                  "Info über vergebenen Auftrag - Skulptur",
                  "Information über die vergebenen Aufträge Skulptur",
                  "Ex-post-Veröffentlichung Skulptur",
                  "  Ex post Skulptur"):
            m = vm.match(kand(title=t), self.CFG)
            self.assertFalse(m["hit"], t)
            self.assertEqual(m["reject"], "schon vergeben (Titel)", t)
        # gilt auch für Profile mit Stufen
        self.assertFalse(vm.match(kand(title="Vergebener Auftrag: Bildhauerarbeiten"), PROFIL)["hit"])

    def test_offene_auftraege_bleiben(self):
        for t in ("Restaurierung Skulptur",
                  "Information über beabsichtigte Vergabe Skulptur",   # ex ante bleibt
                  "Ersatz für den 2019 vergebenen Auftrag Skulptur",   # nicht am Anfang
                  "Expostauftrag Skulptur"):
            self.assertTrue(vm.match(kand(title=t), self.CFG)["hit"], t)


class VergebenWeitere(unittest.TestCase):
    def test_ex_post_information_nach_paragraf(self):
        for t in ("Information nach § 20 Abs. 3 VOB/A über die Erteilung eines Auftrages, Skulptur",
                  "Information nach § 30 Absatz 1 UVgO über die Erteilung eines Auftrages, Skulptur"):
            self.assertTrue(vm.ist_vergeben_am_titel(t), t)
        self.assertFalse(vm.ist_vergeben_am_titel("Information nach § 19 VOB/A an die Bieter"))

    def test_gemeinsame_regel(self):
        self.assertTrue(vm.ist_schon_vergeben({"form_type": "result"}))
        self.assertTrue(vm.ist_schon_vergeben({"procedure_type": "Ex post Veröffentlichung"}))
        self.assertTrue(vm.ist_schon_vergeben({"title": "Vergebener Auftrag: X"}))
        self.assertFalse(vm.ist_schon_vergeben({"form_type": "competition", "title": "X"}))

    def test_waechter_zaehlt_titel_als_vergeben(self):
        import waechter
        self.assertTrue(waechter._vergeben({"form_type": "competition",
                                            "title": "Vergebener Auftrag: Baumpflege"}))
        self.assertFalse(waechter._vergeben({"form_type": "competition", "title": "Baumpflege"}))


class Suchraum(unittest.TestCase):
    def test_ohne_stufen_wie_freitext_und_cpv(self):
        p = {"suchraum": {"freitext_muster": ["Fassade"], "cpv_codes": ["45443000"]}}
        self.assertEqual(vm.suchwoerter(p), ["fassade"])
        self.assertEqual(vm.such_cpv(p), ["45443000"])

    def test_mit_stufen_auch_regelwoerter_und_stufen_cpv(self):
        self.assertIn("kopie", vm.suchwoerter(PROFIL))
        self.assertIn("transport", vm.suchwoerter(PROFIL))
        self.assertEqual(vm.such_cpv(PROFIL), ["45262510"])


class KanalServiceBund(unittest.TestCase):
    """sbund_feed.filtere: match() entscheidet, Stufe landet im Kandidaten, kein
    Zweig „nur Volltext“ mehr."""

    def archiv(self, d: Path, zeilen: list[tuple[str, str, str]]) -> Path:
        import sbund_feed
        a = d / "a.db"
        c = sqlite3.connect(a)
        c.executescript(sbund_feed._SCHEMA)
        for guid, titel, begriffe in zeilen:
            c.execute("INSERT INTO sbund (guid, plattform, titel, stelle, plz, ort, erfuellungsort, "
                      "begriffe, im_text, erst_gesehen, zuletzt) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (guid, "vmp-bb", titel, "Stadt", "", "", "", begriffe, 0,
                       "2026-11-03T06:00:00", "2026-11-03T06:00:00"))
        c.commit()
        c.close()
        return a

    def test_stufe_und_kontext(self):
        import sbund_feed
        with tempfile.TemporaryDirectory() as d:
            a = self.archiv(Path(d), [("g1", "Kopie der Sandsteinskulptur am Portal", ""),
                                      ("g2", "Lieferung Kopierpapier", "kopie"),
                                      ("g3", "Vergebener Auftrag: Bildhauerarbeiten", "bildhauer"),
                                      ("g4", "Reinigung Bürogebäude", "bildhauer")])   # nur Volltext
            t = {k["notice_id"]: k for k in sbund_feed.filtere(a, PROFIL)}
        self.assertEqual(set(t), {"g1"})
        self.assertEqual(t["g1"]["stufe"], 1)
        self.assertNotIn("nur_volltext", t["g1"])

    def test_ohne_stufen_wie_bisher_bei_sichtbarem_begriff(self):
        import sbund_feed
        p = {"mandant": "x", "suchraum": {"regionen": ["DE3"], "freitext_muster": ["fassade"]}}
        with tempfile.TemporaryDirectory() as d:
            a = self.archiv(Path(d), [("g1", "Fassadenreinigung Rathaus", "fassade")])
            t = sbund_feed.filtere(a, p)
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0]["_m"]["reasons"], ["Muster: fassade"])
        self.assertNotIn("stufe", t[0])


class KanalVmp(unittest.TestCase):
    """vmp_opendata.filtere: Regelwörter kommen durch die Vorsortierung, die Stufe
    landet im Kandidaten."""

    def test_regelwort_mit_kontext_und_stufe(self):
        import vmp_opendata
        xml = ("<NOTICE><OBJECT_CONTRACT><SHORT_DESC>Restaurierung einer Skulptur aus Sandstein"
               "</SHORT_DESC></OBJECT_CONTRACT></NOTICE>")
        with tempfile.TemporaryDirectory() as d:
            c = vmp_opendata._conn(Path(d) / "a.db")
            c.execute("INSERT INTO bekanntmachungen (id, quelle, regel, art, titel, stelle, plz, ort, frist, "
                      "cpv, erst_gesehen, zuletzt, xml) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      ("CX1", "test", "UVGO", "Öffentliche Ausschreibung", "Leistungen am Denkmal",
                       "Stadt", "", "", "", "", "2026-11-03", "2026-11-03", xml))
            t = vmp_opendata.filtere(c, PROFIL)
            c.close()
        self.assertEqual(len(t), 1)
        self.assertEqual((t[0]["stufe"], t[0]["stufe_name"]), (1, "Kern"))


class FristMarkieren(unittest.TestCase):
    def test_kurze_frist_wird_markiert(self):
        h = vm.frist_knapp(kand(pub_date="2026-09-18", angebotsfrist="2026-09-24T11:30:00"), PROFIL)
        self.assertEqual(h, "Frist unter 7 Tagen: 2026-09-24, 6 Tage nach Veröffentlichung")

    def test_genug_vorlauf(self):
        self.assertIsNone(vm.frist_knapp(kand(pub_date="2026-09-18", angebotsfrist="2026-09-25"), PROFIL))

    def test_nur_mit_schalter(self):
        p = copy.deepcopy(PROFIL)
        del p["schwellen"]["vorlaufzeit_markieren"]
        self.assertIsNone(vm.frist_knapp(kand(angebotsfrist="2026-09-20"), p))

    def test_ohne_frist_kein_flag(self):
        self.assertIsNone(vm.frist_knapp(kand(), PROFIL))
        self.assertIsNone(vm.frist_knapp(kand(angebotsfrist="unbekannt"), PROFIL))

    def test_rueckfaelle_oeffnung_und_exporttag(self):
        self.assertIsNotNone(vm.frist_knapp(kand(opening="2026-09-20 10:00"), PROFIL))
        self.assertIsNotNone(vm.frist_knapp(kand(pub_date="", _pubday="2026-09-18",
                                                 angebotsfrist="2026-09-20"), PROFIL))

    def test_markieren_verwirft_nicht(self):
        # Ohne als_filter bleibt zu_knapp aus: markiert wird, verworfen nicht.
        import datetime as dt
        self.assertFalse(vm.zu_knapp(kand(angebotsfrist="2026-09-20"), PROFIL, dt.date(2026, 9, 18)))


if __name__ == "__main__":
    unittest.main()
