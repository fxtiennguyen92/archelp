from django.contrib.gis.db import models


class Commune(models.Model):
    """
    Commune française. Clé naturelle = code INSEE (5 caractères).
    Source: IGN Admin Express / API Géo.
    """

    code_insee = models.CharField(
        max_length=5,
        unique=True,
        db_index=True,
        help_text="Code INSEE sur 5 caractères, ex: 67482 (Strasbourg)",
    )
    nom = models.CharField(max_length=200, db_index=True)
    code_postal = models.CharField(max_length=5, blank=True)

    code_departement = models.CharField(
        max_length=3,
        db_index=True,
        help_text="Ex: 67, 68, 2A",
    )
    code_region = models.CharField(
        max_length=2,
        db_index=True,
        help_text="Ex: 44 pour Grand Est",
    )

    population = models.IntegerField(null=True, blank=True)
    surface_ha = models.FloatField(null=True, blank=True)

    geom = models.MultiPolygonField(
        srid=4326,
        null=True,
        blank=True,
        spatial_index=True,
        help_text="Limites administratives, WGS84",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    synced_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Dernière synchronisation depuis la source officielle",
    )

    class Meta:
        verbose_name = "Commune"
        verbose_name_plural = "Communes"
        ordering = ["code_insee"]
        indexes = [
            models.Index(fields=["code_region", "code_departement"]),
        ]

    def __str__(self):
        return f"{self.nom} ({self.code_insee})"


class DocumentUrbanisme(models.Model):
    """
    Document d'urbanisme issu du GPU (PLU, PLUi, POS, CC, PSMV).
    Clé naturelle : (partition, idurba). La source contient des doublons
    résiduels ; le pipeline de dérivation ne retient qu'une ligne par couple.
    """

    class TypeDoc(models.TextChoices):
        PLU = "PLU", "Plan Local d'Urbanisme"
        PLUI = "PLUI", "Plan Local d'Urbanisme intercommunal"
        POS = "POS", "Plan d'Occupation des Sols"
        CC = "CC", "Carte Communale"
        PSMV = "PSMV", "Plan de Sauvegarde et de Mise en Valeur"
        RNU = "RNU", "Règlement National d'Urbanisme"

    partition = models.CharField(
        max_length=32,
        db_index=True,
        help_text="Identifiant GPU de partition, ex: DU_67482. Non unique.",
    )
    idurba = models.CharField(
        max_length=30,
        db_index=True,
        help_text="Identifiant CNIG du document, ex: 67482_PLU_20200115",
    )

    type_document = models.CharField(max_length=10, choices=TypeDoc.choices)

    communes = models.ManyToManyField(
        Commune,
        related_name="documents_urbanisme",
        help_text="Plusieurs communes si PLUi",
    )

    date_approbation = models.DateField(null=True, blank=True)
    date_fin_validite = models.DateField(null=True, blank=True)
    est_en_vigueur = models.BooleanField(default=True, db_index=True)

    nom_procedure = models.CharField(
        max_length=10,
        blank=True,
        help_text="Code CNIG de la procédure : R (révision), M1 (modification), MS1...",
    )

    nom_reglement = models.CharField(max_length=200, blank=True)
    url_reglement = models.URLField(max_length=500, blank=True)
    url_plan = models.URLField(max_length=500, blank=True)
    site_web = models.URLField(max_length=500, blank=True)

    a_doublon_source = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Vrai si la source contenait plusieurs lignes opposables "
        "pour cette partition. Réponse à vérifier auprès de la mairie.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Document d'urbanisme"
        verbose_name_plural = "Documents d'urbanisme"
        ordering = ["-date_approbation"]
        constraints = [
            models.UniqueConstraint(
                fields=["partition", "idurba"],
                name="unique_partition_idurba",
            )
        ]

    def __str__(self):
        return f"{self.type_document} {self.idurba}"

