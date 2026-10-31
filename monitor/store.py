#!/usr/bin/env python3
"""
Vergabe-Monitor — Treffer-Speicher (SQLite)

Damit ein Treffer aus der Liste später per Kurz-ID („a1b2") wieder auffindbar
ist, hält dieser Speicher jeden gemeldeten Treffer fest — inklusive des
vollständigen Kandidaten-JSON, damit Details ohne erneutes Parsen des
Tagespakets bereitstehen.

Kurz-ID = 4 Zeichen base36 aus notice_id+lot_id → stabil (gleicher Treffer =
gleiche ID, keine Doppel) und von Hand tippbar. Kein Secret, DB liegt in
raw/ (gitignored).

🔴 Der Schlüssel ist (mandant, short_id), nicht short_id allein. Die Kurz-ID
kennt den Mandanten nicht — mit ihr als alleinigem Primärschlüssel bekam
denselben Treffer nur der Mandant, dessen Lauf zuerst lief; jeder weitere fiel
still durch INSERT OR IGNORE. Gefunden 21.08.2026: ein frischer Lauf mit
75 Treffern schrieb null Zeilen, weil dieselben Lose schon unter der alten
Config standen. Dass zwei Mandanten dieselbe Ausschreibung treffen, ist der
Normalfall — bei Vermessung und Fassade sogar der interessante.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS hits (
  short_id   TEXT NOT NULL,
  mandant    TEXT NOT NULL,
  pubday     TEXT NOT NULL,
  notice_id  TEXT NOT NULL,
  lot_id     TEXT,
  title      TEXT,
  score      INTEGER,
  candidate  TEXT NOT NULL,       -- vollständiges Kandidaten-JSON
  first_seen TEXT NOT NULL,
  dossier_built INTEGER DEFAULT 0,
  PRIMARY KEY (mandant, short_id)
);
CREATE INDEX IF NOT EXISTS idx_hits_mandant ON hits(mandant, score DESC);
"""

_B36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def short_id(notice_id: str, lot_id: str) -> str:
    h = int(hashlib.sha1(f"{notice_id}|{lot_id}".encode(), usedforsecurity=False).hexdigest(), 16)
    out = ""
    for _ in range(4):
        out += _B36[h % 36]
        h //= 36
    return out


def _conn(db: Path) -> sqlite3.Connection:
    db.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(db, timeout=15)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    _pruefe_schluessel(c, db)
    return c


def _pruefe_schluessel(c: sqlite3.Connection, db: Path) -> None:
    """CREATE TABLE IF NOT EXISTS repariert eine bestehende Datei nicht. Eine
    alte hits.db nimmt also weiter still nur den ersten Mandanten je Treffer an.
    Lieber laut abbrechen als leise Treffer verlieren."""
    zeile = c.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='hits'"
    ).fetchone()
    if zeile and "PRIMARY KEY (mandant, short_id)" not in zeile[0]:
        raise RuntimeError(
            f"{db} hat noch den alten Schlüssel (short_id allein). Treffer eines "
            f"zweiten Mandanten würden verworfen. Migration nötig: Tabelle hits "
            f"mit PRIMARY KEY (mandant, short_id) neu anlegen und umkopieren."
        )


