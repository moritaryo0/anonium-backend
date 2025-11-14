from django.db import IntegrityError, transaction
from django.db.models import F, Value, OuterRef, Subquery, Case, When, IntegerField
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView
from django.conf import settings
from django.core import signing
from django.contrib.auth import get_user_model
from PIL import Image
import os, time
import logging

from .models import Community, CommunityMembership
from .serializers import CommunityCreateSerializer, CommunitySerializer, CommunityParticipantSerializer, CommunityBlockedUserSerializer
from app.utils import delete_media_file_by_url
from posts.models import Post
from accounts.utils import get_or_create_guest_user, get_guest_token_from_request

User = get_user_model()
logger = logging.getLogger(__name__)


def _resolve_user(request):
    """認証ユーザーまたはゲストユーザーを取得"""
    if request.user and request.user.is_authenticated:
        return request.user
    # ゲストユーザー（未登録の場合は None を返す）
    return get_or_create_guest_user(request, create_if_not_exists=False)




def _get_community_user_status(community: Community, user: User | None) -> dict:
    """コミュニティに対するユーザーの状態を取得"""
    if not user:
        return {
            'is_member': False,
            'membership_status': None,
            'membership_role': None,
            'is_admin': False,
            'is_blocked': False,
            'is_favorite': False,
        }
    
    # メンバーシップを取得（APPROVED のみ）
    membership = CommunityMembership.objects.filter(
        community=community,
        user=user,
        status=CommunityMembership.Status.APPROVED,
    ).first()
    
    # すべてのメンバーシップ状態を取得（PENDING も含む）
    all_membership = CommunityMembership.objects.filter(
        community=community,
        user=user,
    ).first()
    
    is_member = membership is not None
    membership_status = all_membership.status if all_membership else None
    membership_role = membership.role if membership else None
    is_admin = membership_role in (CommunityMembership.Role.OWNER, CommunityMembership.Role.ADMIN_MODERATOR, CommunityMembership.Role.MODERATOR) if membership_role else False
    
    # ブロック状態をチェック
    from .models import CommunityBlock
    is_blocked = CommunityBlock.objects.filter(community=community, user=user).exists()
    
    # お気に入り状態をチェック（メンバーシップが存在し、is_favorite属性がある場合のみ）
    is_favorite = False
    if membership and hasattr(membership, 'is_favorite'):
        is_favorite = membership.is_favorite
    
    return {
        'is_member': is_member,
        'membership_status': membership_status,
        'membership_role': membership_role,
        'is_admin': is_admin,
        'is_blocked': is_blocked,
        'is_favorite': is_favorite,
    }


class CommunityListCreateView(generics.ListCreateAPIView):
    queryset = Community.objects.filter(is_deleted=False).select_related('creator', 'clip_post').order_by('-created_at')
    
    def get_queryset(self):
        """クエリセットを取得（エラーハンドリング付き）"""
        try:
            return Community.objects.filter(is_deleted=False).select_related('creator', 'clip_post').order_by('-created_at')
        except Exception as e:
            logger.error(f"Error getting queryset: {e}", exc_info=True)
            # エラーが発生した場合は、より単純なクエリを試す
            try:
                return Community.objects.filter(is_deleted=False).order_by('-created_at')
            except Exception as e2:
                logger.error(f"Error with simple queryset: {e2}", exc_info=True)
                raise

    def get_permissions(self):
        if self.request.method == 'POST':
            # 作成は認証必須
            return [permissions.IsAuthenticated()]
        # 取得は全員許可
        return [permissions.AllowAny()]

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return CommunityCreateSerializer
        return CommunitySerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        # ユーザーを解決してコンテキストに追加（シリアライザーで再利用）
        try:
            if self.request.user and self.request.user.is_authenticated:
                context['resolved_user'] = self.request.user
            else:
                # ゲストユーザーの解決を試みる（エラーが発生しても続行）
                try:
                    context['resolved_user'] = get_or_create_guest_user(self.request, create_if_not_exists=False)
                except Exception as e:
                    logger.error(f"Error resolving guest user in view: {e}", exc_info=True)
                    context['resolved_user'] = None
        except Exception as e:
            logger.error(f"Error in get_serializer_context: {e}", exc_info=True)
            context['resolved_user'] = None
        return context

    def list(self, request, *args, **kwargs):
        """リスト取得をオーバーライドしてエラーハンドリングを強化"""
        try:
            return super().list(request, *args, **kwargs)
        except Exception as e:
            logger.error(f"Error in CommunityListCreateView.list: {e}", exc_info=True)
            # エラーを再発生させて、DRFの標準的なエラーハンドリングに任せる
            raise

    def create(self, request, *args, **kwargs):
        """コミュニティ作成をオーバーライドして、作成後にCommunitySerializerでレスポンスを返す"""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        
        # 作成後のコミュニティをCommunitySerializerでシリアライズ
        community = serializer.instance
        response_serializer = CommunitySerializer(community, context=self.get_serializer_context())
        
        # キャッシュ削除: コミュニティ一覧
        from app.utils import invalidate_cache
        invalidate_cache(pattern='/api/communities/*')
        invalidate_cache(pattern='/api/accounts/*/communities/*')  # ユーザーの参加コミュニティ一覧
        
        # レスポンスシリアライザーからヘッダーを生成
        headers = self.get_success_headers(response_serializer.data)
        
        from rest_framework import status
        return Response(response_serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def perform_create(self, serializer):
        """コミュニティ作成処理"""
        serializer.save()


class CommunityDetailView(generics.RetrieveUpdateAPIView):
    queryset = Community.objects.filter(is_deleted=False)
    lookup_field = 'id'
    serializer_class = CommunitySerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        # ユーザーを解決してコンテキストに追加（シリアライザーで再利用）
        try:
            if self.request.user and self.request.user.is_authenticated:
                context['resolved_user'] = self.request.user
            else:
                # ゲストユーザーの解決を試みる（エラーが発生しても続行）
                try:
                    context['resolved_user'] = get_or_create_guest_user(self.request, create_if_not_exists=False)
                except Exception as e:
                    logger.error(f"Error resolving guest user in detail view: {e}", exc_info=True)
                    context['resolved_user'] = None
        except Exception as e:
            logger.error(f"Error in get_serializer_context (detail view): {e}", exc_info=True)
            context['resolved_user'] = None
        return context

    def get_permissions(self):
        if self.request.method in ('GET',):
            # コミュニティ詳細の閲覧は常に許可（参加ポリシーは参加時にのみ適用）
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated()]

    def perform_update(self, serializer):
        community: Community = self.get_object()
        from .models import CommunityMembership as CM
        membership = CM.objects.filter(community=community, user=self.request.user, status=CM.Status.APPROVED).first()
        if not membership or membership.role not in (CM.Role.OWNER, CM.Role.ADMIN_MODERATOR):
            raise PermissionDenied('アノニウムの編集権限がありません。')
        
        # 参加ポリシーが変更される場合、承認待ちの申請を全て拒否する
        old_join_policy = community.join_policy
        new_join_policy = serializer.validated_data.get('join_policy', old_join_policy)
        
        if old_join_policy == Community.JoinPolicy.APPROVAL and new_join_policy != Community.JoinPolicy.APPROVAL:
            # approvalから他のポリシーに変更された場合、pending状態のメンバーシップを全て削除
            pending_memberships = CM.objects.filter(
                community=community,
                status=CM.Status.PENDING
            )
            pending_memberships.delete()
        
        if old_join_policy == Community.JoinPolicy.OPEN and new_join_policy != Community.JoinPolicy.OPEN:
            # openから他のポリシーに変更された場合、ゲストユーザー（usernameがAnonium-で始まる）を除名
            with transaction.atomic():
                # ゲストユーザーのメンバーシップを取得（usernameがAnonium-で始まるユーザー）
                guest_memberships = CM.objects.filter(
                    community=community,
                    status=CM.Status.APPROVED,
                    user__username__startswith='Anonium-'
                ).select_related('user')
                count_to_remove = guest_memberships.count()
                # メンバーシップを削除
                guest_memberships.delete()
                # メンバー数を更新
                if count_to_remove > 0:
                    Community.objects.filter(pk=community.pk, members_count__gte=count_to_remove).update(
                        members_count=F('members_count') - count_to_remove
                    )
        
        # コミュニティ名が変更される場合、古いslugのキャッシュも削除
        old_name = community.name
        old_slug = community.slug
        new_name = serializer.validated_data.get('name', old_name)
        
        serializer.save()
        
        # コミュニティ情報を再取得（更新後のslugを取得）
        community.refresh_from_db()
        new_slug = community.slug
        
        # キャッシュ削除: コミュニティ詳細、コミュニティ一覧、コミュニティ投稿一覧
        from app.utils import invalidate_cache
        invalidate_cache(key=f'/api/communities/{community.id}/')
        # 名前が変更された場合、古いslugのキャッシュも削除（slugはread_onlyだが念のため）
        if old_name != new_name and old_slug != new_slug:
            invalidate_cache(key=f'/api/communities/{old_slug}/')
        invalidate_cache(pattern='/api/communities/*')
        invalidate_cache(pattern=f'/api/communities/{community.id}/posts/*')
        invalidate_cache(pattern='/api/accounts/*/communities/*')  # ユーザーの参加コミュニティ一覧
        # メッセージ関連のキャッシュも削除（コミュニティ名が変更された場合）
        invalidate_cache(pattern=f'/api/messages/chat-rooms/*')
        invalidate_cache(pattern=f'/api/messages/group-chat/community/{community.id}/*')


