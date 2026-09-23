"""
Enregistre, pour chaque zone, la plage de pages de son chapitre dans le
règlement, repérée sans modèle de langage (voir detecter_chapitres).

    python manage.py enregistrer_chapitres --region 76
    python manage.py enregistrer_chapitres --region 44 --limit 20
    python manage.py enregistrer_chapitres --region 44

Seuls les rattachements « exact » et « secteur » sont conservés : le type
« famille » (intitulé du genre « zones agricoles ») s'est révélé trop peu fiable.
Rejouable : les documents déjà traités sont ignorés, sauf --refaire.
"""

from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.urbanism_docs.management.commands.detecter_chapitres import (
    canon, detecter, lire_pages, rattacher,
)

FAMILLES = {"U", "AU", "A", "N"}
TYPES_RETENUS = {"exact", "secteur"}


def qualifier(zone, ch, libelles_document):
    """
    « exact »   : le chapitre nomme la zone elle-même.
    « secteur » : la zone est un secteur d'un libellé nommé par le chapitre —
                  y compris quand ce libellé est « N » ou « A », dès lors qu'il
                  existe comme zone du document et non comme simple famille.
    « famille » : le chapitre ne cite qu'un genre de zone (« zones agricoles »).
    """
    cz = canon(zone.libelle)
    libs = ch["libelles"]
    if cz in libs:
        t = "exact"
    elif any(
        cz != l and cz.startswith(l) and (l not in FAMILLES or l in libelles_document)
        for l in libs
    ):
        t = "secteur"
    else:
        t = "famille"
    return f"{t}/{ch['force']}"


class Command(BaseCommand):
    help = "Enregistre la plage de pages du chapitre de chaque zone"

    def add_arguments(self, parser):
        parser.add_argument("--region")
        parser.add_argument("--titre")
        parser.add_argument("--limit", type=int)
        parser.add_argument("--refaire", action="store_true")

    def handle(self, *args, **options):
        from apps.parcels.models import Zone
        from apps.urbanism_docs.models import ReglementDocument

        qs = ReglementDocument.objects.exclude(fichier="").filter(est_numerise=False)
        if options.get("titre"):
            qs = qs.filter(titre=options["titre"])
        elif options.get("region"):
            qs = qs.filter(document__communes__code_region=options["region"]).distinct()
        else:
            raise CommandError("Précisez --region ou --titre.")

        # Un même PDF peut être rattaché à plusieurs documents : on trie par
        # fichier pour ne le lire qu'une fois, mais on traite chaque document.
        reglements = list(qs.select_related("document").order_by("fichier"))
        if not options["refaire"]:
            reglements = [
                r for r in reglements
                if r.document.zones.filter(chapitre_at__isnull=True).exists()
            ]
        if options.get("limit"):
            reglements = reglements[: options["limit"]]

        total = len(reglements)
        self.stdout.write(f"{total} règlements à traiter.")

        stats = Counter()
        fichier_lu, pages = None, None
        maintenant = timezone.now()

        for i, regl in enumerate(reglements, 1):
            if regl.fichier.name != fichier_lu:
                try:
                    pages = lire_pages(regl)
                    fichier_lu = regl.fichier.name
                except Exception:
                    stats["pdf_illisible"] += 1
                    continue

            zones = list(regl.document.zones.all())
            chapitres = detecter(pages, zones)
            libelles_document = {canon(z.libelle) for z in zones}

            for z in zones:
                # Si nomfic désigne un autre PDF du document, ce n'est pas le bon.
                nom = (z.nom_fichier_reglement or "").split("#")[0]
                if nom and nom != regl.titre:
                    continue
                ch = rattacher(z, chapitres)
                if ch is None:
                    stats["sans_chapitre"] += 1
                    Zone.objects.filter(pk=z.pk).update(chapitre_at=maintenant)
                    continue
                t = qualifier(z, ch, libelles_document)
                if t.split("/")[0] not in TYPES_RETENUS:
                    stats["ecarte_famille"] += 1
                    Zone.objects.filter(pk=z.pk).update(chapitre_at=maintenant)
                    continue
                Zone.objects.filter(pk=z.pk).update(
                    page_chapitre_debut=ch["page"],
                    page_chapitre_fin=ch["page_fin"],
                    type_chapitre=t,
                    chapitre_at=maintenant,
                )
                stats["enregistre"] += 1

            if i % 25 == 0 or i == total:
                self.stdout.write(
                    f"  {i}/{total} — {stats['enregistre']} zones enregistrées, "
                    f"{stats['ecarte_famille']} écartées (famille), "
                    f"{stats['sans_chapitre']} sans chapitre"
                )

        self.stdout.write(self.style.SUCCESS("Terminé."))