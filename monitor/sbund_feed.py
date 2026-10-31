#!/usr/bin/env python3
"""
Vergabe-Monitor — Kanal 4: der Ausschreibungs-Feed von service.bund.de

🔑 NICHT der allgemeine Feed, sondern EIN FEED JE SUCHBEGRIFF.
   Der allgemeine Feed (RSSGenerator_Ausschreibungen.xml) traegt 500 Eintraege,
   aber **nur Titel und Erfuellungsort** -- im Median 55 plus 15 Zeichen. Gegen
   die echten Suchmuster dreier Profile gemessen: **null Treffer**, und zwar
   nicht wegen des Marktes, sondern weil es fast nichts zu matchen gibt.

   Die Suchmaske derselben Seite liefert denselben Feed **gefiltert**, und das
   aendert zwei Dinge grundlegend:
     · Gesucht wird im **Volltext der Bekanntmachung**, nicht nur im Titel.
     · Die Beschreibung traegt dann **Angebotsfrist** -- die echte Frist, die im
       allgemeinen Feed fehlt. Dort steht nur „Veroeffentlichungsende", und das
       ist ein anderer, spaeterer Termin.
   Gemessen: „Vermessung" -> 19 Treffer, „Fassade" -> 100.

Der Feed aggregiert ueber zwanzig Vergabeplattformen, und der Link verraet
je Eintrag, welche:

    /IMPORTE/Ausschreibungen/vmp-bb/...       Vergabemarktplatz Brandenburg
    /IMPORTE/Ausschreibungen/vmp-nds/...      Niedersachsen
    /IMPORTE/Ausschreibungen/vmp-egomv/...    Mecklenburg-Vorpommern
    /IMPORTE/Ausschreibungen/dtvp/...         Deutsches Vergabeportal
    /IMPORTE/Ausschreibungen/subreport/...    subreport
    /IMPORTE/Ausschreibungen/healyhudson/...  Healy Hudson
    ...

Genau die Plattformen also, fuer die unsere Recherche „keine Schnittstelle
gefunden" notiert hatte. Ueber diesen Feed sind sie heute erreichbar.

⚠️ WIE VIEL DAVON NEU IST — gemessen am 21.08.2026, nicht behauptet
   55 Eintraege aus vmp-bb, vmp-nds, vmp-egomv und dtvp gegen fuenf
   Datenservice-Tagespakete abgeglichen: **47 waren schon da, 8 nicht.**
   Der Feed ist also groesstenteils redundant — das bestaetigt, dass
   service.bund.de in den Datenservice einfliesst. Aber eben nicht vollstaendig.
   Der Kanal ersetzt nichts, er ergaenzt am Rand. Wer ihn als „jetzt haben wir
   Brandenburg" verkauft, verkauft zu viel.

🔴 ZWEI FALLEN, DIE DIESE QUELLE MITBRINGT
──────────────────────────────────────────
1. **Es gibt keine CPV-Codes.** Der Feed traegt nur Titel, Erfuellungsort,
   Vergabestelle und ein Datum. Gematcht werden kann also **ausschliesslich ueber
   Freitext**. Fuer eine Config, die ihre Trennschaerfe aus CPV zieht, faellt
   damit die Haelfte der Bewertung weg — die Treffer sind unschaerfer als die aus
   dem Datenservice. Deshalb tragen sie ein eigenes Herkunftsmerkmal.

2. **„Veroeffentlichungsende" ist NICHT die Angebotsfrist.** Es ist der Tag, an
   dem die Bekanntmachung von der Seite verschwindet. Der liegt regelmaessig
   Wochen nach der Angebotsfrist. Genau derselbe Fehler wie `publicOpeningDate`
   im Datenservice, der am 21.08. aufgefallen ist — und er ist
   teuer, weil eine zu spaete Frist zur Untaetigkeit einlaedt. Der Wert wird
   deshalb als `veroeffentlichungsende` gefuehrt und **nie** als `angebotsfrist`.

Der Feed ist ein rollendes Fenster von rund zwei Tagen. Wer ihn nicht taeglich
holt, verliert, was dazwischen lag — deshalb wird archiviert.

Aufruf:
    sbund_feed.py --archiv /var/lib/vergabe-monitor/vmp_archiv.db \\
                  --config configs/beispiel-geodaten-it.yaml --db .../hits.db
"""

