# Generated manually for trending score feature

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('posts', '0022_alter_comment_body'),
    ]

    operations = [
        migrations.AddField(
            model_name='post',
            name='trending_score',
            field=models.FloatField(default=0.0, db_index=True, help_text='トレンドスコア（バッチ処理で更新）'),
        ),
        migrations.AddIndex(
            model_name='post',
            index=models.Index(fields=['-trending_score', '-created_at'], name='posts_post_trendi_idx'),
        ),
    ]

