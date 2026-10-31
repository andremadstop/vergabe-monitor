"""Tests der Kanäle ohne Netz: Bewertung, eForms-Fristen, Kontakte aus dem Archiv,
Wächter, Treffer-Speicher und das sichere Lesen fremden XMLs."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

import vergabe_monitor as vm


class Matcher(unittest.TestCase):
    """vergabe_monitor.match: vergebene raus, Auftraggeber, Ortsersatz (opt-in)."""

    def kand(self, **x):
        k = {"title": "Bauaufnahme Kavalierhaus", "description": "", "nuts": "DE404", "cpv": {"71000000"},
             "value": "", "currency": "", "sme_suitable": "", "form_type": "competition",
             "notice_type": "cn-standard", "buyer": "Stiftung Preußische Schlösser und Gärten",
             "buyer_id": "DE138408360", "buyer_nuts": "DE404"}
        k.update(x)
        return k

    CFG = {"suchraum": {"regionen": ["DE3", "DE4"], "cpv_codes": [], "freitext_muster": [],
                        "auftraggeber": [{"name": "Stiftung Preußische Schlösser und Gärten",
                                          "kennung": "DE138408360", "kurz": "SPSG",
                                          "cpv_codes": ["71000000"]}]}}

    def test_zuschlaege_sind_keine_treffer(self):
        for form in ("result", "cont-modif", "dir-awa-pre"):
            self.assertFalse(vm.match(self.kand(form_type=form), self.CFG)["hit"], form)

    def test_vmp_ex_post_raus_ex_ante_bleibt(self):
        cfg = {"suchraum": {"regionen": ["DE4"], "freitext_muster": ["bauaufnahme"]}}
        for art in ("Ex post Veröffentlichung", "Ex post Veröffentlichung (§ 30 Abs. 1)",
                    "Bekanntmachung vergebener Aufträge"):
            k = self.kand(quelle_art="vmp-opendata", form_type=None, procedure_type=art)
            self.assertFalse(vm.match(k, cfg)["hit"], art)
        for art in ("Ex ante Veröffentlichung (Binnenmarktrelevanz)", "Öffentliche Ausschreibung"):
            k = self.kand(quelle_art="vmp-opendata", form_type=None, procedure_type=art)
            self.assertTrue(vm.match(k, cfg)["hit"], art)

    def test_auftraggeber_mit_cpv_bereich(self):
        self.assertTrue(vm.match(self.kand(), self.CFG)["hit"])
        self.assertFalse(vm.match(self.kand(cpv={"45262510"}), self.CFG)["hit"])   # Bau zaehlt nicht
        self.assertTrue(vm.match(self.kand(buyer_id="", buyer="SPSG Stiftung Preussische Schloesser "
                                                             "und Gaerten Berlin-Brandenburg"), self.CFG)["hit"])

    def test_ohne_auftraggeber_schluessel_unveraendert(self):
        cfg = {"suchraum": {"regionen": ["DE4"], "cpv_codes": [], "freitext_muster": ["bauaufnahme"]}}
        self.assertTrue(vm.match(self.kand(nuts=""), cfg)["hit"])      # Ort unbekannt bleibt Kandidat

    def test_ortsersatz_nur_mit_schalter(self):
        cfg = {"suchraum": {"regionen": ["DE4"], "freitext_muster": ["bauaufnahme"],
                            "ort_ersatz_vergabestelle": True}}
        self.assertTrue(vm.match(self.kand(nuts="", buyer_nuts="DE404"), cfg)["hit"])
        self.assertFalse(vm.match(self.kand(nuts="", buyer_nuts="DEE02"), cfg)["hit"])
        self.assertFalse(vm.match(self.kand(nuts="", buyer_nuts=""), cfg)["hit"])

    def test_ortsersatz_verwirft_andere_kanaele_nicht(self):
        # service.bund.de / Vergabemarktplaetze: kein buyer_nuts, nuts bei mehrdeutigem
        # Ort leer. Die duerfen nicht still wegfallen.
        cfg = {"suchraum": {"regionen": ["DE4"], "freitext_muster": ["bauaufnahme"],
                            "ort_ersatz_vergabestelle": True}}
        for art in ("service.bund.de", "vmp-opendata"):
            k = self.kand(nuts="", quelle_art=art)
            k.pop("buyer_nuts")
            m = vm.match(k, cfg)
            self.assertTrue(m["hit"], art)
            self.assertIn("Ort unbekannt (nicht verworfen)", m["flags"])


EFORMS = """<?xml version="1.0" encoding="UTF-8"?>
<ContractNotice xmlns="urn:oasis:names:specification:ubl:schema:xsd:ContractNotice-2"
  xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
  xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
 <cac:ProcurementProjectLot><cac:TenderingTerms>
  <cac:CallForTendersDocumentReference><cac:Attachment><cac:ExternalReference>
   <cbc:URI>{uri}</cbc:URI>
  </cac:ExternalReference></cac:Attachment></cac:CallForTendersDocumentReference>
 </cac:TenderingTerms>
 <cac:TenderingProcess><cac:TenderSubmissionDeadlinePeriod>
  <cbc:EndDate>2026-10-27+01:00</cbc:EndDate><cbc:EndTime>08:30:00+01:00</cbc:EndTime>
 </cac:TenderSubmissionDeadlinePeriod></cac:TenderingProcess>
 </cac:ProcurementProjectLot>