from __future__ import annotations

import argparse
import html
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET  # nosec B405 -- nur Typen und ParseError; geparst wird mit defusedxml
from datetime import UTC, datetime
from pathlib import Path

import defusedxml.ElementTree as SICHER_XML

sys.path.insert(0, str(Path(__file__).resolve().parent))
import store  # noqa: E402
from vergabe_monitor import match  # noqa: E402
from vmp_opendata import _conn as _archiv_conn

# Ortsaufloesung und PLZ-Zwischenspeicher werden geteilt, nicht nachgebaut:
# zwei Auflegungen derselben Logik waeren zwei Wahrheiten ueber denselben Ort.
from vmp_opendata import ort_laender  # noqa: E402

# Die Suchmaske mit jobsrss=true gibt ihre Ergebnisliste als RSS zurueck.
SUCHE = ("https://www.service.bund.de/Content/DE/Ausschreibungen/Suche/"
         "Formular.html?nn=4641482&resultsPerPage=100"
         "&sortOrder=dateOfIssue_dt+desc&jobsrss=true&templateQueryString=")
UA = {"User-Agent": "vergabe-monitor/1.0 (+https://github.com/andremadstop/vergabe-monitor)"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sbund (
  guid          TEXT PRIMARY KEY,
  plattform     TEXT,            -- aus dem Linkpfad, z. B. vmp-bb
  titel         TEXT,
  stelle        TEXT,
  plz           TEXT,
  ort           TEXT,
  erfuellungsort TEXT,           -- vollstaendig, inkl. Zusaetzen
  frist         TEXT,            -- Angebotsfrist. Nur der Suchfeed liefert sie.
  veroeff_ende  TEXT,            -- NICHT die Angebotsfrist -- spaeterer Termin.
  begriffe      TEXT,            -- welche Suchbegriffe diesen Eintrag fanden
  im_text       INTEGER,         -- 1 = Begriff steht sichtbar in Titel/Beschreibung
  pub_datum     TEXT,
  link          TEXT,
  erst_gesehen  TEXT NOT NULL,
  zuletzt       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sbund_plattform ON sbund(plattform, erst_gesehen);
"""

_PLATTFORM = re.compile(r"/IMPORTE/Ausschreibungen/([^/]+)/")
_PLZ_ORT = re.compile(r"(\d{5})\s+([^;,<]{2,40})")


def _sauber(t: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub("<[^>]+>", " ", t or ""))).strip()


def lies_eintrag(item: ET.Element) -> dict | None:
    guid = (item.findtext("guid") or item.findtext("link") or "").strip()
    if not guid:
        return None
    beschreibung = _sauber(item.findtext("description") or "")

    # "Erfuellungsort: 03046 Cottbus Vergabestelle: ... Veroeffentlichungsende: ..."
    def feld(name: str, bis: tuple[str, ...]) -> str:
        m = re.search(rf"{name}:\s*(.*)", beschreibung)
        if not m:
            return ""
        rest = m.group(1)
        for b in bis:
            rest = rest.split(b)[0]
        return rest.strip(" ;,")

    erf = feld("Erfüllungsort", ("Vergabestelle:", "Angebotsfrist:",
                                "Veröffentlichungsende:"))
    stelle = feld("Vergabestelle", ("Angebotsfrist:", "Veröffentlichungsende:"))
    frist = feld("Angebotsfrist", ("Veröffentlichungsende:",))
    ende = feld("Veröffentlichungsende", ())

    plz = ort = ""
    m = _PLZ_ORT.search(erf)
    if m:
        plz, ort = m.group(1), m.group(2).strip()

    p = _PLATTFORM.search(item.findtext("link") or "")
    return {
        "guid": guid,
        "plattform": p.group(1) if p else "?",
        "titel": _sauber(item.findtext("title") or ""),
        "stelle": stelle,
        "plz": plz,
        "ort": ort,
        "erfuellungsort": erf,
        "frist": frist,
        "veroeff_ende": ende,
        "pub_datum": (item.findtext("pubDate") or "").strip(),
        "link": (item.findtext("link") or "").strip(),
        "beschreibung": beschreibung,
    }


def hole(archiv: Path, begriffe: list[str], pause: float = 1.0) -> tuple[int, int]:
    """Ein Abruf je Suchbegriff. Rueckgabe: (Eintraege gesehen, davon neu).

    Eine Sekunde Pause zwischen den Abrufen. Das ist kein Aberglaube: es sind bis
    zu 50 Anfragen taeglich an ein kostenloses Portal des Bundes, und ein Dienst,
    der sich dort unhoeflich verhaelt, verliert den Zugang fuer alle.
    """
    jetzt = datetime.now(UTC).isoformat(timespec="seconds")
    c = _archiv_conn(archiv)
    c.executescript(_SCHEMA)
    gesehen = neu = 0
    for i, begriff in enumerate(begriffe):
        url = SUCHE + urllib.parse.quote(begriff)
        try:
            # url beginnt fest mit SUCHE (https)
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA),  # nosec B310
                                        timeout=90) as x:
                r = SICHER_XML.fromstring(x.read())
        except Exception as e:
            print(f"  '{begriff}': FEHLER {type(e).__name__}", file=sys.stderr)
            continue
        posten = r.findall(".//item")
        print(f"  '{begriff}': {len(posten)}", file=sys.stderr)
        with c:
            for item in posten:
                d = lies_eintrag(item)
                if not d or not d["guid"]:
                    continue
                gesehen += 1
                # Steht der Begriff sichtbar im Feed -- oder hat ihn nur der Index
                # im Volltext der Bekanntmachung gefunden? Das ist ein Unterschied,
                # den der Leser spaeter sehen koennen muss.
                sichtbar = begriff.lower() in (d["titel"] + " " + d["beschreibung"]).lower()
                vorher = c.execute("SELECT begriffe, im_text FROM sbund WHERE guid=?",
                                   (d["guid"],)).fetchone()
                if vorher is None:
                    neu += 1
                    alle, im_text = begriff, int(sichtbar)
                else:
                    vor = [b for b in (vorher["begriffe"] or "").split("|") if b]
                    if begriff not in vor:
                        vor.append(begriff)
                    alle = "|".join(vor)
                    im_text = int(bool(vorher["im_text"]) or sichtbar)
                c.execute("""
                    INSERT INTO sbund (guid,plattform,titel,stelle,plz,ort,
                        erfuellungsort,frist,veroeff_ende,begriffe,im_text,
                        pub_datum,link,erst_gesehen,zuletzt)
                    VALUES (:guid,:plattform,:titel,:stelle,:plz,:ort,
                        :erfuellungsort,:frist,:veroeff_ende,:begriffe,:im_text,
                        :pub_datum,:link,:jetzt,:jetzt)
                    ON CONFLICT(guid) DO UPDATE SET zuletzt=excluded.zuletzt,
                        frist=excluded.frist, veroeff_ende=excluded.veroeff_ende,
                        begriffe=excluded.begriffe, im_text=excluded.im_text
                """, {**{k: v for k, v in d.items() if k != "beschreibung"},
                      "begriffe": alle, "im_text": im_text, "jetzt": jetzt})
        if i + 1 < len(begriffe):
            time.sleep(pause)
    c.close()
    return gesehen, neu


_FRIST = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})(?:\s+(\d{1,2}):(\d{2}))?")


def _iso_frist(roh: str | None) -> str:
    """"14.09.2026 10:00" -> "2026-09-14T10:00:00". Leer, wenn nicht lesbar --
    lieber keine Frist als eine falsche."""
    m = _FRIST.search(roh or "")
    if not m:
        return ""
    t, mo, j, st, mi = m.groups()
    return f"{j}-{int(mo):02d}-{int(t):02d}T{int(st or 0):02d}:{int(mi or 0):02d}:00"


def als_kandidat(z: sqlite3.Row, nuts: list[str], mehrdeutig: bool) -> dict:
    return {
        "notice_id": z["guid"],
        "lot_id": "",
        "title": z["titel"] or "",
        "description": z["erfuellungsort"] or "",
        "value": "",
        "currency": "",
        # 🔴 Leer, und das ist keine Nachlaessigkeit: der Feed fuehrt keine
        # CPV-Codes. Ein erfundener Code waere schlimmer als keiner.
        "cpv": [],
        "city": z["ort"] or "",
        "plz": z["plz"] or "",
        "nuts": nuts[0] if (nuts and not mehrdeutig) else "",
        "buyer": z["stelle"] or "",
        "buyer_url": z["link"] or "",
        "procedure_type": "",
        "legal_basis": "",
        "opening": "",
        # Der SUCHfeed liefert die echte Angebotsfrist -- der allgemeine nicht.
        # Fehlt sie, bleibt das Feld leer; „Veroeffentlichungsende" wird NIE
        # hineingeschrieben, das ist ein spaeterer und irrefuehrender Termin.
        "angebotsfrist": _iso_frist(z["frist"]),
        "frist_roh": z["frist"] or "",
        "veroeffentlichungsende": z["veroeff_ende"] or "",
        "such_begriffe": z["begriffe"] or "",
        "sme_suitable": "",
        "pub_date": (z["erst_gesehen"] or "")[:10],
        "quelle": f"service.bund.de/{z['plattform']}",
        "quelle_art": "service.bund.de",
        "ohne_cpv": ("Diese Quelle liefert keine CPV-Codes — bewertet wurde nur "
                     "ueber Freitext. Der Treffer ist unschaerfer als einer aus "
                     "dem Datenservice."),
    }


def filtere(archiv: Path, cfg: dict) -> list[dict]:
    """Die Suchmaschine hat den Freitext schon erledigt -- hier bleibt Ort und Rest.

    🔴 Warum dieser Kanal `match()` NICHT ueber den Text laufen laesst:
    Der Index von service.bund.de durchsucht den **Volltext der Bekanntmachung**,
    der Feed zeigt davon nur Titel und Erfuellungsort. Gemessen: bei „Vermessung"
    stand der Begriff in 13 von 19 Eintraegen sichtbar, bei „Fassade" in 65 von
    100. Wuerde hier `match()` ueber den sichtbaren Text entscheiden, fielen
    ausgerechnet die Eintraege heraus, bei denen die Quelle **mehr weiss als wir**.

    Deshalb: Der Suchtreffer selbst ist der Freitext-Beleg. Wo der Begriff
    sichtbar ist, bewertet `match()` normal und voll. Wo nicht, wird der Eintrag
    aufgenommen, aber **niedriger bewertet und sichtbar markiert** -- er ist ohne
    Oeffnen der Bekanntmachung nicht beurteilbar. Das ist die einzige Stelle im
    System mit einer zweiten Bewertung, und sie steht hier begruendet.
    """
    c = _archiv_conn(archiv)
    c.executescript(_SCHEMA)
    regionen = [r.upper() for r in cfg.get("suchraum", {}).get("regionen", [])]
    muster = [m.lower() for m in cfg.get("suchraum", {}).get("freitext_muster", [])]
    if not muster:
        c.close()
        return []

    treffer = []
    for z in c.execute("SELECT * FROM sbund ORDER BY erst_gesehen DESC").fetchall():
        gefunden = [b for b in (z["begriffe"] or "").split("|")
                    if b and b.lower() in muster]
        if not gefunden:
            continue                       # von einem anderen Profil gefunden

        nuts = ort_laender(c, z["plz"], z["ort"])
        mehrdeutig = len(nuts) > 1
        if nuts and not mehrdeutig and regionen:
            if not any(nuts[0].startswith(r) for r in regionen):
                continue

        # 🔴 Der Begriff steckt im NAMEN DER VERGABESTELLE, nicht im Auftrag.
        # „Bayerisches Landesamt fuer Digitalisierung, Breitband und Vermessung"
        # laesst jede seiner Ausschreibungen auf „vermessung" ansprechen -- auch
        # 170 Gitlab-Lizenzen und ein SUV-Leasing. Gemessen: 3 von 80 nicht
        # sichtbaren Treffern. Wenige, aber sicher falsch und sicher erkennbar.
        # ⚠ Nur verwerfen, wenn der Begriff SONST nirgends steht: eine
        # Vermessungsbehoerde schreibt selbstverstaendlich auch Vermessung aus.
        sichtbar = f"{z['titel'] or ''} {z['erfuellungsort'] or ''}".lower()
        if not any(b.lower() in sichtbar for b in gefunden):
            if any(b.lower() in (z["stelle"] or "").lower() for b in gefunden):
                continue

        k = als_kandidat(z, nuts, mehrdeutig)
        if any(b.lower() in sichtbar for b in gefunden):
            m = match(k, cfg)
            if not m.get("hit"):
                continue
        else:
            m = {"hit": True, "score": 20,
                 "reasons": [f"Suchtreffer service.bund.de: {', '.join(gefunden)}"],
                 "flags": ["⚠ Begriff steht nur im Volltext der Bekanntmachung, "
                           "nicht im Feed — ohne Oeffnen nicht beurteilbar"]}
            k["nur_volltext"] = ("Der Suchindex fand den Begriff im Volltext; im "
                                 "Feed ist er nicht sichtbar.")
        if mehrdeutig:
            k["ort_unsicher"] = (f"Ort nicht eindeutig: {z['ort'] or z['plz']} liegt "
                                 f"in mehreren Bundeslaendern ({', '.join(nuts)})")
            m.setdefault("flags", []).append("⚠ " + k["ort_unsicher"])
        elif not nuts:
            k["ort_unsicher"] = "Leistungsort nicht bestimmbar — nicht verworfen"
            m.setdefault("flags", []).append("⚠ " + k["ort_unsicher"])
        m.setdefault("flags", []).append("⚠ ohne CPV bewertet (Quelle liefert keine)")
        k["_m"] = m
        treffer.append(k)
    c.close()
    return treffer


def _yaml(p: Path) -> dict:
    import yaml  # type: ignore
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Ausschreibungs-Feed von service.bund.de")
    ap.add_argument("--archiv", required=True, type=Path)
    ap.add_argument("--config", action="append", default=[], type=Path)
    ap.add_argument("--db", type=Path)
    ap.add_argument("--nur-holen", action="store_true")
    a = ap.parse_args()

    # Begriffe aller Profile zusammen, dedupliziert: derselbe Suchbegriff wird
    # einmal abgerufen und nicht je Config erneut.
    begriffe: list[str] = []
    for pfad in a.config:
        for m in (_yaml(pfad).get("suchraum", {}).get("freitext_muster") or []):
            if m not in begriffe:
                begriffe.append(str(m))
    if not begriffe:
        print("Keine Suchbegriffe in den Configs — nichts zu holen.", file=sys.stderr)
        return 0
    print(f"{len(begriffe)} Suchbegriffe (dedupliziert):", file=sys.stderr)
    gesamt, neu = hole(a.archiv, begriffe)
    print(f"Feed: {gesamt} Eintraege gesehen, {neu} neu", file=sys.stderr)
    c = _archiv_conn(a.archiv)
    c.executescript(_SCHEMA)
    stand = c.execute("SELECT count(*) FROM sbund").fetchone()[0]
    verteilung = c.execute("SELECT plattform, count(*) FROM sbund GROUP BY 1 "
                           "ORDER BY 2 DESC LIMIT 8").fetchall()
    c.close()
    print(f"Archiv gesamt: {stand}", file=sys.stderr)
    print("  " + " · ".join(f"{p}:{n}" for p, n in verteilung), file=sys.stderr)

    if a.nur_holen or not a.config:
        return 0

    jetzt = datetime.now(UTC).isoformat(timespec="seconds")
    pubday = jetzt[:10].replace("-", "")
    for pfad in a.config:
        cfg = _yaml(pfad)
        treffer = filtere(a.archiv, cfg)
        print(f"\n{cfg['mandant']}: {len(treffer)} Treffer aus service.bund.de",
              file=sys.stderr)
        for t in treffer[:10]:
            print(f"   [{t['_m']['score']:3}] {t['title'][:58]}", file=sys.stderr)
            print(f"         {t['buyer'][:40]} | {t['quelle']}", file=sys.stderr)
        if a.db and treffer:
            store.record_hits(a.db, cfg["mandant"], pubday, treffer, jetzt)
            with sqlite3.connect(a.db) as h:
                n = h.execute("SELECT count(*) FROM hits WHERE mandant=? "
                              "AND candidate LIKE ?",
                              (cfg["mandant"],
                               '%"quelle_art": "service.bund.de"%')).fetchone()[0]
            print(f"   -> in hits.db aus diesem Kanal: {n}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
