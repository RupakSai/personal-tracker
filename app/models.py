from django.db import models


class NutritionEntry(models.Model):
    SOURCE_MANUAL = 'manual'
    SOURCE_AI = 'ai'

    SOURCE_CHOICES = [
        (SOURCE_MANUAL, 'Manual'),
        (SOURCE_AI, 'AI estimate'),
    ]

    date = models.DateField(db_index=True)
    source = models.CharField(max_length=12, choices=SOURCE_CHOICES)
    calories = models.PositiveIntegerField()
    protein_g = models.DecimalField(max_digits=7, decimal_places=1)
    fat_g = models.DecimalField(max_digits=7, decimal_places=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.date} - {self.get_source_display()} ({self.calories} kcal)'
