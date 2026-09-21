"""
Essai d'extraction de structure SRU niveau 1 avec Gemini Flash.

    python manage.py tester_extraction --titre 246700488_reglement_20260206.pdf
    python manage.py tester_extraction --titre 67411_reglement_20250414.pdf --pages 30

Rien n'est écrit en base : le but est de mesurer la qualité et le coût
avant d'engager un traitement de masse.
"""

import json
import re
import time

import pdfplumber
import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

TIMEOUT = 600  # un modèle local sur 14 pages peut être lent

INSTRUCTION = """Tu analyses le règlement écrit d'un PLU français.

Tâche : restituer la structure hiérarchique du document. Rien d'autre.
N'extrais AUCUNE valeur réglementaire (hauteur, emprise, recul).

Réponds uniquement par un objet JSON :
{
  "source": "sommaire" | "corps",
  "fragments": [
    {
      "type": "TITRE|CHAPITRE|SECTION|ARTICLE",
      "numero": "<ex: UB 10, 1, 2.3 — chaîne vide si absent>",
      "titre": "<intitulé exact, jamais vide>",
      "zones": ["<libellés de zone, ex: UB, UB1 — vide si général>"],
      "page": <numéro de la PAGE PDF où se trouve le fragment, jamais null>,
      "profondeur": <0 à 3 uniquement>
    }
  ]
}

Règles strictes :
- Chaque bloc d'entrée commence par « === PAGE PDF N === ». Le champ "page"
  vaut ce N, celui du bloc où l'intitulé apparaît. Ne recopie jamais un
  numéro de page imprimé dans le texte.
- "profondeur" ne dépasse jamais 3 : 0=TITRE, 1=CHAPITRE, 2=SECTION, 3=ARTICLE.
- Ignore la page de garde : nom de la commune, date, logo, maître d'ouvrage,
  intitulé du bureau d'études ne sont pas des fragments.
- Une énumération de secteurs dans un paragraphe (« Ua : les centres anciens »)
  n'est PAS une hiérarchie. Ne la découpe pas en fragments.
- Si un intitulé de zone figure en en-tête de page (« ZONE UA »), reporte-le
  dans "zones" pour tous les articles de cette page.
- N'invente rien. Si tu n'es pas sûr d'un fragment, ne le produis pas.
"""


class Command(BaseCommand):
    help = "Essai d'extraction de structure avec Gemini Flash"

    def add_arguments(self, parser):
        parser.add_argument("--titre", required=True, help="Nom du fichier PDF")
        parser.add_argument(
            "--pages", type=int, default=14,
            help="Nombre de pages à envoyer depuis le début",
        )
        parser.add_argument(
            "--depuis", type=int, default=1,
            help="Première page à envoyer (1-indexé)",
        )
        parser.add_argument(
            "--modele", default=None,
            help="Nom du modèle Ollama, ex: mistral, qwen2.5:7b",
        )

    def handle(self, *args, **options):
        from apps.urbanism_docs.models import ReglementDocument

        regl = ReglementDocument.objects.filter(titre=options["titre"]).first()
        if regl is None or not regl.fichier:
            raise CommandError(f"Règlement introuvable : {options['titre']}")

        debut = options["depuis"] - 1
        fin = debut + options["pages"]

        with pdfplumber.open(regl.fichier.path) as pdf:
            total = len(pdf.pages)
            fin = min(fin, total)
            morceaux = []
            for i in range(debut, fin):
                texte = pdf.pages[i].extract_text() or ""
                morceaux.append(f"=== PAGE PDF {i + 1} ===\n{texte}")

        contenu = "\n\n".join(morceaux)
        self.stdout.write(
            f"{regl.titre} — {total} pages, envoi des pages "
            f"{debut + 1} à {fin} ({len(contenu)} caractères)"
        )

        modele = options["modele"] or settings.OLLAMA_MODEL
        self.stdout.write(f"Modèle : {modele} sur {settings.OLLAMA_HOST}")

        depart = time.time()
        reponse = None
        derniere = None
        for essai in range(1):
            try:
                r = requests.post(
                    f"{settings.OLLAMA_HOST}/api/generate",
                    json={
                        "model": modele,
                        "system": INSTRUCTION,
                        "prompt": contenu,
                        "stream": False,
                        "format": "json",
                        "options": {
                            "temperature": 0,
                            "num_ctx": 8192,
                            "num_predict": 4096,
                        },
                    },
                    timeout=TIMEOUT,
                )
                r.raise_for_status()
                reponse = r.json().get("response", "")
                break
            except requests.RequestException as exc:
                derniere = exc
                if essai < 1:
                    self.stdout.write(self.style.WARNING("  réseau, nouvelle tentative"))
                    time.sleep(3)
            except Exception as exc:
                # Sortie tronquée ou JSON invalide : réessayer donnera
                # exactement le même résultat.
                raise CommandError(f"Réponse inexploitable : {exc}")
        if reponse is None:
            raise CommandError(f"Appel Ollama échoué : {derniere}")

        duree = time.time() - depart
        self.stdout.write(f"Durée : {duree:.1f} s")

        usage = getattr(reponse, "usage_metadata", None)
        if usage:
            self.stdout.write(
                f"Jetons : {usage.prompt_token_count} entrée, "
                f"{usage.candidates_token_count} sortie — {duree:.1f} s"
            )

        try:
            data = json.loads(reponse)
        except json.JSONDecodeError:
            self.stdout.write(self.style.ERROR("Réponse non JSON :"))
            self.stdout.write(reponse[:1500])
            return

        fragments = data.get("fragments", [])
        self.stdout.write(
            f"\nsource={data.get('source')} · "
            f"{len(fragments)} fragments\n"
        )

        for f in fragments[:40]:
            zones = ",".join(f.get("zones") or []) or "—"
            page = f.get("page")
            self.stdout.write(
                f"  p.{str(page):>4}  d{f.get('profondeur')}  "
                f"{f.get('type', ''):10} {(f.get('numero') or ''):10} "
                f"[{zones:12}] {(f.get('titre') or '')[:55]}"
            )
        if len(fragments) > 40:
            self.stdout.write(f"  … et {len(fragments) - 40} autres")

        self._verifier(data, regl)

    def _verifier(self, data, regl):
        """
        Contrôle indépendant : on recherche les intitulés dans le texte du PDF.
        Le modèle peut inventer une page ; la chaîne, elle, s'y trouve ou non.
        """
        import unicodedata

        def norm(s):
            s = unicodedata.normalize("NFKD", (s or "").lower())
            s = "".join(c for c in s if not unicodedata.combining(c))
            return re.sub(r"[^a-z0-9]+", " ", s).strip()

        fragments = [f for f in data.get("fragments", []) if f.get("titre")]
        if not fragments:
            return

        with pdfplumber.open(regl.fichier.path) as pdf:
            pages = [norm(p.extract_text() or "") for p in pdf.pages]

        trouves = 0
        for f in fragments:
            cible = norm(f["titre"])[:45]
            if len(cible) < 20:
                continue
            if any(cible in p for p in pages):
                trouves += 1

        verifiables = sum(1 for f in fragments if len(norm(f["titre"])) >= 20)
        if verifiables:
            self.stdout.write(
                f"\nVérification : {trouves}/{verifiables} intitulés "
                f"retrouvés dans le PDF ({100 * trouves / verifiables:.0f} %)"
            )