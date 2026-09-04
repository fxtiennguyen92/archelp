"""
Récupère des parcelles cadastrales via l'API Carto (module Cadastre)
et calcule leur intersection avec le zonage.

    python manage.py sync_parcelles --point 7.7521 48.5734
    python manage.py sync_parcelles --idu 67482000DI0120
    python manage.py sync_parcelles --insee 67482 --section AB --numero 0001
    python manage.py sync_parcelles --insee 67482 --section AB        # 163 parcelles

Fonctionnement en cache-aside : la parcelle est cherchée en base d'abord,
appelée à l'API seulement si absente ou si --force est passé.
"""

import json
import time

import requests
from django.contrib.gis.geos import GEOSGeometry, MultiPolygon, Point
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

API_PARCELLE = "https://apicarto.ign.fr/api/cadastre/parcelle"
TIMEOUT = 60
LIMITE_SANS_CONFIRMATION = 50


class Command(BaseCommand):
    help = "Importe des parcelles cadastrales et calcule leur zonage"

    def add_arguments(self, parser):
        parser.add_argument("--idu", help="Identifiant à 14 caractères")
        parser.add_argument(
            "--point",
            nargs=2,
            type=float,
            metavar=("LON", "LAT"),
            help="Coordonnées WGS84, ex: --point 7.7521 48.5734",
        )
        parser.add_argument("--insee", help="Code INSEE de la commune")
        parser.add_argument("--section", help="Section cadastrale, ex: AB")
        parser.add_argument("--numero", help="Numéro de parcelle, ex: 0001")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Rappeler l'API même si la parcelle est déjà en base",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Ne pas demander confirmation au-delà de 50 parcelles",
        )

    def handle(self, *args, **options):
        from apps.parcels.models import Parcelle

        params = self._construire_params(options)

        # Cache-aside : on ne rappelle pas l'API pour une parcelle connue.
        if options.get("idu") and not options["force"]:
            existante = Parcelle.objects.filter(idu=options["idu"]).first()
            if existante:
                self.stdout.write(f"Déjà en base : {existante.idu}")
                self._afficher(existante)
                return

        self.stdout.write("Appel de l'API Cadastre...")
        features = self._appeler(params)

        if not features:
            raise CommandError(
                "Aucune parcelle trouvée. Vérifiez les paramètres — l'API "
                "renvoie une collection vide sans signaler d'erreur."
            )

        nb = len(features)
        self.stdout.write(f"{nb} parcelles reçues.")

        if nb > LIMITE_SANS_CONFIRMATION and not options["yes"]:
            reponse = input(f"Importer {nb} parcelles ? [o/N] ")
            if reponse.strip().lower() not in ("o", "oui", "y", "yes"):
                self.stdout.write("Annulé.")
                return

        crees, maj, ignorees = 0, 0, 0
        parcelles = []

        for feat in features:
            resultat = self._enregistrer(feat)
            if resultat is None:
                ignorees += 1
                continue
            parcelle, cree = resultat
            parcelles.append(parcelle)
            if cree:
                crees += 1
            else:
                maj += 1

        self.stdout.write(
            self.style.SUCCESS(f"{crees} créées, {maj} mises à jour, {ignorees} ignorées.")
        )

        self.stdout.write("Calcul des intersections avec le zonage...")
        total_liens = 0
        for parcelle in parcelles:
            total_liens += self._calculer_zones(parcelle)

        self.stdout.write(self.style.SUCCESS(f"{total_liens} relations parcelle × zone."))

        if len(parcelles) <= 5:
            for parcelle in parcelles:
                self._afficher(parcelle)

    def _construire_params(self, options):
        if options.get("idu"):
            idu = options["idu"].strip()
            if len(idu) != 14:
                raise CommandError(f"L'IDU doit faire 14 caractères, reçu {len(idu)}.")
            # IDU = INSEE(5) + com_abs(3) + section(2) + numero(4)
            return {
                "code_insee": idu[:5],
                "section": idu[8:10],
                "numero": idu[10:14],
            }

        if options.get("point"):
            lon, lat = options["point"]
            return {
                "geom": json.dumps({"type": "Point", "coordinates": [lon, lat]})
            }

        if options.get("insee"):
            params = {"code_insee": options["insee"]}
            if options.get("section"):
                params["section"] = options["section"]
            if options.get("numero"):
                params["numero"] = options["numero"]
            return params

        raise CommandError("Précisez --idu, --point, ou --insee [--section] [--numero].")

    @staticmethod
    def _appeler(params, tentatives=3):
        derniere = None
        for essai in range(tentatives):
            try:
                reponse = requests.get(API_PARCELLE, params=params, timeout=TIMEOUT)
                reponse.raise_for_status()
                return reponse.json().get("features") or []
            except (requests.RequestException, ValueError) as exc:
                derniere = exc
                if essai < tentatives - 1:
                    time.sleep(2 ** essai)
        raise CommandError(f"Échec après {tentatives} tentatives : {derniere}")

    def _enregistrer(self, feature):
        from apps.parcels.models import Commune, Parcelle

        props = feature["properties"]
        idu = (props.get("idu") or "").strip()
        insee = (props.get("code_insee") or "").strip()

        if not idu or not insee:
            return None

        commune = Commune.objects.filter(code_insee=insee).first()
        if commune is None:
            self.stdout.write(
                self.style.WARNING(
                    f"  {idu} : commune {insee} absente en base, parcelle ignorée."
                )
            )
            return None

        geom = self._en_multipolygone(feature.get("geometry"))
        if geom is None:
            self.stdout.write(self.style.WARNING(f"  {idu} : géométrie inexploitable."))
            return None

        return Parcelle.objects.update_or_create(
            idu=idu,
            defaults={
                "commune": commune,
                "prefixe": (props.get("com_abs") or "000")[:3],
                "section": (props.get("section") or "")[:2],
                "numero": (props.get("numero") or "")[:4],
                "feuille": props.get("feuille") or 1,
                "code_arrondissement": (props.get("code_arr") or "000")[:3],
                "contenance_m2": props.get("contenance"),
                "gid_ign": props.get("gid"),
                "geom": geom,
                "synced_at": timezone.now(),
            },
        )

    def _calculer_zones(self, parcelle):
        """
        Intersection parcelle × zones, mesurée en Lambert-93 (SRID 2154).
        Les géométries sont stockées en 4326, dont l'unité est le degré :
        toute mesure de surface exige une reprojection.
        """
        from django.contrib.gis.db.models.functions import Area, Intersection, Transform
        from apps.parcels.models import ParcelleZone, Zone

        candidates = (
            Zone.objects.filter(geom__intersects=parcelle.geom)
            .annotate(
                aire_commune=Area(
                    Transform(Intersection("geom", parcelle.geom), 2154)
                )
            )
        )

        surface_parcelle = None
        with transaction.atomic():
            ParcelleZone.objects.filter(parcelle=parcelle).delete()

            resultats = []
            for zone in candidates:
                if zone.aire_commune is None:
                    continue
                m2 = zone.aire_commune.sq_m
                if m2 < 1:  # bruit de bord, non significatif
                    continue
                resultats.append((zone, m2))

            if not resultats:
                return 0

            # Le pourcentage se calcule entièrement sur la géométrie, sinon
            # la somme dépasse 100 % : la contenance cadastrale et la surface
            # géométrique proviennent de deux mesures différentes et diffèrent
            # couramment de plusieurs pour cent.
            surface_geom = sum(m2 for _, m2 in resultats)
            if surface_geom <= 0:
                return 0

            # La surface affichée est ramenée à la contenance, valeur juridique.
            base_affichage = parcelle.contenance_m2 or surface_geom

            dominante = max(resultats, key=lambda r: r[1])[0].id

            for zone, m2 in resultats:
                part = m2 / surface_geom
                ParcelleZone.objects.create(
                    parcelle=parcelle,
                    zone=zone,
                    surface_intersection_m2=round(part * base_affichage, 2),
                    part_pct=round(100 * part, 2),
                    est_dominante=(zone.id == dominante),
                )

        return len(resultats)

    def _afficher(self, parcelle):
        liens = parcelle.parcellezone_set.select_related(
            "zone", "zone__document"
        ).order_by("-part_pct")

        self.stdout.write("")
        self.stdout.write(f"Parcelle {parcelle.idu} — {parcelle.commune.nom}")
        self.stdout.write(f"  Contenance : {parcelle.contenance_m2} m²")

        if not liens:
            self.stdout.write(
                self.style.WARNING("  Aucune zone : commune sans document d'urbanisme ?")
            )
            return

        for lien in liens:
            marque = " ← dominante" if lien.est_dominante else ""
            self.stdout.write(
                f"  {lien.zone.libelle:8} {lien.zone.type_zone:6} "
                f"{lien.part_pct:5.1f}%  ({lien.surface_intersection_m2:.0f} m²){marque}"
            )
            if lien.zone.nom_fichier_reglement:
                self.stdout.write(f"           → {lien.zone.nom_fichier_reglement}")

        if liens.count() > 1:
            docs = {lien.zone.document_id for lien in liens}
            if len(docs) > 1:
                self.stdout.write(
                    self.style.WARNING(
                        "  ⚠ Zones issues de documents différents — à vérifier en mairie."
                    )
                )

    @staticmethod
    def _en_multipolygone(geojson_geom):
        if not geojson_geom:
            return None
        try:
            geom = GEOSGeometry(json.dumps(geojson_geom), srid=4326)
        except Exception:
            return None

        if geom.geom_type == "Polygon":
            geom = MultiPolygon(geom, srid=4326)
        elif geom.geom_type != "MultiPolygon":
            return None

        # Le cadastre contient aussi des géométries invalides.
        if not geom.valid:
            repare = geom.buffer(0)
            if repare.geom_type == "Polygon":
                geom = MultiPolygon(repare, srid=4326)
            elif repare.geom_type == "MultiPolygon":
                geom = repare

        return geom