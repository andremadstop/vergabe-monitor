#!/bin/sh
# Prüfungen vor jedem Commit und in der CI. Bricht beim ersten Fehler ab.
#   ruff        Stil und typische Fehler, inkl. Sicherheitsregeln (S)
#   bandit      Sicherheitsprüfung des Codes
#   pip-audit   bekannte Lücken in den festgeschriebenen Abhängigkeiten
#   pytest      Tests ohne Netz und ohne Datenbank
#   Secrets     Zugangsdaten im Repository: gitleaks, sonst detect-secrets
#   sbom        Stückliste der Abhängigkeiten (CycloneDX) nach sbom.cdx.json
# Braucht nur uv (https://docs.astral.sh/uv/).
set -eu
cd "$(dirname "$0")"

echo "== ruff";      uvx ruff check --no-cache .
echo "== bandit";    uvx -q bandit -q -r monitor -x monitor/test_filter.py
echo "== pip-audit"; uvx -q pip-audit -r requirements.txt --require-hashes --disable-pip
echo "== pytest";    uv run -q --no-project --with-requirements requirements.txt --with pytest python -m pytest -q
if command -v gitleaks >/dev/null 2>&1; then
  echo "== gitleaks"; gitleaks git --no-banner --redact .
else
  echo "== detect-secrets"
  # Commit-Hashes (40 Hex-Zeichen) in Metadaten sind keine Geheimnisse, nur genau diese Felder ausnehmen.
  uvx -q detect-secrets scan --exclude-lines '"(gitCommitHash|commit)": "[0-9a-f]{40}"' | python3 -c '
import json, sys
funde = json.load(sys.stdin)["results"]
for datei, liste in funde.items():
    for f in liste:
        zeile, art = f["line_number"], f["type"]
        print(f"  {datei}:{zeile} {art}")
sys.exit(1 if funde else 0)'
fi
echo "== sbom";      uvx -q --from cyclonedx-bom cyclonedx-py requirements requirements.txt \
                       --of JSON -o sbom.cdx.json
echo "Alles in Ordnung."
