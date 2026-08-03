from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0002_expenseentry_weightentry'),
    ]

    operations = [
        migrations.AddField(
            model_name='nutritionentry',
            name='carbs_g',
            field=models.DecimalField(decimal_places=1, default=0, max_digits=7),
        ),
        migrations.AddField(
            model_name='nutritionentry',
            name='fibre_g',
            field=models.DecimalField(decimal_places=1, default=0, max_digits=7),
        ),
        migrations.AddField(
            model_name='nutritionentry',
            name='sugar_g',
            field=models.DecimalField(decimal_places=1, default=0, max_digits=7),
        ),
    ]
