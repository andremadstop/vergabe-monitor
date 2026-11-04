# Vergabe-Monitor

Findet öffentliche Ausschreibungen, die zu einem Unternehmen passen. Jeden Tag holt er die neuen Bekanntmachungen aus offenen Quellen, führt sie in ein gemeinsames Format zusammen, bewertet sie gegen ein Suchprofil und schreibt einen Bericht.

    Datenservice Öffentlicher Einkauf    Vergabemarktplätze     service.bund.de
    Tagespaket (CSV), eForms-XML         Open-Data-Export       allgemeiner Feed
                   |                            |                       |
                   +-------------+--------------+-----------------------+
                                 v
                  ein gemeinsames Format, ein Los je Kandidat
                                 v
                  Suchprofil: Region, CPV, Begriffe, Wert, Frist
                                 v
                  Bericht: jeder Treffer mit seiner Begründung
                                 |
                  Wächter: prüft jeden Lauf an seinen eigenen Zahlen

Import-Kern des Vergabe-Monitors der [Stiebitz Souverain Systems UG](https://souverain-systems.de), Potsdam. Seit August 2026 läuft er täglich für mehrere Profile: vom 21.08. bis 31.10.2026 an 71 Tagen 3.255 Treffer für 7 Profile ([Betriebsnachweis](beispielberichte/laeufe/2026-10-31-betrieb.txt)). Dieses Repository ist ein Auszug: Kundenprofile, Zustellung (Mail, Portal) und Auswertungen für Kunden gehören nicht dazu. Mit KI-Unterstützung geschrieben, von uns geprüft und betrieben.

Warum er so gebaut ist:

- **Offene Daten, mit Respekt.** Abgerufen wird nur, was als offene Daten bereitsteht. Eine robots.txt, die Automaten ausschließt, gilt. Quelle und Lizenz stehen in jedem Bericht.
- **Nichts still verwerfen.** Was unsicher ist (Ort unbekannt, Ausschluss-Verdacht, Frist fehlt), bleibt sichtbar und wird markiert, statt zu verschwinden.
- **Gehört dem, der ihn betreibt.** Kein Konto, kein Schlüssel, keine fremde Plattform: Ein Suchprofil ist eine YAML-Datei, der Speicher eine SQLite-Datei, der Code steht unter der EUPL. Wer ihn nutzt, kann ihn lesen, ändern und weitergeben.

Wem er was verdankt, Werkzeug für Werkzeug, und wie alt die öffentliche Bekanntmachung ist: [DANK.md](DANK.md).

## Quellen

| Quelle | Was | Abruf |
|---|---|---|
| [Datenservice Öffentlicher Einkauf](https://oeffentlichevergabe.de) | alle Bekanntmachungen im eForms-Format, Bund, Länder, Kommunen, ober- und unterschwellig | Tagespaket als ZIP (CSV), Fristen aus dem eForms-XML, `monitor/vergabe_monitor.py`, `monitor/enrich.py` |
| Vergabemarktplätze der Länder | unterschwellige Verfahren, die nicht beim Datenservice stehen | Open-Data-Export (cosinex), nur wo freigeschaltet, `monitor/vmp_opendata.py`, Liste in `configs/plattformen.yaml` |
| [service.bund.de](https://www.service.bund.de) | allgemeiner Ausschreibungs-Feed, ein Abruf je Lauf (die Suchmaske ist per robots.txt gesperrt) | RSS, `monitor/sbund_feed.py` |

Datenservice und Vergabemarktplätze stellen ihre Daten unter der Datenlizenz Deutschland bereit (2.0 bzw. Zero 2.0); service.bund.de nennt für seinen Feed keine Lizenz, aus ihm übernimmt der Monitor Titel, Vergabestelle, Ort und den Link zur Bekanntmachung. Ein Konto oder API-Schlüssel ist nicht nötig.

**Plattformen, die laut robots.txt keine Automaten wollen, werden nicht abgerufen**, auch der Vergabemarktplatz Brandenburg nicht. Abgerufen wird nur, was als offene Daten bereitsteht. `configs/plattformen.yaml` hält für jede Landesplattform fest, ob es einen solchen Weg gibt und warum nicht.

## Was er macht

- **Zusammenführen auf Los-Ebene:** Ein Kandidat ist ein Los, nicht das ganze Verfahren. Einzelne Leistungen stecken oft als Teil-Los in großen Vergaben.
- **Bewerten gegen ein Suchprofil:** Region (NUTS), CPV-Codes, Suchbegriffe im Text, Auftragswert, Mindestvorlauf bis zur Frist. Jeder Treffer trägt seine Begründung („Warum: Region DE4; CPV 72000000; Muster: geodaten, wfs“).
- **Nichts still verwerfen:** Was nach Ausschluss klingt (Generalunternehmer gefordert, Präqualifikation), bleibt sichtbar und wird markiert. Ohne Leistungsort bleibt ein Treffer drin und wird als „Ort unbekannt“ geführt.
- **Vergebenes heraushalten:** Zuschläge, Vertragsänderungen und Direktvergaben stehen auch im Tagespaket, sind aber keine offenen Ausschreibungen.
- **Echte Fristen:** Der CSV-Export kennt nur den Öffnungstermin. Für Treffer liest der Monitor die Angebotsfrist aus dem eForms-XML nach.
- **Orte ohne NUTS-Code:** Die Länderplattformen liefern keinen NUTS-Code. Der Monitor ermittelt das Land aus PLZ und Ortsname und verwirft nur bei eindeutiger Zuordnung.
- **Treffer-Speicher:** jeder Treffer mit stabiler Kurz-ID in SQLite, je Profil getrennt.
- **Wächter:** prüft jeden Tageslauf an seinen eigenen Zahlen gegen den Median der letzten 14 Läufe (Kanal still, Tagespaket fehlt, Vergebenes durchgerutscht, auffällig viele Treffer ohne Frist). `monitor/waechter.py`

## Start

    uv run --no-project --with-requirements requirements.txt \
        python monitor/vergabe_monitor.py --config configs/beispiel-geodaten-it.yaml --days 7

Rohdaten landen in `raw/` (je Tag einmal geladen), Berichte in `reports/`. Ein eigenes Profil ist eine Kopie von `configs/beispiel-geodaten-it.yaml`.

Weitere Kanäle:

    python monitor/vmp_opendata.py --quellen configs/plattformen.yaml --archiv raw/vmp_archiv.db \
        --config configs/beispiel-geodaten-it.yaml --db raw/hits.db
    python monitor/sbund_feed.py --archiv raw/vmp_archiv.db \
        --config configs/beispiel-geodaten-it.yaml --db raw/hits.db

## Beispiel

[`beispielberichte/`](beispielberichte/) enthält echte Läufe vom 31.10.2026 aller drei Kanäle und des Wächters, mit dem erfundenen Profil eines kleinen Geodaten-IT-Büros in Berlin und Brandenburg. Beim Datenservice: sieben Tagespakete, 8.305 Lose, 43 Treffer, für 24 davon die Angebotsfrist aus dem eForms-XML.

## Was absichtlich fehlt

Dieser Auszug enthält, was Bekanntmachungen holt, vereinheitlicht und bewertet. Nicht enthalten sind die Profile unserer Kunden, die Zustellung (tägliche Mail, Kundenportal mit eigener Instanz je Kunde), Dossiers je Ausschreibung und die Auswertung von Zuschlägen. Diese Teile hängen an Kundendaten oder an unserem Betrieb.

## Sicherheit

- Fremdes XML wird nur über `defusedxml` bzw. `lxml` ohne Entitäten, DTD und Netzzugriff gelesen.
- Abrufe nur über https: feste Adressen im Code, Quellen aus der Konfiguration werden vor dem Abruf geprüft.
- SQL nur mit Parametern.
- Abhängigkeiten mit Prüfsummen festgeschrieben (`requirements.txt`), Lückenprüfung bei jeder Änderung und wöchentlich.

## Prüfen

    ./pruefen.sh

ruff, bandit, pip-audit, Tests ohne Netz (Bewertung, Fristen, Kontakte, Wächter, Speicher und ein Test, dass präpariertes XML abgewiesen wird), Secret-Scan und Stückliste (SBOM). Dieselben Prüfungen laufen in GitHub Actions bei jedem Push und wöchentlich.

## Lizenz

Code: [EUPL-1.2](LICENSE), © 2026 Stiebitz Souverain Systems UG (haftungsbeschränkt). Daten in den Berichten: Datenlizenz Deutschland 2.0, Quelle Datenservice Öffentlicher Einkauf.

Komponenten und Datenquellen mit Lizenz: [DANK.md](DANK.md) (English: [ACKNOWLEDGEMENTS.md](ACKNOWLEDGEMENTS.md)). Zitieren: [CITATION.cff](CITATION.cff), GitHub zeigt dafür „Cite this repository“. Sicherheitslücken bitte nach [SECURITY.md](SECURITY.md) melden.
