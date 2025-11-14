from django.urls import path

from .views import CommunityDetailView, CommunityListCreateView, JoinCommunityView, LeaveCommunityView, UploadCommunityIconView, UploadCommunityBannerView, CommunityMembersView, CommunityModeratorsView, RemoveMemberView, BlockMemberView, UnblockMemberView, PromoteModeratorView, DemoteModeratorView, CommunityBlocksView, PendingRequestsView, ApproveRequestView, RejectRequestView, MyCommunitiesView, FavoriteCommunityView, FavoriteCommunitiesView, PromoteAdminModeratorView, DemoteAdminModeratorView, CommunityMuteListView, CommunityMuteCreateView, CommunityMuteDeleteView, CommunityClipPostView, CommunityStatusView, CommunityStatusListView, DeleteCommunityView


urlpatterns = [
    path('', CommunityListCreateView.as_view(), name='community_list_create'),
    path('me/', MyCommunitiesView.as_view(), name='my_communities'),
    path('favorites/', FavoriteCommunitiesView.as_view(), name='community_favorites'),
    path('mutes/', CommunityMuteListView.as_view(), name='community_mute_list'),
    path('<int:id>/', CommunityDetailView.as_view(), name='community_detail'),
    path('<int:id>/delete/', DeleteCommunityView.as_view(), name='community_delete'),
    path('<int:id>/favorite/', FavoriteCommunityView.as_view(), name='community_favorite'),
    path('<int:id>/mute/', CommunityMuteCreateView.as_view(), name='community_mute_create'),
    path('<int:id>/unmute/', CommunityMuteDeleteView.as_view(), name='community_mute_delete'),
    path('<int:id>/members/', CommunityMembersView.as_view(), name='community_members'),
    path('<int:id>/moderators/', CommunityModeratorsView.as_view(), name='community_moderators'),
    path('<int:id>/blocks/', CommunityBlocksView.as_view(), name='community_blocks'),
    path('<int:id>/join/', JoinCommunityView.as_view(), name='community_join'),
    path('<int:id>/leave/', LeaveCommunityView.as_view(), name='community_leave'),
    path('<int:id>/icon/', UploadCommunityIconView.as_view(), name='community_icon_upload'),
    path('<int:id>/banner/', UploadCommunityBannerView.as_view(), name='community_banner_upload'),
    # owner management
    path('<int:id>/members/<int:user_id>/remove/', RemoveMemberView.as_view(), name='community_member_remove'),
    path('<int:id>/members/<int:user_id>/block/', BlockMemberView.as_view(), name='community_member_block'),
    path('<int:id>/members/<int:user_id>/unblock/', UnblockMemberView.as_view(), name='community_member_unblock'),
    path('<int:id>/members/<int:user_id>/promote/', PromoteModeratorView.as_view(), name='community_member_promote'),
    path('<int:id>/members/<int:user_id>/demote/', DemoteModeratorView.as_view(), name='community_member_demote'),
    path('<int:id>/members/<int:user_id>/promote_admin/', PromoteAdminModeratorView.as_view(), name='community_member_promote_admin'),
    path('<int:id>/members/<int:user_id>/demote_admin/', DemoteAdminModeratorView.as_view(), name='community_member_demote_admin'),
    # pending requests management
    path('<int:id>/requests/', PendingRequestsView.as_view(), name='community_pending_requests'),
    path('<int:id>/requests/<int:user_id>/approve/', ApproveRequestView.as_view(), name='community_approve_request'),
    path('<int:id>/requests/<int:user_id>/reject/', RejectRequestView.as_view(), name='community_reject_request'),
    # clip post
    path('<int:id>/posts/<int:post_id>/clip/', CommunityClipPostView.as_view(), name='community_clip_post'),
    # status
    path('<int:id>/status/', CommunityStatusView.as_view(), name='community_status'),
    path('status/', CommunityStatusListView.as_view(), name='community_status_list'),
]


