# Auf den Schultern von …

*English: [ACKNOWLEDGEMENTS.md](ACKNOWLEDGEMENTS.md)*

Dieser Monitor ist klein: sechs Module und drei Bibliotheken. Was er kann, verdankt er fast ganz anderen: Leuten, die Python, SQLite und die XML-Werkzeuge gebaut und offen weitergegeben haben, Ämtern, die ihre Bekanntmachungen als offene Daten bereitstellen, und einer alten Regel, nach der öffentliche Aufträge öffentlich bekannt gemacht werden. Hier steht, wer das ist, wofür wir es brauchen und unter welcher Lizenz. Die vollständige, maschinenlesbare Stückliste erzeugt `./pruefen.sh` als `sbom.cdx.json`.

## Was beim Tageslauf läuft

| Werkzeug | Wofür | Lizenz |
|---|---|---|
| [Python](https://www.python.org/) ab 3.12 | Sprache des Monitors. Aus der Standardbibliothek: `csv` und `zipfile` für die Tagespakete, `urllib` für den Abruf, `sqlite3` für den Speicher, `statistics` und `zoneinfo` für den Wächter | PSF-2.0 |
| [SQLite](https://sqlite.org/) | Treffer-Speicher, Archiv der Marktplätze und des Feeds, Zahlen des Wächters: je eine Datei in `raw/` | Public Domain |
| [lxml](https://lxml.de/) | eForms-XML der Bekanntmachungen lesen (Angebotsfrist), ohne Entitäten, DTD und Netzzugriff | BSD-3-Clause |
| [defusedxml](https://github.com/tiran/defusedxml) | Open-Data-Exporte der Marktplätze und den Feed von service.bund.de lesen; weist präpariertes XML ab | PSF-2.0 |
| [PyYAML](https://pyyaml.org/) | Suchprofile und Plattformliste | MIT |

## Womit geprüft und gebaut wird

| Werkzeug | Wofür | Lizenz |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | Umgebung, festgeschriebene Abhängigkeiten mit Prüfsummen | MIT / Apache-2.0 |
| [Ruff](https://docs.astral.sh/ruff/) | Stil und typische Fehler, mit Sicherheitsregeln | MIT |
| [Bandit](https://bandit.readthedocs.io/) | Sicherheitsprüfung des Codes | Apache-2.0 |
| [pip-audit](https://github.com/pypa/pip-audit) | bekannte Lücken in Abhängigkeiten | Apache-2.0 |
| [pytest](https://pytest.org/) | Tests ohne Netz | MIT |
| [detect-secrets](https://github.com/Yelp/detect-secrets) / [gitleaks](https://github.com/gitleaks/gitleaks) | keine Zugangsdaten im Repository | Apache-2.0 / MIT |
| [CycloneDX](https://cyclonedx.org/) | Stückliste der Abhängigkeiten | Apache-2.0 |
| GitHub Actions und Dependabot | dieselben Prüfungen bei jedem Push und wöchentlich; Updates als Pull Request. Actions auf Commit-Hashes festgeschrieben | Dienste von GitHub |
| [Git](https://git-scm.com/) | Versionen, jede Änderung signiert | GPL-2.0 |

## Woher die Daten kommen

| Daten | Anbieter | Lizenz |
|---|---|---|
| Bekanntmachungen als Tagespaket (CSV), je Treffer das eForms-XML und OCDS-JSON | [Datenservice Öffentlicher Einkauf](https://www.oeffentlichevergabe.de/), Beschaffungsamt des Bundesministeriums des Innern (BeschA), aufgebaut gemeinsam mit der Freien Hansestadt Bremen | dl-de/by-2-0, Quellenangabe in jedem Bericht |
| Unterschwellige Verfahren, täglich ein ZIP je Plattform, sieben Endpunkte in Nordrhein-Westfalen | Vergabemarktplätze auf cosinex-Basis, Liste in `configs/plattformen.yaml` | dl-de/zero-2-0 |
| allgemeiner Ausschreibungs-Feed, ein Abruf je Lauf | [service.bund.de](https://www.service.bund.de/), Bundesverwaltungsamt | auf der Seite keine Lizenz genannt; der Monitor übernimmt Titel, Vergabestelle, Ort und den Link zur Bekanntmachung |

Nicht abgerufen werden Plattformen, deren robots.txt Automaten ausschließt, darunter der Vergabemarktplatz Brandenburg. `configs/plattformen.yaml` hält für jede Landesplattform fest, ob es einen offenen Weg gibt und warum nicht.

## Nach welchen Standards er liest und bewertet

| Standard | Wer | Grundlage |
|---|---|---|
| eForms, in Deutschland als eForms-DE | Europäische Kommission; die deutsche Ausprägung läuft über den Datenservice | [Durchführungsverordnung (EU) 2019/1780](https://eur-lex.europa.eu/eli/reg_impl/2019/1780/oj) vom 23. September 2019, Pflicht für EU-weite Bekanntmachungen seit 25. Oktober 2023 |
| CPV, Gemeinsames Vokabular für öffentliche Aufträge | Europäische Union | [Verordnung (EG) Nr. 2195/2002](https://eur-lex.europa.eu/eli/reg/2002/2195/oj) |
| NUTS, Gebietssystematik, hier für den Leistungsort | Eurostat | [Verordnung (EG) Nr. 1059/2003](https://eur-lex.europa.eu/eli/reg/2003/1059/oj) |
| OCDS, Open Contracting Data Standard, für Details je Treffer | [Open Contracting Partnership](https://standard.open-contracting.org/) | offener Standard |

## Womit Code und Text entstanden sind

| Werkzeug | Wofür | Lizenz |
|---|---|---|
| Claude (Anthropic) | Hilfe beim Schreiben von Code und Text | nicht offen, deshalb hier eigens genannt |

Zur letzten Zeile: Ich baue mit KI. Was davon ins Repository kommt, prüfe ich: Der Monitor läuft seit August 2026 jeden Tag gegen die echten Quellen, jede Änderung geht vor dem Commit durch `pruefen.sh`, und jede Zahl in der README steht in einem Laufbericht unter `beispielberichte/`.

## Kolophon

    1727         1811         2002/03      2014         2023       2026
     |            |            |            |            |          |
    Intelligenz- Amtsblätter, CPV und NUTS Datenlizenz  eForms-DE  dieser
    blätter in   auch für     als EU-Recht Deutschland  und Daten- Monitor
    Preußen      Potsdam                   2.0          service

Gebaut in Potsdam. Dass öffentliche Aufträge öffentlich bekannt gemacht werden, ist älter als jede Plattform, und der Monitor liest nur, was dafür bestimmt ist.

1727 ließ Friedrich Wilhelm I. in Berlin und anderen preußischen Städten Intelligenzblätter einrichten: staatliche Anzeigenblätter, in denen amtliche Bekanntmachungen und Anzeigen zu erscheinen hatten, mit Anzeigenmonopol und Bezugspflicht für Magistrate, Zünfte, Advokaten, Handwerker und Gastwirte. Die Einkünfte gingen an das Militärwaisenhaus in Potsdam. 1811 ordnete Friedrich Wilhelm III. „die Einrichtung der Amtsblätter in den Regierungs-Departements“ an; acht Tage nach dem Erscheinen galt eine Verfügung als „gehörig bekannt gemacht“, und als Beilage lief ein Öffentlicher Anzeiger für Vorladungen und Stellen. Das Blatt für Potsdam erschien ab 1811 als „Amtsblatt der Königlichen Kurmärkischen Regierung“, ab 1816 als „Amtsblatt der Königlichen Regierung zu Potsdam“.

Die Vokabulare, mit denen der Monitor heute bewertet, sind europäisches Recht: CPV seit der Verordnung (EG) Nr. 2195/2002, NUTS seit der Verordnung (EG) Nr. 1059/2003. Dass die Bekanntmachungen frei weiterverwendet werden dürfen, regelt die Datenlizenz Deutschland, Version 1.0 vom Februar 2013, Version 2.0 vom Juli 2014. Seit dem 25. Oktober 2023 gehen alle EU-weiten Bekanntmachungen aus Deutschland im Format eForms-DE über den Datenservice Öffentlicher Einkauf nach TED, der Online-Ausgabe des Supplements zum Amtsblatt der EU; Grundlage ist die Durchführungsverordnung (EU) 2019/1780. Das Tagespaket, das der Monitor jeden Morgen holt, ist der Öffentliche Anzeiger von heute.

Eine jüngere Regel hält er genauso ein: robots.txt, 1994 von Martijn Koster vorgeschlagen und seit September 2022 als RFC 9309 ein Standard der IETF. Eine Plattform, die keine Automaten will, wird nicht abgerufen, auch wenn ihre Daten öffentlich wären.

Quellen (abgerufen am 03.11.2026): Holger Böning, [Pressewesen und Aufklärung](https://publikationen.ub.uni-frankfurt.de/opus4/files/9488/Boening_Pressewesen_und_Aufklaerung.pdf) (Universitätsbibliothek Frankfurt); PR-Museum, [Staatliche Öffentlichkeitsarbeit in Preußen, Anfänge bis Friedrich II.](https://pr-museum.de/organisationen/institutionen/staatliche-oeffentlichkeitsarbeit-in-preussen-anfaenge-bis-friedrich-ii/megatrends-wirtschaftsfoerderung-und-lenkung/) (nach Stader 1989); Busch-Schirm, [Was Amts-Blätter uns berichten](http://www.wgff.de/koblenz/download/Amtsblaetter.pdf) (Westdeutsche Gesellschaft für Familienkunde, Bezirksgruppe Koblenz, 2015, mit dem Wortlaut der Verordnung vom 28. März 1811); Wikisource, [Amtsblätter der preußischen Bezirksregierungen](https://de.wikisource.org/wiki/Amtsbl%C3%A4tter_der_preu%C3%9Fischen_Bezirksregierungen), Abschnitt Potsdam und Berlin; EUR-Lex, Verordnungen [2195/2002](https://eur-lex.europa.eu/eli/reg/2002/2195/oj), [1059/2003](https://eur-lex.europa.eu/eli/reg/2003/1059/oj) und [2019/1780](https://eur-lex.europa.eu/eli/reg_impl/2019/1780/oj); Bundesrat, [Drucksache 203/23](https://dserver.bundestag.de/brd/2023/0203-23.pdf) vom 04.05.2023; Freie Hansestadt Bremen und BeschA, [Pressemitteilung vom 26.10.2023](https://www.finanzen.bremen.de/sixcms/media.php/13/231027_Pressemitteilung_eForms_D%C3%96E-v04.pdf); AG Lizenzierung offener Geodaten, [Empfehlungen zur Lizenzierung offener Geodaten](https://www.geodaten.niedersachsen.de/download/205142/Lizenzierung_offener_Geodaten_21.04.2020_.pdf) (21.04.2020) und GovData, [Lizenztext dl-de/by-2-0](https://www.govdata.de/dl-de/by-2-0); IETF, [RFC 9309](https://www.rfc-editor.org/rfc/rfc9309.html); Amt für Veröffentlichungen der EU, [About TED](https://ted.europa.eu/en/about-ted).

André Stiebitz, Potsdam, November 2026
