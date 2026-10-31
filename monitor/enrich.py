#!/usr/bin/env python3
"""
Vergabe-Monitor — Detail-Anreicherung je Treffer

Das CSV (Kern-Filter) ist die Kurzfassung. Für Fristen und Details holt dieses
Modul den REICHEN Datensatz einer einzelnen Bekanntmachung — on demand, nur für
Treffer, die es wert sind (nicht für den ganzen Tag).

Zwei Quellen desselben offenen API-Endpunkts (kein Account, DL-DE 2.0):
  OCDS-JSON   → Vergabeunterlagen-Link, Vertragslaufzeit, alle Lose, Kontakt
  eForms-XML  → exakte Angebotsfrist + Nachfragefrist (im CSV/OCDS NICHT enthalten)

Tagespakete werden in raw/ gecacht (wie im Kern), also 1 Download je Tag/Format.
"""

from __future__ import annotations

import io
import urllib.request
import zipfile
from pathlib import Path

from lxml import etree

API = "https://oeffentlichevergabe.de/api/notice-exports"
ACC_OCDS = "application/vnd.bekanntmachungsservice.ocds.zip+zip"
ACC_EFORMS = "application/vnd.bekanntmachungsservice.eforms.zip+zip"

# Fremdes XML wird ausdruecklich gehaertet gelesen. Der Datenservice ist
# vertrauenswuerdig, die Inhalte stellen aber beliebige Vergabestellen ein.
#
# Geprueft 21.08.2026 mit lxml 6.1.2: Der Standard-Parser laedt externe Entities
# dort ohnehin nicht (er weist ein Dokument mit undefinierter Entity sogar ab) --
# eine akute Luecke war es also nicht. Die Angabe steht trotzdem hier, weil das
# Verhalten von der lxml-Version abhaengt und die Absicht sonst nirgends steht.
# eForms braucht keine Entities, das Abschalten kostet nichts.
_PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    huge_tree=False,
    load_dtd=False,
)

# eForms/UBL-Namespaces (nur die gebrauchten)
NS = {
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
}


def _fetch_zip(day: str, accept: str, raw: Path, tag: str) -> Path | None:
    raw.mkdir(parents=True, exist_ok=True)
    target = raw / f"{tag}-{day}.zip"
    if target.exists() and target.stat().st_size > 0:
        return target
    req = urllib.request.Request(f"{API}?pubDay={day}", headers={"Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:  # nosec B310 -- API ist fest https
            data = r.read()
    except Exception:  # noqa: BLE001
        return None
    if not data:
        return None
    target.write_bytes(data)
    return target


def _member(zip_path: Path, notice_id: str, suffix: str) -> bytes | None:
    """Findet die Datei zur notice_id im Tagespaket (Name beginnt mit der UUID)."""
    with zipfile.ZipFile(zip_path) as zf:
        for n in zf.namelist():
            if n.endswith(suffix) and notice_id in n:
                return zf.read(n)
    return None


# --------------------------------------------------------------------------- #
# OCDS: strukturierte Zusatzfelder
# --------------------------------------------------------------------------- #
def from_ocds(notice_id: str, day: str, raw: Path) -> dict:
    zp = _fetch_zip(day, ACC_OCDS, raw, "ocds")
    if not zp:
        return {}
    blob = _member(zp, notice_id, ".json")
    if not blob:
        return {}
    import json
    rel = json.loads(blob).get("releases", [{}])[0]
    t = rel.get("tender", {})
    out: dict = {}

    # Vergabeunterlagen-Link (erste eindeutige URL)
    urls = []
    for d in t.get("documents") or []:
        u = d.get("url")
        if u and u not in urls:
            urls.append(u)
    out["unterlagen_urls"] = urls

    # Vertragslaufzeit (aus dem ersten Los mit contractPeriod)
    for lot in t.get("lots") or []:
        cp = lot.get("contractPeriod") or {}
        if cp.get("startDate") or cp.get("endDate"):
            out["laufzeit_start"] = (cp.get("startDate") or "")[:10]
            out["laufzeit_ende"] = (cp.get("endDate") or "")[:10]
            break

    # Lose-Übersicht
    out["lose"] = [
        {"id": los.get("id"), "titel": (los.get("title") or "").strip()}
        for los in (t.get("lots") or [])
    ]

    # Vergabestelle-Kontakt (procuringEntity / buyer-Partei)
    for p in rel.get("parties") or []:
        if "buyer" in (p.get("roles") or []) or "procuringEntity" in (p.get("roles") or []):
            c = p.get("contactPoint") or {}
            addr = p.get("address") or {}
            out["kontakt"] = {
                "name": p.get("name"),
                "email": c.get("email"),
                "tel": c.get("telephone"),
                "url": (c.get("url") or ""),
                "ort": f'{addr.get("postalCode","")} {addr.get("locality","")}'.strip(),
            }
            break

    out["verfahren_details"] = t.get("procurementMethodDetails")
    return out


# --------------------------------------------------------------------------- #
# eForms-XML: exakte Fristen (im OCDS/CSV nicht abgebildet)
# --------------------------------------------------------------------------- #
def from_eforms(notice_id: str, day: str, raw: Path) -> dict:
    zp = _fetch_zip(day, ACC_EFORMS, raw, "eforms")
    if not zp:
        return {}
    blob = _member(zp, notice_id, ".xml")
    if not blob:
        return {}
    try:
        root = etree.parse(io.BytesIO(blob), _PARSER).getroot()
    except etree.XMLSyntaxError:
        return {}
    out: dict = {}

    dl = root.find(".//cac:TenderSubmissionDeadlinePeriod", NS)
    if dl is not None:
        d = dl.findtext("cbc:EndDate", "", NS)[:10]
        tm = dl.findtext("cbc:EndTime", "", NS)[:5]
        if d:
            out["angebotsfrist"] = f"{d} {tm}".strip()

    # Link zu den Vergabeunterlagen auf der Plattform der Vergabestelle (BT-15).
    # Befund 16.10.2026: Die Adresse beim Datenservice funktioniert nur per Klick; aus
    # der Adresszeile kopiert führt sie auf eine 404. Der Plattform-Link ist dagegen
    # eine feste Adresse, und dort liegen die Unterlagen. Gemessen am Tagespaket
    # 15.10.2026: 649 von 660 Bekanntmachungen nennen ihn, Stichprobe über 40
    # Plattformen lieferte die richtige Seite (JavaScript-Portale und die
    # Cookie-Abfrage von evergabe-online.de im Browser).
    for uri in root.iterfind(".//cac:CallForTendersDocumentReference/cac:Attachment/"
                             "cac:ExternalReference/cbc:URI", NS):
        u = (uri.text or "").strip()
        if u.lower().startswith(("https://", "http://")):
            out["unterlagen_url"] = u
            break

    # Nachfrage-/Bieterfragen-Frist (falls vorhanden)
    for period in root.findall(".//cac:AdditionalInformationRequestPeriod", NS):
        d = period.findtext("cbc:EndDate", "", NS)[:10]
        if d:
            out["nachfragefrist"] = d
            break
    return out


def enrich(cand: dict, day: str, raw: Path) -> dict:
    """Kombiniert OCDS + eForms zu einem Zusatz-dict je Treffer.
    day = pubDay des Treffers (aus dem Report/Kandidaten ableitbar)."""
    nid = cand.get("notice_id", "")
    data = {}
    data.update(from_ocds(nid, day, raw))
    data.update(from_eforms(nid, day, raw))
    return data
