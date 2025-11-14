from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('communities', '0002_alter_community_join_policy'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.CreateModel(
            name='CommunityBlock',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reason', models.CharField(blank=True, max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('community', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='blocks', to='communities.community')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='community_blocks', to='auth.user')),
            ],
        ),
        migrations.AddIndex(
            model_name='communityblock',
            index=models.Index(fields=['community', 'user'], name='communities_community_user_idx'),
        ),
        migrations.AlterUniqueTogether(
            name='communityblock',
            unique_together={('community', 'user')},
        ),
    ]


