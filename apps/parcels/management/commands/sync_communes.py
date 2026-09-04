"""
Synchronise le référentiel des communes depuis l'API Découpage administratif.

    python manage.py sync_communes --region 44
    python manage.py sync_communes --region 44 --with-geom
    python manage.py sync_communes --departement 67 --with-geom
"""

from django.contrib.gis.geos import GEOSGeometry, MultiPolygon
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

import requests

API_BASE = "https://geo.api.gouv.fr"
TIMEOUT = 120

CHAMPS = [
    "code",
    "nom",
    "codesPostaux",
    "codeDepartement",
    "codeRegion",
    "population",
    "surface",
]


class Command(BaseCommand):
    help = "Importe les communes d'une région ou d'un département depuis geo.api.gouv.fr"

    def add_arguments(self, parser):
        parser.add_argument(
            "--region",
            help="Code région INSEE, ex: 44 pour le Grand Est",
        )
        parser.add_argument(
            "--departement",
            help="Code département INSEE, ex: 67",
        )
        parser.add_argument(
            "--with-geom",
            action="store_true",
            help="Récupère aussi les contours (réponse bien plus lourde)",
        )

    def handle(self, *args, **options):
        from apps.parcels.models import Commune

        region = options.get("region")
        departement = options.get("departement")
        with_geom = options["with_geom"]

        if not region and not departement:
            raise CommandError("Précisez --region ou --departement.")
        if region and departement:
            raise CommandError("Précisez --region OU --departement, pas les deux.")

        params = {"fields": ",".join(CHAMPS)}
        if region:
            params["codeRegion"] = region
            cible = f"région {region}"
        else:
            params["codeDepartement"] = departement
            cible = f"département {departement}"

        if with_geom:
            params["format"] = "geojson"
            params["geometry"] = "contour"
            params["fields"] = params["fields"] + ",contour"

        self.stdout.write(f"Appel de l'API pour la {cible}...")
        if with_geom:
            self.stdout.write(
                self.style.WARNING("Avec contours : le téléchargement peut prendre plusieurs minutes.")
            )

        try:
            reponse = requests.get(f"{API_BASE}/communes", params=params, timeout=TIMEOUT)
            reponse.raise_for_status()
        except requests.RequestException as exc:
            raise CommandError(f"Échec de l'appel API : {exc}")

        charge = reponse.json()
        entrees = charge["features"] if with_geom else charge

        if not entrees:
            raise CommandError(f"Aucune commune retournée pour la {cible}.")

        self.stdout.write(f"{len(entrees)} communes reçues. Écriture en base...")

        crees = 0
        maj = 0
        sans_geom = 0
        maintenant = timezone.now()

        with transaction.atomic():
            for entree in entrees:
                if with_geom:
                    props = entree["properties"]
                    brut = entree.get("geometry")
                else:
                    props = entree
                    brut = None

                valeurs = {
                    "nom": props["nom"],
                    "code_departement": props["codeDepartement"],
                    "code_region": props["codeRegion"],
                    "population": props.get("population"),
                    "surface_ha": props.get("surface"),
                    "synced_at": maintenant,
                }

                codes_postaux = props.get("codesPostaux") or []
                if codes_postaux:
                    valeurs["code_postal"] = codes_postaux[0]

                if with_geom:
                    geom = self._en_multipolygone(brut)
                    if geom is None:
                        sans_geom += 1
                    else:
                        valeurs["geom"] = geom

                _, cree = Commune.objects.update_or_create(
                    code_insee=props["code"],
                    defaults=valeurs,
                )
                if cree:
                    crees += 1
                else:
                    maj += 1

        self.stdout.write(
            self.style.SUCCESS(f"Terminé : {crees} créées, {maj} mises à jour.")
        )
        if sans_geom:
            self.stdout.write(
                self.style.WARNING(f"{sans_geom} communes sans contour exploitable.")
            )

    @staticmethod
    def _en_multipolygone(geojson_geom):
        """L'API renvoie Polygon ou MultiPolygon ; le modèle exige MultiPolygon."""
        if not geojson_geom:
            return None
        import json

        geom = GEOSGeometry(json.dumps(geojson_geom), srid=4326)
        if geom.geom_type == "Polygon":
            return MultiPolygon(geom, srid=4326)
        if geom.geom_type == "MultiPolygon":
            return geom
        return None