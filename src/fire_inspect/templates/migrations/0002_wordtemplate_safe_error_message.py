from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("templates", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="wordtemplate",
            name="safe_error_message",
            field=models.CharField(blank=True, max_length=500),
        ),
    ]