class Zone(models.Model):
    """
    Zone de zonage d'un document d'urbanisme (U, AU, A, N + sous-zones).
    Les polygones épars portant le même libellé sont fusionnés en une
    seule entrée : le règlement s'applique au libellé, pas à l'îlot.
    """

    class TypeZone(models.TextChoices):
        U = "U", "Zone urbaine"
        AU = "AU", "Zone à urbaniser"
        AUC = "AUc", "Zone à urbaniser constructible"
        AUS = "AUs", "Zone à urbaniser stricte"
        A = "A", "Zone agricole"
        N = "N", "Zone naturelle et forestière"
        CC_OUVERT = "CC01", "CC — ouvert à la construction"
        CC_ACTIVITES = "CC02", "CC — réservé aux activités"
        CC_FERME = "CC03", "CC — fermé à la construction"
        CC_NON_COUVERT = "CC99", "CC — non couvert par la carte"
        AUTRE = "AUTRE", "Autre / non normalisé"

    document = models.ForeignKey(
        DocumentUrbanisme,
        on_delete=models.CASCADE,
        related_name="zones",
    )

    libelle = models.CharField(
        max_length=50,
        db_index=True,
        help_text="Libellé exact du PLU, ex: UD1, IAUB, Nzh",
    )
    libelle_long = models.CharField(max_length=500, blank=True)

    type_zone = models.CharField(
        max_length=10,
        choices=TypeZone.choices,
        db_index=True,
        help_text="Type normalisé CNIG, champ typezone de l'API",
    )

    nom_fichier_reglement = models.CharField(
        max_length=300,
        blank=True,
        help_text="Champ nomfic du GPU, ex: 246700488_reglement_20260206.pdf#page=95",
    )
    page_reglement = models.IntegerField(
        null=True,
        blank=True,
        help_text="Page extraite de nomfic. Point d'entrée dans le PDF.",
    )

    nb_polygones = models.IntegerField(
        default=1,
        help_text="Nombre de polygones fusionnés sous ce libellé",
    )

    geom = models.MultiPolygonField(srid=4326, spatial_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Zone"
        verbose_name_plural = "Zones"
        ordering = ["document", "libelle"]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "libelle"],
                name="unique_zone_par_document",
            )
        ]
        indexes = [
            models.Index(fields=["type_zone", "libelle"]),
        ]

    def __str__(self):
        return f"{self.libelle} — {self.document.idurba}"

class Parcelle(models.Model):
    """
    Parcelle cadastrale. Source: API Carto module Cadastre (IGN).
    Récupérée à la demande (cache-aside), pas en bulk.
    """

    idu = models.CharField(
        max_length=14,
        unique=True,
        db_index=True,
        help_text="Identifiant unique: code_insee + prefixe(3) + section(2) + numero(4). Ex: 674820000AB0123",
    )

    commune = models.ForeignKey(
        Commune,
        on_delete=models.PROTECT,
        related_name="parcelles",
    )

    prefixe = models.CharField(
        max_length=3,
        default="000",
        help_text="Préfixe, non nul si commune fusionnée",
    )
    section = models.CharField(max_length=2, help_text="Ex: AB")
    numero = models.CharField(max_length=4, help_text="Ex: 0123")

    contenance_m2 = models.IntegerField(
        null=True,
        blank=True,
        help_text="Surface cadastrale déclarée, en m²",
    )

    geom = models.MultiPolygonField(
        srid=4326,
        spatial_index=True,
    )

    zones = models.ManyToManyField(
        Zone,
        through="ParcelleZone",
        related_name="parcelles",
        blank=True,
    )

    feuille = models.IntegerField(
        default=1,
        help_text="Numéro de feuille cadastrale",
    )
    code_arrondissement = models.CharField(
        max_length=3,
        default="000",
        help_text="Code arrondissement, non nul à Paris/Lyon/Marseille",
    )
    gid_ign = models.IntegerField(
        null=True,
        blank=True,
        help_text="Identifiant interne IGN, pour traçabilité",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Parcelle"
        verbose_name_plural = "Parcelles"
        ordering = ["idu"]
        indexes = [
            models.Index(fields=["commune", "section", "numero"]),
        ]

    def __str__(self):
        return self.idu


class ParcelleZone(models.Model):
    """
    Intersection parcelle × zone, avec la part de surface concernée.
    Une parcelle peut chevaucher plusieurs zones : c'est fréquent et
    c'est précisément ce que l'architecte doit savoir.
    """

    parcelle = models.ForeignKey(Parcelle, on_delete=models.CASCADE)
    zone = models.ForeignKey(Zone, on_delete=models.CASCADE)

    surface_intersection_m2 = models.FloatField(
        help_text="Surface de la parcelle dans cette zone, en m² (calculée en Lambert-93)",
    )
    part_pct = models.FloatField(
        help_text="Part de la parcelle dans cette zone, en %",
    )
    est_dominante = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Vrai pour la zone couvrant la plus grande part",
    )

    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Parcelle × Zone"
        verbose_name_plural = "Parcelles × Zones"
        ordering = ["-part_pct"]
        constraints = [
            models.UniqueConstraint(
                fields=["parcelle", "zone"],
                name="unique_parcelle_zone",
            )
        ]

    def __str__(self):
        return f"{self.parcelle.idu} ∩ {self.zone.libelle} ({self.part_pct:.1f}%)"