</ContractNotice>"""


class Unterlagen(unittest.TestCase):
    """enrich.from_eforms liest den Plattform-Link, unterlagen_nachtragen ergänzt ihn."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raw = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def paket(self, nid, uri):
        with zipfile.ZipFile(self.raw / "eforms-2026-10-15.zip", "w") as z:
            z.writestr(f"{nid}-01.xml", EFORMS.format(uri=uri))

    def test_link_und_frist(self):
        import enrich
        self.paket("n1", "https://www.meinauftrag.rib.de/public/DetailsByPlatformIdAndTenderId/platformId/2/tenderId/210643")
        r = enrich.from_eforms("n1", "2026-10-15", self.raw)
        self.assertTrue(r["unterlagen_url"].endswith("tenderId/210643"))
        self.assertEqual(r["angebotsfrist"], "2026-10-27 08:30")

    def test_kein_http_kein_link(self):
        import enrich
        self.paket("n1", "siehe Vergabeunterlagen")
        self.assertNotIn("unterlagen_url", enrich.from_eforms("n1", "2026-10-15", self.raw))


class KontakteAusArchiv(unittest.TestCase):
    XML = ("<NOTICE><CONTRACTING_BODY><ADDRESS_CONTRACTING_BODY><OFFICIALNAME>TH Wildau</OFFICIALNAME>"
           "<CONTACT_PERSON>Frau Beispiel</CONTACT_PERSON><PHONE>+49 3375 1</PHONE>"
           "<E_MAIL>vergabe@th-wildau.example</E_MAIL></ADDRESS_CONTRACTING_BODY></CONTRACTING_BODY>"
           "<COMPLEMENTARY_INFO><ADDRESS_REVIEW_BODY><E_MAIL>vk@land.example</E_MAIL></ADDRESS_REVIEW_BODY>"
           "</COMPLEMENTARY_INFO></NOTICE>")

    def test_kontakt_und_nachtragen(self):
        import json as _json
        from datetime import date as _d

        import store
        import vmp_opendata
        k = vmp_opendata.kontakt(vmp_opendata.SICHER_XML.fromstring(self.XML))
        self.assertEqual(k, {"kontakt_person": "Frau Beispiel", "kontakt_tel": "+49 3375 1",
                             "kontakt_mail": "vergabe@th-wildau.example"})   # nicht die Vergabekammer
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            arch = sqlite3.connect(d / "a.db")
            arch.execute("CREATE TABLE bekanntmachungen (id TEXT PRIMARY KEY, xml TEXT)")
            arch.execute("INSERT INTO bekanntmachungen VALUES ('CXP9YRUH3F8', ?)", (self.XML,))
            arch.commit()
            heute = _d.today().isoformat()
            store.record_hits(d / "h.db", "ug", heute, [{
                "notice_id": "u1", "lot_id": "", "title": "Vibe", "cpv": set(), "_m": {"score": 50},
                "procedure_type": "beabsichtigte Beschränkte Ausschreibung",
                "unterlagen_url": "https://vergabemarktplatz.brandenburg.de/VMPSatellite/notice/CXP9YRUH3F8"}],
                "2026-10-13 09:20")
            self.assertEqual(vmp_opendata.kontakte_nachtragen(arch, d / "h.db"), 1)
            self.assertEqual(vmp_opendata.kontakte_nachtragen(arch, d / "h.db"), 0)   # schon da
            with sqlite3.connect(d / "h.db") as c:
                kand, erst = c.execute("SELECT candidate, first_seen FROM hits").fetchone()
            self.assertEqual(_json.loads(kand)["kontakt_mail"], "vergabe@th-wildau.example")
            self.assertEqual(erst, "2026-10-13 09:20")


