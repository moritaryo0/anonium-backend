from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('posts', '0004_comment_score_commentvote'),
    ]

    operations = [
        migrations.CreateModel(
            name='OGPCache',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('url', models.CharField(max_length=512, unique=True)),
                ('canonical_url', models.CharField(blank=True, default='', max_length=512)),
                ('title', models.CharField(blank=True, default='', max_length=300)),
                ('description', models.TextField(blank=True, default='')),
                ('image', models.CharField(blank=True, default='', max_length=512)),
                ('site_name', models.CharField(blank=True, default='', max_length=120)),
                ('fetched_at', models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.AddIndex(
            model_name='ogpcache',
            index=models.Index(fields=['url'], name='posts_ogp_url_idx'),
        ),
    ]


