from django.urls import path

from .views import CommunityPostListCreateView, PostListView, TrendingPostListView, PostVoteView, PostFollowView, CommentListCreateView, PostDetailView, CommentVoteView, OGPPreviewView, UserCommentedPostsView, MeCommentedPostsView, MeFollowedPostsView, CommentImageUploadView, PostBodyImageUploadView, PostReportView, CommentDetailView, CommentReportView, CommunityCommentsPurgeView, CommentVideoUploadView, PostBodyVideoUploadView, MeCommunitiesPostsView, PollVoteView, CommentDescendantsListView


urlpatterns = [
    path('communities/<int:id>/posts/', CommunityPostListCreateView.as_view(), name='community_posts'),
    path('communities/<int:id>/comments/purge/', CommunityCommentsPurgeView.as_view(), name='community_comments_purge'),
    path('posts/', PostListView.as_view(), name='posts_all'),
    path('posts/trending/', TrendingPostListView.as_view(), name='posts_trending'),
    path('posts/me/communities/', MeCommunitiesPostsView.as_view(), name='me_communities_posts'),
    path('posts/<int:pk>/', PostDetailView.as_view(), name='post_detail'),
    path('posts/<int:pk>/report/', PostReportView.as_view(), name='post_report'),
    path('posts/<int:pk>/vote/', PostVoteView.as_view(), name='post_vote'),
    path('posts/<int:pk>/follow/', PostFollowView.as_view(), name='post_follow'),
    path('posts/<int:pk>/comments/', CommentListCreateView.as_view(), name='post_comments'),
    path('comments/<int:pk>/', CommentDetailView.as_view(), name='comment_detail_delete'),
    path('comments/<int:pk>/report/', CommentReportView.as_view(), name='comment_report'),
    path('posts/<int:pk>/comments/image/', CommentImageUploadView.as_view(), name='post_comment_image_upload'),
    path('posts/<int:pk>/comments/video/', CommentVideoUploadView.as_view(), name='post_comment_video_upload'),
    path('posts/images/', PostBodyImageUploadView.as_view(), name='post_body_image_upload'),
    path('posts/videos/', PostBodyVideoUploadView.as_view(), name='post_body_video_upload'),
    path('comments/<int:pk>/vote/', CommentVoteView.as_view(), name='comment_vote'),
    path('comments/<int:pk>/children/', CommentDescendantsListView.as_view(), name='comment_children'),
    path('polls/<int:pk>/vote/', PollVoteView.as_view(), name='poll_vote'),
    path('ogp/preview/', OGPPreviewView.as_view(), name='ogp_preview'),
    path('users/me/commented-posts/', MeCommentedPostsView.as_view(), name='me_commented_posts'),
    path('users/me/followed-posts/', MeFollowedPostsView.as_view(), name='me_followed_posts'),
    path('users/<str:username>/commented-posts/', UserCommentedPostsView.as_view(), name='user_commented_posts'),
]