def record_hits(db: Path, mandant: str, pubday: str, hits: list[dict],
                now: str) -> list[tuple[str, dict]]:
    """Speichert Treffer, gibt (short_id, hit) je Treffer zurück.
    `now` wird übergeben (Date.now steht im Kern nicht zur Verfügung → aus main)."""
    out = []
    with _conn(db) as c:
        for h in hits:
            sid = short_id(h.get("notice_id", ""), h.get("lot_id", ""))
            cand = {k: (sorted(v) if isinstance(v, set) else v)
                    for k, v in h.items() if k != "_m"}
            cand["_score"] = h.get("_m", {}).get("score")
            daten = json.dumps(cand, ensure_ascii=False)
            score = h.get("_m", {}).get("score")
            c.execute(
                "INSERT OR IGNORE INTO hits(short_id,mandant,pubday,notice_id,"
                "lot_id,title,score,candidate,first_seen) VALUES(?,?,?,?,?,?,?,?,?)",
                (sid, mandant, pubday, h.get("notice_id"), h.get("lot_id"),
                 h.get("title"), score, daten, now),
            )
            # Ein zweiter Lauf bringt oft bessere Daten mit -- etwa die
            # Angebotsfrist, die erst aus dem eForms-XML nachgeladen wird. Mit
            # INSERT OR IGNORE allein blieben die alten Zeilen stehen, und ein
            # Treffer ohne Frist bliebe fuer immer einer ohne Frist.
            #
            # first_seen und dossier_built werden bewusst NICHT angefasst: wann
            # ein Treffer zuerst auffiel und ob schon ein Dossier existiert,
            # weiss der neue Lauf nicht besser.
            c.execute(
                "UPDATE hits SET title=?, score=?, candidate=? "
                "WHERE mandant=? AND short_id=?",
                (h.get("title"), score, daten, mandant, sid),
            )
            out.append((sid, h))
    return out


class MehrdeutigeKurzID(LookupError):
    """Dieselbe Kurz-ID steht bei mehreren Mandanten.

    Seit der Schlüssel (mandant, short_id) ist, kann das vorkommen -- gemessen
    21.08.2026: 66 von 877 Kurz-IDs. Wer hier rät, zeigt einen Treffer
    unter dem falschen Mandanten an. Deshalb ein Fehler und keine Auswahl
    nach Zufall.
    """

    def __init__(self, sid: str, mandanten: list[str]) -> None:
        self.sid = sid
        self.mandanten = mandanten
        super().__init__(
            f"Kurz-ID '{sid}' steht bei mehreren Mandanten: {', '.join(mandanten)}. "
            f"Mandant mit angeben."
        )


def get_hit(db: Path, sid: str, mandant: str | None = None) -> dict | None:
    """Ein Treffer. Ohne Mandant nur, solange die Kurz-ID eindeutig ist --
    sonst MehrdeutigeKurzID."""
    with _conn(db) as c:
        if mandant:
            row = c.execute("SELECT * FROM hits WHERE short_id=? AND mandant=?",
                            (sid.lower(), mandant)).fetchone()
            return dict(row) if row else None
        zeilen = c.execute("SELECT * FROM hits WHERE short_id=?",
                           (sid.lower(),)).fetchall()
    if len(zeilen) > 1:
        raise MehrdeutigeKurzID(sid, sorted({z["mandant"] for z in zeilen}))
    return dict(zeilen[0]) if zeilen else None


def candidate_of(row: dict) -> dict:
    """Rekonstruiert den Kandidaten (cpv zurück zu set) aus der DB-Zeile."""
    cand = json.loads(row["candidate"])
    if isinstance(cand.get("cpv"), list):
        cand["cpv"] = set(cand["cpv"])
    cand["_m"] = {"score": cand.get("_score"), "reasons": []}
    return cand


def mark_built(db: Path, sid: str, mandant: str | None = None) -> None:
    with _conn(db) as c:
        if mandant:
            c.execute("UPDATE hits SET dossier_built=1 WHERE short_id=? AND mandant=?",
                      (sid.lower(), mandant))
        else:
            c.execute("UPDATE hits SET dossier_built=1 WHERE short_id=?", (sid.lower(),))


def recent(db: Path, mandant: str | None = None, limit: int = 15) -> list[dict]:
    q = "SELECT short_id,mandant,title,score,pubday FROM hits"
    args: list = []
    if mandant:
        q += " WHERE mandant=?"
        args.append(mandant)
    q += " ORDER BY first_seen DESC, score DESC LIMIT ?"
    args.append(limit)
    with _conn(db) as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]
