"""
Dérive les DocumentUrbanisme à partir des tables brutes GPU.

    python manage.py build_documents --region 44
    python manage.py build_documents --region 44 --dry-run

Prérequis : gpu_raw_doc_urba et gpu_raw_doc_urba_com chargées via ogr2ogr,
et les communes de la région déjà synchronisées.
"""

from collections import defaultdict
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

ETAT_OPPOSABLE = "03"

# Le GPU écrit PLUI et PLUi selon les dépôts.
NORMALISATION_TYPE = {
    "PLU": "PLU",
    "PLUI": "PLUI",
    "PLUi": "PLUI",
    "POS": "POS",
    "CC": "CC",
    "PSMV": "PSMV",
}

REQUETE = """
    SELECT
        d.partition,
        d.idurba,
        d.typedoc,
        d.datappro,
        d.datefin,
        d.nomproc,
        d.nomreg,
        d.urlreg,
        d.urlplan,
        d.siteweb,
        c.insee
    FROM gpu_raw_doc_urba d
    JOIN gpu_raw_doc_urba_com c ON c.idurba = d.idurba
    JOIN parcels_commune pc ON pc.code_insee = c.insee
    WHERE d.etat = %s AND pc.code_region = %s
"""


class Command(BaseCommand):
    help = "Construit les DocumentUrbanisme depuis les tables brutes du GPU"

    def add_arguments(self, parser):
        parser.add_argument("--region", required=True, help="Code région, ex: 44")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Analyse sans rien écrire en base",
        )

    def handle(self, *args, **options):
        from apps.parcels.models import Commune, DocumentUrbanisme

        region = options["region"]
        dry_run = options["dry_run"]

        self._verifier_tables_brutes()

        with connection.cursor() as cur:
            cur.execute(REQUETE, [ETAT_OPPOSABLE, region])
            colonnes = [col[0] for col in cur.description]
            lignes = [dict(zip(colonnes, row)) for row in cur.fetchall()]

        if not lignes:
            raise CommandError(
                f"Aucune ligne pour la région {region}. "
                "Vérifiez que les communes sont synchronisées et les tables brutes chargées."
            )

        self.stdout.write(f"{len(lignes)} associations document/commune trouvées.")

        # Regroupement par couple (partition, idurba) : une ligne de la source
        # apparaît autant de fois qu'elle a de communes rattachées.
        documents = {}
        communes_par_doc = defaultdict(set)

        for ligne in lignes:
            cle = (ligne["partition"], ligne["idurba"])
            communes_par_doc[cle].add(ligne["insee"])
            if cle not in documents:
                documents[cle] = ligne

        self.stdout.write(f"{len(documents)} documents distincts (partition, idurba).")

        # Détection des partitions ayant plusieurs documents opposables.
        # Cas DU_01202 : deux PLU opposables, la source ne dit pas lequel prime.
        # Cas DU_03018 : PLUi regroupant les anciens documents communaux ;
        # les communes rattachées diffèrent, ce n'est pas une contradiction.
        par_partition = defaultdict(list)
        for (partition, idurba) in documents:
            par_partition[partition].append(idurba)

        partitions_en_conflit = set()
        for partition, idurbas in par_partition.items():
            if len(idurbas) < 2:
                continue
            communes_vues = set()
            for idurba in idurbas:
                communes = communes_par_doc[(partition, idurba)]
                if communes & communes_vues:
                    partitions_en_conflit.add(partition)
                    break
                communes_vues |= communes

        if partitions_en_conflit:
            self.stdout.write(
                self.style.WARNING(
                    f"{len(partitions_en_conflit)} partitions avec documents "
                    "opposables concurrents sur une même commune."
                )
            )

        if dry_run:
            self._afficher_conflits(documents, communes_par_doc, partitions_en_conflit)
            self.stdout.write(self.style.WARNING("Dry run : rien n'a été écrit."))
            return

        index_communes = {
            c.code_insee: c
            for c in Commune.objects.filter(code_region=region)
        }

        crees = 0
        maj = 0
        maintenant = timezone.now()

        with transaction.atomic():
            for cle, ligne in documents.items():
                partition, idurba = cle

                type_doc = NORMALISATION_TYPE.get(ligne["typedoc"])
                if type_doc is None:
                    self.stdout.write(
                        self.style.WARNING(f"Type inconnu ignoré : {ligne['typedoc']} ({idurba})")
                    )
                    continue

                doc, cree = DocumentUrbanisme.objects.update_or_create(
                    partition=partition,
                    idurba=idurba,
                    defaults={
                        "type_document": type_doc,
                        "date_approbation": self._parser_date(ligne["datappro"]),
                        "date_fin_validite": self._parser_date(ligne["datefin"]),
                        "est_en_vigueur": True,
                        "nom_procedure": (ligne["nomproc"] or "")[:10],
                        "nom_reglement": (ligne["nomreg"] or "")[:200],
                        "url_reglement": (ligne["urlreg"] or "")[:500],
                        "url_plan": (ligne["urlplan"] or "")[:500],
                        "site_web": (ligne["siteweb"] or "")[:500],
                        "a_doublon_source": partition in partitions_en_conflit,
                        "synced_at": maintenant,
                    },
                )

                objets = [
                    index_communes[insee]
                    for insee in communes_par_doc[cle]
                    if insee in index_communes
                ]
                doc.communes.set(objets)

                if cree:
                    crees += 1
                else:
                    maj += 1

        self.stdout.write(
            self.style.SUCCESS(f"Terminé : {crees} créés, {maj} mis à jour.")
        )
        if partitions_en_conflit:
            self.stdout.write(
                f"{len(partitions_en_conflit)} partitions marquées a_doublon_source."
            )

    @staticmethod
    def _verifier_tables_brutes():
        with connection.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM information_schema.tables
                WHERE table_name IN ('gpu_raw_doc_urba', 'gpu_raw_doc_urba_com')
            """)
            if cur.fetchone()[0] < 2:
                raise CommandError(
                    "Tables brutes absentes. Chargez-les d'abord avec ogr2ogr."
                )

    @staticmethod
    def _parser_date(valeur):
        """Le GPU stocke les dates en texte AAAAMMJJ, parfois vides ou nulles."""
        if not valeur or not valeur.strip():
            return None
        try:
            return datetime.strptime(valeur.strip(), "%Y%m%d").date()
        except ValueError:
            return None

    def _afficher_conflits(self, documents, communes_par_doc, partitions_en_conflit):
        if not partitions_en_conflit:
            self.stdout.write("Aucun conflit détecté.")
            return
        self.stdout.write("\nExemples de conflits :")
        montres = 0
        for partition in sorted(partitions_en_conflit):
            for (p, idurba), ligne in documents.items():
                if p != partition:
                    continue
                communes = sorted(communes_par_doc[(p, idurba)])
                self.stdout.write(
                    f"  {partition} | {idurba} | {ligne['typedoc']} | "
                    f"appro {ligne['datappro']} | proc {ligne['nomproc']} | "
                    f"communes {','.join(communes[:3])}"
                )
            montres += 1
            self.stdout.write("")
            if montres >= 5:
                break