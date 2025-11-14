# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('communities', '0014_community_karma'),
    ]

    operations = [
        migrations.AddField(
            model_name='community',
            name='is_deleted',
            field=models.BooleanField(default=False, help_text='コミュニティが削除されたかどうか'),
        ),
        migrations.AddIndex(
            model_name='community',
            index=models.Index(fields=['is_deleted'], name='communities_is_dele_idx'),
        ),
    ]

