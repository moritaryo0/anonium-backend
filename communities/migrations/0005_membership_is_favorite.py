from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('communities', '0004_change_rules_to_json'),
    ]

    operations = [
        migrations.AddField(
            model_name='communitymembership',
            name='is_favorite',
            field=models.BooleanField(default=False),
        ),
    ]