class JoinCommunityView(generics.GenericAPIView):
    def get_permissions(self):
        # openポリシーの場合は認証不要（ゲストユーザーも参加可能）
        community = get_object_or_404(Community.objects.filter(is_deleted=False), id=self.kwargs.get('id'))
        if community.join_policy == Community.JoinPolicy.OPEN:
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated()]

    def _get_guest_user(self, request):
        """ゲストユーザーを取得または作成（IPアドレスも保存）"""
        return get_or_create_guest_user(request, create_if_not_exists=True)

    def post(self, request, id: int):
        community = get_object_or_404(Community.objects.filter(is_deleted=False), id=id)
        from .models import CommunityMembership as CM, CommunityBlock

        # ユーザーを取得（認証済みユーザーまたはゲストユーザー）
        if request.user.is_authenticated:
            user = request.user
        else:
            # ゲストユーザーの場合
            if community.join_policy != Community.JoinPolicy.OPEN:
                return Response({'detail': 'このコミュニティに参加するにはログインが必要です。'}, status=status.HTTP_403_FORBIDDEN)
            user = self._get_guest_user(request)
            if not user:
                return Response({'detail': 'ゲストユーザーの識別に失敗しました。'}, status=status.HTTP_400_BAD_REQUEST)

        # Blocked users cannot join
        if CommunityBlock.objects.filter(community=community, user=user).exists():
            return Response({'detail': 'あなたはこのコミュニティにブロックされています。'}, status=status.HTTP_403_FORBIDDEN)
        existing = CM.objects.filter(community=community, user=user).first()
        if existing:
            if existing.status == CM.Status.APPROVED:
                return Response({'detail': '既に参加済みです。'}, status=status.HTTP_400_BAD_REQUEST)
            return Response({'detail': '現在、承認待ちです。'}, status=status.HTTP_400_BAD_REQUEST)

        if community.join_policy == Community.JoinPolicy.APPROVAL:
            if not request.user.is_authenticated:
                return Response({'detail': '承認制のコミュニティに参加するにはログインが必要です。'}, status=status.HTTP_403_FORBIDDEN)
            try:
                CM.objects.create(
                    community=community,
                    user=user,
                    role=CM.Role.MEMBER,
                    status=CM.Status.PENDING,
                )
            except IntegrityError:
                pass
            return Response({'detail': '参加申請を受け付けました。'}, status=status.HTTP_202_ACCEPTED)

        if community.join_policy == Community.JoinPolicy.LOGIN:
            # ログインユーザーのみの場合は即時承認
            if not request.user.is_authenticated:
                return Response({'detail': 'このコミュニティに参加するにはログインが必要です。'}, status=status.HTTP_403_FORBIDDEN)
            try:
                with transaction.atomic():
                    CM.objects.create(
                        community=community,
                        user=user,
                        role=CM.Role.MEMBER,
                        status=CM.Status.APPROVED,
                    )
                    Community.objects.filter(pk=community.pk).update(
                        members_count=F('members_count') + 1
                    )
            except IntegrityError:
                return Response({'detail': '既に参加済みです。'}, status=status.HTTP_400_BAD_REQUEST)
            return Response({'detail': '参加しました。'}, status=status.HTTP_201_CREATED)

        # OPEN - ゲストユーザーもログインユーザーも即時参加
        try:
            with transaction.atomic():
                CM.objects.create(
                    community=community,
                    user=user,
                    role=CM.Role.MEMBER,
                    status=CM.Status.APPROVED,
                )
                Community.objects.filter(pk=community.pk).update(
                    members_count=F('members_count') + 1
                )
        except IntegrityError:
            return Response({'detail': '既に参加済みです。'}, status=status.HTTP_400_BAD_REQUEST)
        
        # キャッシュ削除: コミュニティ一覧、コミュニティ詳細、メンバー一覧、投稿一覧、ユーザーの参加コミュニティ一覧
        from app.utils import invalidate_cache
        invalidate_cache(pattern='/api/communities/*')  # コミュニティ一覧（メンバー数変更のため）
        invalidate_cache(key=f'/api/communities/{community.id}/')
        invalidate_cache(pattern=f'/api/communities/{community.id}/members/*')
        invalidate_cache(pattern=f'/api/communities/{community.id}/posts/*')
        if user.is_authenticated:
            invalidate_cache(pattern=f'/api/accounts/{user.username}/*')
            invalidate_cache(pattern=f'/api/accounts/{user.username}/communities/*')
        
        return Response({'detail': '参加しました。'}, status=status.HTTP_201_CREATED)


