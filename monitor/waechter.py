#!/usr/bin/env python3
"""
Vergabe-Monitor — Wächter: prüft jeden Tageslauf an seinen eigenen Zahlen (16.10.2026)

Warum: Zwei Fehler liefen wochenlang unbemerkt, obwohl sie in einfachen Zahlen
sichtbar waren. Zuschläge kamen als Treffer durch (in zwei Profilen rund ein
Drittel), und die Vergabemarktplätze lieferten Ex-post-Meldungen.

Was er prüft, je Lauf (Fundtag = heute, Berlin):
  rot   Vergebenes durchgerutscht (formType result/cont-modif/dir-awa-pre oder Ex post)
  rot   Tagespaket des Datenservice für gestern fehlt oder ist leer
  gelb  Kanal still (Datenservice, service.bund.de, Vergabemarktplätze), obwohl er
        sonst liefert
  gelb  Profil ohne neue Treffer, obwohl er sonst welche bekommt; oder auffällig viele
  gelb  Anteil ohne Frist / ohne Ort / ohne Plattform-Link deutlich über dem Üblichen
Verglichen wird mit dem Median der letzten 14 Läufe, getrennt nach Werktag und
Wochenende (sonntags ist weniger los, das ist kein Fehler). Die ersten 5 Läufe sammeln
nur, ohne Vergleichsmeldung -- sonst meldet der Wächter am ersten Tag alles.

Ausgabe (nur Kennzahlen und Profilnamen, keine Treffer):
  <aus>/aktuell.json          letzter Lauf -- für Tagesmail und Betriebsübersicht
  <aus>/waechter-<tag>.json   je Tag, für den Verlauf
  <verlauf>                   SQLite mit den Kennzahlen je Tag (nur der Monitor)

Bleibt der Lauf ganz aus, kann der Wächter das selbst nicht melden. Das übernimmt der
Leser: ist aktuell.json älter als 30 Stunden, steht das in der Tagesmail (Totmann).

    waechter.py --hits /var/lib/vergabe-monitor/hits.db --markt /var/lib/vergabe-monitor \\
        --raw /var/lib/vergabe-monitor/raw --verlauf /var/lib/vergabe-monitor/waechter.db \\
        --aus /var/lib/vergabe-waechter
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ORTSZEIT = ZoneInfo("Europe/Berlin")
VERGEBEN_FORMTYPEN = {"result", "cont-modif", "dir-awa-pre"}
KANAELE = {None: "Datenservice", "service.bund.de": "service.bund.de",
           "vmp-opendata": "Vergabemarktplätze"}
MIN_VERLAUF = 5          # so viele vergleichbare Läufe, bevor Abweichungen gemeldet werden
FENSTER = 14
MIN_N_ANTEIL = 10        # Anteile erst ab so vielen Treffern bewerten


def _vergeben(k: dict) -> bool:
    art = (k.get("procedure_type") or "").strip().lower()
    return (k.get("form_type") in VERGEBEN_FORMTYPEN
            or art.startswith("ex post") or "vergebener auftr" in art)


def kennzahlen(hits: Path, markt_dir: Path | None, raw: Path | None, tag: date) -> dict:
    """Kennzahlen des Laufs vom `tag` (Fundtag). Reine Zählung, keine Bewertung."""
    t = tag.isoformat()
    mandanten: dict[str, dict] = {}
    kanaele = {name: 0 for name in KANAELE.values()}
    with sqlite3.connect(f"file:{hits}?mode=ro", uri=True, timeout=30) as c:
        alle = {m for (m,) in c.execute("SELECT DISTINCT mandant FROM hits")}
        for m in alle:
            mandanten[m] = {"neu": 0, "vergeben": 0, "ohne_frist": 0, "ohne_ort": 0,
                            "doe": 0, "doe_ohne_plattform": 0, "ankuendigungen": 0, "radar_neu": 0}
        for m, roh in c.execute("SELECT mandant, candidate FROM hits WHERE substr(first_seen,1,10)=?", (t,)):
            k = json.loads(roh)
            z = mandanten[m]
            z["neu"] += 1
            z["vergeben"] += _vergeben(k)
            z["ohne_frist"] += not (k.get("angebotsfrist") or k.get("opening"))
            z["ohne_ort"] += not (k.get("nuts") or "").strip()
            art = (k.get("procedure_type") or "").lower()
            z["ankuendigungen"] += art.startswith("ex ante") or "beabsichtigt" in art
            q = k.get("quelle_art")
            kanaele[KANAELE.get(q, str(q))] = kanaele.get(KANAELE.get(q, str(q)), 0) + 1
            if q is None:
                z["doe"] += 1
                z["doe_ohne_plattform"] += not (k.get("unterlagen_url") or "").startswith("http")
    if markt_dir:
        for db in sorted(markt_dir.glob("markt-*.db")):
            try:
                with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=15) as c:
                    for m, n in c.execute("SELECT mandant, count(*) FROM nu_zuschlaege "
                                          "WHERE substr(erstmals,1,10)=? GROUP BY mandant", (t,)):
                        mandanten.setdefault(m, {"neu": 0, "vergeben": 0, "ohne_frist": 0, "ohne_ort": 0,
                                                 "doe": 0, "doe_ohne_plattform": 0, "ankuendigungen": 0,
                                                 "radar_neu": 0})["radar_neu"] = n
            except sqlite3.Error:
                continue          # Datei ohne Radar-Tabelle
    paket = None
    if raw:
        p = raw / f"doe-{(tag - timedelta(days=1)).isoformat()}.zip"
        paket = p.stat().st_size if p.exists() else 0
    return {"tag": t, "werktag": tag.weekday() < 5, "mandanten": mandanten,
            "kanaele": kanaele, "paket_gestern_bytes": paket}


def _median(werte: list[float]) -> float | None:
    return statistics.median(werte) if werte else None


def bewerten(heute: dict, verlauf: list[dict]) -> list[dict]:
    """Meldungen aus dem Vergleich mit früheren Läufen derselben Tagesart."""
    meld: list[dict] = []
    gleich = [v for v in verlauf if v.get("werktag") == heute["werktag"]][-FENSTER:]
    genug = len(gleich) >= MIN_VERLAUF

    if heute.get("paket_gestern_bytes") == 0:
        meld.append({"stufe": "rot", "text": "Tagespaket des Datenservice für gestern fehlt oder ist leer."})

    for m, z in sorted(heute["mandanten"].items()):
        if z["vergeben"]:
            meld.append({"stufe": "rot", "mandant": m,
                         "text": f"{z['vergeben']} schon vergebene Aufträge als Treffer durchgerutscht."})
        frueher = [v["mandanten"].get(m) for v in gleich if v["mandanten"].get(m)]
        if not genug or len(frueher) < MIN_VERLAUF:
            continue
        med_neu = _median([f["neu"] for f in frueher]) or 0
        if z["neu"] == 0 and med_neu >= 5:
            meld.append({"stufe": "gelb", "mandant": m,
                         "text": f"keine neuen Treffer (sonst etwa {med_neu:.0f})."})
        elif med_neu and z["neu"] > max(3 * med_neu, med_neu + 30):
            meld.append({"stufe": "gelb", "mandant": m,
                         "text": f"{z['neu']} neue Treffer, sonst etwa {med_neu:.0f}. Filter prüfen."})
        if z["neu"] >= MIN_N_ANTEIL:
            for feld, wort in (("ohne_frist", "ohne Frist"), ("ohne_ort", "ohne Ort")):
                anteil = z[feld] / z["neu"]
                med = _median([f[feld] / f["neu"] for f in frueher if f["neu"]]) or 0
                if anteil > med + 0.25:
                    meld.append({"stufe": "gelb", "mandant": m,
                                 "text": f"{anteil:.0%} {wort}, sonst etwa {med:.0%}."})
        if z["doe"] >= MIN_N_ANTEIL and z["doe_ohne_plattform"] / z["doe"] > 0.3:
            meld.append({"stufe": "gelb", "mandant": m,
                         "text": f"{z['doe_ohne_plattform']} von {z['doe']} Treffern aus dem Datenservice "
                                 f"ohne Plattform-Link (sonst wenige Prozent)."})

    if genug:
        for kanal, n in sorted(heute["kanaele"].items()):
            med = _median([v["kanaele"].get(kanal, 0) for v in gleich]) or 0
            if n == 0 and med >= 3:
                meld.append({"stufe": "gelb", "text": f"Kanal {kanal} still: heute 0, sonst etwa {med:.0f}."})
    return meld


def _verlauf(db: Path) -> sqlite3.Connection:
    db.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(db, timeout=15)
    c.execute("CREATE TABLE IF NOT EXISTS lauf (tag TEXT PRIMARY KEY, daten TEXT NOT NULL)")
    return c


def _schreiben(pfad: Path, daten: dict) -> None:
    neu = pfad.with_suffix(".neu")
    neu.write_text(json.dumps(daten, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(neu, 0o640)          # Gruppe betrieb liest (Dashboard); umask des Dienstes ist 077
    neu.replace(pfad)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--hits", type=Path, required=True)
    ap.add_argument("--markt", type=Path, help="Verzeichnis mit markt-*.db")
    ap.add_argument("--raw", type=Path)
    ap.add_argument("--verlauf", type=Path, required=True)
    ap.add_argument("--aus", type=Path, required=True)
    ap.add_argument("--tag", help="Fundtag JJJJ-MM-TT, Vorgabe heute (Berlin)")
    a = ap.parse_args(argv)

    tag = date.fromisoformat(a.tag) if a.tag else datetime.now(ORTSZEIT).date()
    heute = kennzahlen(a.hits, a.markt, a.raw, tag)
    with _verlauf(a.verlauf) as c:
        verlauf = [json.loads(d) for (d,) in c.execute(
            "SELECT daten FROM lauf WHERE tag < ? ORDER BY tag", (heute["tag"],))]
        meldungen = bewerten(heute, verlauf)
        c.execute("INSERT OR REPLACE INTO lauf VALUES (?,?)", (heute["tag"], json.dumps(heute)))
    stufe = ("rot" if any(m["stufe"] == "rot" for m in meldungen)
             else "gelb" if meldungen else "ok")
    ergebnis = {**heute, "status": stufe, "meldungen": meldungen,
                "verlauf_laeufe": len(verlauf),
                "erzeugt": datetime.now(UTC).isoformat(timespec="seconds")}
    a.aus.mkdir(parents=True, exist_ok=True)
    _schreiben(a.aus / f"waechter-{heute['tag']}.json", ergebnis)
    _schreiben(a.aus / "aktuell.json", ergebnis)
    print(f"Wächter {heute['tag']}: {stufe}, {len(meldungen)} Meldung(en), "
          f"Verlauf {len(verlauf)} Läufe.", file=sys.stderr)
    for m in meldungen:
        print(f"  [{m['stufe']}] {m.get('mandant', '') + ': ' if m.get('mandant') else ''}{m['text']}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
