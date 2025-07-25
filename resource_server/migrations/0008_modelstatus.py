# Generated by Django 5.1.4 on 2025-07-16 22:17

import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("resource_server", "0007_federatedendpoint"),
    ]

    operations = [
        migrations.CreateModel(
            name="ModelStatus",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("cluster", models.CharField(max_length=128, unique=True)),
                ("result", models.TextField(blank=True, default="")),
                ("error", models.TextField(blank=True, default="")),
                ("timestamp", models.DateTimeField(default=django.utils.timezone.now)),
            ],
        ),
    ]