class Waechter(unittest.TestCase):
    """waechter.bewerten: Vergleich mit dem Verlauf derselben Tagesart."""

    @staticmethod
    def lauf(neu=20, ohne_frist=4, kanal_vmp=5, werktag=True, vergeben=0, paket=1000):
        z = {"neu": neu, "vergeben": vergeben, "ohne_frist": ohne_frist, "ohne_ort": 1, "doe": neu,
             "doe_ohne_plattform": 0, "ankuendigungen": 0, "radar_neu": 0}
        return {"tag": "x", "werktag": werktag, "mandanten": {"m": z},
                "kanaele": {"Datenservice": neu, "service.bund.de": 2, "Vergabemarktplätze": kanal_vmp},
                "paket_gestern_bytes": paket}

    def test_rot_ohne_verlauf(self):
        import waechter
        m = waechter.bewerten(self.lauf(vergeben=2, paket=0), [])
        self.assertEqual([x["stufe"] for x in m], ["rot", "rot"])

    def test_ohne_genug_verlauf_keine_vergleichsmeldung(self):
        import waechter
        self.assertEqual(waechter.bewerten(self.lauf(neu=0), [self.lauf()] * 4), [])

    def test_abweichungen(self):
        import waechter
        verlauf = [self.lauf()] * 6
        texte = " | ".join(x["text"] for x in waechter.bewerten(self.lauf(neu=0, kanal_vmp=0), verlauf))
        self.assertIn("keine neuen Treffer", texte)
        self.assertIn("Vergabemarktplätze still", texte)
        texte = " | ".join(x["text"] for x in waechter.bewerten(self.lauf(neu=100, ohne_frist=70), verlauf))
        self.assertIn("100 neue Treffer", texte)
        self.assertIn("70% ohne Frist", texte)

    def test_wochenende_vergleicht_mit_wochenende(self):
        import waechter
        verlauf = [self.lauf()] * 6 + [self.lauf(neu=0, kanal_vmp=0, werktag=False)] * 6
        self.assertEqual(waechter.bewerten(self.lauf(neu=0, kanal_vmp=0, werktag=False), verlauf), [])

    def test_lauf_schreibt_lesbare_dateien(self):
        from datetime import date as _d

        import store
        import waechter
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            store.record_hits(d / "hits.db", "m", "2026-10-16", [{"notice_id": "n1", "lot_id": "", "title": "x",
                              "cpv": set(), "_m": {"score": 80}}], f"{_d.today().isoformat()} 09:20")
            waechter.main(["--hits", str(d / "hits.db"), "--verlauf", str(d / "w.db"), "--aus", str(d / "aus")])
            daten = __import__("json").loads((d / "aus" / "aktuell.json").read_text())
            self.assertEqual(daten["mandanten"]["m"]["neu"], 1)
            self.assertEqual(oct((d / "aus" / "aktuell.json").stat().st_mode & 0o777), "0o640")


class Speicher(unittest.TestCase):
    """store: Kurz-ID stabil, derselbe Treffer bei zwei Profilen zweimal."""

    def test_kurz_id_stabil_und_je_profil(self):
        import store
        treffer = {"notice_id": "n1", "lot_id": "LOT-0001", "title": "x", "cpv": set(), "_m": {"score": 80}}
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "hits.db"
            a = store.record_hits(db, "profil-a", "2026-10-30", [treffer], "2026-10-31 07:00")[0][0]
            b = store.record_hits(db, "profil-b", "2026-10-30", [treffer], "2026-10-31 07:00")[0][0]
            self.assertEqual(a, b)
            self.assertEqual(len(a), 4)
            with sqlite3.connect(db) as c:
                self.assertEqual(c.execute("SELECT count(*) FROM hits").fetchone()[0], 2)
            with self.assertRaises(store.MehrdeutigeKurzID):
                store.get_hit(db, a)
            self.assertEqual(store.get_hit(db, a, "profil-b")["mandant"], "profil-b")


class FremdesXml(unittest.TestCase):
    """Entitäten in fremdem XML werden abgewiesen, nicht aufgelöst (Billion Laughs, XXE)."""

    BOMBE = ('<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaaaaaaaa">'
             '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]><r>&b;</r>')
    XXE = '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>'

    def test_marktplatz_xml_ohne_entitaeten(self):
        import vmp_opendata
        from defusedxml import DefusedXmlException
        for xml in (self.BOMBE, self.XXE):
            with self.assertRaises(DefusedXmlException):
                vmp_opendata.SICHER_XML.fromstring(xml)

    def test_kontakte_nachtragen_ueberspringt_boeses_xml(self):
        from datetime import date as _d

        import store
        import vmp_opendata
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            arch = sqlite3.connect(d / "a.db")
            arch.execute("CREATE TABLE bekanntmachungen (id TEXT PRIMARY KEY, xml TEXT)")
            arch.execute("INSERT INTO bekanntmachungen VALUES ('CXBOESE01', ?)", (self.XXE,))
            arch.commit()
            store.record_hits(d / "h.db", "p", _d.today().isoformat(), [{
                "notice_id": "u1", "lot_id": "", "title": "t", "cpv": set(), "_m": {"score": 50},
                "unterlagen_url": "https://vmp.example/VMPSatellite/notice/CXBOESE01"}], "2026-10-31 07:00")
            self.assertEqual(vmp_opendata.kontakte_nachtragen(arch, d / "h.db"), 0)

    def test_feed_parser_ist_defusedxml(self):
        import sbund_feed
        from defusedxml import DefusedXmlException
        with self.assertRaises(DefusedXmlException):
            sbund_feed.SICHER_XML.fromstring(self.BOMBE)


class NurHttps(unittest.TestCase):
    def test_quelle_ohne_https_wird_nicht_abgerufen(self):
        import vmp_opendata
        with self.assertRaises(ValueError):
            vmp_opendata.hole_quelle({"url": "file:///etc/passwd"}, sqlite3.connect(":memory:"))


if __name__ == "__main__":
    unittest.main()
