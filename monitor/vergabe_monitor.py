#!/usr/bin/env python3
"""
Vergabe-Monitor — Tageslauf gegen den Datenservice Öffentlicher Einkauf

Zieht die offenen Bekanntmachungen des Datenservice Öffentlicher Einkauf
(oeffentlichevergabe.de) für einen oder mehrere Tage, joint sie auf LOS-Ebene,
filtert gegen ein Suchprofil und schreibt einen Markdown-Report.

Kein Account, kein API-Key. Datenlizenz Deutschland 2.0 (Quellenangabe im Report).
Profil: configs/<profil>.yaml (Beispiel: configs/beispiel-geodaten-it.yaml)

Grenzen (ehrlich):
- Die exakte Angebotsfrist steht NICHT im CSV-Export, nur im vollen eForms-XML.
  Wir nehmen publicOpeningDate als Näherung ("Frist ≈ Öffnung") und markieren das.
- Ausschluss-Erkennung (Rahmenvertrag/GU/Präqualifikation) ist Freitext-Heuristik
  über die Beschreibung — sie irrt in beide Richtungen. Deshalb wird ausgeschlossenes
  NICHT verworfen, sondern separat als "geflaggt" ausgewiesen (kein stiller Cap).
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML fehlt:  pip install pyyaml")

API = "https://oeffentlichevergabe.de/api/notice-exports"
ACCEPT_CSV_ZIP = "application/vnd.bekanntmachungsservice.csv.zip+zip"
LIZENZ = "Datenlizenz Deutschland 2.0 — Quelle: Datenservice Öffentlicher Einkauf (oeffentlichevergabe.de)"

# CSV-Feldgrößen können groß sein (lange Beschreibungstexte in purpose.csv)
csv.field_size_limit(10_000_000)


# --------------------------------------------------------------------------- #
# Abruf
# --------------------------------------------------------------------------- #
def fetch(pub_day: str, raw_dir: Path) -> Path | None:
    """Lädt das Tagespaket (ZIP) für pub_day; überspringt, wenn schon vorhanden.
    Gibt den Pfad zurück, oder None wenn für den Tag keine Daten existieren."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    target = raw_dir / f"doe-{pub_day}.zip"
    if target.exists() and target.stat().st_size > 0:
        return target
    url = f"{API}?pubDay={pub_day}"
    req = urllib.request.Request(url, headers={"Accept": ACCEPT_CSV_ZIP})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310 -- API ist fest https
            data = resp.read()
    except urllib.error.HTTPError as e:
        # 400 = kein Datensatz für diesen Tag (z. B. Zukunft/Wochenende früh)
        if e.code == 400:
            return None
        raise
    if not data:
        return None
    target.write_bytes(data)
    return target


# --------------------------------------------------------------------------- #
# Laden + Join auf Losebene
# --------------------------------------------------------------------------- #
def _read(zf: zipfile.ZipFile, name: str) -> list[dict]:
    if name not in zf.namelist():
        return []
    raw = zf.read(name).decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(raw)))


def _key(row: dict) -> tuple:
    return (row.get("noticeIdentifier", ""), row.get("noticeVersion", ""))


def _lotkey(row: dict) -> tuple:
    return (row.get("noticeIdentifier", ""), row.get("noticeVersion", ""),
            row.get("lotIdentifier", ""))


