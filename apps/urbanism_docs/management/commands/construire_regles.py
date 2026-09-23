"""
Construit la table RegleZone à partir des extractions enregistrées.
N'appelle aucun modèle : rejouable autant de fois que nécessaire.

    python manage.py construire_regles
    python manage.py construire_regles --region 44
"""

import re
import unicodedata

from django.core.management.base import BaseCommand
from django.db import transaction


def normaliser(texte):
    t = unicodedata.normalize("NFKD", (texte or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", t)


def concerne(regle, libelle_zone):
    """
    Une règle sans secteur vaut pour toute la zone du chapitre. Avec secteurs,
    elle ne vaut que pour eux : le chapitre A couvre A, Ah, Ap et Aag, mais
    « hauteur 3,5 m en secteur Ah » ne s'applique pas à Ap.
    """
    secteurs = [s for s in (regle.get("secteurs") or []) if isinstance(s, str) and s.strip()]
    if not secteurs:
        return True
    cible = normaliser(libelle_zone)
    return any(normaliser(s) == cible for s in secteurs)


class Command(BaseCommand):
    help = "Construit la table des règles affichables"

    def add_arguments(self, parser):
        parser.add_argument("--region")

    def handle(self, *args, **options):
        from apps.urbanism_docs.models import ExtractionChapitre, RegleZone

        qs = ExtractionChapitre.objects.filter(statut="OK").prefetch_related("zones")
        if options.get("region"):
            qs = qs.filter(zones__document__communes__code_region=options["region"]).distinct()

        stats = {"extractions": 0, "regles": 0, "zones": 0, "ecartees": 0}
        for ext in qs.iterator(chunk_size=200):
            zones = list(ext.zones.all())
            if not zones:
                continue
            stats["extractions"] += 1
            with transaction.atomic():
                RegleZone.objects.filter(extraction=ext).delete()
                lignes = []
                for zone in zones:
                    stats["zones"] += 1
                    ordre = 0
                    for r in ext.regles:
                        if not isinstance(r, dict) or not r.get("citation"):
                            stats["ecartees"] += 1
                            continue
                        if not concerne(r, zone.libelle):
                            continue
                        theme = str(r.get("theme") or "autre")
                        groupe = RegleZone.GROUPE_PAR_THEME.get(theme, "equipement")
                        valeur = str(r.get("valeur") or "").strip()
                        ordre += 1
                        lignes.append(RegleZone(
                            zone=zone, extraction=ext, groupe=groupe, theme=theme,
                            valeur=valeur[:300],
                            condition=str(r.get("condition") or "")[:500],
                            citation=str(r.get("citation") or ""),
                            page=r.get("page") if isinstance(r.get("page"), int) else None,
                            a_valeur=bool(valeur),
                            ordre=ordre,
                        ))
                RegleZone.objects.bulk_create(lignes, batch_size=1000)
                stats["regles"] += len(lignes)

        self.stdout.write(self.style.SUCCESS(
            f"{stats['extractions']} extractions · {stats['zones']} zones · "
            f"{stats['regles']} règles · {stats['ecartees']} écartées"
        ))