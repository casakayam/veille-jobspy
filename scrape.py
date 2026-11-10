"""Veille JobSpy pour Jérôme et Loren : postes en couple logés en Europe.

Lancé chaque jour par GitHub Actions. Écrit :
  data/offres-jobspy.json  offres retenues, vues pour la première fois depuis 7 jours
  data/journal.json        résultat de chaque requête (nombre, erreurs)
  data/vues.json           mémoire des offres déjà vues (dédoublonnage d'un jour à l'autre)
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from jobspy import scrape_jobs

DATA = Path(__file__).parent / "data"
HOURS_OLD = 72          # fenêtre de publication : 3 jours, pour couvrir un jour raté
KEEP_DAYS = 7           # une offre reste dans le JSON 7 jours après sa première vue
FORGET_DAYS = 180       # au-delà, on oublie l'offre dans vues.json
DESC_MAX = 4000         # caractères de description gardés par offre

# --------------------------------------------------------------------------
# Requêtes. "sites" : indeed, hellowork, linkedin, google.
# Pour Indeed, "pays" = nom JobSpy du pays (france, spain, ireland…).
# --------------------------------------------------------------------------
QUERIES: list[dict] = [
    # France — Indeed
    *[{"sites": ["indeed"], "pays": "france", "lieu": "France", "terme": t} for t in [
        '"couple de gardiens"',
        '"couple de gérants"',
        '"couple" gérants camping',
        '"couple" responsables gîte OR auberge OR refuge',
        '"poste en couple" logé',
        '"couple" régisseurs OR intendants domaine',
        '"couple" hôtel logé gérance',
        '"gardien de refuge"',
    ]],
    # France — HelloWork
    *[{"sites": ["hellowork"], "lieu": None, "terme": t} for t in [
        "couple gardiens",
        "couple gérants",
        "couple camping",
        "poste en couple",
    ]],
    # Espagne
    *[{"sites": ["indeed"], "pays": "spain", "lieu": "España", "terme": t} for t in [
        "pareja guardeses",
        "pareja caseros",
        "pareja encargados",
        "matrimonio interno",
    ]],
    # Portugal (en anglais, langue de travail possible)
    {"sites": ["indeed"], "pays": "portugal", "lieu": "Portugal", "terme": '"couple" caretakers OR managers'},
    # Irlande
    *[{"sites": ["indeed"], "pays": "ireland", "lieu": "Ireland", "terme": t} for t in [
        '"live-in couple"',
        '"management couple"',
        '"couple" caretakers OR housekeepers',
    ]],
    # Suisse, Belgique, Luxembourg
    {"sites": ["indeed"], "pays": "switzerland", "lieu": "Suisse", "terme": '"couple" gérants OR gardiens OR concierges'},
    {"sites": ["indeed"], "pays": "belgium", "lieu": "Belgique", "terme": '"couple" gérants OR gardiens'},
    {"sites": ["indeed"], "pays": "luxembourg", "lieu": "Luxembourg", "terme": '"couple" gérants OR gardiens'},
    # Italie, Grèce (offres en anglais)
    {"sites": ["indeed"], "pays": "italy", "lieu": "Italia", "terme": '"couple" villa OR estate managers'},
    {"sites": ["indeed"], "pays": "greece", "lieu": "Greece", "terme": '"couple" villa OR hotel managers'},
    # LinkedIn — souvent bloqué depuis GitHub, gardé en bonus
    {"sites": ["linkedin"], "lieu": "European Union", "terme": "couple manager hotel OR guesthouse OR lodge"},
    {"sites": ["linkedin"], "lieu": "European Union", "terme": "caretaker couple"},
    # Google Jobs — agrège beaucoup de sites, souvent capricieux
    {"sites": ["google"], "google": "couple de gardiens offre d'emploi logé France since last 3 days"},
    {"sites": ["google"], "google": "couple gérants camping OR gîte emploi since last 3 days"},
    {"sites": ["google"], "google": "live-in couple managers hotel Europe jobs since last 3 days"},
]

# --------------------------------------------------------------------------
# Filtres
# --------------------------------------------------------------------------
# Expressions qui contiennent "couple" sans parler d'un poste en couple
FAUX_COUPLE = re.compile(
    r"a couple of|couple of (years|months|weeks|days|hours)|couple d'(années|heures|semaines)|"
    r"couple moteur|couple de serrage",
    re.I,
)
# Il faut au moins un de ces indices dans le titre ou la description
INDICE_COUPLE = re.compile(
    r"\bcouples?\b|\bpareja\b|\bmatrimonio\b|\bbin[ôo]me\b|\bduo\b|\bdeux personnes\b|"
    r"\bhusband and wife\b|\bwife and husband\b|\btwo people\b|\bcasal\b|\bcoppia\b|"
    r"gardien(ne)? de refuge|guarda de refugio|hut warden",
    re.I,
)
# Titres à écarter d'office
TITRE_EXCLU = re.compile(
    r"immeuble|copropri|syndic|r[ée]sidence autonomie|ehpad|nettoyage|agent d'entretien|"
    r"aide[- ]soignant|infirmi|chauffeur|vendeu|caissi|cleaner|nanny|nounou|baby-?sitt|"
    r"au pair|portier|conseill(er|ère) en|th[ée]rapeut|psycho|stage|intern(ship)?\b|alternance",
    re.I,
)
# Lieux hors cible (au cas où LinkedIn ou Google débordent)
LIEU_EXCLU = re.compile(
    r"\b(USA|United States|Canada|Australia|South Africa|New Zealand|India|UAE|Dubai)\b|"
    r", (AL|AK|AZ|CA|CO|FL|GA|NY|TX|WA|NC|VA|MA|IL)\b",
    re.I,
)


def texte(v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    return str(v)


def lieu_str(v) -> str:
    if isinstance(v, dict):
        return ", ".join(x for x in (v.get("city"), v.get("state"), texte(v.get("country"))) if x)
    return texte(v)


def salaire_str(row) -> str:
    lo, hi = row.get("min_amount"), row.get("max_amount")
    lo = None if lo is None or (isinstance(lo, float) and math.isnan(lo)) else lo
    hi = None if hi is None or (isinstance(hi, float) and math.isnan(hi)) else hi
    if lo is None and hi is None:
        return ""
    montant = f"{lo:,.0f}" if lo is not None else ""
    if hi is not None and hi != lo:
        montant = f"{montant}–{hi:,.0f}" if montant else f"{hi:,.0f}"
    cur = texte(row.get("currency"))
    per = {"yearly": "an", "monthly": "mois", "weekly": "semaine", "daily": "jour",
           "hourly": "heure"}.get(texte(row.get("interval")), texte(row.get("interval")))
    return f"{montant} {cur} / {per}".replace(",", " ").strip()


def garder(titre: str, desc: str, lieu: str) -> tuple[bool, str]:
    if TITRE_EXCLU.search(titre):
        return False, "titre exclu"
    if LIEU_EXCLU.search(lieu):
        return False, "hors Europe"
    corpus = FAUX_COUPLE.sub(" ", f"{titre}\n{desc}")
    if not INDICE_COUPLE.search(corpus):
        return False, "pas de couple"
    return True, ""


def lancer(q: dict):
    params = dict(
        site_name=q["sites"],
        results_wanted=30,
        hours_old=HOURS_OLD,
        description_format="markdown",
        verbose=0,
    )
    if "terme" in q:
        params["search_term"] = q["terme"]
    if "google" in q:
        params["google_search_term"] = q["google"]
    if q.get("lieu"):
        params["location"] = q["lieu"]
    else:
        params["distance"] = None
    if "pays" in q:
        params["country_indeed"] = q["pays"]
    if "linkedin" in q["sites"]:
        params["linkedin_fetch_description"] = True
    return scrape_jobs(**params)


def main() -> None:
    DATA.mkdir(exist_ok=True)
    today = date.today()
    vues_path = DATA / "vues.json"
    vues = json.loads(vues_path.read_text()) if vues_path.exists() else {}

    sortie_path = DATA / "offres-jobspy.json"
    anciennes = {}
    if sortie_path.exists():
        anciennes = {o["id"]: o for o in json.loads(sortie_path.read_text()).get("offres", [])}

    journal, nouvelles, ecartees = [], {}, {}
    for i, q in enumerate(QUERIES):
        label = q.get("terme") or q.get("google")
        entree = {"sites": q["sites"], "pays": q.get("pays", ""), "requete": label}
        try:
            df = lancer(q)
            entree["resultats"] = len(df)
            if df.attrs.get("errors"):
                entree["erreurs"] = {str(k): str(v)[:200] for k, v in df.attrs["errors"].items()}
        except Exception as e:  # une requête qui plante ne bloque pas les autres
            entree["resultats"] = 0
            entree["erreurs"] = {"exception": f"{type(e).__name__}: {str(e)[:200]}"}
            df = None
        retenues = 0
        if df is not None:
            for row in df.to_dict("records"):
                titre, desc = texte(row.get("title")), texte(row.get("description"))
                lieu = lieu_str(row.get("location"))
                url = texte(row.get("job_url"))
                if not url:
                    continue
                site = texte(row.get("site"))
                oid = f"{site}-{texte(row.get('id')) or hashlib.sha1(url.encode()).hexdigest()[:12]}"
                ok, raison = garder(titre, desc, lieu)
                if not ok:
                    ecartees[raison] = ecartees.get(raison, 0) + 1
                    continue
                cle_doublon = f"{titre.lower()}|{texte(row.get('company')).lower()}"
                if any(o["_cle"] == cle_doublon for o in nouvelles.values()):
                    continue
                retenues += 1
                v = vues.setdefault(oid, {"premiere_vue": today.isoformat()})
                v["derniere_vue"] = today.isoformat()
                nouvelles[oid] = {
                    "id": oid,
                    "_cle": cle_doublon,
                    "site": site,
                    "titre": titre,
                    "employeur": texte(row.get("company")),
                    "lieu": lieu,
                    "date_publication": texte(row.get("date_posted"))[:10],
                    "url": url,
                    "url_directe": texte(row.get("job_url_direct")),
                    "salaire": salaire_str(row),
                    "type_contrat": texte(row.get("job_type")),
                    "emails": texte(row.get("emails")),
                    "description": desc[:DESC_MAX],
                    "requete": label,
                    "premiere_vue": v["premiere_vue"],
                }
        entree["retenues"] = retenues
        journal.append(entree)
        if i < len(QUERIES) - 1:
            time.sleep(random.uniform(4, 9))

    # Fusion : offres vues aujourd'hui + offres des jours précédents encore dans la fenêtre
    limite = (today - timedelta(days=KEEP_DAYS)).isoformat()
    offres = {k: v for k, v in anciennes.items() if v.get("premiere_vue", "") >= limite}
    offres.update(nouvelles)
    liste = sorted(offres.values(), key=lambda o: (o["premiere_vue"], o["date_publication"]), reverse=True)
    for o in liste:
        o.pop("_cle", None)

    oubli = (today - timedelta(days=FORGET_DAYS)).isoformat()
    vues = {k: v for k, v in vues.items() if v.get("derniere_vue", "") >= oubli}

    maj = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sortie_path.write_text(json.dumps(
        {"mis_a_jour": maj, "nb_offres": len(liste), "offres": liste},
        ensure_ascii=False, indent=1))
    (DATA / "journal.json").write_text(json.dumps(
        {"mis_a_jour": maj, "ecartees": ecartees, "requetes": journal},
        ensure_ascii=False, indent=1))
    vues_path.write_text(json.dumps(vues, ensure_ascii=False, indent=0))

    total = sum(e["resultats"] for e in journal)
    print(f"{len(QUERIES)} requêtes, {total} résultats bruts, {len(nouvelles)} retenues aujourd'hui, "
          f"{len(liste)} dans le fichier. Écartées : {ecartees}")
    for e in journal:
        if e.get("erreurs"):
            print("ERREUR", e["sites"], e["requete"], e["erreurs"])


if __name__ == "__main__":
    main()
