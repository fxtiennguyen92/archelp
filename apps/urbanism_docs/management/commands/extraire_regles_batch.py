"""
Extraction des règles en mode Batch de Gemini, au demi-tarif.

    python manage.py extraire_regles_batch --soumettre --lots 1          # un lot de 100 chapitres
    python manage.py extraire_regles_batch --recuperer                   # à relancer jusqu'à la fin
    python manage.py extraire_regles_batch --etat
    python manage.py extraire_regles_batch --soumettre --niveau high --lots 1   # chapitres A_REPRENDRE

Les lots sont enregistrés en base : la machine peut être éteinte entre la
soumission et la récupération. Un chapitre déjà extrait ou déjà dans un lot
en attente n'est jamais soumis une seconde fois.
"""

import json
from collections import Counter

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.urbanism_docs.management.commands.extraire_regles import (
    MODELE, PRIX_ENTREE, PRIX_SORTIE, SORTIE_MAX, VERSION_PROMPT, couverture, lister_chapitres,
)
from apps.urbanism_docs.management.commands.tester_regles import INSTRUCTION, lire, zone_confirmee

ETATS_FINAUX = {"JOB_STATE_SUCCEEDED", "JOB_STATE_FAILED",
                "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED"}


def juger(regles, zones_lues, textes, attendues, niveau, seuil):
    couv, nb = couverture(textes, regles)
    if not regles:
        return "VIDE", couv, nb
    if not zones_lues or not any(zone_confirmee(z, zones_lues) for z in attendues):
        return "ZONE_REJETEE", couv, nb
    if niveau == "low" and couv < seuil:
        return "A_REPRENDRE", couv, nb
    return "OK", couv, nb


def requete(textes, niveau):
    # La zone attendue n'est pas transmise : le contrôle Z n'aurait plus de sens.
    contenu = "\n\n".join(f"=== PAGE PDF {i} ===\n{t}" for i, t in textes.items())
    return {
        "contents": [{"parts": [{"text": contenu}], "role": "user"}],
        "config": {
            "system_instruction": INSTRUCTION,
            "response_mime_type": "application/json",
            "temperature": 0,
            "max_output_tokens": SORTIE_MAX,
            "thinking_config": {"thinking_level": niveau},
        },
    }


