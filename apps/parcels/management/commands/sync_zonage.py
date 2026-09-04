"""
Synchronise le zonage depuis l'API Carto GPU, partition par partition.

    python manage.py sync_zonage --region 44 --limit 5      # essai
    python manage.py sync_zonage --region 44
    python manage.py sync_zonage --partition DU_246700488

Les polygones partageant le même libellé sont fusionnés en un MultiPolygon.
"""

import re
import time

import requests
from django.contrib.gis.geos import GEOSGeometry, MultiPolygon
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

API_ZONE = "https://apicarto.ign.fr/api/gpu/zone-urba"
API_DOC = "https://apicarto.ign.fr/api/gpu/document"
TIMEOUT = 120
PAUSE = 0.3  # politesse envers un service public gratuit

API_SECTEUR_CC = "https://apicarto.ign.fr/api/gpu/secteur-cc"

TYPESECT_CC = {
    "01": "CC01",
    "02": "CC02",
    "03": "CC03",
    "99": "CC99",
}

TYPES_VALIDES = {"U", "AU", "AUc", "AUs", "A", "N"}

MOTIF_PAGE = re.compile(r"#page=(\d+)")

REQUETE_PARTITIONS = """
    SELECT DISTINCT c.partition
    FROM gpu_raw_doc_urba_com c
    JOIN parcels_commune pc ON pc.code_insee = c.insee
    WHERE pc.code_region = %s AND c.partition IS NOT NULL
    ORDER BY c.partition
"""