def build_candidates(zip_path: Path) -> list[dict]:
    """Ein Kandidat = ein LOS (bzw. das Verfahren selbst, wenn ohne Lose).
    Einzelne Gewerke stecken oft als Teil-Lose in großen Bauvergaben,
    darum ist das Los die Einheit, nicht das Verfahren."""
    with zipfile.ZipFile(zip_path) as zf:
        notices = {_key(r): r for r in _read(zf, "notice.csv")}
        purpose = _read(zf, "purpose.csv")
        classif = _read(zf, "classification.csv")
        places = _read(zf, "placeOfPerformance.csv")
        procedures = {_key(r): r for r in _read(zf, "procedure.csv")}
        subterms = {_lotkey(r): r for r in _read(zf, "submissionTerms.csv")}
        addinfo = {_lotkey(r): r for r in _read(zf, "additionalInformation.csv")}
        orgs = _read(zf, "organisation.csv")

    # Käufer je notice (erste buyer-Organisation)
    buyers: dict[tuple, dict] = {}
    for r in orgs:
        if r.get("organisationRole") == "buyer":
            buyers.setdefault(_key(r), r)

    # CPV-Codes je Losschlüssel sammeln (main + additional)
    cpv_by_lot: dict[tuple, set[str]] = {}
    for r in classif:
        if r.get("classificationType") != "cpv":
            continue
        codes = set()
        if r.get("mainClassificationCode"):
            codes.add(r["mainClassificationCode"].strip())
        for c in (r.get("additionalClassificationCodes") or "").split(","):
            c = c.strip()
            if c:
                codes.add(c)
        cpv_by_lot.setdefault(_lotkey(r), set()).update(codes)

    # Ort je Losschlüssel
    place_by_lot: dict[tuple, dict] = {}
    for r in places:
        place_by_lot.setdefault(_lotkey(r), r)

    # Dedup: Jedes Verfahren hat eine purpose-Zeile auf VERFAHRENSEBENE (lot='')
    # UND je eine auf LOSEBENE (LOT-xxxx), oft mit identischem Titel. Die
    # Sammelzeile ist dann ein Duplikat der Losdaten → verwerfen, sobald das
    # Verfahren mindestens ein echtes Los hat. Verfahren ganz OHNE Lose behalten
    # ihre ''-Zeile. (Echte Mehr-Los-Verfahren bleiben vollständig — jedes Los eigen.)
    # Cross-notice-Dubletten (dieselbe Ausschreibung als Erst- + Änderungs-
    # bekanntmachung, verschiedene noticeIdentifier) werden bewusst NICHT gemerged
    # — das bräuchte den procedureIdentifier-Link und könnte echte Zweitverfahren
    # verstecken (Phase-2-Verfeinerung über changes.csv).
    notices_with_lots = {
        _key(p) for p in purpose if (p.get("lotIdentifier") or "").strip()
    }
    purpose = [
        p for p in purpose
        if (p.get("lotIdentifier") or "").strip()
        or _key(p) not in notices_with_lots
    ]

    candidates = []
    for p in purpose:
        lk = _lotkey(p)
        nk = _key(p)
        notice = notices.get(nk, {})
        place = place_by_lot.get(lk) or place_by_lot.get((nk[0], nk[1], "")) or {}
        # CPV: erst Losebene, sonst Verfahrensebene (leerer lotIdentifier)
        cpv = cpv_by_lot.get(lk) or cpv_by_lot.get((nk[0], nk[1], "")) or set()
        buyer = buyers.get(nk, {})
        sub = subterms.get(lk, {})
        info = addinfo.get(lk, {})
        candidates.append({
            "notice_id": nk[0],
            "lot_id": p.get("lotIdentifier", ""),
            "legal_basis": notice.get("procedureLegalBasis", ""),
            "notice_type": notice.get("noticeType", ""),
            "form_type": notice.get("formType", ""),
            "pub_date": notice.get("publicationDate", ""),
            "title": (p.get("title") or "").strip(),
            "description": (p.get("description") or "").strip(),
            "value": (p.get("estimatedValue") or "").strip(),
            "currency": (p.get("estimatedValueCurrency") or "").strip(),
            "cpv": cpv,
            "city": (place.get("placePerformanceCity") or "").strip(),
            "plz": (place.get("placePerformancePostCode") or "").strip(),
            "nuts": (place.get("placePerformanceCountrySubdivision") or "").strip(),
            "buyer": (buyer.get("organisationName") or "").strip(),
            "buyer_id": (buyer.get("organisationIdentifier") or "").strip(),
            "buyer_nuts": (buyer.get("organisationCountrySubdivision") or "").strip(),
            "buyer_url": (buyer.get("organisationInternetAddress") or "").strip(),
            "procedure_type": procedures.get(nk, {}).get("procedureType", ""),
            "opening": (sub.get("publicOpeningDate") or "").strip(),
            "sme_suitable": (info.get("suitableForSMEs") or "").strip(),
        })
    return candidates