class Command(BaseCommand):
    help = "Extraction des règles en mode Batch"

    def add_arguments(self, parser):
        parser.add_argument("--soumettre", action="store_true")
        parser.add_argument("--recuperer", action="store_true")
        parser.add_argument("--etat", action="store_true")
        parser.add_argument("--niveau", default="low", choices=["low", "high"])
        parser.add_argument("--lots", type=int, default=1)
        parser.add_argument("--taille-lot", type=int, default=100)
        parser.add_argument("--region", default="44")
        parser.add_argument("--seuil", type=float, default=0.8)

    def handle(self, *args, **options):
        from google import genai

        if not getattr(settings, "GEMINI_API_KEY", ""):
            raise CommandError("GEMINI_API_KEY absent de la configuration.")
        self.client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self.jetons = Counter()

        if options["soumettre"]:
            self.soumettre(options["niveau"], options["lots"], options["taille_lot"], options["region"])
        elif options["recuperer"]:
            self.recuperer(options["seuil"])
        elif options["etat"]:
            self.etat()
        else:
            raise CommandError("Précisez --soumettre, --recuperer ou --etat.")

    # --- Soumission -------------------------------------------------------

    def soumettre(self, niveau, nb_lots, taille, region):
        from apps.urbanism_docs.models import ExtractionChapitre, LotBatch

        en_attente = {
            c["cle"]
            for lot in LotBatch.objects.filter(etat="SOUMIS")
            for c in lot.chapitres
        }

        if niveau == "low":
            deja = set(ExtractionChapitre.objects.values_list("cle", flat=True))
            candidats = [
                {"cle": c["cle"], "reglement_id": c["reglement"].id, "debut": c["debut"],
                 "fin": c["fin"], "zones": [z.id for z in c["zones"]],
                 "libelles": sorted({z.libelle for z in c["zones"]})}
                for c in lister_chapitres(region)
                if c["cle"] not in deja and c["cle"] not in en_attente
            ]
        else:
            candidats = [
                {"cle": e.cle, "reglement_id": e.reglement_id, "debut": e.page_debut,
                 "fin": e.page_fin, "zones": list(e.zones.values_list("id", flat=True)),
                 "libelles": e.zones_attendues}
                for e in ExtractionChapitre.objects.filter(statut="A_REPRENDRE")
                if e.cle not in en_attente
            ]

        self.stdout.write(f"{len(candidats)} chapitres candidats · niveau {niveau}")
        from apps.urbanism_docs.models import ReglementDocument

        for k in range(nb_lots):
            lot = candidats[k * taille:(k + 1) * taille]
            if not lot:
                break
            requetes = []
            for ch in lot:
                regl = ReglementDocument.objects.get(pk=ch["reglement_id"])
                requetes.append(requete(lire(regl.fichier.path, ch["debut"], ch["fin"]), niveau))
            nom = f"archelp-{niveau}-{timezone.now():%Y%m%d-%H%M%S}-{k + 1}"
            try:
                job = self.client.batches.create(
                    model=MODELE, src=requetes, config={"display_name": nom},
                )
            except Exception as exc:
                raise CommandError(f"Soumission refusée au lot {k + 1} : {exc}")
            LotBatch.objects.create(
                nom_job=job.name, niveau=niveau, version_prompt=VERSION_PROMPT, chapitres=lot,
            )
            self.stdout.write(f"  lot {k + 1} soumis : {job.name} · {len(lot)} chapitres")

    # --- Récupération -----------------------------------------------------

    def recuperer(self, seuil):
        from apps.urbanism_docs.models import LotBatch

        lots = list(LotBatch.objects.filter(etat="SOUMIS"))
        if not lots:
            self.stdout.write("Aucun lot en attente.")
            return

        for lot in lots:
            job = self.client.batches.get(name=lot.nom_job)
            etat = getattr(job.state, "name", str(job.state))
            lot.etat_api = etat
            if etat not in ETATS_FINAUX:
                lot.save(update_fields=["etat_api"])
                self.stdout.write(f"  {lot.nom_job} : {etat}")
                continue
            if etat != "JOB_STATE_SUCCEEDED":
                # Les chapitres redeviennent candidats à la prochaine soumission.
                lot.etat = "ECHEC"
                lot.save(update_fields=["etat", "etat_api"])
                self.stdout.write(self.style.ERROR(f"  {lot.nom_job} : {etat}"))
                continue

            reponses = job.dest.inlined_responses or []
            if len(reponses) != len(lot.chapitres):
                self.stdout.write(self.style.WARNING(
                    f"  {lot.nom_job} : {len(reponses)} réponses pour {len(lot.chapitres)} chapitres"
                ))
            statuts = Counter()
            for ch, rep in zip(lot.chapitres, reponses):
                statuts[self.enregistrer(ch, rep, lot.niveau, seuil)] += 1
            lot.etat = "TERMINE"
            lot.termine_at = timezone.now()
            lot.save(update_fields=["etat", "etat_api", "termine_at"])
            self.stdout.write(self.style.SUCCESS(f"  {lot.nom_job} : {dict(statuts)}"))

        if self.jetons:
            e, s = self.jetons["entree"], self.jetons["sortie"]
            cout = (e * PRIX_ENTREE + s * PRIX_SORTIE) / 1e6 / 2
            self.stdout.write(f"\nJetons récupérés : {e} entrée, {s} sortie · "
                              f"coût estimé au tarif Batch : {cout:.2f} USD")

    def enregistrer(self, ch, rep, niveau, seuil):
        from apps.urbanism_docs.models import ExtractionChapitre, ReglementDocument

        regl = ReglementDocument.objects.get(pk=ch["reglement_id"])
        entree = sortie = 0
        statut, brut, regles, lues, renvois = None, "", [], [], []

        if getattr(rep, "error", None):
            statut, brut = "ERREUR", str(rep.error)[:2000]
        else:
            r = rep.response
            u = r.usage_metadata
            if u:
                entree = u.prompt_token_count or 0
                sortie = (u.candidates_token_count or 0) + (u.thoughts_token_count or 0)
            brut = r.text or ""
            fin = str(r.candidates[0].finish_reason) if r.candidates else ""
            if "MAX_TOKENS" in fin:
                statut = "TRONQUE"
            else:
                try:
                    data = json.loads(brut)
                except json.JSONDecodeError:
                    statut = "JSON_INVALIDE"
                else:
                    if isinstance(data, dict):
                        regles = data.get("regles") or []
                        lues = data.get("zones_du_chapitre") or []
                        renvois = data.get("renvois") or []
                    elif isinstance(data, list):
                        # Le modèle renvoie parfois le tableau de règles seul :
                        # sans titres de zone lus, le contrôle Z écartera le chapitre.
                        regles, lues, renvois = data, [], []
                    else:
                        statut = "JSON_INVALIDE"

        self.jetons["entree"] += entree
        self.jetons["sortie"] += sortie

        couv = nb = None
        if statut is None:
            textes = lire(regl.fichier.path, ch["debut"], ch["fin"])
            statut, couv, nb = juger(regles, lues, textes, ch["libelles"], niveau, seuil)

        ext = ExtractionChapitre.objects.filter(cle=ch["cle"]).first()
        nouveau = ext is None
        if nouveau:
            ext = ExtractionChapitre(
                cle=ch["cle"], reglement=regl, page_debut=ch["debut"], page_fin=ch["fin"],
                zones_attendues=ch["libelles"],
            )
        ext.jetons_entree = (ext.jetons_entree or 0) + entree
        ext.jetons_sortie = (ext.jetons_sortie or 0) + sortie
        ext.modele, ext.niveau, ext.version_prompt = MODELE, niveau, VERSION_PROMPT
        ext.brut, ext.statut = brut, statut
        if regles or lues:
            ext.regles, ext.zones_lues, ext.renvois = regles, lues, renvois
        if couv is not None:
            ext.couverture, ext.nb_nombres = couv, nb
        ext.save()
        if nouveau:
            ext.zones.set(ch["zones"])
        return statut

    # --- État -------------------------------------------------------------

    def etat(self):
        from django.db.models import Sum
        from apps.urbanism_docs.models import ExtractionChapitre, LotBatch

        self.stdout.write("Lots : " + str(dict(Counter(l.etat for l in LotBatch.objects.all()))))
        for lot in LotBatch.objects.filter(etat="SOUMIS"):
            self.stdout.write(f"  en attente : {lot.nom_job} · {lot.niveau} · {len(lot.chapitres)} ch. · {lot.etat_api or '—'}")
        qs = ExtractionChapitre.objects.all()
        self.stdout.write(f"\nExtractions : {qs.count()} · " + str(dict(Counter(qs.values_list('statut', flat=True)))))
        t = qs.aggregate(e=Sum("jetons_entree"), s=Sum("jetons_sortie"))
        e, s = t["e"] or 0, t["s"] or 0
        self.stdout.write(f"Jetons cumulés : {e} entrée, {s} sortie")