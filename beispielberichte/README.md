# Beispielläufe vom 31.10.2026

Alle mit dem erfundenen Profil `configs/beispiel-geodaten-it.yaml` (Geodaten-IT, Berlin und Brandenburg).

| Datei | Kanal | Ergebnis |
|---|---|---|
| `beispiel-geodaten-it_2026-10-24_bis_2026-10-30.md` | Datenservice Öffentlicher Einkauf, sieben Tagespakete | 8.305 Lose, 43 Treffer, Angebotsfrist für 24 davon aus dem eForms-XML |
| `laeufe/2026-10-31-vergabemarktplaetze.txt` | Open-Data-Exporte der Vergabemarktplätze (sieben NRW-Endpunkte) | 1.465 Bekanntmachungen archiviert, 0 Treffer, weil keine in Berlin oder Brandenburg liegt. Drei Endpunkte verweigerten an diesem Morgen die Verbindung; der Lauf meldete sie je Quelle als Fehler und holte die übrigen vier |
| `laeufe/2026-10-31-service-bund.txt` | Feed von service.bund.de, ein Abruf je Suchbegriff | 50 Einträge, 3 Treffer |
| `laeufe/2026-10-31-betrieb.txt` | laufender Betrieb (Kennzahlen aus der Treffer-Datenbank) | seit 21.08.2026 an 71 Tagen 3.255 Treffer für 7 Profile; im September an allen 30 Tagen, im Oktober an 30 von 31 |
| `laeufe/2026-10-31-waechter.json` | Wächter über alle Kanäle | Kennzahlen des Tages; Vergleichsmeldungen gibt er erst ab dem sechsten Lauf |

Die Ausschreibung „Geodaten-Import-Pipeline (WFS/CSV → PostgreSQL)“ der TH Brandenburg steht im Bericht des Datenservice auf Platz 2 und kommt auch über service.bund.de (dort aus dem Vergabemarktplatz Brandenburg weitergereicht). Den Marktplatz selbst ruft der Monitor nicht ab, seine robots.txt schließt Automaten aus.

Daten: Datenlizenz Deutschland 2.0 bzw. Zero 2.0, Quellen Datenservice Öffentlicher Einkauf (oeffentlichevergabe.de), service.bund.de, Vergabemarktplätze NRW.