class LeaveCommunityView(generics.GenericAPIView):
    def get_permissions(self):
        # openポリシーの場合は認証不要（ゲストユーザーも離脱可能）
        community = get_object_or_404(Community.objects.filter(is_deleted=False), id=self.kwargs.get('id'))
        if community.join_policy == Community.JoinPolicy.OPEN:
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated()]

    def _get_guest_user(self, request):
        """ゲストユーザーを取得（既存のみ、新規作成はしない）"""
        return get_or_create_guest_user(request, create_if_not_exists=False)

    def post(self, request, id: int):
        community = get_object_or_404(Community.objects.filter(is_deleted=False), id=id)
        from .models import CommunityMembership as CM

        # ユーザーを取得（認証済みユーザーまたはゲストユーザー）
        if request.user.is_authenticated:
            user = request.user
        else:
            # ゲストユーザーの場合
            if community.join_policy != Community.JoinPolicy.OPEN:
                return Response({'detail': 'このコミュニティから退会するにはログインが必要です。'}, status=status.HTTP_403_FORBIDDEN)
            user = self._get_guest_user(request)
            if not user:
                return Response({'detail': 'ゲストユーザーの識別に失敗しました。'}, status=status.HTTP_400_BAD_REQUEST)

        membership = CM.objects.filter(community=community, user=user).first()
        if membership is None:
            return Response({'detail': '参加していません。'}, status=status.HTTP_400_BAD_REQUEST)
        if membership.role == CM.Role.OWNER:
            return Response({'detail': 'オーナーは退会できません。'}, status=status.HTTP_403_FORBIDDEN)

        with transaction.atomic():
            if membership.status == CM.Status.APPROVED:
                Community.objects.filter(pk=community.pk, members_count__gt=0).update(
                    members_count=F('members_count') - 1
                )
            membership.delete()
        
        # キャッシュ削除: コミュニティ一覧、コミュニティ詳細、メンバー一覧、投稿一覧、ユーザーの参加コミュニティ一覧
        from app.utils import invalidate_cache
        invalidate_cache(pattern='/api/communities/*')  # コミュニティ一覧（メンバー数変更のため）
        invalidate_cache(key=f'/api/communities/{community.id}/')
        invalidate_cache(pattern=f'/api/communities/{community.id}/members/*')
        invalidate_cache(pattern=f'/api/communities/{community.id}/posts/*')
        if user.is_authenticated:
            invalidate_cache(pattern=f'/api/accounts/{user.username}/*')
            invalidate_cache(pattern=f'/api/accounts/{user.username}/communities/*')
        else:
            # ゲストユーザーの場合、ユーザー名からパターンを生成
            if user and hasattr(user, 'username'):
                invalidate_cache(pattern=f'/api/accounts/{user.username}/*')
        
        return Response({'detail': '退会しました。'}, status=status.HTTP_200_OK)


class ManageMemberBaseView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _require_owner(self, community: Community, user) -> None:
        from .models import CommunityMembership as CM
        membership = CM.objects.filter(community=community, user=user, status=CM.Status.APPROVED).first()
        if not membership or membership.role != CM.Role.OWNER:
            raise PermissionDenied('オーナー権限が必要です。')

    def _require_owner_or_admin_mod(self, community: Community, user) -> None:
        from .models import CommunityMembership as CM
        membership = CM.objects.filter(community=community, user=user, status=CM.Status.APPROVED).first()
        if not membership or membership.role not in (CM.Role.OWNER, CM.Role.ADMIN_MODERATOR):
            raise PermissionDenied('管理モデレーター以上の権限が必要です。')


class RemoveMemberView(ManageMemberBaseView):
    def post(self, request, id: int, user_id: int):
        community = get_object_or_404(Community.objects.filter(is_deleted=False), id=id)
        self._require_owner_or_admin_mod(community, request.user)
        from .models import CommunityMembership as CM
        target = get_object_or_404(User, id=user_id)
        if target == request.user:
            return Response({'detail': '自分自身は除名できません。'}, status=status.HTTP_400_BAD_REQUEST)
        m = CM.objects.filter(community=community, user=target).first()
        if not m:
            return Response({'detail': 'メンバーではありません。'}, status=status.HTTP_400_BAD_REQUEST)
        if m.role == CM.Role.OWNER:
            return Response({'detail': 'オーナーは除名できません。'}, status=status.HTTP_400_BAD_REQUEST)
        from django.db import transaction
        with transaction.atomic():
            if m.status == CM.Status.APPROVED:
                Community.objects.filter(pk=community.pk, members_count__gt=0).update(members_count=F('members_count') - 1)
            m.delete()
        
        # キャッシュ削除: コミュニティ詳細、メンバー一覧、ユーザーの参加コミュニティ一覧
        from app.utils import invalidate_cache
        invalidate_cache(key=f'/api/communities/{community.id}/')
        invalidate_cache(pattern=f'/api/communities/{community.id}/members/*')
        invalidate_cache(pattern=f'/api/accounts/{target.username}/*')
        invalidate_cache(pattern=f'/api/accounts/{target.username}/communities/*')
        
        return Response({'detail': '除名しました。'})


