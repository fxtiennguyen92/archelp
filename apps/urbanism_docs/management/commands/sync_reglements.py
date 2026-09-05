"""
Télécharge les règlements écrits depuis les archives ZIP du GPU.

    python manage.py sync_reglements --partition DU_246700488
    python manage.py sync_reglements --region 44 --limit 10
    python manage.py sync_reglements --region 44

Les archives pèsent de 15 Mo à plus de 3 Go, mais le serveur accepte les
requêtes Range : remotezip lit l'index de l'archive puis ne télécharge que
le PDF voulu, soit quelques Mo au lieu de plusieurs Go.
"""

import hashlib
import re
import time
from pathlib import Path

import requests
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from remotezip import RemoteZip

API_PACK = "https://www.geoportail-urbanisme.gouv.fr/api/document/download-by-partition"
TIMEOUT = 60
PAUSE = 0.5

DOSSIER_REGLEMENT = "3_reglement"
MOTIF_REGLEMENT = re.compile(r"_reglement_\d{8}\.pdf$", re.IGNORECASE)
EXCLUS = ("graphique", "prescription", "annexe", "liste")

TAILLE_MAX_PDF = 80 * 1024 * 1024  # au-delà, c'est presque sûrement un plan


class Command(BaseCommand):
    help = "Récupère les PDF de règlement écrit via les archives GPU"

    def add_arguments(self, parser):
        parser.add_argument("--region", help="Code région, ex: 44")
        parser.add_argument("--partition", help="Une seule partition")
        parser.add_argument("--limit", type=int)
        parser.add_argument(
            "--force", action="store_true",
            help="Retélécharger même si le fichier est déjà en base",
        )

    def handle(self, *args, **options):
        from apps.parcels.models import DocumentUrbanisme

        qs = DocumentUrbanisme.objects.all()
        if options.get("partition"):
            qs = qs.filter(partition=options["partition"])
        elif options.get("region"):
            qs = qs.filter(communes__code_region=options["region"]).distinct()
        else:
            raise CommandError("Précisez --region ou --partition.")

        # Les cartes communales n'ont pas de règlement écrit : leurs archives
        # ne contiennent que rapport de présentation, annexes et procédure.
        qs = qs.exclude(type_document="CC")

        if not options["force"]:
            qs = qs.exclude(reglements__fichier__gt="")

        documents = list(qs.order_by("partition"))
        if options.get("limit"):
            documents = documents[: options["limit"]]

        if not documents:
            raise CommandError("Aucun document à traiter.")

        total = len(documents)
        self.stdout.write(f"{total} documents à traiter.")

        stats = {"ok": 0, "sans_reglement": 0, "erreurs": 0, "octets": 0}
        debut = time.time()

        for i, doc in enumerate(documents, 1):
            try:
                resultat = self._traiter(doc, options["force"])
            except Exception as exc:
                stats["erreurs"] += 1
                self.stdout.write(self.style.ERROR(f"  {doc.partition} : {exc}"))
                continue

            if resultat == 0:
                stats["sans_reglement"] += 1
            else:
                stats["ok"] += 1
                stats["octets"] += resultat

            if i % 10 == 0 or i == total:
                ecoule = time.time() - debut
                reste = (ecoule / i) * (total - i)
                self.stdout.write(
                    f"  {i}/{total} — {stats['ok']} récupérés, "
                    f"{stats['sans_reglement']} sans règlement, "
                    f"{stats['erreurs']} erreurs, "
                    f"{stats['octets']/1e6:.0f} Mo — reste ~{reste/60:.0f} min"
                )

            time.sleep(PAUSE)

        self.stdout.write(
            self.style.SUCCESS(
                f"\nTerminé : {stats['ok']} règlements, {stats['octets']/1e6:.0f} Mo."
            )
        )
        if stats["sans_reglement"]:
            self.stdout.write(
                self.style.WARNING(f"{stats['sans_reglement']} sans règlement écrit.")
            )
        if stats["erreurs"]:
            self.stdout.write(self.style.ERROR(f"{stats['erreurs']} erreurs."))

    def _traiter(self, doc, force):
        from apps.urbanism_docs.models import ReglementDocument

        url_zip = self._resoudre_url(doc.partition)

        # Nom attendu d'après le champ nomfic du zonage : c'est la source
        # la plus fiable quand elle existe (18 à 21 % des zones).
        attendus = {
            z.nom_fichier_reglement.split("#")[0]
            for z in doc.zones.all()
            if z.nom_fichier_reglement
        }

        with RemoteZip(url_zip) as archive:
            entrees = archive.namelist()
            cibles = self._choisir(entrees, attendus)

            if not cibles:
                return 0

            octets = 0
            for chemin in cibles:
                info = archive.getinfo(chemin)
                if info.file_size > TAILLE_MAX_PDF:
                    self.stdout.write(
                        self.style.WARNING(
                            f"  {doc.partition} : {Path(chemin).name} ignoré "
                            f"({info.file_size/1e6:.0f} Mo)"
                        )
                    )
                    continue

                with archive.open(chemin) as flux:
                    contenu = flux.read()

                self._enregistrer(doc, url_zip, chemin, contenu)
                octets += len(contenu)

        return octets

        def _choisir(self, entrees, attendus):
        """
        Sélectionne les PDF de règlement écrit.
        Le filtre porte sur le nom de fichier, jamais sur le chemin :
        le dossier s'appelle « 3_Reglement », ce qui ferait passer les
        prescriptions et le règlement graphique pour du règlement écrit.
        Les exclusions s'appliquent aussi aux noms tirés de nomfic :
        certaines zones y renvoient au règlement graphique.
        """
        def acceptable(chemin):
            nom = Path(chemin).name.lower()
            return not any(mot in nom for mot in EXCLUS)

        if attendus:
            correspondances = [
                e for e in entrees
                if Path(e).name in attendus and acceptable(e)
            ]
            if correspondances:
                return correspondances

        candidats = []
        for entree in entrees:
            nom = Path(entree).name.lower()
            if not MOTIF_REGLEMENT.search(nom):
                continue
            if not acceptable(entree):
                continue
            if DOSSIER_REGLEMENT not in entree.lower():
                continue
            candidats.append(entree)
        return candidats

        def _enregistrer(self, doc, url_zip, chemin, contenu):
        from apps.urbanism_docs.models import ReglementDocument

        nom = Path(chemin).name
        sha = hashlib.sha256(contenu).hexdigest()

        valeurs = {
            "type_piece": ReglementDocument.TypePiece.REGLEMENT,
            "url_source": url_zip[:1000],
            "chemin_interne": chemin[:500],
            "sha256": sha,
            "taille_octets": len(contenu),
            "date_document": doc.date_approbation,
            "statut": ReglementDocument.Statut.TELECHARGE,
            "telecharge_at": timezone.now(),
        }

        # Une même partition peut porter plusieurs DocumentUrbanisme
        # (doublons de la source conservés volontairement). Le PDF, lui,
        # est identique : on réutilise le fichier déjà sur disque.
        jumeau = (
            ReglementDocument.objects.filter(sha256=sha)
            .exclude(fichier="")
            .first()
        )

        regl, _ = ReglementDocument.objects.update_or_create(
            document=doc,
            titre=nom,
            defaults={
                **valeurs,
                "nb_pages": jumeau.nb_pages if jumeau else self._compter_pages(contenu),
            },
        )

        if jumeau:
            regl.fichier.name = jumeau.fichier.name
            regl.save(update_fields=["fichier"])
        else:
            regl.fichier.save(nom, ContentFile(contenu), save=True)

        return regl

    @staticmethod
    def _compter_pages(contenu):
        import io
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(contenu)) as pdf:
                return len(pdf.pages)
        except Exception:
            return None

    @staticmethod
    def _resoudre_url(partition, tentatives=3):
        derniere = None
        for essai in range(tentatives):
            try:
                reponse = requests.head(
                    f"{API_PACK}/{partition}",
                    allow_redirects=True,
                    timeout=TIMEOUT,
                )
                reponse.raise_for_status()
                return reponse.url
            except requests.RequestException as exc:
                derniere = exc
                if essai < tentatives - 1:
                    time.sleep(2 ** essai)
        raise RuntimeError(f"Archive introuvable : {derniere}")