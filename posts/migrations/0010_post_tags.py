from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('communities', '0008_remove_communitytag_permission_scope_and_more'),
        ('posts', '0009_rename_posts_comm_communi_0a92b6_idx_posts_comme_communi_f9556a_idx'),
    ]

    operations = [
        migrations.AddField(
            model_name='post',
            name='tags',
            field=models.ManyToManyField(blank=True, related_name='posts', to='communities.communitytag'),
        ),
    ]


