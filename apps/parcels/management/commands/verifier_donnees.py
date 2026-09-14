"""
Contrôle de cohérence des données. À lancer après tout chargement
en masse ou toute suppression manuelle.

    python manage.py verifier_donnees
    python manage.py verifier_donnees --corriger
"""

from django.core.management.base import BaseCommand
from django.db import connection
from django.db.models import Count


class Command(BaseCommand):
    help = "Vérifie la cohérence des données spatiales et relationnelles"

    def add_arguments(self, parser):
        parser.add_argument(
            "--corriger", action="store_true",
            help="Recalcule les intersections manquantes",
        )

    def handle(self, *args, **options):
        from apps.parcels.models import Parcelle, Zone, Prescription, Servitude
        from apps.urbanism_docs.models import ReglementDocument
        import os

        problemes = 0

        # Géométries invalides : une intersection sur une géométrie invalide
        # renvoie un chiffre faux plutôt qu'une erreur.
        self.stdout.write("Géométries invalides")
        with connection.cursor() as cur:
            for table, nom in [
                ("parcels_zone", "zones"),
                ("parcels_parcelle", "parcelles"),
                ("parcels_prescription", "prescriptions"),
                ("parcels_servitude", "servitudes"),
            ]:
                cur.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE NOT ST_IsValid(geom)"
                )
                n = cur.fetchone()[0]
                problemes += n
                style = self.style.ERROR if n else self.style.SUCCESS
                self.stdout.write(style(f"  {nom:15} {n}"))

        # Parcelles sans zonage : souvent le signe d'une suppression en masse
        # suivie d'un rechargement partiel.
        self.stdout.write("\nParcelles sans zonage")
        orphelines = Parcelle.objects.annotate(
            n=Count("parcellezone")
        ).filter(n=0)
        n = orphelines.count()
        problemes += n
        style = self.style.ERROR if n else self.style.SUCCESS
        self.stdout.write(style(f"  {n} / {Parcelle.objects.count()}"))

        if n and options["corriger"]:
            from apps.parcels import services
            ok = 0
            for p in orphelines:
                try:
                    services.calculer_zones(p)
                    ok += 1
                except Exception as exc:
                    self.stdout.write(
                        self.style.ERROR(f"  {p.idu} : {type(exc).__name__}")
                    )
            self.stdout.write(self.style.SUCCESS(f"  {ok} recalculées"))
            problemes -= ok

        # Fichiers référencés mais absents du disque
        self.stdout.write("\nFichiers PDF manquants")
        manquants = [
            r for r in ReglementDocument.objects.exclude(fichier="")
            if not os.path.exists(r.fichier.path)
        ]
        problemes += len(manquants)
        style = self.style.ERROR if manquants else self.style.SUCCESS
        self.stdout.write(style(f"  {len(manquants)}"))
        for r in manquants[:5]:
            self.stdout.write(f"    {r.titre}")

        self.stdout.write("")
        if problemes:
            self.stdout.write(
                self.style.WARNING(f"{problemes} problème(s) détecté(s).")
            )
        else:
            self.stdout.write(self.style.SUCCESS("Aucun problème détecté."))