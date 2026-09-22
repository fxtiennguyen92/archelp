"""
Repère le début du chapitre de chaque zone dans le corps du règlement,
sans sommaire ni modèle de langage, et rattache les zones du GPU.

    python manage.py detecter_chapitres --titre 32331_reglement_20241212.pdf
    python manage.py detecter_chapitres --region 44 --limit 50

La détection s'ancre sur la liste des zones que le GPU donne pour le
document : un intitulé n'est retenu que si les libellés qu'il cite existent.
Rien n'est écrit en base.
"""

import re
import unicodedata
from collections import Counter, defaultdict

import pdfplumber
from django.core.management.base import BaseCommand, CommandError

FAMILLES_TYPE = {"U": "U", "AU": "AU", "AUc": "AU", "AUs": "AU", "A": "A", "N": "N"}
LIAISONS = {"ET", "OU", "&"}

# Intitulés descriptifs ramenés à la famille CNIG. Le préfixe § distingue
# une famille déduite d'une phrase d'un libellé écrit tel quel.
PHRASES = [
    (re.compile(r"\bA\s+URBANISER\b"), " §AU "),
    (re.compile(r"\bURBAINES?\b"), " §U "),
    (re.compile(r"\bAGRICOLES?\b"), " §A "),
    (re.compile(r"\bNATURELLES?(\s+(ET|OU)\s+FORESTIERES?)?\b"), " §N "),
    (re.compile(r"\bFORESTIERES?\b"), " §N "),
]

TITRE = re.compile(r"^(ZONES?|CHAPITRE|TITRE|DISPOSITIONS|REGLES?|REGLEMENT)\b")
INTITULE_CHAPITRE = re.compile(
    r"\bCHAPITRE\b|DISPOSITIONS\s+APPLICABLES|REGLEMENT\s+APPLICABLE"
    r"|REGLES\s+PARTICULIERES|APPLICABLES?\s+(AUX?|A\s+LA)\s+ZONES?"
)
MARQUEURS = re.compile(
    r"\bARTICLE\s*1\b|\bSECTION\s*(1|I)\b|OCCUPATIONS?\s+(ET|DES)\b"
    r"|DESTINATIONS?\s+DES\s+CONSTRUCTIONS|INTERDIT"
)


def majuscules(texte):
    t = unicodedata.normalize("NFKD", texte)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return t.upper()


def canon(libelle):
    s = re.sub(r"[\s.\-_]", "", str(libelle)).upper()
    s = re.sub(r"^IIAU", "2AU", s)
    s = re.sub(r"^IAU", "1AU", s)
    return s


def famille_libelle(c):
    """Famille CNIG d'un libellé normalisé : U, AU, A ou N."""
    c = re.sub(r"^\d", "", c)
    return "AU" if c.startswith("AU") else c[:1]


def reconnu(c, connus):
    """Le jeton désigne-t-il une zone du document, ou une zone mère ?"""
    c0 = re.sub(r"^\d", "", c)
    return any(
        cz == c or cz.startswith(c) or re.sub(r"^\d", "", cz).startswith(c0)
        for cz in connus
    )


def ressemble_sommaire(lignes):
    """Page de sommaire : beaucoup de lignes terminées par un numéro de page."""
    n = sum(1 for l in lignes if re.search(r"(\.{3,}|\s)\d{1,3}$", l))
    texte = majuscules(" ".join(lignes))
    return n >= 6 or (n >= 3 and ("SOMMAIRE" in texte or "TABLE DES MATIERES" in texte))


