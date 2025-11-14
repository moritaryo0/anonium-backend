# Generated manually

from django.conf import settings
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('communities', '0013_community_clip_post'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('user_messages', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='GroupChatMessage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('body', models.TextField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('community', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='group_chat_messages', to='communities.community')),
                ('sender', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='group_chat_messages', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='groupchatmessage',
            index=models.Index(fields=['community', '-created_at'], name='user_messages_communi_g_idx'),
        ),
        migrations.AddIndex(
            model_name='groupchatmessage',
            index=models.Index(fields=['sender', '-created_at'], name='user_messages_sender_g_idx'),
        ),
    ]

