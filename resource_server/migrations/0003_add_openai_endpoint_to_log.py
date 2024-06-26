from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('resource_server', '0002_log_alter_endpoint_cluster_and_more'),  # replace with the name of your last migration
    ]

    operations = [
        migrations.AddField(
            model_name='log',
            name='openai_endpoint',
            field=models.CharField(default='Empty', max_length=100),
        ),
    ]