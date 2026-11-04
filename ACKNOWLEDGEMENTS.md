# Standing on the shoulders of …

*Deutsch: [DANK.md](DANK.md)*

This monitor is small: six modules and three libraries. Almost everything it can do it owes to others: people who built Python, SQLite and the XML tools and gave them away openly, public agencies that publish their procurement notices as open data, and an old rule that public contracts are announced in public. This is who they are, what we use their work for, and under which licence. `./pruefen.sh` produces the complete machine-readable bill of materials as `sbom.cdx.json`.

## What runs during a daily run

| Tool | Used for | Licence |
|---|---|---|
| [Python](https://www.python.org/) 3.12 or newer | language of the monitor. From the standard library: `csv` and `zipfile` for the daily packages, `urllib` for fetching, `sqlite3` for storage, `statistics` and `zoneinfo` for the watchdog | PSF-2.0 |
| [SQLite](https://sqlite.org/) | hit store, archive of the marketplaces and the feed, the watchdog's figures: one file each in `raw/` | Public Domain |
| [lxml](https://lxml.de/) | reading the eForms XML of notices (tender deadline), without entities, DTD or network access | BSD-3-Clause |
| [defusedxml](https://github.com/tiran/defusedxml) | reading the marketplaces' open-data exports and the service.bund.de feed; rejects crafted XML | PSF-2.0 |
| [PyYAML](https://pyyaml.org/) | search profiles and platform list | MIT |

## What we check and build with

| Tool | Used for | Licence |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | environment, pinned dependencies with hashes | MIT / Apache-2.0 |
| [Ruff](https://docs.astral.sh/ruff/) | style and common mistakes, with security rules | MIT |
| [Bandit](https://bandit.readthedocs.io/) | security check of the code | Apache-2.0 |
| [pip-audit](https://github.com/pypa/pip-audit) | known vulnerabilities in dependencies | Apache-2.0 |
| [pytest](https://pytest.org/) | tests without network | MIT |
| [detect-secrets](https://github.com/Yelp/detect-secrets) / [gitleaks](https://github.com/gitleaks/gitleaks) | no credentials in the repository | Apache-2.0 / MIT |
| [CycloneDX](https://cyclonedx.org/) | bill of materials | Apache-2.0 |
| GitHub Actions and Dependabot | the same checks on every push and weekly; updates as pull requests. Actions pinned to commit hashes | services of GitHub |
| [Git](https://git-scm.com/) | versions, every change signed | GPL-2.0 |

## Where the data comes from

| Data | Provider | Licence |
|---|---|---|
| Notices as a daily package (CSV), per hit the eForms XML and OCDS JSON | [Datenservice Öffentlicher Einkauf](https://www.oeffentlichevergabe.de/), Procurement Agency of the Federal Ministry of the Interior (BeschA), built together with the Free Hanseatic City of Bremen | dl-de/by-2-0, source credit in every report |
| Below-threshold procedures, one ZIP per platform and day, seven endpoints in North Rhine-Westphalia | procurement marketplaces based on cosinex, list in `configs/plattformen.yaml` | dl-de/zero-2-0 |
| general tender feed, one fetch per run | [service.bund.de](https://www.service.bund.de/), Federal Office of Administration | no licence stated on the site; the monitor takes title, contracting authority, place and the link to the notice |

Platforms whose robots.txt excludes automated clients are not fetched, among them the Brandenburg marketplace. `configs/plattformen.yaml` records for every state platform whether an open route exists and why not.

## The standards it reads and scores by

| Standard | Who | Basis |
|---|---|---|
| eForms, in Germany as eForms-DE | European Commission; the German profile runs through the Datenservice | [Implementing Regulation (EU) 2019/1780](https://eur-lex.europa.eu/eli/reg_impl/2019/1780/oj) of 23 September 2019, mandatory for EU-wide notices since 25 October 2023 |
| CPV, Common Procurement Vocabulary | European Union | [Regulation (EC) No 2195/2002](https://eur-lex.europa.eu/eli/reg/2002/2195/oj) |
| NUTS, territorial classification, here for the place of performance | Eurostat | [Regulation (EC) No 1059/2003](https://eur-lex.europa.eu/eli/reg/2003/1059/oj) |
| OCDS, Open Contracting Data Standard, for details per hit | [Open Contracting Partnership](https://standard.open-contracting.org/) | open standard |

## What code and text were made with

| Tool | Used for | Licence |
|---|---|---|
| Claude (Anthropic) | help with writing code and text | not open, therefore named separately here |

About the last line: I build with AI. I check what goes into the repository: the monitor has run against the real sources every day since August 2026, every change goes through `pruefen.sh` before the commit, and every figure in the README is backed by a run report in `beispielberichte/`.

## Colophon

    1727         1811         2002/03      2014          2023       2026
     |            |            |            |             |          |
    Prussian     official     CPV and NUTS Data licence  eForms-DE  this
    Intelligenz- gazettes,    as EU law    Germany 2.0   and Daten- monitor
    blätter      Potsdam                                 service

Built in Potsdam. That public contracts are announced in public is older than any platform, and the monitor only reads what was meant to be read.

In 1727 Frederick William I had Intelligenzblätter set up in Berlin and other Prussian towns: state advertising gazettes in which official announcements and advertisements had to appear, with an advertising monopoly and compulsory subscription for magistrates, guilds, lawyers, craftsmen and innkeepers. The income went to the military orphanage in Potsdam. In 1811 Frederick William III ordered "the establishment of official gazettes in the government departments"; eight days after publication a decree counted as "duly made known", and a Public Advertiser for summonses and vacancies ran as a supplement. The gazette for Potsdam appeared from 1811 as "Amtsblatt der Königlichen Kurmärkischen Regierung", from 1816 as "Amtsblatt der Königlichen Regierung zu Potsdam".

The vocabularies the monitor scores by today are European law: CPV since Regulation (EC) No 2195/2002, NUTS since Regulation (EC) No 1059/2003. That the notices may be freely reused is governed by the Data licence Germany, version 1.0 of February 2013, version 2.0 of July 2014. Since 25 October 2023 all EU-wide notices from Germany go in the eForms-DE format through the Datenservice Öffentlicher Einkauf to TED, the online edition of the Supplement to the Official Journal of the EU; the basis is Implementing Regulation (EU) 2019/1780. The daily package the monitor fetches every morning is today's Public Advertiser.

It keeps a younger rule just the same: robots.txt, proposed by Martijn Koster in 1994 and an IETF standard since September 2022 as RFC 9309. A platform that does not want automated clients is not fetched, even where its data would be public.

Sources (retrieved 3 November 2026): Holger Böning, [Pressewesen und Aufklärung](https://publikationen.ub.uni-frankfurt.de/opus4/files/9488/Boening_Pressewesen_und_Aufklaerung.pdf) (Frankfurt University Library); PR-Museum, [Staatliche Öffentlichkeitsarbeit in Preußen, Anfänge bis Friedrich II.](https://pr-museum.de/organisationen/institutionen/staatliche-oeffentlichkeitsarbeit-in-preussen-anfaenge-bis-friedrich-ii/megatrends-wirtschaftsfoerderung-und-lenkung/) (after Stader 1989); Busch-Schirm, [Was Amts-Blätter uns berichten](http://www.wgff.de/koblenz/download/Amtsblaetter.pdf) (Westdeutsche Gesellschaft für Familienkunde, Koblenz group, 2015, quoting the ordinance of 28 March 1811); Wikisource, [Amtsblätter der preußischen Bezirksregierungen](https://de.wikisource.org/wiki/Amtsbl%C3%A4tter_der_preu%C3%9Fischen_Bezirksregierungen), section Potsdam und Berlin; EUR-Lex, Regulations [2195/2002](https://eur-lex.europa.eu/eli/reg/2002/2195/oj), [1059/2003](https://eur-lex.europa.eu/eli/reg/2003/1059/oj) and [2019/1780](https://eur-lex.europa.eu/eli/reg_impl/2019/1780/oj); Bundesrat, [Drucksache 203/23](https://dserver.bundestag.de/brd/2023/0203-23.pdf) of 4 May 2023; Free Hanseatic City of Bremen and BeschA, [press release of 26 October 2023](https://www.finanzen.bremen.de/sixcms/media.php/13/231027_Pressemitteilung_eForms_D%C3%96E-v04.pdf); working group on licensing open geodata, [Empfehlungen zur Lizenzierung offener Geodaten](https://www.geodaten.niedersachsen.de/download/205142/Lizenzierung_offener_Geodaten_21.04.2020_.pdf) (21 April 2020) and GovData, [licence text dl-de/by-2-0](https://www.govdata.de/dl-de/by-2-0); IETF, [RFC 9309](https://www.rfc-editor.org/rfc/rfc9309.html); Publications Office of the EU, [About TED](https://ted.europa.eu/en/about-ted).

André Stiebitz, Potsdam, November 2026
