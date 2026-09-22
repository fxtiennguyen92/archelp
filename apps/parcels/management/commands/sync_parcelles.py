"""
Récupère des parcelles cadastrales et calcule zonage, prescriptions et
servitudes. Toute la logique vit dans apps/parcels/services.py : la commande
et l'API passent par le même code.

    python manage.py sync_parcelles --point 7.7521 48.5734
    python manage.py sync_parcelles --idu 67482000DI0120
    python manage.py sync_parcelles --insee 67043 --section 01 --yes
"""

import json

from django.core.management.base import BaseCommand, CommandError

from apps.parcels import services

LIMITE_SANS_CONFIRMATION = 50


class Command(BaseCommand):
    help = "Importe des parcelles cadastrales et calcule leurs contraintes"

    def add_arguments(self, parser):
        parser.add_argument("--idu", help="Identifiant à 14 caractères")
        parser.add_argument("--point", nargs=2, type=float, metavar=("LON", "LAT"))
        parser.add_argument("--insee", help="Code INSEE de la commune")
        parser.add_argument("--section", help="Section cadastrale")
        parser.add_argument("--numero", help="Numéro de parcelle")
        parser.add_argument("--force", action="store_true",
                            help="Rappeler l'API même si la parcelle est en base")
        parser.add_argument("--yes", action="store_true",
                            help="Pas de confirmation au-delà de 50 parcelles")

    def handle(self, *args, **options):
        from apps.parcels.models import Parcelle

        params = self._construire_params(options)

        if options.get("idu") and not options["force"]:
            existante = Parcelle.objects.filter(idu=options["idu"]).first()
            if existante:
                self.stdout.write(f"Déjà en base : {existante.idu}")
                self._afficher(existante)
                return

        self.stdout.write("Appel de l'API Cadastre...")
        try:
            features = services.appeler_cadastre(params)
        except services.ErreurSource as exc:
            raise CommandError(str(exc))

        if not features:
            raise CommandError(
                "Aucune parcelle trouvée. L'API renvoie une collection vide "
                "sans signaler d'erreur : vérifiez les paramètres."
            )

        if len(features) > LIMITE_SANS_CONFIRMATION and not options["yes"]:
            reponse = input(f"Importer {len(features)} parcelles ? [o/N] ")
            if reponse.strip().lower() not in ("o", "oui", "y", "yes"):
                self.stdout.write("Annulé.")
                return

        parcelles, ignorees = [], 0
        for feat in features:
            p = services.enregistrer_parcelle(feat)
            if p is None:
                ignorees += 1
            else:
                parcelles.append(p)
        self.stdout.write(f"{len(parcelles)} parcelles enregistrées, {ignorees} ignorées.")

        totaux = {"zones": 0, "prescriptions": 0, "servitudes": 0}
        for p in parcelles:
            totaux["zones"] += services.calculer_zones(p)
            for nom, fonction in (("prescriptions", services.calculer_prescriptions),
                                  ("servitudes", services.calculer_servitudes)):
                try:
                    totaux[nom] += fonction(p)
                except services.ErreurSource as exc:
                    self.stdout.write(self.style.WARNING(f"  {p.idu} : {nom} indisponibles ({exc})"))

        self.stdout.write(self.style.SUCCESS(
            f"{totaux['zones']} liens zone · {totaux['prescriptions']} prescriptions · "
            f"{totaux['servitudes']} servitudes"
        ))

        if len(parcelles) <= 5:
            for p in parcelles:
                self._afficher(p)

    def _construire_params(self, options):
        if options.get("idu"):
            idu = options["idu"].strip()
            if len(idu) != 14:
                raise CommandError(f"L'IDU doit faire 14 caractères, reçu {len(idu)}.")
            return {"code_insee": idu[:5], "section": idu[8:10], "numero": idu[10:14]}
        if options.get("point"):
            lon, lat = options["point"]
            return {"geom": json.dumps({"type": "Point", "coordinates": [lon, lat]})}
        if options.get("insee"):
            params = {"code_insee": options["insee"]}
            if options.get("section"):
                params["section"] = options["section"]
            if options.get("numero"):
                params["numero"] = options["numero"]
            return params
        raise CommandError("Précisez --idu, --point, ou --insee [--section] [--numero].")

    def _afficher(self, parcelle):
        self.stdout.write(f"\nParcelle {parcelle.idu} — {parcelle.commune.nom}")
        self.stdout.write(f"  Contenance : {parcelle.contenance_m2} m²")

        liens = parcelle.parcellezone_set.select_related("zone").order_by("-part_pct")
        if not liens:
            self.stdout.write(self.style.WARNING("  Aucune zone : commune sans document ?"))
        for l in liens:
            marque = " ← dominante" if l.est_dominante else ""
            self.stdout.write(
                f"  {l.zone.libelle:8} {l.zone.type_zone:6} "
                f"{l.part_pct:5.1f}%  ({l.surface_intersection_m2:.0f} m²){marque}"
            )

        psc = parcelle.parcelleprescription_set.select_related("prescription").order_by("-part_pct")
        if psc:
            self.stdout.write("  Prescriptions :")
            for l in psc:
                p = l.prescription
                valeur = ""
                if p.valeur_num is not None:
                    valeur = f"  →  {p.valeur_num:g} {p.unite} {p.reference_mesure}".rstrip()
                self.stdout.write(f"    {l.part_pct:5.1f}%  [{p.type_psc}] {p.libelle[:60]}{valeur}")

        serv = parcelle.parcelleservitude_set.select_related("servitude")
        if serv:
            types = sorted({l.servitude.sup_type for l in serv})
            self.stdout.write(f"  Servitudes : {', '.join(types)} ({serv.count()} périmètres)")