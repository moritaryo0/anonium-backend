from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('communities', '0008_remove_communitytag_permission_scope_and_more'),
        ('posts', '0010_post_tags'),
    ]

    operations = [
        migrations.AddField(
            model_name='post',
            name='tag',
            field=models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name='posts', to='communities.communitytag'),
        ),
    ]