# --------------------------------------------------------------------------- #
# Filter / Matching
# --------------------------------------------------------------------------- #
def _cpv_prefixes(codes: list[str]) -> list[str]:
    """Config-CPV in signifikante Präfixe wandeln (Nullen abschneiden).
    90910000 -> '9091'  ·  45262690 -> '4526269'  ·  44316500 -> '443165'.
    CPV ist hierarchisch; ein Kandidat matcht, wenn sein Code mit dem Präfix
    beginnt (fängt also feinere Unterkategorien mit)."""
    out = []
    for c in codes:
        c = str(c).strip().rstrip("0") or "0"
        out.append(c)
    return out


EXCLUSION_PATTERNS = {
    "rahmenvereinbarung": "rahmenvertraege",
    "rahmenvertrag": "rahmenvertraege",
    "generalunternehmer": "generalunternehmer_pflicht",
    "präqualifik": "praequalifikation_pflicht",
    "praequalifik": "praequalifikation_pflicht",
    "pq-verein": "praequalifikation_pflicht",
}


# Bekanntmachungsarten, hinter denen keine offene Ausschreibung mehr steht.
# 🔴 Befund 16.10.2026: build_candidates nimmt JEDE Bekanntmachung des Tagespakets,
#    also auch Zuschlaege (result), Vertragsaenderungen (cont-modif) und
#    Direktvergaben (dir-awa-pre). Im September waren das in zwei Profilen 60 von
#    197 und 245 von 587 Treffern -- sie standen als Ausschreibungen ohne Frist
#    in der Liste. Vergebene Auftraege gehoeren in eine eigene Zuschlagsauswertung,
#    nicht in die Trefferliste.
#    Kanaele ohne formType (service.bund.de, Vergabemarktplaetze) sind nicht betroffen.
VERGEBEN_FORMTYPEN = {"result", "cont-modif", "dir-awa-pre"}


def ist_ex_post(verfahrensart: str | None) -> bool:
    """Vergabemarktplaetze (cosinex) melden Vergebenes ueber die Verfahrensart:
    "Ex post Veroeffentlichung …" und "Bekanntmachung vergebener Auftraege". Im
    lokalen Archiv vom 16.10.2026 waren das 1.554 von 2.264 Bekanntmachungen.
    "Ex ante" bleibt: das kuendigt eine beschraenkte Ausschreibung an, bei der man
    Interesse anmelden kann -- gerade die kleinen Auftraege."""
    v = (verfahrensart or "").strip().lower()
    return v.startswith("ex post") or "vergebener auftr" in v


def _norm_name(s: str) -> str:
    return " ".join((s or "").lower().replace("ß", "ss").replace("ä", "ae")
                    .replace("ö", "oe").replace("ü", "ue").split())


def auftraggeber_treffer(cand: dict, liste: list) -> str | None:
    """Name des Auftraggebers aus der Config, wenn die Vergabestelle dazu passt.

    Abgleich zuerst ueber die Kennung (`organisationIdentifier`, bei der SPSG die
    USt-IdNr. DE138408360), dann ueber den Namen als Teilzeichenkette nach
    Umlaut-Normalisierung. Eintraege: {name, kennung?, cpv_codes?}. Mit
    `cpv_codes` zaehlt die Stelle nur fuer Lose in diesen Bereichen -- die SPSG
    vergibt zu 90 % Bauleistungen, die fuer die UG nichts sind.
    """
    bid = (cand.get("buyer_id") or "").strip().upper()
    bname = _norm_name(cand.get("buyer", ""))
    for a in liste or []:
        kennung = str(a.get("kennung") or "").strip().upper()
        name = _norm_name(a.get("name", ""))
        passt = (kennung and bid == kennung) or (name and bname and name in bname)
        if not passt:
            continue
        pref = _cpv_prefixes(a.get("cpv_codes") or [])
        if pref and not any(c.startswith(p) for c in cand["cpv"] for p in pref):
            continue
        return a.get("kurz") or a.get("name")
    return None


