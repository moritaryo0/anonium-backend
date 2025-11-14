from django.db import migrations, models


def populate_comment_community(apps, schema_editor):
    Comment = apps.get_model('posts', 'Comment')
    # Use iterator() for large tables
    for c in Comment.objects.select_related('post__community').all().iterator():
        if getattr(c, 'community_id', None):
            continue
        post = getattr(c, 'post', None)
        community_id = getattr(post, 'community_id', None) if post else None
        if community_id:
            Comment.objects.filter(pk=c.pk).update(community_id=community_id)


def noop_reverse(apps, schema_editor):
    # no-op
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('posts', '0005_ogpcache'),
        ('communities', '0002_alter_community_join_policy'),
    ]

    operations = [
        migrations.AddField(
            model_name='comment',
            name='community',
            field=models.ForeignKey(null=True, on_delete=models.deletion.CASCADE, related_name='comments', to='communities.community'),
        ),
        migrations.RunPython(populate_comment_community, noop_reverse),
        migrations.AlterField(
            model_name='comment',
            name='community',
            field=models.ForeignKey(null=False, on_delete=models.deletion.CASCADE, related_name='comments', to='communities.community'),
        ),
        migrations.AddIndex(
            model_name='comment',
            index=models.Index(fields=['community', 'parent', 'created_at'], name='posts_comm_communi_0a92b6_idx'),
        ),
    ]


