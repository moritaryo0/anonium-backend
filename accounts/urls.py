from django.urls import path

from .views import (
    LoginView,
    MeView,
    SignupView,
    UserDetailView,
    UploadUserIconView,
    GuestIssueView,
    MuteListView,
    MuteCreateView,
    MuteDeleteView,
    NotificationListView,
    NotificationUnreadCountView,
    NotificationMarkAllReadView,
    EmailVerificationView,
    ResendVerificationEmailView,
)
from .views_oauth import GoogleOAuthView, GoogleOAuthAuthorizeView, GoogleOAuthCallbackView, AppleOAuthView, XOAuthView, XOAuthAuthorizeView, XOAuthCallbackView
from .views_refresh import TokenRefreshView, LogoutView


urlpatterns = [
    path('signup/', SignupView.as_view(), name='signup'),
    path('login/', LoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('me/', MeView.as_view(), name='me'),
    path('me/icon/', UploadUserIconView.as_view(), name='me_icon'),
    path('guest/issue/', GuestIssueView.as_view(), name='guest_issue'),
    # OAuth (Mock)
    path('oauth/google/', GoogleOAuthView.as_view(), name='oauth_google'),
    path('oauth/apple/', AppleOAuthView.as_view(), name='oauth_apple'),
    path('oauth/x/', XOAuthView.as_view(), name='oauth_x'),
    # Google OAuth 2.0
    path('oauth/google/authorize/', GoogleOAuthAuthorizeView.as_view(), name='oauth_google_authorize'),
    path('oauth/google/callback/', GoogleOAuthCallbackView.as_view(), name='oauth_google_callback'),
    # X (Twitter) OAuth 2.0
    path('oauth/x/authorize/', XOAuthAuthorizeView.as_view(), name='oauth_x_authorize'),
    path('oauth/x/callback/', XOAuthCallbackView.as_view(), name='oauth_x_callback'),
    # mute
    path('mutes/', MuteListView.as_view(), name='mute_list'),
    path('mute/', MuteCreateView.as_view(), name='mute_create'),
    path('mute/<str:username>/', MuteDeleteView.as_view(), name='mute_delete'),
    path('users/<str:username>/', UserDetailView.as_view(), name='user_detail'),
    # notifications
    path('notifications/', NotificationListView.as_view(), name='notification_list'),
    path('notifications/unread-count/', NotificationUnreadCountView.as_view(), name='notification_unread_count'),
    path('notifications/mark-all-read/', NotificationMarkAllReadView.as_view(), name='notification_mark_all_read'),
    # email verification
    path('verify-email/', EmailVerificationView.as_view(), name='verify_email'),
    path('resend-verification/', ResendVerificationEmailView.as_view(), name='resend_verification'),
]