def libelles_intitule(ligne, connus):
    """
    Libellés cités par un intitulé, et qualité du signal :
    2 — intitulé de chapitre écrit comme un titre (« CHAPITRE… », « DISPOSITIONS
        APPLICABLES AUX ZONES… ») ;
    1 — autre titre ou ligne en majuscules ;
    0 — ligne ordinaire terminée par un libellé : en-tête de page ou fin de phrase.
    """
    L = majuscules(ligne)
    L = re.sub(r"\b([12]|I{1,2})\s+AU\b", r"\1AU", L)   # « ZONE 1 AU »

    # Dernière occurrence : « … ZONES AGRICOLES – ZONE A » doit donner A.
    m = None
    for m in re.finditer(r"\bZONES?\s+", L):
        pass
    if m is None:
        return [], None

    en_titre = TITRE.match(L) is not None
    comme_titre = en_titre or ligne == ligne.upper()
    if INTITULE_CHAPITRE.search(L) and comme_titre:
        qualite = 2
    elif comme_titre:
        qualite = 1
    else:
        qualite = 0

    reste = re.split(r"\s[-–]\s|:", L[m.end():])[0]
    for motif, remplacement in PHRASES:
        reste = motif.sub(remplacement, reste)
    entre_parentheses = re.findall(r"\(([^)]+)\)", reste)
    if entre_parentheses:
        reste = " ".join(entre_parentheses)

    jetons = [j for j in re.split(r"[\s,/;]+", reste) if j and j not in LIAISONS]
    if not jetons:
        return [], None

    explicites, familles = [], []
    for j in jetons:
        if j.startswith("§"):
            familles.append(j[1:])
            continue
        c = canon(j)
        if not re.search(r"[A-Z]", c) or not reconnu(c, connus):
            return [], None
        explicites.append(c)

    if explicites:
        return explicites, qualite
    # Une famille seule (« zones agricoles ») n'est admise que dans un titre.
    return (familles, qualite) if qualite >= 1 and (en_titre or qualite == 2) else ([], None)


def plus_longue_suite(pages_occ):
    """Début et longueur de la plus longue suite de pages quasi consécutives."""
    pages_occ = sorted(set(pages_occ))
    meilleur, longueur_max = pages_occ[0], 1
    debut, precedente, longueur = pages_occ[0], pages_occ[0], 1
    for p in pages_occ[1:]:
        if p - precedente <= 2:
            longueur += 1
        else:
            if longueur > longueur_max:
                meilleur, longueur_max = debut, longueur
            debut, longueur = p, 1
        precedente = p
    if longueur > longueur_max:
        meilleur, longueur_max = debut, longueur
    return meilleur, longueur_max


def choisir_debut(occurrences, pages_maj):
    """
    Un véritable intitulé de chapitre l'emporte sur toute autre mention.
    À défaut : en-tête répété, puis première mention suivie de contenu
    réglementaire, puis en-tête faible répété sur trois pages au moins.
    """
    chapitres = sorted({p for p, q in occurrences if q == 2})
    if chapitres:
        return chapitres[0]

    fortes = [p for p, q in occurrences if q == 1]
    faibles = [p for p, q in occurrences if q == 0]
    if fortes:
        debut, longueur = plus_longue_suite(fortes)
        if longueur >= 3:
            return debut
        for p in sorted(set(fortes)):
            if MARQUEURS.search(" ".join(pages_maj[p - 1:p + 1])):
                return p
        return min(fortes)
    if faibles:
        debut, longueur = plus_longue_suite(faibles)
        if longueur >= 3:
            return debut
    return None


def detecter(pages, zones):
    connus = [canon(z.libelle) for z in zones]
    pages_maj = [majuscules(t) for t in pages]
    occurrences = defaultdict(list)
    intitules = {}
    for i, texte in enumerate(pages):
        lignes = [" ".join(l.split()) for l in texte.split("\n")]
        if ressemble_sommaire(lignes):
            continue
        precedente = ""
        for l in lignes:
            if not l:
                continue
            candidats = [(l, 100)]
            # Intitulé coupé sur deux lignes : « CHAPITRE 3. DISPOSITIONS
            # APPLICABLES » puis « AUX ZONES A URBANISER ».
            if precedente and TITRE.match(majuscules(precedente)):
                candidats.append((precedente + " " + l, 160))
            precedente = l
            for texte_ligne, longueur_max in candidats:
                if len(texte_ligne) > longueur_max or re.search(r"[.·_]{3,}", texte_ligne):
                    continue
                libs, qualite = libelles_intitule(texte_ligne, connus)
                if not libs:
                    continue
                cle = tuple(sorted(set(libs)))
                occurrences[cle].append((i + 1, qualite))
                intitules.setdefault(cle, texte_ligne)

    chapitres = []
    for cle, occ in occurrences.items():
        debut = choisir_debut(occ, pages_maj)
        if debut is not None:
            chapitres.append({
                "page": debut,
                "libelles": list(cle),
                "intitule": intitules[cle],
                "force": "forte" if any(q >= 1 for _, q in occ) else "faible",
            })
    chapitres.sort(key=lambda c: c["page"])
    for k, ch in enumerate(chapitres):
        suivant = chapitres[k + 1]["page"] if k + 1 < len(chapitres) else len(pages) + 1
        ch["page_fin"] = max(ch["page"], suivant - 1)
    return chapitres


