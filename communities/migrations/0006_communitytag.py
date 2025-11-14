from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('communities', '0005_membership_is_favorite'),
    ]

    operations = [
        migrations.CreateModel(
            name='CommunityTag',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=32)),
                ('color', models.CharField(default='#1e3a8a', max_length=16)),
                ('permission_scope', models.CharField(choices=[('all', 'All Participants'), ('moderator', 'Moderators'), ('owner', 'Owner')], default='all', max_length=16)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('community', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tags', to='communities.community')),
            ],
            options={
                'indexes': [
                    models.Index(fields=['community', 'name'], name='communities_community_id_name_idx'),
                ],
            },
        ),
        migrations.AlterUniqueTogether(
            name='communitytag',
            unique_together={('community', 'name')},
        ),
    ]


