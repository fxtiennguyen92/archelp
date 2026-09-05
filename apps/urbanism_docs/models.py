import hashlib
from django.db import models

from apps.parcels.models import DocumentUrbanisme, Zone


class ReglementDocument(models.Model):
    """
    Un fichier PDF de règlement, tel que publié sur le GPU ou l'open data
    communal. Un document d'urbanisme produit plusieurs PDF (règlement écrit,
    annexes, OAP...). Le fichier est stocké tel quel, sans retraitement.
    """

    class TypePiece(models.TextChoices):
        REGLEMENT = "REGLEMENT", "Règlement écrit"
        OAP = "OAP", "Orientations d'Aménagement et de Programmation"
        RAPPORT = "RAPPORT", "Rapport de présentation"
        ANNEXE = "ANNEXE", "Annexe"
        PADD = "PADD", "Projet d'Aménagement et de Développement Durables"
        AUTRE = "AUTRE", "Autre"

    class Statut(models.TextChoices):
        A_TELECHARGER = "A_TELECHARGER", "À télécharger"
        TELECHARGE = "TELECHARGE", "Téléchargé"
        TEXTE_EXTRAIT = "TEXTE_EXTRAIT", "Texte brut extrait"
        STRUCTURE_N1 = "STRUCTURE_N1", "Structuré SRU niveau 1"
        REGLES_N2 = "REGLES_N2", "Règles extraites SRU niveau 2"
        ERREUR = "ERREUR", "Erreur"

    document = models.ForeignKey(
        DocumentUrbanisme,
        on_delete=models.CASCADE,
        related_name="reglements",
    )

    titre = models.CharField(max_length=500)
    type_piece = models.CharField(
        max_length=20,
        choices=TypePiece.choices,
        default=TypePiece.REGLEMENT,
        db_index=True,
    )

    url_source = models.URLField(
        max_length=1000,
        help_text="URL d'origine du PDF",
    )
    chemin_interne = models.CharField(
        max_length=500,
        blank=True,
        help_text="Chemin du fichier dans l'archive ZIP du GPU",
    )
    fichier = models.FileField(
        upload_to="reglements/%Y/%m/",
        blank=True,
        help_text="Copie locale du PDF",
    )

    sha256 = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        help_text="Empreinte du fichier, pour détecter les mises à jour",
    )
    taille_octets = models.BigIntegerField(null=True, blank=True)
    nb_pages = models.IntegerField(null=True, blank=True)

    date_document = models.DateField(
        null=True,
        blank=True,
        help_text="Date d'approbation ou de mise à jour du règlement. "
        "À citer obligatoirement dans toute réponse à l'utilisateur.",
    )

    statut = models.CharField(
        max_length=20,
        choices=Statut.choices,
        default=Statut.A_TELECHARGER,
        db_index=True,
    )
    message_erreur = models.TextField(blank=True)

    est_numerise = models.BooleanField(
        null=True,
        blank=True,
        help_text="Vrai si PDF scanné sans couche texte (OCR nécessaire)",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    telecharge_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Règlement (PDF)"
        verbose_name_plural = "Règlements (PDF)"
        ordering = ["document", "type_piece", "titre"]
        indexes = [
            models.Index(fields=["document", "statut"]),
        ]

    def __str__(self):
        return f"{self.titre} ({self.document.partition})"

    def calculer_sha256(self):
        """Recalcule l'empreinte à partir du fichier local."""
        if not self.fichier:
            return ""
        h = hashlib.sha256()
        with self.fichier.open("rb") as f:
            for bloc in iter(lambda: f.read(65536), b""):
                h.update(bloc)
        return h.hexdigest()

class ReglementSection(models.Model):
    """
    Fragment de règlement, structuré selon le standard CNIG SRU niveau 1 :
    arborescence de titres, sous-titres, paragraphes et alinéas.

    La hiérarchie n'est pas cosmétique : un règlement renvoie sans cesse à
    d'autres articles. Sans l'arbre, impossible de fournir le contexte
    nécessaire à une réponse correcte.
    """

    class TypeFragment(models.TextChoices):
        TITRE = "TITRE", "Titre"
        SOUS_TITRE = "SOUS_TITRE", "Sous-titre"
        CHAPITRE = "CHAPITRE", "Chapitre"
        SECTION = "SECTION", "Section"
        ARTICLE = "ARTICLE", "Article"
        PARAGRAPHE = "PARAGRAPHE", "Paragraphe"
        ALINEA = "ALINEA", "Alinéa"
        ILLUSTRATION = "ILLUSTRATION", "Schéma ou illustration"

    reglement = models.ForeignKey(
        ReglementDocument, on_delete=models.CASCADE, related_name="sections"
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="enfants",
    )

    type_fragment = models.CharField(
        max_length=20, choices=TypeFragment.choices, db_index=True
    )
    profondeur = models.IntegerField(
        default=0, help_text="Niveau dans l'arbre, 0 pour la racine"
    )
    ordre = models.IntegerField(help_text="Rang parmi les frères")
    chemin = models.CharField(
        max_length=200,
        blank=True,
        db_index=True,
        help_text="Chemin matérialisé, ex: 0002.0005.0001 — pour trier "
        "et retrouver une sous-arborescence en une requête",
    )

    zones = models.ManyToManyField(
        Zone,
        blank=True,
        related_name="sections",
        help_text="Zones concernées. Un même chapitre couvre souvent "
        "plusieurs zones (A1 à A5 partagent la page 177).",
    )

    numero = models.CharField(
        max_length=50, blank=True, db_index=True,
        help_text="Ex: UB 10, ou UB-4.2 pour un PLU post-2015"
    )
    titre = models.CharField(max_length=500, blank=True)
    texte = models.TextField(blank=True)

    page_debut = models.IntegerField(null=True, blank=True)
    page_fin = models.IntegerField(null=True, blank=True)

    renvois = models.ManyToManyField(
        "self",
        blank=True,
        symmetrical=False,
        related_name="cite_par",
        help_text="Articles cités par ce fragment",
    )

    nb_caracteres = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Fragment de règlement (SRU niveau 1)"
        verbose_name_plural = "Fragments de règlement (SRU niveau 1)"
        ordering = ["reglement", "chemin"]
        indexes = [
            models.Index(fields=["reglement", "chemin"]),
            models.Index(fields=["reglement", "type_fragment"]),
        ]

    def __str__(self):
        return f"{self.numero or self.type_fragment} — {self.titre[:60]}"

    def save(self, *args, **kwargs):
        self.nb_caracteres = len(self.texte)
        super().save(*args, **kwargs)