class BlockMemberView(ManageMemberBaseView):
    def post(self, request, id: int, user_id: int):
        community = get_object_or_404(Community.objects.filter(is_deleted=False), id=id)
        self._require_owner_or_admin_mod(community, request.user)
        from .models import CommunityMembership as CM, CommunityBlock
        target = get_object_or_404(User, id=user_id)
        if target == request.user:
            return Response({'detail': '自分自身はブロックできません。'}, status=status.HTTP_400_BAD_REQUEST)
        reason = (request.data.get('reason') or '').strip()
        from django.db import transaction
        with transaction.atomic():
            # remove membership if any
            m = CM.objects.filter(community=community, user=target).first()
            if m:
                if m.role == CM.Role.OWNER:
                    return Response({'detail': 'オーナーはブロックできません。'}, status=status.HTTP_400_BAD_REQUEST)
                if m.status == CM.Status.APPROVED:
                    Community.objects.filter(pk=community.pk, members_count__gt=0).update(members_count=F('members_count') - 1)
                m.delete()
            CommunityBlock.objects.get_or_create(community=community, user=target, defaults={'reason': reason[:255]})
        
        # キャッシュ削除: コミュニティ詳細、メンバー一覧、ブロック一覧、ユーザーの参加コミュニティ一覧
        from app.utils import invalidate_cache
        invalidate_cache(key=f'/api/communities/{community.id}/')
        invalidate_cache(pattern=f'/api/communities/{community.id}/members/*')
        invalidate_cache(pattern=f'/api/communities/{community.id}/blocks/*')
        invalidate_cache(pattern=f'/api/accounts/{target.username}/*')
        invalidate_cache(pattern=f'/api/accounts/{target.username}/communities/*')
        invalidate_cache(pattern=f'/api/communities/{community.id}/posts/*')  # ブロックされたユーザーの投稿が非表示になる
        
        return Response({'detail': 'ブロックしました。'})


class UnblockMemberView(ManageMemberBaseView):
    def post(self, request, id: int, user_id: int):
        community = get_object_or_404(Community.objects.filter(is_deleted=False), id=id)
        self._require_owner_or_admin_mod(community, request.user)
        from .models import CommunityBlock
        target = get_object_or_404(User, id=user_id)
        CommunityBlock.objects.filter(community=community, user=target).delete()
        
        # キャッシュ削除: コミュニティ詳細、ブロック一覧
        from app.utils import invalidate_cache
        invalidate_cache(key=f'/api/communities/{community.id}/')
        invalidate_cache(pattern=f'/api/communities/{community.id}/blocks/*')
        
        return Response({'detail': 'ブロックを解除しました。'})


class PromoteModeratorView(ManageMemberBaseView):
    def post(self, request, id: int, user_id: int):
        community = get_object_or_404(Community.objects.filter(is_deleted=False), id=id)
        # 標準モデレーターの任命: オーナー or 管理モデレーター
        self._require_owner_or_admin_mod(community, request.user)
        from .models import CommunityMembership as CM
        target = get_object_or_404(User, id=user_id)
        m = CM.objects.filter(community=community, user=target).first()
        if not m:
            return Response({'detail': 'メンバーではありません。'}, status=status.HTTP_400_BAD_REQUEST)
        if m.role == CM.Role.OWNER:
            return Response({'detail': 'オーナーは変更できません。'}, status=status.HTTP_400_BAD_REQUEST)
        if m.role == CM.Role.ADMIN_MODERATOR:
            return Response({'detail': '既に管理モデレーターです。'}, status=status.HTTP_400_BAD_REQUEST)
        # 任命実行
        appointer = CM.objects.filter(community=community, user=request.user, status=CM.Status.APPROVED).first()
        m.role = CM.Role.MODERATOR
        if appointer and appointer.role == CM.Role.ADMIN_MODERATOR:
            m.appointed_by_admin = request.user
        else:
            m.appointed_by_admin = None
        m.save(update_fields=['role', 'appointed_by_admin'])
        
        # キャッシュ削除: コミュニティ詳細、メンバー一覧、モデレーター一覧
        from app.utils import invalidate_cache
        invalidate_cache(key=f'/api/communities/{community.id}/')
        invalidate_cache(pattern=f'/api/communities/{community.id}/members/*')
        invalidate_cache(pattern=f'/api/communities/{community.id}/moderators/*')
        
        return Response({'detail': '標準モデレーターに任命しました。'})


class DemoteModeratorView(ManageMemberBaseView):
    def post(self, request, id: int, user_id: int):
        community = get_object_or_404(Community.objects.filter(is_deleted=False), id=id)
        # 標準モデレーターの解除: オーナー or 管理モデレーター
        self._require_owner_or_admin_mod(community, request.user)
        from .models import CommunityMembership as CM
        target = get_object_or_404(User, id=user_id)
        m = CM.objects.filter(community=community, user=target).first()
        if not m:
            return Response({'detail': 'メンバーではありません。'}, status=status.HTTP_400_BAD_REQUEST)
        if m.role == CM.Role.OWNER:
            return Response({'detail': 'オーナーは変更できません。'}, status=status.HTTP_400_BAD_REQUEST)
        if m.role == CM.Role.MODERATOR:
            m.role = CM.Role.MEMBER
            m.appointed_by_admin = None
            m.save(update_fields=['role', 'appointed_by_admin'])
            
            # キャッシュ削除: コミュニティ詳細、メンバー一覧、モデレーター一覧
            from app.utils import invalidate_cache
            invalidate_cache(key=f'/api/communities/{community.id}/')
            invalidate_cache(pattern=f'/api/communities/{community.id}/members/*')
            invalidate_cache(pattern=f'/api/communities/{community.id}/moderators/*')
            
            return Response({'detail': '標準モデレーターを解除しました。'})
        return Response({'detail': '標準モデレーターではありません。'}, status=status.HTTP_400_BAD_REQUEST)


class PromoteAdminModeratorView(ManageMemberBaseView):
    def post(self, request, id: int, user_id: int):
        community = get_object_or_404(Community.objects.filter(is_deleted=False), id=id)
        # 管理モデレーターの任命: オーナーのみ
        self._require_owner(community, request.user)
        from .models import CommunityMembership as CM
        target = get_object_or_404(User, id=user_id)
        m = CM.objects.filter(community=community, user=target).first()
        if not m:
            return Response({'detail': 'メンバーではありません。'}, status=status.HTTP_400_BAD_REQUEST)
        if m.role == CM.Role.OWNER:
            return Response({'detail': 'オーナーは変更できません。'}, status=status.HTTP_400_BAD_REQUEST)
        if m.role == CM.Role.ADMIN_MODERATOR:
            return Response({'detail': '既に管理モデレーターです。'}, status=status.HTTP_400_BAD_REQUEST)
        m.role = CM.Role.ADMIN_MODERATOR
        m.appointed_by_admin = None
        m.save(update_fields=['role', 'appointed_by_admin'])
        
        # キャッシュ削除: コミュニティ詳細、メンバー一覧、モデレーター一覧
        from app.utils import invalidate_cache
        invalidate_cache(key=f'/api/communities/{community.id}/')
        invalidate_cache(pattern=f'/api/communities/{community.id}/members/*')
        invalidate_cache(pattern=f'/api/communities/{community.id}/moderators/*')
        
        return Response({'detail': '管理モデレーターに任命しました。'})


