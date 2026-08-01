from django.contrib import admin

from .models import NutritionEntry


@admin.register(NutritionEntry)
class NutritionEntryAdmin(admin.ModelAdmin):
    list_display = ('date', 'source', 'calories', 'protein_g', 'fat_g', 'created_at')
    list_filter = ('source', 'date')
    search_fields = ('date',)