class Command(BaseCommand):
    help = "Importe le zonage GPU par partition via l'API Carto"

    def add_arguments(self, parser):
        parser.add_argument("--region", help="Code région, ex: 44")
        parser.add_argument("--partition", help="Une seule partition, ex: DU_246700488")
        parser.add_argument("--limit", type=int, help="S'arrêter après N partitions")
        parser.add_argument(
            "--skip-existing",
            action="store_true",
            help="Ignorer les partitions déjà en base",
        )

    def handle(self, *args, **options):
        from apps.parcels.models import DocumentUrbanisme

        region = options.get("region")
        partition_unique = options.get("partition")

        if not region and not partition_unique:
            raise CommandError("Précisez --region ou --partition.")

        if partition_unique:
            partitions = [partition_unique]
        else:
            with connection.cursor() as cur:
                cur.execute(REQUETE_PARTITIONS, [region])
                partitions = [row[0] for row in cur.fetchall()]

        if options.get("skip_existing"):
            deja = set(
                DocumentUrbanisme.objects.filter(zones__isnull=False)
                .values_list("partition", flat=True)
                .distinct()
            )
            partitions = [p for p in partitions if p not in deja]

        if options.get("limit"):
            partitions = partitions[: options["limit"]]

        if not partitions:
            raise CommandError("Aucune partition à traiter.")

        total = len(partitions)
        self.stdout.write(f"{total} partitions à traiter.")

        stats = {"zones": 0, "vides": 0, "erreurs": 0, "documents": 0}
        debut = time.time()

        for i, partition in enumerate(partitions, 1):
            try:
                resultat = self._traiter_partition(partition)
            except Exception as exc:
                stats["erreurs"] += 1
                self.stdout.write(self.style.ERROR(f"  {partition} : {exc}"))
                continue

            if resultat is None:
                stats["vides"] += 1
            else:
                stats["documents"] += 1
                stats["zones"] += resultat

            if i % 25 == 0 or i == total:
                ecoule = time.time() - debut
                reste = (ecoule / i) * (total - i)
                self.stdout.write(
                    f"  {i}/{total} — {stats['zones']} zones, "
                    f"{stats['vides']} vides, {stats['erreurs']} erreurs "
                    f"— reste ~{reste / 60:.0f} min"
                )

            time.sleep(PAUSE)

        self.stdout.write(
            self.style.SUCCESS(
                f"\nTerminé : {stats['documents']} documents, {stats['zones']} zones."
            )
        )
        if stats["vides"]:
            self.stdout.write(
                self.style.WARNING(
                    f"{stats['vides']} partitions sans zonage — normal pour les CC "
                    "et les documents non numérisés."
                )
            )
        if stats["erreurs"]:
            self.stdout.write(self.style.ERROR(f"{stats['erreurs']} erreurs."))

    def _traiter_partition(self, partition):
        from apps.parcels.models import Commune, DocumentUrbanisme, Zone

        features = self._appeler(API_ZONE, {"partition": partition})
        est_cc = False
        if not features:
            # Les cartes communales n'utilisent pas zone_urba mais secteur_cc.
            features = self._appeler(API_SECTEUR_CC, {"partition": partition})
            est_cc = True
        if not features:
            return None

        infos_doc_precoce = self._info_document(partition)

        # Regroupement par (idurba, libelle) : un même libellé revient
        # sur des dizaines d'îlots séparés.
        groupes = {}
        ignores = 0
        for feat in features:
            props = feat["properties"]
            libelle = (props.get("libelle") or "").strip()
            if not libelle:
                ignores += 1
                continue

            # L'API laisse idurba à null sur certaines partitions.
            # On retombe alors sur l'identifiant du document du GPU,
            # sinon sur la partition elle-même.
            idurba = (props.get("idurba") or "").strip()
            if not idurba:
                idurba = (infos_doc_precoce.get("name") or "").strip()
            if not idurba:
                idurba = partition

            cle = (idurba, libelle)
            if cle not in groupes:
                groupes[cle] = {"props": props, "polygones": [], "insee": set()}

            geom = self._extraire_polygones(feat.get("geometry"))
            groupes[cle]["polygones"].extend(geom)
            if props.get("insee"):
                groupes[cle]["insee"].add(props["insee"])

        if not groupes:
            raise ValueError(
                f"{len(features)} features reçues mais aucune exploitable "
                f"({ignores} sans libellé)"
            )

        infos_doc = infos_doc_precoce
        maintenant = timezone.now()
        nb_zones = 0

        with transaction.atomic():
            documents = {}

            for (idurba, libelle), data in groupes.items():
                if not data["polygones"]:
                    continue

                if idurba not in documents:
                    documents[idurba] = self._obtenir_document(
                        DocumentUrbanisme, Commune, partition, idurba, infos_doc, maintenant
                    )
                doc = documents[idurba]

                props = data["props"]
                nomfic = (props.get("nomfic") or "").strip()

                Zone.objects.update_or_create(
                    document=doc,
                    libelle=libelle[:50],
                    defaults={
                        "libelle_long": (props.get("libelong") or "")[:500],
                        "type_zone": self._normaliser_type(
                            props.get("typesect") if est_cc else props.get("typezone"),
                            est_cc,
                        ),
                        "nom_fichier_reglement": nomfic[:300],
                        "page_reglement": self._extraire_page(nomfic),
                        "nb_polygones": len(data["polygones"]),
                        "geom": MultiPolygon(data["polygones"], srid=4326),
                        "synced_at": maintenant,
                    },
                )
                nb_zones += 1

        return nb_zones

    def _obtenir_document(self, ModeleDoc, ModeleCommune, partition, idurba, infos, maintenant):
        """
        Crée ou récupère le DocumentUrbanisme. L'API écrit PLUI et PLUi
        selon l'endpoint : on compare sur la forme normalisée.
        """
        type_doc = self._deviner_type(idurba, infos)

        doc, _ = ModeleDoc.objects.update_or_create(
            partition=partition,
            idurba=idurba,
            defaults={
                "type_document": type_doc,
                "est_en_vigueur": True,
                "nom_reglement": (infos.get("grid_title") or "")[:200],
                "synced_at": maintenant,
            },
        )

        # Rattachement des communes via la table brute, seule source
        # complète pour la relation commune ↔ document.
        with connection.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT insee FROM gpu_raw_doc_urba_com WHERE partition = %s",
                [partition],
            )
            insees = [row[0] for row in cur.fetchall()]

        if insees:
            communes = ModeleCommune.objects.filter(code_insee__in=insees)
            doc.communes.add(*communes)

        return doc

    def _info_document(self, partition):
        try:
            features = self._appeler(API_DOC, {"partition": partition})
        except Exception:
            return {}
        return features[0]["properties"] if features else {}

    @staticmethod
    def _appeler(url, params, tentatives=3):
        derniere = None
        for essai in range(tentatives):
            try:
                reponse = requests.get(url, params=params, timeout=TIMEOUT)
                reponse.raise_for_status()
                charge = reponse.json()
                # L'API renvoie HTTP 200 avec une collection vide quand le
                # paramètre ne correspond à rien : il faut tester le contenu.
                return charge.get("features") or []
            except (requests.RequestException, ValueError) as exc:
                derniere = exc
                if essai < tentatives - 1:
                    time.sleep(2 ** essai)  # 1s, puis 2s
        raise RuntimeError(f"Échec après {tentatives} tentatives : {derniere}")

    @staticmethod
    def _extraire_polygones(geojson_geom):
        """Aplatit Polygon et MultiPolygon en une liste de Polygon."""
        if not geojson_geom:
            return []
        import json

        try:
            geom = GEOSGeometry(json.dumps(geojson_geom), srid=4326)
        except Exception:
            return []

        if geom.geom_type == "Polygon":
            return [geom]
        if geom.geom_type == "MultiPolygon":
            return list(geom)
        return []

    @staticmethod
    def _extraire_page(nomfic):
        if not nomfic:
            return None
        trouve = MOTIF_PAGE.search(nomfic)
        return int(trouve.group(1)) if trouve else None

    @staticmethod
    def _normaliser_type(valeur, est_cc=False):
        v = (valeur or "").strip()
        if est_cc:
            return TYPESECT_CC.get(v, "AUTRE")
        return v if v in TYPES_VALIDES else "AUTRE"

    @staticmethod
    def _deviner_type(idurba, infos):
        brut = (infos.get("du_type") or "").upper()
        if not brut:
            majuscule = idurba.upper()
            for candidat in ("PLUI", "PSMV", "PLU", "POS", "CC"):
                if candidat in majuscule:
                    brut = candidat
                    break
        if brut in ("PLUI", "PLU", "POS", "CC", "PSMV"):
            return brut
        return "PLU"