class DemoteAdminModeratorView(ManageMemberBaseView):
    def post(self, request, id: int, user_id: int):
        community = get_object_or_404(Community.objects.filter(is_deleted=False), id=id)
        # 管理モデレーターの解除: オーナーのみ
        self._require_owner(community, request.user)
        from .models import CommunityMembership as CM
        target = get_object_or_404(User, id=user_id)
        m = CM.objects.filter(community=community, user=target).first()
        if not m:
            return Response({'detail': 'メンバーではありません。'}, status=status.HTTP_400_BAD_REQUEST)
        if m.role != CM.Role.ADMIN_MODERATOR:
            return Response({'detail': '管理モデレーターではありません。'}, status=status.HTTP_400_BAD_REQUEST)
        m.role = CM.Role.MEMBER
        # save() 経由で連鎖解除（標準モデレーターの解除）を発火
        m.save(update_fields=['role'])
        
        # キャッシュ削除: コミュニティ詳細、メンバー一覧、モデレーター一覧
        from app.utils import invalidate_cache
        invalidate_cache(key=f'/api/communities/{community.id}/')
        invalidate_cache(pattern=f'/api/communities/{community.id}/members/*')
        invalidate_cache(pattern=f'/api/communities/{community.id}/moderators/*')
        
        return Response({'detail': '管理モデレーターを解除しました。'})

