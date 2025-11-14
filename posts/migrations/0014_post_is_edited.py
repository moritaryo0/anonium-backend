from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('posts', '0013_post_post_type_poll_polloption_pollvote_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='post',
            name='is_edited',
            field=models.BooleanField(default=False),
        ),
    ]