def match(cand: dict, cfg: dict) -> dict:
    """Bewertet einen Kandidaten gegen die Config.
    Rückgabe: dict mit hit(bool), reasons(list), flags(list), score(int)."""
    sr = cfg.get("suchraum", {})
    schwellen = cfg.get("schwellen", {})
    regionen = [r.upper() for r in sr.get("regionen", [])]
    cpv_prefixes = _cpv_prefixes(sr.get("cpv_codes", []))
    muster = [m.lower() for m in sr.get("freitext_muster", [])]
    ausschluss_cfg = set(cfg.get("ausschluss", []))
    # Opt-in (22.10.2026): Höchstwert und Ausschlüsse verwerfen statt nur
    # zu flaggen. Bewusst kein neuer Standard -- bereits kalibrierte Profile
    # laufen weiter wie gemessen, bis sie den Schalter selbst setzen.
    als_filter = bool(schwellen.get("als_filter"))

    reasons, flags, score = [], [], 0
    text = f"{cand['title']} {cand['description']}".lower()

    if cand.get("form_type") in VERGEBEN_FORMTYPEN or ist_ex_post(cand.get("procedure_type")):
        return {"hit": False, "reasons": [], "flags": [], "score": 0,
                # .get statt ['form_type']: VMP-Kandidaten haben das Feld nicht, der
                # Kanal brach daran am 21.10.2026 komplett ab (KeyError im Tageslauf).
                "reject": "schon vergeben ("
                          f"{cand.get('notice_type') or cand.get('form_type')
                             or cand.get('procedure_type')})"}

    # --- Region (hartes Kriterium, aber Ort-unbekannt wird nicht verworfen) ---
    nuts = cand["nuts"].upper()
    # Opt-in (16.10.2026): ohne Leistungsort gilt der Sitz der Vergabestelle.
    # Ohne den Schalter liefen im Halbjahrestest GIZ-Pools in Westafrika, Thueringen
    # und Schleswig-Holstein in ein Berlin/Brandenburg-Profil. Bewusst kein neuer
    # Standard: bereits kalibrierte Profile bleiben, wie sie gemessen sind.
    if not nuts and sr.get("ort_ersatz_vergabestelle"):
        nuts = (cand.get("buyer_nuts") or "").upper()
        # Verworfen wird NUR beim Datenservice (quelle_art None), wo jede Vergabestelle
        # eine Region tragen kann und im Test Halle, Naumburg, Stendal und Hamburg (DESY)
        # ohne beides durchkamen. service.bund.de und die Vergabemarktplaetze kennen kein
        # buyer_nuts und lassen `nuts` bei mehrdeutigem Ort bewusst leer -- dort bleibt
        # es beim alten "Ort unbekannt (nicht verworfen)", sonst fiele genau der
        # unterschwellige Brandenburger Kanal (auch die SPSG) still weg.
        if not nuts and cand.get("quelle_art") is None:
            return {"hit": False, "reasons": [], "flags": [], "score": 0,
                    "reject": "Ort unbekannt, Vergabestelle ohne Region"}
    if nuts:
        if any(nuts.startswith(r) for r in regionen):
            reasons.append(f"Region {nuts}")
            score += 20
        else:
            return {"hit": False, "reasons": [], "flags": [], "score": 0,
                    "reject": f"Ort {nuts} außerhalb {regionen}"}
    else:
        flags.append("Ort unbekannt (nicht verworfen)")

    # --- CPV (Losebene) ---
    cpv_hits = sorted(c for c in cand["cpv"]
                      if any(c.startswith(pref) for pref in cpv_prefixes))
    if cpv_hits:
        reasons.append("CPV " + ", ".join(cpv_hits))
        score += 50

    # --- Freitext ---
    text_hits = [m for m in muster if m in text]
    if text_hits:
        reasons.append("Muster: " + ", ".join(text_hits))
        score += 30

    # --- Auftraggeber (optional, suchraum.auftraggeber) ---
    ag = auftraggeber_treffer(cand, sr.get("auftraggeber") or [])
    if ag:
        reasons.append(f"Auftraggeber {ag}")
        score += 40

    # Ohne CPV-, Freitext- UND Auftraggeber-Treffer ist es kein Kandidat
    if not cpv_hits and not text_hits and not ag:
        return {"hit": False, "reasons": [], "flags": [], "score": 0,
                "reject": "kein CPV-/Freitext-Treffer"}

    # --- Wert ---
    val_min = schwellen.get("auftragswert_min")
    val_max = schwellen.get("auftragswert_max")
    if cand["value"]:
        try:
            v = float(cand["value"])
            if als_filter and val_max and v > float(val_max):
                return {"hit": False, "reasons": [], "flags": [], "score": 0,
                        "reject": f"Wert {v:.0f} € > Max {val_max} €"}
            if val_min and v < float(val_min):
                flags.append(f"Wert {v:.0f} € < Min {val_min} €")
            else:
                reasons.append(f"Wert {v:,.0f} {cand['currency'] or '€'}".replace(",", "."))
                score += 10
        except ValueError:
            flags.append("Wert nicht parsebar")
    else:
        flags.append("Wert nicht angegeben")

    # --- SME-Eignung (Bonus) ---
    if cand["sme_suitable"].lower() in ("true", "yes", "ja", "1"):
        reasons.append("KMU-geeignet")
        score += 10

    # --- Ausschluss-Heuristik (flaggt; mit als_filter verwirft sie) ---
    for pat, key in EXCLUSION_PATTERNS.items():
        if pat in text and key in ausschluss_cfg:
            if als_filter:
                return {"hit": False, "reasons": [], "flags": [], "score": 0,
                        "reject": f"Ausschluss {key} (Freitext '{pat}')"}
            flags.append(f"⚠ Ausschluss-Verdacht: {key} (Freitext '{pat}')")

    return {"hit": True, "reasons": reasons, "flags": flags, "score": score}


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
def zu_knapp(cand: dict, cfg: dict, heute: dt.date) -> bool:
    """Vorlaufzeit-Filter (nur mit `schwellen.als_filter`, 22.10.2026).

    Läuft erst NACH dem eForms-Nachladen: vorher kennt der Kandidat nur die
    Angebotsöffnung aus dem CSV, die echte Frist fehlt bei drei von vier.
    Ohne bekannte Frist wird nichts verworfen -- lieber ein Treffer zu viel als
    ein still verschwundener.
    """
    schw = cfg.get("schwellen") or {}
    tage = schw.get("vorlaufzeit_min_tage")
    if not schw.get("als_filter") or not tage:
        return False
    roh = str(cand.get("angebotsfrist") or cand.get("opening") or "").strip()
    try:
        frist = dt.date.fromisoformat(roh[:10])
    except ValueError:
        return False
    return (frist - heute).days < int(tage)


