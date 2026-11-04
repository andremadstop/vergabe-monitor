#!/usr/bin/env python3
"""
Vergabe-Monitor: Kanal 4, der Ausschreibungs-Feed von service.bund.de

Ein Abruf je Lauf: der allgemeine Feed (RSSGenerator_Ausschreibungen.xml), den
die Seite als RSS-Abo anbietet und dessen Pfad die robots.txt nicht sperrt. Er
trägt 500 Einträge, aber nur Titel und Erfüllungsort, im Median 55 plus 15
Zeichen. Gematcht wird lokal über Titel und Beschreibung, entsprechend selten.

Bis 03.11.2026 fragte dieser Kanal stattdessen die Suchmaske ab, einen gefilterten
Feed je Suchbegriff mit Volltextsuche und Angebotsfrist („Vermessung" 19 Treffer,
„Fassade" 100). Die robots.txt sperrt diesen Pfad für alle Programme; deshalb ist
das vorbei, auch wenn es Treffer kostet (siehe den Kommentar bei FEED).

Der Feed aggregiert über zwanzig Vergabeplattformen, und der Link verrät je
Eintrag, welche:

    /IMPORTE/Ausschreibungen/vmp-bb/...       Vergabemarktplatz Brandenburg
    /IMPORTE/Ausschreibungen/vmp-nds/...      Niedersachsen
    /IMPORTE/Ausschreibungen/vmp-egomv/...    Mecklenburg-Vorpommern
    /IMPORTE/Ausschreibungen/dtvp/...         Deutsches Vergabeportal
    /IMPORTE/Ausschreibungen/subreport/...    subreport
    /IMPORTE/Ausschreibungen/healyhudson/...  Healy Hudson
    ...

Genau die Plattformen also, für die configs/plattformen.yaml „keine Schnittstelle
gefunden" notiert. Über diesen Feed sind sie erreichbar.

Wie viel davon neu ist, gemessen am 21.08.2026: 55 Einträge aus vmp-bb, vmp-nds,
vmp-egomv und dtvp gegen fünf Datenservice-Tagespakete abgeglichen, 47 waren
schon da, 8 nicht. Der Feed ist also größtenteils redundant; das bestätigt, dass
service.bund.de in den Datenservice einfließt, nur nicht vollständig. Der Kanal
ersetzt nichts und deckt Brandenburg nicht ab, er ergänzt am Rand.

Zwei Eigenheiten dieser Quelle:

1. Es gibt keine CPV-Codes. Der Feed trägt nur Titel, Erfüllungsort,
   Vergabestelle und ein Datum. Gematcht wird also ausschließlich über Freitext.
   Für ein Profil, das seine Trennschärfe aus CPV zieht, fällt damit die Hälfte
   der Bewertung weg; die Treffer sind unschärfer als die aus dem Datenservice
   und tragen deshalb ein eigenes Herkunftsmerkmal.

2. „Veröffentlichungsende" ist nicht die Angebotsfrist, sondern der Tag, an dem
   die Bekanntmachung von der Seite verschwindet, regelmäßig Wochen nach der
   Angebotsfrist. Derselbe Fehler wie `publicOpeningDate` im Datenservice,
   aufgefallen am 21.08.; eine zu späte Frist lädt zur Untätigkeit ein. Der Wert
   wird deshalb als `veroeffentlichungsende` geführt und nie als `angebotsfrist`.

Der Feed ist ein rollendes Fenster von rund zwei Tagen. Wer ihn nicht täglich
holt, verliert, was dazwischen lag; deshalb wird archiviert.

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
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET  # nosec B405 -- nur Typen und ParseError; geparst wird mit defusedxml
from datetime import UTC, datetime
from pathlib import Path

import defusedxml.ElementTree as SICHER_XML

sys.path.insert(0, str(Path(__file__).resolve().parent))
import store  # noqa: E402
from vergabe_monitor import match, suchwoerter, treffer_uebernehmen  # noqa: E402
from vmp_opendata import _conn as _archiv_conn

# Ortsaufloesung und PLZ-Zwischenspeicher werden geteilt, nicht nachgebaut:
# zwei Auflegungen derselben Logik waeren zwei Wahrheiten ueber denselben Ort.
from vmp_opendata import ort_laender  # noqa: E402

# robots.txt von service.bund.de (geprüft 03.11.2026): „Disallow: /Content/DE/
# Ausschreibungen/Suche/“ und „Crawl-delay: 30“. Bis 03.11. fragte dieser Kanal genau
# diese Suchmaske ab, einmal je Suchbegriff mit 1 s Pause. Das war nicht erlaubt.
# Seitdem: nur noch der allgemeine Ausschreibungs-Feed, der als RSS-Abo angeboten
# wird und dessen Pfad nicht gesperrt ist, EIN Abruf je Lauf. Gematcht wird lokal
# über Titel und Beschreibung. Folge, ehrlich: keine Volltextsuche und keine
# Angebotsfrist mehr aus dieser Quelle, deutlich weniger Treffer.
FEED = ("https://www.service.bund.de/Content/Globals/Functions/RSSFeed/"
        "RSSGenerator_Ausschreibungen.xml")
SUCHE_GESPERRT = "/Content/DE/Ausschreibungen/Suche/"
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
  veroeff_ende  TEXT,            -- nicht die Angebotsfrist, ein spaeterer Termin.
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


def hole(archiv: Path, begriffe: list[str], pause: float = 0.0) -> tuple[int, int]:
    """Ein Abruf des allgemeinen Feeds, dann lokaler Abgleich mit allen Begriffen.
    Rueckgabe: (Eintraege gesehen, davon neu). `pause` bleibt nur fuer die
    alte Aufrufform stehen; es gibt keine Folgeabrufe mehr.
    """
    if SUCHE_GESPERRT in FEED:
        raise RuntimeError("robots.txt: Suchpfad von service.bund.de ist gesperrt")
    jetzt = datetime.now(UTC).isoformat(timespec="seconds")
    c = _archiv_conn(archiv)
    c.executescript(_SCHEMA)
    try:
        with urllib.request.urlopen(urllib.request.Request(FEED, headers=UA),  # nosec B310
                                    timeout=90) as x:
            r = SICHER_XML.fromstring(x.read())
    except Exception as e:
        print(f"  Feed: FEHLER {type(e).__name__}", file=sys.stderr)
        c.close()
        return 0, 0
    posten = r.findall(".//item")
    print(f"  allgemeiner Feed: {len(posten)} Eintraege", file=sys.stderr)
    gesehen = neu = 0
    klein = [(b, b.lower()) for b in begriffe]
    with c:
        for item in posten:
            d = lies_eintrag(item)
            if not d or not d["guid"]:
                continue
            gesehen += 1
            text = (d["titel"] + " " + d["beschreibung"]).lower()
            gefunden = [b for b, k in klein if k in text]
            vorher = c.execute("SELECT begriffe FROM sbund WHERE guid=?",
                               (d["guid"],)).fetchone()
            if vorher is None:
                neu += 1
                alle = gefunden
            else:
                alle = [b for b in (vorher["begriffe"] or "").split("|") if b]
                alle += [b for b in gefunden if b not in alle]
            c.execute("""
                INSERT INTO sbund (guid,plattform,titel,stelle,plz,ort,
                    erfuellungsort,frist,veroeff_ende,begriffe,im_text,
                    pub_datum,link,erst_gesehen,zuletzt)
                VALUES (:guid,:plattform,:titel,:stelle,:plz,:ort,
                    :erfuellungsort,:frist,:veroeff_ende,:begriffe,:im_text,
                    :pub_datum,:link,:jetzt,:jetzt)
                ON CONFLICT(guid) DO UPDATE SET zuletzt=excluded.zuletzt,
                    veroeff_ende=excluded.veroeff_ende,
                    begriffe=excluded.begriffe,
                    im_text=MAX(sbund.im_text, excluded.im_text)
            """, {**{k: v for k, v in d.items() if k != "beschreibung"},
                  "begriffe": "|".join(alle), "im_text": int(bool(gefunden)),
                  "jetzt": jetzt})
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
        # Leer, und das mit Absicht: der Feed führt keine CPV-Codes. Ein
        # erfundener Code wäre schlimmer als keiner.
        "cpv": [],
        "city": z["ort"] or "",
        "plz": z["plz"] or "",
        "nuts": nuts[0] if (nuts and not mehrdeutig) else "",
        "buyer": z["stelle"] or "",
        "buyer_url": z["link"] or "",
        "procedure_type": "",
        "legal_basis": "",
        "opening": "",
        # Der Suchfeed liefert die echte Angebotsfrist, der allgemeine nicht.
        # Fehlt sie, bleibt das Feld leer; „Veröffentlichungsende" wird nie
        # hineingeschrieben, das ist ein späterer und irreführender Termin.
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
    """Archiv gegen ein Profil. Bewertet wird mit match(), wie in den anderen Kanälen.

    Bis 03.11.2026 kam der Freitext-Beleg aus der Volltextsuche von service.bund.de:
    Ein Eintrag, den die Suche fand, ohne dass der Begriff im Feed sichtbar war, lief
    mit eigener, niedrigerer Bewertung als „nur Volltext“ durch. Die Suchseite ist
    per robots.txt gesperrt (siehe FEED); der allgemeine Feed zeigt nur Titel,
    Erfüllungsort und Vergabestelle, und darüber entscheidet jetzt match() allein.
    Damit gelten auch hier Stufen, Kontextregeln und die Titelregel für Vergebenes.
    Der Zweig „nur Volltext“ ist entfallen; Altbestände in hits.db bleiben stehen.

    Die Spalte `begriffe` des Archivs wird nicht mehr gelesen. Sie kannte nur die
    `freitext_muster` der Profile, nicht die Regelwörter der Stufen.
    """
    c = _archiv_conn(archiv)
    c.executescript(_SCHEMA)
    regionen = [r.upper() for r in cfg.get("suchraum", {}).get("regionen", [])]
    woerter = suchwoerter(cfg)
    if not woerter:
        c.close()
        return []

    treffer = []
    for z in c.execute("SELECT * FROM sbund ORDER BY erst_gesehen DESC").fetchall():
        # Grobe Vorsortierung, damit nicht jeder Eintrag die Ortsauflösung kostet.
        # Entschieden wird unten in match(), über denselben sichtbaren Text. Ein
        # Begriff nur im Namen der Vergabestelle zählt nicht: „Landesamt für …
        # Vermessung“ schreibt auch Gitlab-Lizenzen aus.
        sichtbar = f"{z['titel'] or ''} {z['erfuellungsort'] or ''}".lower()
        if not any(w in sichtbar for w in woerter):
            continue

        nuts = ort_laender(c, z["plz"], z["ort"])
        mehrdeutig = len(nuts) > 1
        if nuts and not mehrdeutig and regionen:
            if not any(nuts[0].startswith(r) for r in regionen):
                continue

        k = als_kandidat(z, nuts, mehrdeutig)
        m = match(k, cfg)
        if not m.get("hit"):
            continue
        if mehrdeutig:
            k["ort_unsicher"] = (f"Ort nicht eindeutig: {z['ort'] or z['plz']} liegt "
                                 f"in mehreren Bundeslaendern ({', '.join(nuts)})")
            m.setdefault("flags", []).append("⚠ " + k["ort_unsicher"])
        elif not nuts:
            k["ort_unsicher"] = "Leistungsort nicht bestimmbar — nicht verworfen"
            m.setdefault("flags", []).append("⚠ " + k["ort_unsicher"])
        m.setdefault("flags", []).append("⚠ ohne CPV bewertet (Quelle liefert keine)")
        treffer_uebernehmen(k, m)          # Stufe ins Kandidaten-JSON, sonst verliert sie der Speicher
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