class Prescription(models.Model):
    """
    Prescription surfacique du GPU : servitudes graphiques, périmètres,
    emplacements réservés, hauteurs maximales.

    Ces couches portent des règles que le règlement écrit ne contient pas.
    L'article 10 UD du PLUi de l'Eurométropole renvoie explicitement au
    règlement graphique pour la hauteur : sans cette table, le paramètre
    le plus important pour un architecte reste introuvable.
    """

    # Nomenclature CNIG, codes observés sur le terrain.
    TYPES = {
        "01": "Espace boisé classé",
        "02": "Périmètre de protection ou de vigilance",
        "04": "Plan de déplacement urbain",
        "05": "Emplacement réservé",
        "07": "Élément de patrimoine ou paysage protégé",
        "15": "Périmètre particulier",
        "17": "Secteur de mixité sociale",
        "18": "Secteur d'OAP",
        "23": "Taille minimale des logements",
        "25": "Continuité écologique",
        "31": "Protection d'espèce",
        "39": "Hauteur maximale",
        "99": "Autre",
    }

    document = models.ForeignKey(
        DocumentUrbanisme,
        on_delete=models.CASCADE,
        related_name="prescriptions",
    )

    gid_ign = models.IntegerField(db_index=True)
    type_psc = models.CharField(max_length=2, db_index=True)
    stype_psc = models.CharField(max_length=2, blank=True)

    libelle = models.CharField(max_length=300, db_index=True)
    txt = models.CharField(
        max_length=200,
        blank=True,
        help_text="Valeur brute. Pour la hauteur : « 12mHT » (hors tout) "
        "ou « 10mET » (égout de toiture) — deux modes de mesure distincts.",
    )

    valeur_num = models.FloatField(
        null=True, blank=True,
        help_text="Valeur numérique extraite de txt, quand elle existe",
    )
    unite = models.CharField(
        max_length=10, blank=True,
        help_text="m, %, ou vide",
    )
    reference_mesure = models.CharField(
        max_length=10, blank=True,
        help_text="HT (hors tout) ou ET (égout de toiture) pour les hauteurs",
    )

    geom = models.MultiPolygonField(srid=4326, spatial_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Prescription"
        verbose_name_plural = "Prescriptions"
        ordering = ["type_psc", "libelle"]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "gid_ign"],
                name="unique_prescription_ign",
            )
        ]

    def __str__(self):
        v = f" — {self.txt}" if self.txt else ""
        return f"{self.libelle}{v}"

    @property
    def type_libelle(self):
        return self.TYPES.get(self.type_psc, "Inconnu")


class ParcellePrescription(models.Model):
    """
    Intersection parcelle × prescription. Comme pour le zonage, une
    prescription peut ne couvrir qu'une partie du terrain : un espace
    planté à conserver sur 10 % de la parcelle ne bloque pas le projet,
    sur 80 % il le condamne.
    """

    parcelle = models.ForeignKey(Parcelle, on_delete=models.CASCADE)
    prescription = models.ForeignKey(Prescription, on_delete=models.CASCADE)

    surface_intersection_m2 = models.FloatField()
    part_pct = models.FloatField()

    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Parcelle × Prescription"
        verbose_name_plural = "Parcelles × Prescriptions"
        ordering = ["-part_pct"]
        constraints = [
            models.UniqueConstraint(
                fields=["parcelle", "prescription"],
                name="unique_parcelle_prescription",
            )
        ]

    def __str__(self):
        return f"{self.parcelle.idu} ∩ {self.prescription.libelle} ({self.part_pct:.0f}%)"