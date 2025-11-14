from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts'

    def ready(self):
        """アプリケーション起動時にシグナルを登録"""
        import accounts.signals  # noqa
