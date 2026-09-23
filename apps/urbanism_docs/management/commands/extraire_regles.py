"""
Extraction des règles de chaque chapitre de zone, en deux passages.

  1. niveau « low » pour tous les chapitres, avec mesure de couverture ;
  2. niveau « high » seulement pour les chapitres sous le seuil.

    python manage.py extraire_regles --pilote 20            # essai, conservé en base
    python manage.py extraire_regles --region 44 --duree-max 420
    python manage.py extraire_regles --reprendre             # passage high

Chaque réponse est enregistrée : rien n'est payé deux fois. Un chapitre partagé
par plusieurs zones ou plusieurs documents n'est appelé qu'une fois.
"""

import hashlib
import json
import random
import re
import time
from collections import Counter

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.urbanism_docs.management.commands.tester_regles import (
    INSTRUCTION, lire, normaliser, zone_confirmee,
)

VERSION_PROMPT = hashlib.sha1((INSTRUCTION + "|v3-renvois").encode()).hexdigest()[:10]
MODELE = "gemini-3.1-pro-preview"
PAGES_MAX = 25
SORTIE_MAX = 32768
# Tarif standard en USD par million de jetons ; le mode Batch coûte moitié.
PRIX_ENTREE, PRIX_SORTIE = 2.0, 12.0
MOTIF_NOMBRE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(m²|m2|mètres?|m\b|%)", re.I)


def couverture(textes, regles):
    """
    Part des nombres suivis d'une unité dans le texte qui sont repris par une règle.
    Première chance : la phrase qui suit le nombre figure dans une citation.
    Seconde chance : le nombre et son unité figurent dans une valeur, une
    condition ou une citation — une citation limitée à 300 caractères peut
    s'arrêter avant le nombre alors que la règle a bien été relevée.
    """
    citations = normaliser(" ".join(r.get("citation") or "" for r in regles))
    tout = normaliser(" ".join(
        " ".join([r.get("valeur") or "", r.get("condition") or "", r.get("citation") or ""])
        for r in regles
    ))
    trouves = total = 0
    for t in textes.values():
        for m in MOTIF_NOMBRE.finditer(t):
            total += 1
            cle = re.sub(r"( \d+)+$", "", normaliser(t[m.start():m.end() + 40])[:22])
            court = normaliser(m.group(0))
            if (cle and cle in citations) or court in tout:
                trouves += 1
    return (trouves / total if total else 1.0), total


def lister_chapitres(region):
    """Chapitres uniques de la région : (PDF, début, fin) avec leurs zones."""
    from apps.parcels.models import Zone
    from apps.urbanism_docs.models import ReglementDocument

    par_doc = {}
    for r in (ReglementDocument.objects.exclude(fichier="")
              .filter(type_piece="REGLEMENT", est_numerise=False)):
        par_doc.setdefault(r.document_id, {})[r.titre] = r

    chapitres = {}
    zones = (Zone.objects.filter(document__communes__code_region=region,
                                 page_chapitre_debut__isnull=False)
             .exclude(type_chapitre__startswith="famille").distinct())
    for z in zones.iterator():
        regls = par_doc.get(z.document_id, {})
        nom = (z.nom_fichier_reglement or "").split("#")[0]
        regl = regls.get(nom) or next(iter(regls.values()), None)
        if regl is None:
            continue
        
        debut = z.page_chapitre_debut
        fin = min(z.page_chapitre_fin or debut, debut + PAGES_MAX - 1)
        # Une fin calculée sur une seule page vient d'un titre parasite juste
        # après le début du chapitre : on lit six pages et on laisse les
        # contrôles Z et « vide » écarter les faux positifs.
        if fin - debut < 1:
            fin = min(debut + 5, (z.document.reglements.first().nb_pages or debut + 5))

        cle = f"{regl.sha256 or regl.fichier.name}:{debut}-{fin}"
        ch = chapitres.setdefault(cle, {"cle": cle, "reglement": regl,
                                        "debut": debut, "fin": fin, "zones": []})
        ch["zones"].append(z)
    
    # Les communes peuplées d'abord : c'est là que se trouveront les premiers
    # utilisateurs, et les PLUi urbains sont les plus riches en règles.
    from django.db.models import Max

    population = dict(
        Zone.objects.filter(id__in=[z.id for ch in chapitres.values() for z in ch["zones"]])
        .values_list("id")
        .annotate(pop=Max("document__communes__population"))
    )
    resultat = list(chapitres.values())
    resultat.sort(
        key=lambda ch: -max((population.get(z.id) or 0) for z in ch["zones"])
    )
    return resultat


