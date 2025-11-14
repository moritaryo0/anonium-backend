# Generated manually to ensure UserProfile exists for all users

from django.db import migrations


def ensure_userprofile_for_all_users(apps, schema_editor):
    """既存のUserProfileがないユーザーに対してUserProfileを作成"""
    User = apps.get_model('auth', 'User')
    UserProfile = apps.get_model('accounts', 'UserProfile')
    
    # UserProfileがないユーザーを取得
    users_without_profile = User.objects.filter(profile__isnull=True)
    
    # UserProfileを作成
    profiles_to_create = []
    for user in users_without_profile:
        profiles_to_create.append(
            UserProfile(user=user)
        )
    
    if profiles_to_create:
        UserProfile.objects.bulk_create(profiles_to_create)
        print(f"Created {len(profiles_to_create)} UserProfile(s) for existing users")


def reverse_ensure_userprofile(apps, schema_editor):
    """ロールバック時は何もしない（UserProfileは削除しない）"""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0010_userprofile_last_login_ip_and_more'),
    ]

    operations = [
        migrations.RunPython(
            ensure_userprofile_for_all_users,
            reverse_ensure_userprofile,
        ),
    ]

