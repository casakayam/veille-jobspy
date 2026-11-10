# Veille JobSpy

Scraping quotidien (GitHub Actions, lundi à samedi, 5:47 heure de Bogota) des offres d'emploi en couple logées en Europe, avec [JobSpy](https://github.com/speedyapply/JobSpy).

- `scrape.py` : requêtes (liste `QUERIES`), filtres, dédoublonnage.
- `lister.py` : affichage texte des offres récentes, utilisé par la veille Claude.
- `data/offres-jobspy.json` : offres retenues, vues pour la première fois depuis 7 jours.
- `data/journal.json` : résultat de chaque requête, erreurs de sites.
- `data/vues.json` : mémoire des offres déjà vues.

Modifier les recherches : éditer `QUERIES` dans `scrape.py`.
Lancer à la main : onglet **Actions** → **Veille JobSpy** → **Run workflow**.