class Command(BaseCommand):
    help = "Extraction des règles par chapitre de zone, en deux passages"

    def add_arguments(self, parser):
        parser.add_argument("--region", default="44")
        parser.add_argument("--pilote", type=int, help="Échantillon aléatoire, deux passages")
        parser.add_argument("--limit", type=int)
        parser.add_argument("--duree-max", type=int, help="Minutes")
        parser.add_argument("--reprendre", action="store_true",
                            help="Passage high sur les chapitres A_REPRENDRE")
        parser.add_argument("--seuil", type=float, default=0.8)
        parser.add_argument("--graine", type=int, default=1)

    def handle(self, *args, **options):
        from google import genai
        from apps.urbanism_docs.models import ExtractionChapitre

        if not getattr(settings, "GEMINI_API_KEY", ""):
            raise CommandError("GEMINI_API_KEY absent de la configuration.")
        self.client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self.seuil = options["seuil"]
        self.fin_au_plus_tard = (
            time.time() + 60 * options["duree_max"] if options.get("duree_max") else None
        )
        self.jetons = Counter()

        if options["reprendre"]:
            qs = ExtractionChapitre.objects.filter(statut="A_REPRENDRE")
            if options.get("limit"):
                qs = qs[: options["limit"]]
            self.passage_high(list(qs))
            self.cout()
            return

        tous = lister_chapitres(options["region"])
        existants = set(ExtractionChapitre.objects.values_list("cle", flat=True))
        a_faire = [c for c in tous if c["cle"] not in existants]
        self.stdout.write(f"{len(tous)} chapitres uniques · {len(a_faire)} restant à traiter · "
                          f"prompt {VERSION_PROMPT}")

        if options.get("pilote"):
            random.seed(options["graine"])
            random.shuffle(a_faire)
            a_faire = a_faire[: options["pilote"]]
        elif options.get("limit"):
            a_faire = a_faire[: options["limit"]]

        self.passage_low(a_faire)

        if options.get("pilote"):
            cles = [c["cle"] for c in a_faire]
            a_reprendre = list(ExtractionChapitre.objects.filter(cle__in=cles, statut="A_REPRENDRE"))
            self.stdout.write(f"\n{len(a_reprendre)} chapitre(s) sous le seuil → passage high")
            self.passage_high(a_reprendre)
            self.bilan_pilote(cles, len(a_reprendre), len(tous))
        else:
            self.cout()

    # --- Appel au modèle --------------------------------------------------

    def appeler(self, textes, zones, niveau):
        from google.genai import types

        # La zone attendue n'est pas donnée au modèle : il la recopierait dans
        # zones_du_chapitre et le contrôle Z ne vérifierait plus rien.
        contenu = "\n\n".join(
            f"=== PAGE PDF {i} ===\n{t}" for i, t in textes.items()
        )
        derniere = None
        for essai in range(3):
            try:
                rep = self.client.models.generate_content(
                    model=MODELE,
                    contents=contenu,
                    config=types.GenerateContentConfig(
                        system_instruction=INSTRUCTION,
                        response_mime_type="application/json",
                        temperature=0,
                        max_output_tokens=SORTIE_MAX,
                        thinking_config=types.ThinkingConfig(thinking_level=niveau),
                    ),
                )
                break
            except Exception as exc:
                # Crédit épuisé : on arrête tout plutôt que de marquer mille chapitres en erreur.
                if "RESOURCE_EXHAUSTED" in str(exc) and "credit" in str(exc).lower():
                    raise CommandError(f"Crédit épuisé, arrêt : {exc}")
                derniere = exc
                if essai < 2:
                    time.sleep(5 * (2 ** essai))
        else:
            return {"statut": "ERREUR", "brut": str(derniere)[:2000], "entree": 0, "sortie": 0}

        u = rep.usage_metadata
        entree = u.prompt_token_count or 0
        sortie = (u.candidates_token_count or 0) + (u.thoughts_token_count or 0)
        brut = rep.text or ""
        fin = str(rep.candidates[0].finish_reason) if rep.candidates else ""
        if "MAX_TOKENS" in fin:
            return {"statut": "TRONQUE", "brut": brut, "entree": entree, "sortie": sortie}
        try:
            data = json.loads(brut)
        except json.JSONDecodeError:
            return {"statut": "JSON_INVALIDE", "brut": brut, "entree": entree, "sortie": sortie}
        return {
            "statut": None, "brut": brut, "entree": entree, "sortie": sortie,
            "zones_lues": data.get("zones_du_chapitre") or [],
            "renvois": data.get("renvois") or [],
            "regles": data.get("regles") or [],
        }

    def juger(self, res, textes, zones_attendues, passage):
        """Statut d'une réponse valide : contenu, zone, puis couverture."""
        couv, nb = couverture(textes, res["regles"])
        if not res["regles"]:
            return "VIDE", couv, nb
        lues = res["zones_lues"]
        # Sans titre de zone lisible, on ne peut rien confirmer : on écarte.
        if not lues or not any(zone_confirmee(z, lues) for z in zones_attendues):
            return "ZONE_REJETEE", couv, nb
        if passage == "low" and couv < self.seuil:
            return "A_REPRENDRE", couv, nb
        return "OK", couv, nb

    def delai_depasse(self):
        return self.fin_au_plus_tard and time.time() > self.fin_au_plus_tard

    # --- Passages ---------------------------------------------------------

    def passage_low(self, chapitres):
        from apps.urbanism_docs.models import ExtractionChapitre

        for k, ch in enumerate(chapitres, 1):
            if self.delai_depasse():
                self.stdout.write(self.style.WARNING("Durée maximale atteinte."))
                break
            regl = ch["reglement"]
            libelles = sorted({z.libelle for z in ch["zones"]})
            textes = lire(regl.fichier.path, ch["debut"], ch["fin"])
            res = self.appeler(textes, libelles, "low")
            self.jetons["entree"] += res["entree"]
            self.jetons["sortie"] += res["sortie"]

            valeurs = {
                "reglement": regl, "page_debut": ch["debut"], "page_fin": ch["fin"],
                "zones_attendues": libelles, "brut": res["brut"], "modele": MODELE,
                "niveau": "low", "version_prompt": VERSION_PROMPT,
                "jetons_entree": res["entree"], "jetons_sortie": res["sortie"],
            }
            if res["statut"]:
                valeurs.update(statut=res["statut"])
                couv = None
            else:
                statut, couv, nb = self.juger(res, textes, libelles, "low")
                valeurs.update(statut=statut, zones_lues=res["zones_lues"],
                               regles=res["regles"], renvois=res["renvois"],
                               couverture=couv, nb_nombres=nb)

            ext, _ = ExtractionChapitre.objects.update_or_create(cle=ch["cle"], defaults=valeurs)
            ext.zones.set(ch["zones"])
            couv_txt = f"{100 * couv:3.0f} %" if couv is not None else "  — "
            self.stdout.write(
                f"  {k:4}. low  {valeurs['statut']:13} couv {couv_txt} · "
                f"{len(valeurs.get('regles', [])):3} règles · {','.join(libelles)[:18]:18} · "
                f"p.{ch['debut']}–{ch['fin']} · {regl.titre}"
            )

    def passage_high(self, extractions):
        for k, ext in enumerate(extractions, 1):
            if self.delai_depasse():
                self.stdout.write(self.style.WARNING("Durée maximale atteinte."))
                break
            textes = lire(ext.reglement.fichier.path, ext.page_debut, ext.page_fin)
            res = self.appeler(textes, ext.zones_attendues, "high")
            self.jetons["entree"] += res["entree"]
            self.jetons["sortie"] += res["sortie"]
            ext.jetons_entree += res["entree"]
            ext.jetons_sortie += res["sortie"]
            ext.brut = res["brut"]
            ext.niveau = "high"
            if res["statut"]:
                ext.statut = res["statut"]
            else:
                statut, couv, nb = self.juger(res, textes, ext.zones_attendues, "high")
                ext.statut, ext.couverture, ext.nb_nombres = statut, couv, nb
                ext.zones_lues, ext.regles = res["zones_lues"], res["regles"]
                ext.renvois = res["renvois"]
            ext.save()
            couv_txt = f"{100 * ext.couverture:3.0f} %" if ext.couverture is not None else "  — "
            self.stdout.write(
                f"  {k:4}. high {ext.statut:13} couv {couv_txt} · {len(ext.regles):3} règles · "
                f"{ext.reglement.titre} p.{ext.page_debut}–{ext.page_fin}"
            )

    # --- Bilan ------------------------------------------------------------

    def cout(self):
        e, s = self.jetons["entree"], self.jetons["sortie"]
        standard = e * PRIX_ENTREE / 1e6 + s * PRIX_SORTIE / 1e6
        self.stdout.write(f"\nJetons : {e} entrée, {s} sortie (réponse + réflexion)")
        self.stdout.write(f"Coût estimé : {standard:.2f} USD au tarif standard, "
                          f"{standard / 2:.2f} USD en mode Batch")
        return standard

    def bilan_pilote(self, cles, nb_repris, nb_total):
        from apps.urbanism_docs.models import ExtractionChapitre

        exts = list(ExtractionChapitre.objects.filter(cle__in=cles))
        n = len(exts)
        self.stdout.write(f"\n=== Bilan du pilote : {n} chapitres ===")
        self.stdout.write(f"Statuts finaux : {dict(Counter(e.statut for e in exts))}")
        self.stdout.write(f"Repris en high : {nb_repris}/{n} ({100 * nb_repris / max(1, n):.0f} %)")
        couvs = [e.couverture for e in exts if e.couverture is not None]
        if couvs:
            self.stdout.write(f"Couverture moyenne finale : {100 * sum(couvs) / len(couvs):.0f} %")
        standard = self.cout()
        par_chapitre = standard / max(1, n)
        self.stdout.write(
            f"\nPar chapitre : {par_chapitre:.3f} USD · extrapolé à {nb_total} chapitres : "
            f"{par_chapitre * nb_total:.0f} USD standard, {par_chapitre * nb_total / 2:.0f} USD Batch"
        )