def rattacher(zone, chapitres):
    """
    Chapitre le plus spécifique dont le libellé préfixe celui de la zone,
    dans la même famille CNIG. Une correspondance directe l'emporte toujours
    sur le repli sans indice : sinon 2AU tomberait dans le chapitre 1AU.
    """
    cz = canon(zone.libelle)
    fam = FAMILLES_TYPE.get(zone.type_zone) or famille_libelle(cz)
    meilleur, score = None, -1
    for ch in chapitres:
        for lib in ch["libelles"]:
            if famille_libelle(lib) != fam:
                continue
            if cz.startswith(lib):
                s = 100 + len(lib)
            elif re.sub(r"^\d", "", cz).startswith(re.sub(r"^\d", "", lib)):
                s = len(lib)
            else:
                continue
            if s > score:
                meilleur, score = ch, s
    return meilleur


def lire_pages(regl):
    with pdfplumber.open(regl.fichier.path) as pdf:
        return [(p.extract_text() or "") for p in pdf.pages]


class Command(BaseCommand):
    help = "Détection des chapitres de zone dans le corps du règlement"

    def add_arguments(self, parser):
        parser.add_argument("--titre", help="Un seul fichier, affichage détaillé")
        parser.add_argument("--region", help="Validation contre nomfic sur une région")
        parser.add_argument("--limit", type=int)

    def handle(self, *args, **options):
        if options.get("titre"):
            self.detailler(options["titre"])
        elif options.get("region"):
            self.valider(options["region"], options.get("limit"))
        else:
            raise CommandError("Précisez --titre ou --region.")

    def detailler(self, titre):
        from apps.urbanism_docs.models import ReglementDocument

        regl = ReglementDocument.objects.filter(titre=titre).exclude(fichier="").first()
        if regl is None:
            raise CommandError("Règlement introuvable.")

        zones = list(regl.document.zones.order_by("libelle"))
        pages = lire_pages(regl)
        chapitres = detecter(pages, zones)
        self.stdout.write(f"{regl.titre} · {len(pages)} pages · {len(chapitres)} chapitres\n")
        for ch in chapitres:
            self.stdout.write(
                f"  p.{ch['page']:>3}–{ch['page_fin']:<3}  {','.join(ch['libelles']):12} {ch['intitule'][:60]}"
            )

        self.stdout.write("\nRattachement des zones :")
        sans = []
        for z in zones:
            ch = rattacher(z, chapitres)
            if ch is None:
                sans.append(z.libelle)
                continue
            gpu = f" · nomfic p.{z.page_reglement}" if z.page_reglement else ""
            self.stdout.write(
                f"  {z.libelle:8} ({z.type_zone:5}) → {','.join(ch['libelles']):10} "
                f"p.{ch['page']}–{ch['page_fin']}{gpu}"
            )
        if sans:
            self.stdout.write(self.style.WARNING(f"\nSans chapitre : {', '.join(sans)}"))

    def valider(self, region, limite):
        """
        Compare le début de chapitre détecté à la page nomfic du GPU, en
        ventilant la précision par type de détection : l'objectif est de
        n'afficher que les types assez fiables, pas d'atteindre un seuil global.
        """
        from apps.urbanism_docs.models import ReglementDocument

        FAMILLES = {"U", "AU", "A", "N"}

        def qualifier(zone, ch):
            cz = canon(zone.libelle)
            libs = ch["libelles"]
            if cz in libs:
                t = "exact"
            elif any(cz.startswith(l) and l not in FAMILLES for l in libs):
                t = "secteur"
            else:
                t = "famille"
            return f"{t}/{ch['force']}"

        docs = (
            ReglementDocument.objects.exclude(fichier="")
            .filter(est_numerise=False,
                    document__communes__code_region=region,
                    document__zones__page_reglement__isnull=False)
            .distinct()
        )

        stats = Counter()
        par_type = defaultdict(Counter)
        par_doc = []
        fichiers_vus, n = set(), 0

        for regl in docs.iterator():
            if regl.fichier.name in fichiers_vus:
                continue
            fichiers_vus.add(regl.fichier.name)
            n += 1
            if limite and n > limite:
                break

            try:
                pages = lire_pages(regl)
            except Exception:
                stats["pdf_illisible"] += 1
                continue
            toutes = list(regl.document.zones.all())
            chapitres = detecter(pages, toutes)

            zones = [
                z for z in toutes
                if z.page_reglement
                and (z.nom_fichier_reglement or "").split("#")[0] == regl.titre
            ]
            if not zones:
                continue

            pages_nomfic = {z.page_reglement for z in zones}
            familles = {FAMILLES_TYPE.get(z.type_zone) or famille_libelle(canon(z.libelle))
                        for z in zones}
            if len(pages_nomfic) == 1 and len(familles) > 1:
                stats["ecarte_nomfic_unique"] += len(zones)
                continue

            ok_doc = tot_doc = 0
            for z in zones:
                p = z.page_reglement
                texte = pages[p - 1] if 0 < p <= len(pages) else ""
                if len(re.findall(r"[.·_]{3,}\s*\d", texte)) >= 3:
                    stats["ecarte_sommaire"] += 1
                    continue
                tot_doc += 1
                ch = rattacher(z, chapitres)
                if ch is None:
                    stats["sans_chapitre"] += 1
                    continue
                juste = abs(ch["page"] - p) <= 1
                par_type[qualifier(z, ch)]["juste" if juste else "faux"] += 1
                stats["juste" if juste else "faux"] += 1
                ok_doc += juste
            if tot_doc:
                par_doc.append((ok_doc / tot_doc, tot_doc, regl.titre))

            if n % 20 == 0:
                self.stdout.write(f"  {n} documents…")

        trouves = stats["juste"] + stats["faux"]
        retenus = trouves + stats["sans_chapitre"]
        self.stdout.write(f"\n{n} documents · {retenus} zones avec une référence fiable")
        self.stdout.write(f"  écartés (nomfic unique)  : {stats['ecarte_nomfic_unique']}")
        self.stdout.write(f"  écartés (sommaire)       : {stats['ecarte_sommaire']}")
        if trouves:
            self.stdout.write(f"\nPrécision globale : {100 * stats['juste'] / trouves:.1f} %"
                              f" · couverture {100 * trouves / retenus:.1f} %")

        self.stdout.write("\nPrécision par type de détection :")
        for t, c in sorted(par_type.items(), key=lambda x: -(x[1]["juste"] + x[1]["faux"])):
            total = c["juste"] + c["faux"]
            self.stdout.write(f"  {t:18} {total:5} zones  précision {100 * c['juste'] / total:5.1f} %")

        if par_doc:
            bons = sum(1 for taux, _, _ in par_doc if taux >= 0.8)
            self.stdout.write(f"\nDocuments avec ≥ 80 % de zones justes : {bons} / {len(par_doc)}")
        self.stdout.write("\nDocuments les moins bons :")
        for taux, t, titre in sorted(par_doc)[:8]:
            self.stdout.write(f"  {100 * taux:5.0f} %  ({t:3} zones)  {titre}")