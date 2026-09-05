from django.contrib import admin

from .models import ReglementDocument, ReglementSection


class SectionInline(admin.TabularInline):
    model = ReglementSection
    extra = 0
    fields = ("chemin", "type_fragment", "numero", "titre", "page_debut")
    readonly_fields = ("chemin",)
    ordering = ("chemin",)
    show_change_link = True
    max_num = 50


@admin.register(ReglementDocument)
class ReglementDocumentAdmin(admin.ModelAdmin):
    list_display = ("titre", "document", "type_piece", "statut",
                    "nb_pages", "nb_sections", "date_document")
    list_filter = ("statut", "type_piece", "est_numerise")
    search_fields = ("titre", "document__idurba", "document__partition")
    autocomplete_fields = ("document",)
    readonly_fields = ("sha256", "taille_octets", "created_at",
                       "updated_at", "telecharge_at")
    inlines = [SectionInline]

    @admin.display(description="Fragments")
    def nb_sections(self, obj):
        return obj.sections.count()


@admin.register(ReglementSection)
class ReglementSectionAdmin(admin.ModelAdmin):
    list_display = ("chemin", "type_fragment", "numero", "titre",
                    "page_debut", "nb_caracteres")
    list_filter = ("type_fragment", "reglement__document__type_document")
    search_fields = ("numero", "titre", "texte")
    autocomplete_fields = ("reglement", "parent")
    filter_horizontal = ("zones", "renvois")
    readonly_fields = ("nb_caracteres", "created_at")
    ordering = ("reglement", "chemin")