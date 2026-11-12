"""Affiche les offres JobSpy récentes en texte lisible, pour la veille Claude.

Usage : python3 lister.py [jours]   (défaut : 3 jours depuis la première vue)
"""

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DATA = Path(__file__).parent / "data"
jours = int(sys.argv[1]) if len(sys.argv) > 1 else 3
limite = (date.today() - timedelta(days=jours)).isoformat()

offres = json.loads((DATA / "offres-jobspy.json").read_text())
journal = json.loads((DATA / "journal.json").read_text())

recentes = [o for o in offres["offres"] if o["premiere_vue"] >= limite]
print(f"Fichier mis à jour : {offres['mis_a_jour']} — {len(recentes)} offres vues depuis {limite}\n")

age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(offres["mis_a_jour"])).total_seconds() / 3600
if age_h > 30:
    print(f"ATTENTION : fichier JobSpy vieux de {age_h:.0f} h, le scraping GitHub du jour n'a pas tourné.\n")

erreurs = [e for e in journal["requetes"] if e.get("erreurs")]
if erreurs:
    sites = sorted({s for e in erreurs for s in e["sites"]})
    print(f"Sites JobSpy en erreur : {', '.join(sites)} ({len(erreurs)} requêtes)\n")

for o in recentes:
    print("=" * 70)
    for k in ("id", "site", "titre", "employeur", "lieu", "date_publication", "salaire",
              "type_contrat", "url", "url_directe", "emails", "premiere_vue"):
        if o.get(k):
            print(f"{k}: {o[k]}")
    print("description:")
    print(o["description"][:2500])
    print()