def _legal_label(code: str) -> str:
    if code.startswith("32014L"):
        return "EU-oberschwellig"
    return {"de-vob": "VOB/A", "de-vol": "VOL", "de-uvgo": "UVgO",
            "de-hhr": "Haushaltsrecht"}.get(code, code or "—")


def render_report(hits: list[dict], days: list[str], stats: dict, cfg: dict) -> str:
    mandant = cfg.get("mandant", "?")
    firma = cfg.get("firma", "?")
    hits = sorted(hits, key=lambda h: h["_m"]["score"], reverse=True)
    L = []
    L.append(f"# Vergabe-Monitor — {firma} ({mandant})")
    L.append("")
    L.append(f"> Tage: "
             f"{', '.join(days)}. Erzeugt: {stats['now']}.")
    L.append(f"> Quelle: {LIZENZ}")
    L.append("")
    L.append(f"**{len(hits)} Treffer** aus {stats['candidates']} Losen "
             f"in {stats['days_with_data']}/{len(days)} Tagen mit Daten.")
    L.append("")
    L.append("⚠ Radar nur für **förmliche Verfahren**. Direktaufträge < 100.000 € "
             "erscheinen oft gar nicht öffentlich — dieser Report sieht sie nicht.")
    L.append("")
    if not hits:
        L.append("_Keine Treffer. Filter ggf. zu eng — Trefferlog unten prüfen._")
    for h in hits:
        c, m = h, h["_m"]
        frist = c["opening"][:16].replace("T", " ") if c["opening"] else "—"
        L.append("---")
        L.append(f"### {c['title'] or '(ohne Titel)'}  ·  Score {m['score']}")
        L.append("")
        L.append(f"- **Vergabestelle:** {c['buyer'] or '—'}"
                 + (f"  ·  {c['buyer_url']}" if c["buyer_url"] else ""))
        L.append(f"- **Ort:** {c['city']} {c['plz']} ({c['nuts']})")
        L.append(f"- **Verfahren:** {_legal_label(c['legal_basis'])}"
                 f" · {c['procedure_type'] or '—'} · {c['notice_type'] or '—'}")
        L.append(f"- **Frist (≈ Öffnung):** {frist}  ·  **Veröffentlicht:** "
                 f"{c['pub_date'][:10]}")
        L.append(f"- **Warum:** {'; '.join(m['reasons'])}")
        if m["flags"]:
            L.append(f"- **Flags:** {'; '.join(m['flags'])}")
        L.append(f"- **CPV:** {', '.join(sorted(c['cpv'])) or '—'}")
        if c["description"]:
            desc = c["description"][:400].replace("\n", " ")
            L.append(f"- **Auszug:** {desc}…")
        L.append(f"- `notice:{c['notice_id']}` `lot:{c['lot_id'] or '—'}`")
        L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def daterange(days_back: int) -> list[str]:
    """Letzte N Tage bis gestern (Daten sind T+1)."""
    today = dt.date.today()
    return [(today - dt.timedelta(days=i)).isoformat()
            for i in range(1, days_back + 1)]


