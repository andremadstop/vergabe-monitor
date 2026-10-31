#!/usr/bin/env python3
"""
Vergabe-Monitor — unterschwelliger Kanal aus den Vergabemarktplaetzen der Laender

Der Datenservice Oeffentlicher Einkauf fuehrt unterschwellig nur, was eine
Vergabestelle **zusaetzlich freiwillig** dorthin stellt. Wer nur auf dem
Marktplatz seines Landes veroeffentlicht, fehlt. Dieses Modul holt genau die.

Gemessen im August 2026: von 17 passenden NRW-Verfahren hatte der Datenservice eines.

QUELLEN stehen in configs/plattformen.yaml, nicht hier im Code. Ein weiteres Land
ist dann eine Zeile in einer Datei -- und die Datei sagt zugleich, welche Laender
**nicht** gehen und warum. Heute liefert nur NRW (7 Endpunkte, cosinex,
Datenlizenz Deutschland Zero 2.0).

DREI ENTSCHEIDUNGEN, die den Aufbau erklaeren
─────────────────────────────────────────────
1. **Das Rohmaterial wird archiviert, nicht nur die Treffer.**
   Das cosinex-ZIP wird jede Nacht neu erzeugt und das des Vortages geloescht.
   Wer nur speichert, was heute auf die Kriterien passt, verliert jede spaetere
   Kalibrierung rueckwirkend -- und die Kriterien sind ausdruecklich noch nicht
   endgueltig abgestimmt. Deshalb landet jede nationale
   Bekanntmachung im Archiv, und gefiltert wird daraus.

2. **Gematcht wird mit `vergabe_monitor.match()`, nicht mit eigener Logik.**
   Zwei Matcher waeren zwei Wahrheiten. Dass die Quelle ein anderes XML-Format
   hat, ist eine Frage der Abbildung, nicht der Bewertung.

3. **Die Herkunft steht im Kandidaten-JSON, nicht in einer neuen Spalte.**
   `store.record_hits` legt den vollstaendigen Kandidaten als JSON ab; ein Feld
   `quelle` reist damit mit, ohne die laufende hits.db zu veraendern. Ein
   Schemaeingriff waere Risiko ohne Gegenwert.

🔴 DIE FALLE, die diesen Kanal gefaehrlich macht: DER ORT
────────────────────────────────────────────────────────
Die nationalen cosinex-XML enthalten **keinen einzigen NUTS-Code** -- nachgezaehlt
an allen 613 NRW-Bekanntmachungen vom 21.08.2026. Der Regionsfilter des Monitors
arbeitet aber auf NUTS, und er verwirft einen Treffer mit unbekanntem Ort
**nicht** (bewusst so, siehe match()). Ohne Umrechnung liefen also saemtliche
NRW-Verfahren als "Ort unbekannt" durch einen Brandenburg-Filter.

Aufgeloest wird das ueber die PLZ (in 95 % der Faelle vorhanden) gegen die
OpenPLZ-API, die den amtlichen Landesschluessel liefert. Und zwar mit einer
Vorsichtsregel: **verworfen wird nur bei eindeutiger Zuordnung.** Traegt eine PLZ
Orte in mehreren Laendern, bleibt der Treffer drin und wird markiert. Lieber ein
Treffer zu viel mit Hinweis als einer zu wenig ohne.

Aufruf:
    vmp_opendata.py --quellen configs/plattformen.yaml \\
                    --archiv /var/lib/vergabe-monitor/vmp_archiv.db \\
                    --config configs/beispiel-geodaten-it.yaml \\
                    --db /var/lib/vergabe-monitor/hits.db
    vmp_opendata.py --quellen ... --archiv ... --nur-holen     # nur archivieren
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET  # nosec B405 -- nur Typen und ParseError; geparst wird mit defusedxml
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import defusedxml.ElementTree as SICHER_XML
from defusedxml import DefusedXmlException

sys.path.insert(0, str(Path(__file__).resolve().parent))
import store  # noqa: E402
from vergabe_monitor import match  # noqa: E402

OPENPLZ = "https://openplzapi.org/de/"
UA = {"User-Agent": "vergabe-monitor/1.0 (+https://github.com/andremadstop/vergabe-monitor)"}

# Amtlicher Regionalschluessel des Landes -> NUTS-1. Feste, seit Jahrzehnten
# unveraenderte Zuordnung; steht hier ausgeschrieben, damit sie pruefbar ist und
# nicht in einer Rechenvorschrift versteckt.
AGS_NUTS = {
    "01": ("DEF", "Schleswig-Holstein"),   "02": ("DE6", "Hamburg"),
    "03": ("DE9", "Niedersachsen"),        "04": ("DE5", "Bremen"),
    "05": ("DEA", "Nordrhein-Westfalen"),  "06": ("DE7", "Hessen"),
    "07": ("DEB", "Rheinland-Pfalz"),      "08": ("DE1", "Baden-Wuerttemberg"),
    "09": ("DE2", "Bayern"),               "10": ("DEC", "Saarland"),
    "11": ("DE3", "Berlin"),               "12": ("DE4", "Brandenburg"),
    "13": ("DE8", "Mecklenburg-Vorpommern"), "14": ("DED", "Sachsen"),
    "15": ("DEE", "Sachsen-Anhalt"),       "16": ("DEG", "Thueringen"),
}

_ARCHIV_SCHEMA = """
-- Jede nationale Bekanntmachung, die je gesehen wurde. Die Ausschreibungs-ID ist
-- laut cosinex ueber alle Installationen eindeutig -- Dubletten zwischen den
-- sieben NRW-Endpunkten faellt dieser Schluessel also von allein weg.
CREATE TABLE IF NOT EXISTS bekanntmachungen (
  id           TEXT PRIMARY KEY,
  quelle       TEXT NOT NULL,
  land         TEXT,
  regel        TEXT,              -- UVGO | VOB | ...
  art          TEXT,              -- Verfahrensart im Klartext
  titel        TEXT,
  stelle       TEXT,
  plz          TEXT,
  ort          TEXT,
  frist        TEXT,              -- Angebotsfrist, ISO, aus RECEIPT_TENDER
  cpv          TEXT,              -- kommagetrennt
  erst_gesehen TEXT NOT NULL,
  zuletzt      TEXT NOT NULL,
  xml          TEXT NOT NULL      -- das Original. Ohne es waere jede spaetere
);                                -- Kalibrierung nicht rueckwirkend pruefbar.
CREATE INDEX IF NOT EXISTS idx_bkm_quelle ON bekanntmachungen(quelle, erst_gesehen);