class _BaseImageUploadView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    aspect_ratio: tuple[int, int] | None = None  # (w, h)
    kind: str = 'image'

    def post(self, request, id: int):
        community = get_object_or_404(Community.objects.filter(is_deleted=False), id=id)
        from .models import CommunityMembership as CM
        membership = CM.objects.filter(community=community, user=request.user, status=CM.Status.APPROVED).first()
        if not membership or membership.role not in (CM.Role.OWNER, CM.Role.ADMIN_MODERATOR):
            raise PermissionDenied('編集権限がありません。')

        file = request.FILES.get('image')
        if not file:
            return Response({'detail': 'image file required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            image = Image.open(file)
        except Exception:
            return Response({'detail': 'invalid image'}, status=status.HTTP_400_BAD_REQUEST)

        # Normalize to RGB (drop alpha)
        if image.mode not in ('RGB', 'L'):
            image = image.convert('RGB')

        W, H = image.size
        # crop params: relative (0..1) or pixels
        def _as_float(name: str):
            v = request.data.get(name)
            if v is None:
                return None
            try:
                return float(v)
            except Exception:
                return None

        cx = _as_float('crop_x')
        cy = _as_float('crop_y')
        cw = _as_float('crop_w')
        ch = _as_float('crop_h')

        if self.aspect_ratio and (cw is None or ch is None):
            # center-crop to aspect if not provided
            ar_w, ar_h = self.aspect_ratio
            target = ar_w / ar_h
            img_ratio = W / H
            if (img_ratio) > target:
                # image wider -> crop width
                new_w = int(H * target)
                new_h = H
                x0 = (W - new_w) // 2
                y0 = 0
            else:
                new_w = W
                new_h = int(W / target)
                x0 = 0
                y0 = (H - new_h) // 2
            box = (x0, y0, x0 + new_w, y0 + new_h)
        elif cw and ch:
            # if <=1 treat as relative
            if cw <= 1 and ch <= 1:
                px = int((cx or 0) * W)
                py = int((cy or 0) * H)
                pw = int(cw * W)
                ph = int(ch * H)
            else:
                px = int(cx or 0)
                py = int(cy or 0)
                pw = int(cw)
                ph = int(ch)
            # clamp
            px = max(0, min(px, W - 1))
            py = max(0, min(py, H - 1))
            pw = max(1, min(pw, W - px))
            ph = max(1, min(ph, H - py))
            box = (px, py, px + pw, py + ph)
        else:
            box = (0, 0, W, H)

        try:
            cropped = image.crop(box)
        except Exception:
            return Response({'detail': 'failed to crop'}, status=status.HTTP_400_BAD_REQUEST)

        # Resize to reasonable size (icon ~512, banner width ~1200)
        max_size = 512 if self.kind == 'icon' else 1200
        cw2, ch2 = cropped.size
        if self.kind == 'icon':
            target_size = (min(max_size, cw2), min(max_size, ch2))
            # enforce square
            s = min(target_size[0], target_size[1])
            cropped = cropped.resize((s, s), Image.LANCZOS)
        else:
            if cw2 > max_size:
                new_w = max_size
                new_h = int(ch2 * (max_size / cw2))
                cropped = cropped.resize((new_w, new_h), Image.LANCZOS)

        folder = 'communities/icons' if self.kind == 'icon' else 'communities/banners'
        ts = int(time.time())
        filename = f"{community.slug}-{ts}.jpg"
        
        try:
            from app.utils import save_image_locally_or_gcs
            abs_url = save_image_locally_or_gcs(cropped, folder, filename, request)
        except Exception:
            return Response({'detail': 'failed to save'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        previous_url = community.icon_url if self.kind == 'icon' else community.banner_url

        if self.kind == 'icon':
            community.icon_url = abs_url
        else:
            community.banner_url = abs_url
        community.save(update_fields=['icon_url' if self.kind == 'icon' else 'banner_url', 'updated_at'])

        if previous_url and previous_url != abs_url:
            delete_media_file_by_url(previous_url)

        # キャッシュ削除: コミュニティ詳細、コミュニティ一覧
        from app.utils import invalidate_cache
        invalidate_cache(key=f'/api/communities/{community.id}/')
        invalidate_cache(pattern='/api/communities/*')
        
        return Response(CommunitySerializer(community, context={'request': request}).data)


class UploadCommunityIconView(_BaseImageUploadView):
    aspect_ratio = (1, 1)
    kind = 'icon'


class UploadCommunityBannerView(_BaseImageUploadView):
    aspect_ratio = (7, 2)
    kind = 'banner'


class CommunityMembersView(generics.ListAPIView):
    serializer_class = CommunityParticipantSerializer

    def get_permissions(self):
        # メンバー一覧の閲覧は常に許可（参加ポリシーは参加時にのみ適用）
        return [permissions.AllowAny()]

    def get_queryset(self):
        from .models import CommunityMembership as CM, Community
        from accounts.models import UserProfile
        community = get_object_or_404(Community.objects.filter(is_deleted=False), id=self.kwargs.get('id'))
        qs = CM.objects.filter(
            community=community,
            status=CM.Status.APPROVED,
        ).select_related('user', 'user__profile')
        
        # ソート順を取得（デフォルトは作成日時順）
        order_by = self.request.query_params.get('order_by', 'created_at')
        if order_by == 'score':
            # スコア順でソート（降順）
            qs = qs.annotate(
                user_score=Coalesce(
                    Subquery(
                        UserProfile.objects.filter(user=OuterRef('user')).values('score')[:1]
                    ),
                    Value(0)
                )
            ).order_by('-user_score', '-created_at')
        elif order_by == 'score_asc':
            # スコア順でソート（昇順）
            qs = qs.annotate(
                user_score=Coalesce(
                    Subquery(
                        UserProfile.objects.filter(user=OuterRef('user')).values('score')[:1]
                    ),
                    Value(0)
                )
            ).order_by('user_score', '-created_at')
        else:
            # デフォルトは作成日時順（降順）
            qs = qs.order_by('-created_at')
        
        limit = self.request.query_params.get('limit')
        try:
            if limit is not None:
                n = int(limit)
                if n > 0:
                    return qs[: n]
        except ValueError:
            pass
        return qs


class CommunityModeratorsView(generics.ListAPIView):
    serializer_class = CommunityParticipantSerializer

    def get_permissions(self):
        # モデレーター一覧の閲覧は常に許可
        return [permissions.AllowAny()]

    def get_queryset(self):
        from .models import CommunityMembership as CM, Community
        from accounts.models import UserProfile
        community = get_object_or_404(Community.objects.filter(is_deleted=False), id=self.kwargs.get('id'))
        # モデレーター（owner, admin_moderator, moderator）のみをフィルタリング
        qs = CM.objects.filter(
            community=community,
            status=CM.Status.APPROVED,
            role__in=[CM.Role.OWNER, CM.Role.ADMIN_MODERATOR, CM.Role.MODERATOR],
        ).select_related('user', 'user__profile')
        
        # スコアをアノテートして、ロール順、次にスコア順でソート
        qs = qs.annotate(
            user_score=Coalesce(
                Subquery(
                    UserProfile.objects.filter(user=OuterRef('user')).values('score')[:1]
                ),
                Value(0)
            )
        )
        
        # ロール優先順位: owner > admin_moderator > moderator
        qs = qs.annotate(
            role_priority=Case(
                When(role=CM.Role.OWNER, then=Value(0)),
                When(role=CM.Role.ADMIN_MODERATOR, then=Value(1)),
                When(role=CM.Role.MODERATOR, then=Value(2)),
                default=Value(3),
                output_field=IntegerField(),
            )
        ).order_by('role_priority', '-user_score', '-created_at')
        
        return qs


class CommunityBlocksView(generics.ListAPIView):
    serializer_class = CommunityBlockedUserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        from .models import Community, CommunityBlock, CommunityMembership as CM
        community = get_object_or_404(Community.objects.filter(is_deleted=False), id=self.kwargs.get('id'))
        # owner-only visibility (could be extended to moderators if desired)
        membership = CM.objects.filter(community=community, user=self.request.user, status=CM.Status.APPROVED).first()
        if not membership or membership.role != CM.Role.OWNER:
            raise PermissionDenied('オーナー権限が必要です。')
        return CommunityBlock.objects.filter(community=community).select_related('user', 'user__profile').order_by('-created_at')


class PendingRequestsView(generics.ListAPIView):
    serializer_class = CommunityParticipantSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        from .models import Community, CommunityMembership as CM
        community = get_object_or_404(Community.objects.filter(is_deleted=False), id=self.kwargs.get('id'))
        # owner-only visibility
        membership = CM.objects.filter(community=community, user=self.request.user, status=CM.Status.APPROVED).first()
        if not membership or membership.role != CM.Role.OWNER:
            raise PermissionDenied('オーナー権限が必要です。')
        return CM.objects.filter(
            community=community,
            status=CM.Status.PENDING,
        ).select_related('user', 'user__profile').order_by('-created_at')


class ApproveRequestView(ManageMemberBaseView):
    def post(self, request, id: int, user_id: int):
        community = get_object_or_404(Community.objects.filter(is_deleted=False), id=id)
        self._require_owner(community, request.user)
        from .models import CommunityMembership as CM
        target = get_object_or_404(User, id=user_id)
        membership = CM.objects.filter(community=community, user=target, status=CM.Status.PENDING).first()
        if not membership:
            return Response({'detail': '該当する参加申請が見つかりません。'}, status=status.HTTP_404_NOT_FOUND)
        with transaction.atomic():
            membership.status = CM.Status.APPROVED
            membership.save(update_fields=['status'])
            Community.objects.filter(pk=community.pk).update(
                members_count=F('members_count') + 1
            )
        
        # キャッシュ削除: コミュニティ詳細、メンバー一覧、参加申請一覧、ユーザーの参加コミュニティ一覧
        from app.utils import invalidate_cache
        invalidate_cache(key=f'/api/communities/{community.id}/')
        invalidate_cache(pattern=f'/api/communities/{community.id}/members/*')
        invalidate_cache(pattern=f'/api/communities/{community.id}/requests/*')
        invalidate_cache(pattern=f'/api/accounts/{target.username}/*')
        invalidate_cache(pattern=f'/api/accounts/{target.username}/communities/*')
        
        return Response({'detail': '参加申請を承認しました。'})


class RejectRequestView(ManageMemberBaseView):
    def post(self, request, id: int, user_id: int):
        community = get_object_or_404(Community.objects.filter(is_deleted=False), id=id)
        self._require_owner(community, request.user)
        from .models import CommunityMembership as CM
        target = get_object_or_404(User, id=user_id)
        membership = CM.objects.filter(community=community, user=target, status=CM.Status.PENDING).first()
        if not membership:
            return Response({'detail': '該当する参加申請が見つかりません。'}, status=status.HTTP_404_NOT_FOUND)
        membership.delete()
        
        # キャッシュ削除: コミュニティ詳細、参加申請一覧
        from app.utils import invalidate_cache
        invalidate_cache(key=f'/api/communities/{community.id}/')
        invalidate_cache(pattern=f'/api/communities/{community.id}/requests/*')
        
        return Response({'detail': '参加申請を拒否しました。'})


class MyCommunitiesView(generics.ListAPIView):
    """ユーザー（ログイン or ゲスト）が参加中のコミュニティ一覧を返す"""
    serializer_class = CommunitySerializer
    permission_classes = [permissions.AllowAny]

    def _resolve_guest_user(self, request):
        """ゲストユーザーを解決（既存のみ、新規作成はしない）"""
        return get_or_create_guest_user(request, create_if_not_exists=False)

    def get_queryset(self):
        from .models import CommunityMembership as CM
        user = self.request.user if (self.request.user and self.request.user.is_authenticated) else None
        if not user:
            user = self._resolve_guest_user(self.request)
        if not user:
            return Community.objects.none()
        community_ids = CM.objects.filter(user=user, status=CM.Status.APPROVED).values_list('community_id', flat=True)
        return Community.objects.filter(id__in=community_ids, is_deleted=False).order_by('name')


class FavoriteCommunityView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, id: int):
        community = get_object_or_404(Community.objects.filter(is_deleted=False), id=id)
        from .models import CommunityMembership as CM
        m = CM.objects.filter(community=community, user=request.user, status=CM.Status.APPROVED).first()
        if not m:
            return Response({'detail': 'お気に入り登録はメンバーのみ可能です。'}, status=status.HTTP_400_BAD_REQUEST)
        CM.objects.filter(pk=m.pk).update(is_favorite=True)
        
        # キャッシュ削除: ユーザーのお気に入りコミュニティ一覧
        from app.utils import invalidate_cache
        invalidate_cache(pattern=f'/api/accounts/{request.user.username}/*')
        invalidate_cache(pattern=f'/api/communities/*/favorites/*')
        
        return Response({'detail': 'お気に入りに追加しました。', 'is_favorite': True})

    def delete(self, request, id: int):
        community = get_object_or_404(Community.objects.filter(is_deleted=False), id=id)
        from .models import CommunityMembership as CM
        m = CM.objects.filter(community=community, user=request.user, status=CM.Status.APPROVED).first()
        if not m:
            return Response({'detail': 'メンバーではありません。'}, status=status.HTTP_400_BAD_REQUEST)
        CM.objects.filter(pk=m.pk).update(is_favorite=False)
        
        # キャッシュ削除: ユーザーのお気に入りコミュニティ一覧
        from app.utils import invalidate_cache
        invalidate_cache(pattern=f'/api/accounts/{request.user.username}/*')
        invalidate_cache(pattern=f'/api/communities/*/favorites/*')
        
        return Response({'detail': 'お気に入りを解除しました。', 'is_favorite': False})


class FavoriteCommunitiesView(generics.ListAPIView):
    serializer_class = CommunitySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        from .models import CommunityMembership as CM
        fav_ids = CM.objects.filter(user=self.request.user, status=CM.Status.APPROVED, is_favorite=True).values_list('community_id', flat=True)
        return Community.objects.filter(id__in=fav_ids, is_deleted=False).order_by('name')


class CommunityMuteListView(generics.ListAPIView):
    serializer_class = CommunitySerializer
    permission_classes = [permissions.AllowAny]

    def _get_guest_user(self, request):
        """ゲストユーザーを取得または作成（IPアドレスも保存）"""
        return get_or_create_guest_user(request, create_if_not_exists=True)

    def get_queryset(self):
        from .models import CommunityMute
        # ユーザーを取得（認証済みユーザーまたはゲストユーザー）
        if self.request.user.is_authenticated:
            user = self.request.user
        else:
            user = self._get_guest_user(self.request)
        
        if not user:
            return Community.objects.none()
        
        mute_ids = CommunityMute.objects.filter(user=user).values_list('community_id', flat=True)
        return Community.objects.filter(id__in=mute_ids, is_deleted=False).order_by('name')


class CommunityMuteCreateView(APIView):
    permission_classes = [permissions.AllowAny]

    def _get_guest_user(self, request):
        """ゲストユーザーを取得または作成（IPアドレスも保存）"""
        return get_or_create_guest_user(request, create_if_not_exists=True)

    def post(self, request, id: int):
        from .models import CommunityMute, CommunityMembership as CM
        community = get_object_or_404(Community.objects.filter(is_deleted=False), id=id)
        
        # ユーザーを取得（認証済みユーザーまたはゲストユーザー）
        if request.user.is_authenticated:
            user = request.user
        else:
            user = self._get_guest_user(request)
            if not user:
                return Response({'detail': 'ゲストユーザーの識別に失敗しました。'}, status=status.HTTP_400_BAD_REQUEST)
        
        # モデレーター以上のロールのユーザーはミュート不可
        membership = CM.objects.filter(community=community, user=user, status=CM.Status.APPROVED).first()
        if membership and membership.role in (CM.Role.OWNER, CM.Role.ADMIN_MODERATOR, CM.Role.MODERATOR):
            return Response({'detail': 'モデレーター以上のロールのユーザーはコミュニティをミュートできません。'}, status=status.HTTP_403_FORBIDDEN)
        
        CommunityMute.objects.get_or_create(user=user, community=community)
        
        # キャッシュ削除: ユーザーのミュートコミュニティ一覧、コミュニティ一覧（投稿が非表示になる）
        from app.utils import invalidate_cache
        username = user.username if user.is_authenticated else None
        if username:
            invalidate_cache(pattern=f'/api/accounts/{username}/*')
        invalidate_cache(pattern='/api/communities/*/mutes/*')
        invalidate_cache(pattern='/api/posts/*')  # ミュートされたコミュニティの投稿が非表示になる
        invalidate_cache(pattern='/api/posts/trending*')
        
        return Response({'detail': f'{community.name} をミュートしました。'}, status=status.HTTP_201_CREATED)


class CommunityMuteDeleteView(APIView):
    permission_classes = [permissions.AllowAny]

    def _get_guest_user(self, request):
        """ゲストユーザーを取得（既存のみ、新規作成はしない）"""
        return get_or_create_guest_user(request, create_if_not_exists=False)

    def delete(self, request, id: int):
        from .models import CommunityMute
        community = get_object_or_404(Community.objects.filter(is_deleted=False), id=id)
        
        # ユーザーを取得（認証済みユーザーまたはゲストユーザー）
        if request.user.is_authenticated:
            user = request.user
        else:
            user = self._get_guest_user(request)
            if not user:
                return Response({'detail': 'ゲストユーザーの識別に失敗しました。'}, status=status.HTTP_400_BAD_REQUEST)
        
        CommunityMute.objects.filter(user=user, community=community).delete()
        
        # キャッシュ削除: ユーザーのミュートコミュニティ一覧、コミュニティ一覧
        from app.utils import invalidate_cache
        username = user.username if user.is_authenticated else None
        if username:
            invalidate_cache(pattern=f'/api/accounts/{username}/*')
        invalidate_cache(pattern='/api/communities/*/mutes/*')
        invalidate_cache(pattern='/api/posts/*')  # ミュート解除されたコミュニティの投稿が表示される
        invalidate_cache(pattern='/api/posts/trending*')
        
        return Response(status=status.HTTP_204_NO_CONTENT)


class CommunityClipPostView(APIView):
    """コミュニティにポストを固定/解除するAPI"""
    permission_classes = [permissions.IsAuthenticated]

    def _check_moderator_permission(self, user, community):
        """管理モデレーター以上の権限をチェック"""
        if not user or not user.is_authenticated:
            return False
        membership = CommunityMembership.objects.filter(
            community=community,
            user=user,
            status=CommunityMembership.Status.APPROVED
        ).first()
        if not membership:
            return False
        # オーナーまたは管理モデレーター以上
        return membership.role in (
            CommunityMembership.Role.OWNER,
            CommunityMembership.Role.ADMIN_MODERATOR
        )

    def post(self, request, id: int, post_id: int):
        """ポストを固定する"""
        community = get_object_or_404(Community.objects.filter(is_deleted=False), id=id)
        post = get_object_or_404(Post, pk=post_id, community=community)
        
        # 権限チェック
        if not self._check_moderator_permission(request.user, community):
            raise PermissionDenied('この操作を実行する権限がありません。管理モデレーター以上の権限が必要です。')
        
        # ポストを固定
        community.clip_post = post
        community.save(update_fields=['clip_post'])
        
        # キャッシュ削除: コミュニティ詳細、コミュニティ投稿一覧
        from app.utils import invalidate_cache
        invalidate_cache(key=f'/api/communities/{community.id}/')
        invalidate_cache(pattern=f'/api/communities/{community.id}/posts/*')
        invalidate_cache(key=f'/api/posts/{post.id}/')
        
        return Response({
            'detail': 'ポストを固定しました。',
            'clip_post_id': post.id
        }, status=status.HTTP_200_OK)

    def delete(self, request, id: int, post_id: int):
        """ポストの固定を解除する"""
        community = get_object_or_404(Community.objects.filter(is_deleted=False), id=id)
        post = get_object_or_404(Post, pk=post_id, community=community)
        
        # 権限チェック
        if not self._check_moderator_permission(request.user, community):
            raise PermissionDenied('この操作を実行する権限がありません。管理モデレーター以上の権限が必要です。')
        
        # 固定を解除（該当ポストの場合のみ）
        if community.clip_post_id == post.id:
            community.clip_post = None
            community.save(update_fields=['clip_post'])
        
        # キャッシュ削除: コミュニティ詳細、コミュニティ投稿一覧
        from app.utils import invalidate_cache
        invalidate_cache(key=f'/api/communities/{community.id}/')
        invalidate_cache(pattern=f'/api/communities/{community.id}/posts/*')
        invalidate_cache(key=f'/api/posts/{post.id}/')
        
        return Response({
            'detail': 'ポストの固定を解除しました。'
        }, status=status.HTTP_200_OK)


class CommunityStatusView(APIView):
    """コミュニティに対するユーザーの状態を取得するAPI"""
    permission_classes = [permissions.AllowAny]

    def get(self, request, id: int):
        community = get_object_or_404(Community.objects.filter(is_deleted=False), id=id)
        user = _resolve_user(request)
        status = _get_community_user_status(community, user)
        return Response(status)


class CommunityStatusListView(APIView):
    """複数のコミュニティに対するユーザーの状態を一括取得するAPI"""
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        ids_param = request.query_params.get('ids', '')
        if not ids_param:
            return Response({}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            ids = [int(id_str.strip()) for id_str in ids_param.split(',') if id_str.strip()]
        except (ValueError, TypeError):
            return Response({'detail': 'Invalid ids parameter'}, status=status.HTTP_400_BAD_REQUEST)
        
        if not ids:
            return Response({})
        
        communities = Community.objects.filter(id__in=ids, is_deleted=False)
        user = _resolve_user(request)
        
        status_map = {}
        for community in communities:
            status_map[str(community.id)] = _get_community_user_status(community, user)
        
        return Response(status_map)


class DeleteCommunityView(ManageMemberBaseView):
    """コミュニティを削除するAPI（オーナーのみ、ソフト削除）"""
    
    def delete(self, request, id: int):
        # 削除されていないコミュニティのみ取得
        community = get_object_or_404(Community.objects.filter(is_deleted=False), id=id)
        # オーナー権限チェック
        self._require_owner(community, request.user)
        
        # ソフト削除: is_deletedフラグをTrueに設定
        community.is_deleted = True
        community.save(update_fields=['is_deleted', 'updated_at'])
        
        # コミュニティIDとslugを保存（キャッシュ削除用）
        community_id = community.id
        community_slug = community.slug
        
        # キャッシュ削除: コミュニティ一覧、コミュニティ詳細、関連するすべてのキャッシュ
        from app.utils import invalidate_cache
        invalidate_cache(pattern='/api/communities/*')
        invalidate_cache(key=f'/api/communities/{community_id}/')
        invalidate_cache(key=f'/api/communities/{community_slug}/')
        invalidate_cache(pattern=f'/api/communities/{community_id}/*')
        invalidate_cache(pattern='/api/accounts/*/communities/*')  # ユーザーの参加コミュニティ一覧
        invalidate_cache(pattern='/api/posts/*')  # コミュニティの投稿が非表示になる
        invalidate_cache(pattern='/api/messages/chat-rooms/*')
        invalidate_cache(pattern=f'/api/messages/group-chat/community/{community_id}/*')
        
        return Response({'detail': 'コミュニティを削除しました。'}, status=status.HTTP_200_OK)