def main() -> int:
    ap = argparse.ArgumentParser(description="Vergabe-Monitor: Tageslauf Datenservice")
    ap.add_argument("--config", required=True, type=Path,
                    help="Suchprofil (YAML)")
    ap.add_argument("--day", help="Einzeltag YYYY-MM-DD")
    # Bis 21.08.2026 leitete sich der Ort der Treffer-Datenbank aus --raw ab.
    # Das ist der ZIP-Zwischenspeicher: Wer ihn verlegte, verlegte unbemerkt auch
    # die Datenbank. Im Betrieb liefen dadurch zwei hits.db nebeneinander --
    # der Lauf schrieb in die eine, die Auslieferung las die andere,
    # und beide meldeten Erfolg.
    ap.add_argument("--db", type=Path, default=None,
                    help="Treffer-Datenbank (Vorgabe: <raw>/hits.db)")
    ap.add_argument("--ohne-fristen", action="store_true",
                    help="eForms-Nachladen ueberspringen (schneller, aber die "
                         "Angebotsfrist fehlt dann bei der Haelfte der Treffer)")
    ap.add_argument("--days", type=int, default=1,
                    help="Letzte N Tage (Default 1 = gestern)")
    ap.add_argument("--raw", type=Path, default=Path("raw"),
                    help="Verzeichnis für ZIP-Rohdaten")
    ap.add_argument("--out", type=Path, default=Path("reports"),
                    help="Verzeichnis für Reports")
    args = ap.parse_args()

    cfg = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    days = [args.day] if args.day else daterange(args.days)

    all_hits, total_cands, days_with_data = [], 0, 0
    for day in days:
        zp = fetch(day, args.raw)
        if zp is None:
            print(f"  {day}: keine Daten", file=sys.stderr)
            continue
        days_with_data += 1
        cands = build_candidates(zp)
        total_cands += len(cands)
        day_hits = 0
        for c in cands:
            m = match(c, cfg)
            if m["hit"]:
                c["_m"] = m
                c["_pubday"] = day       # Export-Tag (≠ pub_date!) für enrich/store
                all_hits.append(c)
                day_hits += 1
        print(f"  {day}: {len(cands)} Lose → {day_hits} Treffer", file=sys.stderr)

    # --- Echte Angebotsfrist nachladen -------------------------------------
    # Das CSV liefert nur publicOpeningDate -- die Angebots*oeffnung*, nicht die
    # Angebots*frist*. Zwei verschiedene Termine, und die Frist ist der, an dem
    # etwas eingereicht sein muss. Gemessen 21.08.2026 an 74 Treffern: CSV 18,
    # eForms-XML 40. Ohne diesen Schritt steht bei drei von vier Treffern
    # "keine Frist", obwohl die Haelfte davon eine hat.
    #
    # Nur fuer Treffer, nicht fuer alle Kandidaten: ein Tagespaket je Tag
    # (~250 KB), aber das Auslesen kostet je Verfahren Zeit.
    if all_hits and not args.ohne_fristen:
        import enrich
        geholt = 0
        for c in all_hits:
            try:
                zusatz = enrich.from_eforms(c.get("notice_id", ""), c["_pubday"], args.raw)
            except Exception as ex:  # noqa: BLE001 — die Frist ist Kuer, der Lauf darf nie daran scheitern
                print(f"  [frist] {c.get('notice_id','?')[:8]}: {ex}", file=sys.stderr)
                continue
            if zusatz.get("angebotsfrist"):
                c["angebotsfrist"] = zusatz["angebotsfrist"]
                geholt += 1
            if zusatz.get("nachfragefrist"):
                c["nachfragefrist"] = zusatz["nachfragefrist"]
            if zusatz.get("unterlagen_url"):
                c["unterlagen_url"] = zusatz["unterlagen_url"]
        print(f"  Fristen aus eForms: {geholt} von {len(all_hits)} Treffern",
              file=sys.stderr)

    heute = dt.date.today()
    knapp = [c for c in all_hits if zu_knapp(c, cfg, heute)]
    if knapp:
        all_hits = [c for c in all_hits if not zu_knapp(c, cfg, heute)]
        print(f"  Vorlaufzeit: {len(knapp)} Treffer mit Frist unter "
              f"{cfg['schwellen']['vorlaufzeit_min_tage']} Tagen verworfen", file=sys.stderr)

    stats = {
        "now": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "candidates": total_cands,
        "days_with_data": days_with_data,
    }
    report = render_report(all_hits, days, stats, cfg)

    args.out.mkdir(parents=True, exist_ok=True)
    stamp = args.day or f"{days[-1]}_bis_{days[0]}"
    out_file = args.out / f"{cfg.get('mandant', 'report')}_{stamp}.md"
    out_file.write_text(report, encoding="utf-8")
    print(f"\n{len(all_hits)} Treffer → {out_file}", file=sys.stderr)

    # Treffer im Speicher ablegen (per Kurz-ID wieder auffindbar)
    if all_hits:
        try:
            import store
            db = args.db or (args.raw / "hits.db")
            mandant = cfg.get("mandant", "unbekannt")
            recorded = []
            for c in all_hits:
                day = c.get("_pubday") or (args.day or days[0])
                sid = store.record_hits(db, mandant, day, [c], stats["now"])[0][0]
                recorded.append((sid, c))
        except Exception as e:  # noqa: BLE001
            print(f"[store] Fehler: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