-- Ortsaufloesung, damit nicht jeder Lauf dieselben Orte erneut abfragt.
-- schluessel ist "plz:14473" oder "ort:potsdam" -- beide Wege werden gebraucht,
-- siehe ort_laender(). 'laender' ist kommagetrennt: mehr als einer = mehrdeutig.
CREATE TABLE IF NOT EXISTS plz_land (
  schluessel TEXT PRIMARY KEY,
  laender    TEXT NOT NULL,
  gefragt    TEXT NOT NULL
);
"""


def _conn(db: Path) -> sqlite3.Connection:
    db.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(db, timeout=30)
    c.row_factory = sqlite3.Row
    c.executescript(_ARCHIV_SCHEMA)
    return c


# ------------------------------------------------------------------ Ort ------

def _frag(c: sqlite3.Connection, schluessel: str, url: str,
          genau: str | None = None, pause: float = 0.2) -> list[str]:
    """Eine Ortsabfrage gegen OpenPLZ, mit Zwischenspeicher.

    `genau` erzwingt Namensgleichheit. Die Namenssuche von OpenPLZ arbeitet auf
    Teilstrings: "Minden" liefert auch "Holzminden" in Niedersachsen und macht
    aus einer eindeutigen Lage eine scheinbar mehrdeutige. Eine erfundene
    Mehrdeutigkeit ist hier nicht harmlos -- sie schaltet die Vorsichtsregel ein
    und laesst Treffer stehen, die eindeutig ausserhalb liegen.
    """
    r = c.execute("SELECT laender FROM plz_land WHERE schluessel=?",
                  (schluessel,)).fetchone()
    if r is not None:
        return [x for x in r["laender"].split(",") if x]
    codes: list[str] = []
    try:
        # url beginnt fest mit OPENPLZ (https)
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA),  # nosec B310
                                    timeout=25) as x:
            for eintrag in json.loads(x.read()):
                if genau and str(eintrag.get("name", "")).strip().lower() != genau:
                    continue
                k = str((eintrag.get("federalState") or {}).get("key", ""))
                if k in AGS_NUTS and AGS_NUTS[k][0] not in codes:
                    codes.append(AGS_NUTS[k][0])
    except Exception as e:
        # Nicht zwischenspeichern: ein Netzfehler ist kein Befund.
        print(f"  {schluessel} nicht aufloesbar ({type(e).__name__})", file=sys.stderr)
        return []
    time.sleep(pause)
    with c:
        c.execute("INSERT OR REPLACE INTO plz_land VALUES (?,?,?)",
                  (schluessel, ",".join(codes),
                   datetime.now(UTC).isoformat(timespec="seconds")))
    return codes


def ort_laender(c: sqlite3.Connection, plz: str, ort: str) -> list[str]:
    """NUTS-1-Codes eines Leistungsorts. Mehr als einer = mehrdeutig (dann wird
    nicht verworfen). Leere Liste = nicht aufloesbar (dann auch nicht).

    🔴 Zwei Wege, und der zweite ist keine Bequemlichkeit:
    Die PLZ allein reicht nicht. **Grosskunden-Postleitzahlen** (eigene PLZ fuer
    Behoerden, Hochschulen, Postfaecher) kennt kein Ortsverzeichnis -- die
    Universitaet zu Koeln traegt 50923, und OpenPLZ antwortet darauf mit einer
    leeren Liste. Dazu kommen 164 von 2.500 NRW-Bekanntmachungen ganz ohne PLZ.
    Beides zusammen liess im ersten Lauf zehn Koelner und Duesseldorfer Verfahren
    durch einen Brandenburg-Filter, obwohl sie eindeutig ausserhalb liegen.
    Der Ortsname ist in 97 % der Faelle da und faengt genau diese Faelle.
    """
    plz = (plz or "").strip()
    if plz.isdigit() and len(plz) == 5:
        codes = _frag(c, f"plz:{plz}",
                      OPENPLZ + "Localities?postalCode=" + urllib.parse.quote(plz))
        if codes:
            return codes
    ort = (ort or "").strip()
    # Zusaetze wie "Potsdam, OT Golm" oder "Koeln (Innenstadt)" abschneiden --
    # das Ortsverzeichnis kennt nur den Namen selbst.
    ort = re.split(r"[,(/]", ort)[0].strip()
    if len(ort) < 3:
        return []
    return _frag(c, f"ort:{ort.lower()}",
                 OPENPLZ + "Localities?name=" + urllib.parse.quote(ort),
                 genau=ort.lower())


# ------------------------------------------------------------- XML lesen -----

def _txt(el) -> str:
    if el is None:
        return ""
    return " ".join(t.strip() for t in el.itertext() if t and t.strip())


def kontakt(d) -> dict:
    """Ansprechperson, Telefon und Mail der Vergabestelle aus dem nationalen Formular.

    Gebraucht für Ankündigungen (Ex ante): dort meldet man Interesse direkt bei der
    Vergabestelle an. Gemessen 16.10.2026: alle 196 Ex-ante-Bekanntmachungen im Archiv
    nennen eine Mailadresse. Es wird nur übernommen, was die Vergabestelle selbst
    veröffentlicht hat, und nur die erste Kontaktstelle (die Vergabestelle, nicht die
    Nachprüfungsstelle weiter unten im Formular).
    """
    out: dict = {}
    for el in d.iter():
        tag = el.tag.split("}")[-1]
        if tag == "E_MAIL" and "kontakt_mail" not in out and _txt(el):
            out["kontakt_mail"] = _txt(el)
            break
        if tag == "CONTACT_PERSON" and "kontakt_person" not in out and _txt(el):
            out["kontakt_person"] = _txt(el)
        if tag == "PHONE" and "kontakt_tel" not in out and _txt(el):
            out["kontakt_tel"] = _txt(el)
    return out


def lies_nat(xml_text: str) -> dict | None:
    """NOTICE_NAT -> flaches dict. Gibt None zurueck, wenn es keine nationale
    Bekanntmachung ist (EU-Bekanntmachungen hat der Monitor schon)."""
    try:
        r = SICHER_XML.fromstring(xml_text)
    except (ET.ParseError, DefusedXmlException):
        return None
    if not r.tag.startswith("NOTICE_NAT"):
        return None

    oc = r.find("OBJECT_CONTRACT")
    mp = r.find(".//PLACE_OF_FULLFILMENT/MAINPLACE")
    rt = r.find(".//RECEIPT_TENDER")
    frist = None
    if rt is not None and (rt.findtext("YEAR") or "").strip():
        try:
            frist = (f"{int(rt.findtext('YEAR')):04d}-{int(rt.findtext('MONTH')):02d}-"
                     f"{int(rt.findtext('DAY')):02d}T"
                     f"{int(rt.findtext('HOUR') or 0):02d}:"
                     f"{int(rt.findtext('MIN') or 0):02d}:00")
        except (TypeError, ValueError):
            frist = None

    return {
        "id": r.findtext(".//PROCEDURE/PUBLICATION_ID") or "",
        "titel": (r.findtext(".//OBJECT_CONTRACT/TITLE") or "").strip(),
        "beschreibung": _txt(oc.find("SHORT_DESC")) if oc is not None else "",
        "stelle": (r.findtext(".//ADDRESS_CONTRACTING_BODY/OFFICIALNAME") or "").strip(),
        "plz": (mp.findtext("POSTAL_CODE") if mp is not None else "") or "",
        "ort": (mp.findtext("TOWN") if mp is not None else "") or "",
        "cpv": [c.text.strip() for c in r.iter("CPV_CODE") if c.text],
        "regel": r.findtext(".//PROCEDURE/CONTRACTING_RULE") or "",
        "art": r.findtext(".//PROCEDURE/PROCEDURE_TYPE_LABEL") or "",
        "frist": frist,
        "url": (r.findtext(".//ADDRESS_TENDERS_REQUEST/URL_PARTICIPATION")
                or r.findtext(".//ADDRESS_DOCUMENTS/DOCUMENT_URL") or ""),
    }


# ------------------------------------------------------------------ holen ----

def hole_quelle(q: dict, c: sqlite3.Connection) -> tuple[int, int]:
    """Laedt ein Quell-ZIP und legt alle nationalen Bekanntmachungen ins Archiv.
    Rueckgabe: (nationale im Paket, davon neu)."""
    jetzt = datetime.now(UTC).isoformat(timespec="seconds")
    if not str(q["url"]).startswith("https://"):
        raise ValueError(f"nur https-Quellen: {q['url']}")
    req = urllib.request.Request(q["url"], headers=UA)
    with urllib.request.urlopen(req, timeout=180) as x:  # nosec B310 -- Schema oben geprüft
        roh = x.read()
    zf = zipfile.ZipFile(io.BytesIO(roh))

    namen = [n for n in zf.namelist() if n.lower().endswith(".xml")
             and not n.lower().endswith("index.xml")]
    nat = neu = 0
    with c:
        for name in namen:
            text = zf.read(name).decode("utf-8", "replace")
            d = lies_nat(text)
            if not d or not d["id"]:
                continue
            nat += 1
            vorher = c.execute("SELECT 1 FROM bekanntmachungen WHERE id=?",
                               (d["id"],)).fetchone()
            if vorher is None:
                neu += 1
            c.execute("""
                INSERT INTO bekanntmachungen
                  (id,quelle,land,regel,art,titel,stelle,plz,ort,frist,cpv,
                   erst_gesehen,zuletzt,xml)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                  zuletzt=excluded.zuletzt, frist=excluded.frist, xml=excluded.xml
            """, (d["id"], q["name"], q.get("land"), d["regel"], d["art"], d["titel"],
                  d["stelle"], d["plz"], d["ort"], d["frist"], ",".join(d["cpv"]),
                  jetzt, jetzt, text))
    return nat, neu


# --------------------------------------------------------------- abbilden ----

def als_kandidat(zeile: sqlite3.Row, nuts: list[str], mehrdeutig: bool) -> dict:
    """Auf die Feldnamen, die vergabe_monitor.match() erwartet.

    `value` bleibt leer: die nationalen Formulare nennen keinen Auftragswert.
    match() flaggt das als "Wert nicht angegeben" und verwirft nicht -- richtig,
    denn ein fehlender Wert ist keine Aussage ueber die Groesse.
    """
    return {
        "notice_id": zeile["id"],
        "lot_id": "",
        "title": zeile["titel"] or "",
        "description": zeile["ort"] or "",   # Freitext siehe unten
        "value": "",
        "currency": "",
        "cpv": [x for x in (zeile["cpv"] or "").split(",") if x],
        "city": zeile["ort"] or "",
        "plz": zeile["plz"] or "",
        "nuts": nuts[0] if (nuts and not mehrdeutig) else "",
        "buyer": zeile["stelle"] or "",
        "buyer_url": "",
        "procedure_type": zeile["art"] or "",
        "legal_basis": f"de-{(zeile['regel'] or '').lower()}",
        "opening": "",
        "angebotsfrist": zeile["frist"] or "",
        "sme_suitable": "",
        "pub_date": (zeile["erst_gesehen"] or "")[:10],
        "quelle": zeile["quelle"],
        "quelle_art": "vmp-opendata",
    }


def filtere(c: sqlite3.Connection, cfg: dict, seit: str | None = None) -> list[dict]:
    """Archiv gegen ein Suchprofil. Nutzt match() unveraendert."""
    regionen = [r.upper() for r in cfg.get("suchraum", {}).get("regionen", [])]
    wo = "WHERE erst_gesehen >= ?" if seit else ""
    # wo ist einer von zwei festen Bausteinen, der Wert geht als Parameter
    zeilen = c.execute(f"SELECT * FROM bekanntmachungen {wo} ORDER BY erst_gesehen DESC",  # nosec B608
                       (seit,) if seit else ()).fetchall()
    # Billig vor teuer: erst lokal pruefen, ob CPV oder Freitext ueberhaupt
    # ansprechen, und nur fuer die Ueberlebenden die PLZ aufloesen. match()
    # wuerde die Region zuerst pruefen -- die Ergebnismenge ist dieselbe, aber
    # ohne diese Vorsortierung gaebe es je Lauf tausende Anfragen an eine fremde,
    # kostenlose API fuer Zeilen, die ohnehin durchfallen.
    sr = cfg.get("suchraum", {})
    praefixe = tuple(str(x).strip() for x in sr.get("cpv_codes", []))
    muster = [m.lower() for m in sr.get("freitext_muster", [])]

    treffer = []
    for z in zeilen:
        cpvs = [x for x in (z["cpv"] or "").split(",") if x]
        grob = f"{z['titel'] or ''} {z['stelle'] or ''}".lower()
        if not (any(cv.startswith(praefixe) for cv in cpvs) if praefixe else False):
            # Freitext kann auch in der Leistungsbeschreibung stehen -- die liegt
            # nur im XML, deshalb hier einmal hineinschauen statt zu verwerfen.
            if muster and not any(m in grob for m in muster):
                voll = (z["xml"] or "").lower()
                if not any(m in voll for m in muster):
                    continue
            elif not muster:
                continue

        nuts = ort_laender(c, z["plz"], z["ort"])
        mehrdeutig = len(nuts) > 1

        # Eindeutig ausserhalb -> gar nicht erst bewerten. Das ist die einzige
        # Stelle, an der dieser Kanal etwas verwirft, und sie verlangt Eindeutigkeit.
        if nuts and not mehrdeutig and regionen:
            if not any(nuts[0].startswith(r) for r in regionen):
                continue

        # Der Freitext des Monitors liest title+description. Beim Datenservice ist
        # description die Leistungsbeschreibung -- hier muss sie dasselbe sein,
        # sonst matcht derselbe Filter auf zwei verschiedene Textmengen.
        d = SICHER_XML.fromstring(z["xml"])
        oc = d.find("OBJECT_CONTRACT")
        beschreibung = _txt(oc.find("SHORT_DESC")) if oc is not None else ""

        k = als_kandidat(z, nuts, mehrdeutig)
        k["description"] = beschreibung
        k.update(kontakt(d))
        m = match(k, cfg)
        if not m.get("hit"):
            continue
        # 🔴 Der Hinweis muss in den KANDIDATEN, nicht nur in `_m`:
        # store.record_hits speichert das Kandidaten-JSON und laesst `_m` fallen
        # (nur der Punktwert wird uebernommen). Ein Warnhinweis, der nur in `_m`
        # steht, erreicht das Portal also nie -- und dort wuerde ein Treffer mit
        # unsicherem Ort wie ein gesicherter aussehen.
        if mehrdeutig:
            hinweis = (f"Ort nicht eindeutig: {z['ort'] or z['plz']} liegt in "
                       f"mehreren Bundeslaendern ({', '.join(nuts)}) — "
                       f"nicht verworfen")
            m.setdefault("flags", []).append("⚠ " + hinweis)
            k["ort_unsicher"] = hinweis
        elif not nuts:
            k["ort_unsicher"] = ("Leistungsort nicht bestimmbar (weder PLZ noch "
                                 "Ortsname auswertbar) — nicht verworfen")
            m.setdefault("flags", []).append("⚠ " + k["ort_unsicher"])
        k["_m"] = m
        treffer.append(k)
    return treffer


_VMP_ID = re.compile(r"/notice/(CX[0-9A-Z]{6,})")


def kontakte_nachtragen(c: sqlite3.Connection, hits_db: Path, tage: int = 120) -> int:
    """Ankündigungen aus dem Datenservice nennen im eForms-XML meist keine Mailadresse
    (gemessen: 0 von 14 beim UG-Profil), verweisen aber fast immer auf einen
    Vergabemarktplatz (/notice/CX…). Liegt dieselbe Bekanntmachung im Archiv, wird
    deren Kontakt übernommen. Ändert nur die kontakt_*-Felder, nie first_seen."""
    ab = (datetime.now(UTC).date() - timedelta(days=tage)).isoformat()
    n = 0
    with sqlite3.connect(hits_db, timeout=30) as h:
        zeilen = h.execute("SELECT mandant, short_id, candidate FROM hits WHERE pubday >= ? "
                           "AND candidate LIKE '%unterlagen_url%'", (ab.replace("-", ""),)).fetchall() \
            + h.execute("SELECT mandant, short_id, candidate FROM hits WHERE pubday >= ? "
                        "AND candidate LIKE '%unterlagen_url%'", (ab,)).fetchall()
        for mandant, sid, roh in {(m, s_, r) for m, s_, r in zeilen}:
            k = json.loads(roh)
            if k.get("quelle_art") is not None or k.get("kontakt_mail"):
                continue
            m = _VMP_ID.search(k.get("unterlagen_url") or "")
            if not m:
                continue
            z = c.execute("SELECT xml FROM bekanntmachungen WHERE id=?", (m.group(1),)).fetchone()
            if not z or not z[0]:
                continue
            try:
                kt = kontakt(SICHER_XML.fromstring(z[0]))
            except (ET.ParseError, DefusedXmlException):
                continue
            if not kt.get("kontakt_mail"):
                continue
            k.update(kt)
            h.execute("UPDATE hits SET candidate=? WHERE mandant=? AND short_id=?",
                      (json.dumps(k, ensure_ascii=False), mandant, sid))
            n += 1
    return n


# ---------------------------------------------------------------------- CLI --

def _yaml(p: Path) -> dict:
    try:
        import yaml  # type: ignore
    except ImportError as e:
        print("PyYAML fehlt.", file=sys.stderr)
        raise SystemExit(2) from e
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Unterschwelliger Kanal aus den Vergabemarktplaetzen der Laender")
    ap.add_argument("--quellen", required=True, type=Path)
    ap.add_argument("--archiv", required=True, type=Path)
    ap.add_argument("--config", action="append", default=[], type=Path,
                    help="Suchprofil, mehrfach angebbar")
    ap.add_argument("--db", type=Path, help="hits.db — ohne sie wird nur archiviert")
    ap.add_argument("--nur-holen", action="store_true")
    a = ap.parse_args()

    quellen = [q for q in _yaml(a.quellen)["quellen"] if q.get("status") == "offen"]
    if not quellen:
        print("Keine Quelle mit status: offen. Nichts zu tun.", file=sys.stderr)
        return 0

    c = _conn(a.archiv)
    vorher = c.execute("SELECT count(*) FROM bekanntmachungen").fetchone()[0]
    print(f"Archiv vorher: {vorher} nationale Bekanntmachungen", file=sys.stderr)

    for q in quellen:
        try:
            nat, neu = hole_quelle(q, c)
            print(f"  {q['name']:38} {nat:5} national, {neu:5} neu", file=sys.stderr)
        except Exception as e:
            # Ein toter Endpunkt darf die anderen sechs nicht mitnehmen.
            print(f"  {q['name']:38} FEHLER {type(e).__name__}: {e}", file=sys.stderr)

    nachher = c.execute("SELECT count(*) FROM bekanntmachungen").fetchone()[0]
    print(f"Archiv nachher: {nachher} (+{nachher - vorher})", file=sys.stderr)

    if a.nur_holen or not a.config:
        return 0

    jetzt = datetime.now(UTC).isoformat(timespec="seconds")
    pubday = jetzt[:10].replace("-", "")
    for pfad in a.config:
        cfg = _yaml(pfad)
        treffer = filtere(c, cfg)
        print(f"\n{cfg['mandant']}: {len(treffer)} Treffer aus dem "
              f"unterschwelligen Kanal", file=sys.stderr)
        for t in treffer[:12]:
            frist = t["angebotsfrist"][:16] or "ohne Frist"
            print(f"   [{t['_m']['score']:3}] {t['title'][:58]}", file=sys.stderr)
            print(f"         {t['buyer'][:44]} | {frist} | {t['quelle']}",
                  file=sys.stderr)
        if a.db and treffer:
            store.record_hits(a.db, cfg["mandant"], pubday, treffer, jetzt)
            # Trap 2: nachzaehlen statt Erfolg melden.
            with sqlite3.connect(a.db) as h:
                n = h.execute(
                    "SELECT count(*) FROM hits WHERE mandant=? AND candidate LIKE ?",
                    (cfg["mandant"], '%"quelle_art": "vmp-opendata"%')).fetchone()[0]
            print(f"   -> in hits.db aus diesem Kanal: {n}", file=sys.stderr)
    if a.db:
        try:
            print(f"Kontakte für Ankündigungen aus dem Datenservice: "
                  f"{kontakte_nachtragen(c, a.db)} ergänzt", file=sys.stderr)
        except Exception as ex:  # noqa: BLE001 -- Zugabe, darf den Kanal nicht stoppen
            print(f"Kontakte nachtragen gescheitert: {ex}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
