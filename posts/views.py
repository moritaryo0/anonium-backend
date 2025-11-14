from django.db.models import F, Q, Count, Prefetch
from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.pagination import PageNumberPagination
from urllib.parse import urlparse, urljoin
import base64, json
import requests
from bs4 import BeautifulSoup
from django.conf import settings
import os, time, math
from datetime import timedelta
from PIL import Image
from django.utils import timezone
from django.core import signing
import tempfile, subprocess, shutil, mimetypes
import logging

from communities.models import Community, CommunityMembership as CM, CommunityBlock
from .models import Post, PostVote, Comment, CommentVote, OGPCache, Poll, PollOption, PollVote, PostFollow, PostMedia
from accounts.models import UserProfile, UserMute, Notification
from django.contrib.auth.models import User
from django.db import models
from django.db.models import Max
from .serializers import PostCreateSerializer, PostSerializer, CommentSerializer
from app.utils import save_image_locally_or_gcs
from accounts.utils import get_or_create_guest_user, get_client_ip, get_guest_token_from_request

logger = logging.getLogger(__name__)


def calculate_trending_score(upvotes: int, downvotes: int, created_at, *, comment_count: int = 0, comment_weight: float = 0.7, now=None, half_life_hours: float = 6.0) -> float:
    """勢い偏重型のスコア計算"""
    if now is None:
        now = timezone.now()
    half_life_hours = half_life_hours if half_life_hours and half_life_hours > 0 else 6.0

    engagement = (upvotes - downvotes) + max(comment_count, 0) * max(comment_weight, 0)
    score = max(engagement, 0.0)
    if score <= 0:
        return 0.0

    elapsed = now - created_at
    if elapsed.total_seconds() < 0:
        elapsed_hours = 0.0
    else:
        elapsed_hours = elapsed.total_seconds() / 3600.0

    decay = 0.5 ** (elapsed_hours / half_life_hours)
    log_score = math.log10(score + 1)
    return round((10 ** log_score) * decay * 100.0, 7)


class CommunityPostListCreateView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_permissions(self):
        # GET は常に許可（参加ポリシーは参加時にのみ適用）
        # POST はゲスト投稿を許可（詳細は perform_create で判定）
        if self.request.method in ('GET', 'POST'):
            return [permissions.AllowAny()]
        return super().get_permissions()

    def _resolve_guest_user(self, request):
        """ゲストユーザーを解決（作成はしない、IPアドレスは保存しない）"""
        if request.user and request.user.is_authenticated:
            return request.user
        # 既存ユーザーのみ取得（新規作成はしない）
        return get_or_create_guest_user(request, create_if_not_exists=False)

    def get_queryset(self):
        community = get_object_or_404(Community, id=self.kwargs['id'])
        qs = Post.objects.filter(community=community, is_deleted=False)
        # ユーザーを取得（認証済みユーザーまたはゲストユーザー）
        user = self._resolve_guest_user(self.request)
        if user:
            muted_ids = list(UserMute.objects.filter(user=user).values_list('target_id', flat=True))
            if muted_ids:
                qs = qs.exclude(author_id__in=muted_ids)
        
        sort = self.request.query_params.get('sort', 'trending').lower()
        clip_post_id = community.clip_post_id if community.clip_post_id else None
        
        if sort == 'trending':
            # 勢い順の場合はPythonでソート（ページネーションは無効）
            qs = qs.select_related('community', 'author', 'author__profile', 'tag', 'poll').prefetch_related(
                'media',
                Prefetch('poll__options', queryset=PollOption.objects.all().order_by('id'))
            ).annotate(
                active_comments=Count('comments', filter=Q(comments__is_deleted=False))
            )
            posts = list(qs)
            now = timezone.now()
            
            # 固定ポストとその他のポストを分離
            clipped_post = None
            other_posts = []
            
            for post in posts:
                upvotes = max(int((post.votes_total + post.score) / 2), 0)
                downvotes = max(post.votes_total - upvotes, 0)
                comment_count = getattr(post, 'active_comments', 0)
                trending = calculate_trending_score(
                    upvotes,
                    downvotes,
                    post.created_at,
                    comment_count=comment_count,
                    now=now,
                    half_life_hours=6.0,
                )
                post._trending_score = trending
                
                if post.id == clip_post_id:
                    clipped_post = post
                else:
                    other_posts.append(post)
            
            # その他のポストをソート
            other_posts.sort(key=lambda p: getattr(p, '_trending_score', 0.0), reverse=True)
            
            # 固定ポストがあれば最初に、その後にその他のポスト
            if clipped_post:
                return [clipped_post] + other_posts
            return other_posts
        elif sort == 'score':
            # 固定ポストを最初に表示
            qs = qs.select_related('community', 'author', 'author__profile', 'tag', 'poll').prefetch_related(
                'media',
                Prefetch('poll__options', queryset=PollOption.objects.all().order_by('id'))
            )
            if clip_post_id:
                clipped_qs = qs.filter(id=clip_post_id)
                other_qs = qs.exclude(id=clip_post_id).order_by('-score', '-created_at')
                # 固定ポスト + その他のポスト
                from itertools import chain
                return list(chain(clipped_qs, other_qs))
            return qs.order_by('-score', '-created_at')
        elif sort == 'old':
            # 固定ポストを最初に表示
            qs = qs.select_related('community', 'author', 'author__profile', 'tag', 'poll').prefetch_related(
                'media',
                Prefetch('poll__options', queryset=PollOption.objects.all().order_by('id'))
            )
            if clip_post_id:
                clipped_qs = qs.filter(id=clip_post_id)
                other_qs = qs.exclude(id=clip_post_id).order_by('created_at')
                from itertools import chain
                return list(chain(clipped_qs, other_qs))
            return qs.order_by('created_at')
        else:  # 'new' or default
            # 固定ポストを最初に表示
            qs = qs.select_related('community', 'author', 'author__profile', 'tag', 'poll').prefetch_related(
                'media',
                Prefetch('poll__options', queryset=PollOption.objects.all().order_by('id'))
            )
            if clip_post_id:
                clipped_qs = qs.filter(id=clip_post_id)
                other_qs = qs.exclude(id=clip_post_id).order_by('-created_at')
                from itertools import chain
                return list(chain(clipped_qs, other_qs))
            return qs.order_by('-created_at')

    def get_permissions(self):
        # GET は常に許可（参加ポリシーは参加時にのみ適用）
        # POST はゲスト投稿を許可（詳細は perform_create で判定）
        if self.request.method in ('GET', 'POST'):
            return [permissions.AllowAny()]
        return super().get_permissions()

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return PostCreateSerializer
        return PostSerializer

    def get_serializer_context(self):
        return super().get_serializer_context()

    def create(self, request, *args, **kwargs):
        """投稿を作成し、mediaをprefetchして返す"""
        # リクエストデータをログに記録
        logger.info(f"Creating post request data: {request.data}")
        logger.info(f"media_urls in request: {request.data.get('media_urls', 'NOT FOUND')}")
        logger.info(f"post_type in request: {request.data.get('post_type', 'NOT FOUND')}")
        
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        # バリデーション後のデータをログに記録
        logger.info(f"Validated data: {serializer.validated_data}")
        logger.info(f"media_urls in validated_data: {serializer.validated_data.get('media_urls', 'NOT FOUND')}")
        
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        
        # 作成されたポストを取得してmediaをprefetch
        post = serializer.instance
        if post:
            # ログを追加
            logger.info(f"Post created: id={post.id}, post_type={post.post_type}")
            
            # mediaをprefetch
            post = Post.objects.select_related(
                'community', 'author', 'author__profile', 'tag', 'poll'
            ).prefetch_related(
                'media',
                Prefetch('poll__options', queryset=PollOption.objects.all().order_by('id'))
            ).get(pk=post.pk)
            
            # mediaの数を確認
            media_count = post.media.count() if hasattr(post, 'media') else 0
            logger.info(f"Post {post.id} has {media_count} media items after prefetch")
            
            # mediaの詳細をログに記録
            if media_count > 0:
                for media in post.media.all():
                    logger.info(f"  Media: id={media.id}, type={media.media_type}, url={media.url}")
            
            # シリアライザーでシリアライズ
            response_serializer = PostSerializer(post, context=self.get_serializer_context())
            response_data = response_serializer.data
            
            # レスポンスデータのmediaを確認
            media_in_response = response_data.get('media')
            logger.info(f"Media in response: {media_in_response}")
            
            # キャッシュを無効化
            from app.utils import invalidate_cache
            community = post.community
            invalidate_cache(pattern=f'/api/communities/{community.id}/posts/*')
            invalidate_cache(pattern='/api/posts/*')
            invalidate_cache(pattern='/api/posts/trending*')
            # メンバーシップが作成された場合、コミュニティ関連のキャッシュも削除
            if hasattr(post, '_membership_created') and post._membership_created:
                invalidate_cache(pattern='/api/communities/*')  # コミュニティ一覧（メンバー数変更のため）
                invalidate_cache(key=f'/api/communities/{community.id}/')
                invalidate_cache(pattern=f'/api/communities/{community.id}/members/*')
            
            return Response(response_data, status=status.HTTP_201_CREATED, headers=headers)
        
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def perform_create(self, serializer):
        community = get_object_or_404(Community, id=self.kwargs['id'])
        user = self.request.user if (self.request.user and self.request.user.is_authenticated) else None
        membership_created = False  # メンバーシップが作成されたかどうかのフラグ
        # Blocked login users may not post
        if user and CommunityBlock.objects.filter(community=community, user=user).exists():
            raise PermissionDenied('あなたはこのアノニウムにブロックされています。')
        if not user:
            # ゲストユーザーの場合
            # 投稿可否: OPEN のみ即時許可。それ以外は拒否
            if community.join_policy != Community.JoinPolicy.OPEN:
                raise PermissionDenied('このアノニウムはログインまたは承認が必要です。')
            # ゲストユーザーを取得または作成（IPアドレスも保存）
            user = get_or_create_guest_user(self.request, create_if_not_exists=True)
            if not user:
                raise PermissionDenied('ゲスト識別子がありません。')
            # メンバーシップが無ければ付与（APPROVED）
            existed = CM.objects.filter(community=community, user=user).first()
            if not existed:
                CM.objects.create(community=community, user=user, role=CM.Role.MEMBER, status=CM.Status.APPROVED)
                Community.objects.filter(pk=community.pk).update(members_count=F('members_count') + 1)
                membership_created = True
        else:
            # ログインユーザーの場合：参加状態をチェック
            membership = CM.objects.filter(
                community=community,
                user=user,
                status=CM.Status.APPROVED
            ).first()
            if not membership:
                # 参加していない場合
                if community.join_policy == Community.JoinPolicy.OPEN:
                    # OPENポリシーの場合は自動的にメンバーシップを作成
                    CM.objects.create(community=community, user=user, role=CM.Role.MEMBER, status=CM.Status.APPROVED)
                    Community.objects.filter(pk=community.pk).update(members_count=F('members_count') + 1)
                    membership_created = True
                else:
                    # それ以外のポリシーは拒否
                    raise PermissionDenied('このアノニウムに参加していないため、投稿できません。')

        # Blocked guest users may not post (after guest resolution)
        if user and CommunityBlock.objects.filter(community=community, user=user).exists():
            raise PermissionDenied('あなたはこのアノニウムにブロックされています。')
        
        # IPアドレスを取得して保存
        client_ip = get_client_ip(self.request)
        post = serializer.save(community=community, author=user, created_ip=client_ip)
        # メンバーシップが作成された場合のフラグをpostオブジェクトに保存（後でキャッシュ削除時に使用）
        post._membership_created = membership_created


class PostListView(generics.ListAPIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = PostSerializer

    def get_serializer_context(self):
        return super().get_serializer_context()
    
    def get_queryset(self):
        # 非公開コミュニティを除外
        from communities.models import Community
        public_communities = Community.objects.filter(
            visibility=Community.Visibility.PUBLIC
        ).values_list('id', flat=True)
        qs = Post.objects.filter(
            is_deleted=False,
            community_id__in=public_communities
        ).select_related('community', 'author', 'author__profile', 'tag', 'poll').prefetch_related(
            'media',
            Prefetch('poll__options', queryset=PollOption.objects.all().order_by('id'))
        )
        user = getattr(self.request, 'user', None)
        if user and getattr(user, 'is_authenticated', False):
            muted_ids = list(UserMute.objects.filter(user=user).values_list('target_id', flat=True))
            if muted_ids:
                qs = qs.exclude(author_id__in=muted_ids)
        return qs.order_by('-created_at')


class TrendingPostListView(generics.GenericAPIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = PostSerializer

    DEFAULT_LIMIT = 20
    MAX_LIMIT = 50
    SAMPLE_SIZE = 200

    def get_serializer_context(self):
        return super().get_serializer_context()

    def get(self, request, *args, **kwargs):
        try:
            limit = int(request.query_params.get('limit', self.DEFAULT_LIMIT))
        except (TypeError, ValueError):
            limit = self.DEFAULT_LIMIT
        limit = max(1, min(limit, self.MAX_LIMIT))

        try:
            half_life = float(request.query_params.get('half_life_hours', 6))
        except (TypeError, ValueError):
            half_life = 6.0
        if half_life <= 0:
            half_life = 6.0

        try:
            lookback_hours = float(request.query_params.get('lookback_hours', 168))
        except (TypeError, ValueError):
            lookback_hours = 168.0
        lookback_hours = max(0.0, lookback_hours)

        now = timezone.now()
        qs = Post.objects.filter(
            is_deleted=False,
            community__visibility=Community.Visibility.PUBLIC,
        ).annotate(
            active_comments=Count('comments', filter=Q(comments__is_deleted=False))
        ).select_related('community', 'author', 'author__profile', 'tag', 'poll').prefetch_related(
            'media',
            Prefetch('poll__options', queryset=PollOption.objects.all().order_by('id'))
        )

        if lookback_hours > 0:
            qs = qs.filter(created_at__gte=now - timedelta(hours=lookback_hours))

        user = getattr(request, 'user', None)
        if user and getattr(user, 'is_authenticated', False):
            muted_ids = list(UserMute.objects.filter(user=user).values_list('target_id', flat=True))
            if muted_ids:
                qs = qs.exclude(author_id__in=muted_ids)

        posts = list(qs.order_by('-created_at')[:self.SAMPLE_SIZE])

        scored_posts = []
        for post in posts:
            upvotes = max(int((post.votes_total + post.score) / 2), 0)
            downvotes = max(post.votes_total - upvotes, 0)
            comment_count = getattr(post, 'active_comments', 0)
            trending = calculate_trending_score(
                upvotes,
                downvotes,
                post.created_at,
                comment_count=comment_count,
                now=now,
                half_life_hours=half_life,
            )
            post._trending_score = trending
            scored_posts.append((trending, post))

        scored_posts.sort(key=lambda item: item[0], reverse=True)
        top_posts = [p for _, p in scored_posts[:limit]]

        serializer = self.get_serializer(top_posts, many=True, context=self.get_serializer_context())
        return Response(serializer.data)


class MeCommunitiesPostsView(generics.ListAPIView):
    """ログインユーザーまたはゲストユーザーが参加しているコミュニティの投稿を取得"""
    serializer_class = PostSerializer
    permission_classes = [permissions.AllowAny]

    def _resolve_guest_user(self, request):
        """ゲストユーザーを解決（既存のみ、新規作成はしない）"""
        return get_or_create_guest_user(request, create_if_not_exists=False)

    def get_queryset(self):
        # ユーザーを取得（認証済みユーザーまたはゲストユーザー）
        user = self.request.user if (self.request.user and self.request.user.is_authenticated) else None
        if not user:
            user = self._resolve_guest_user(self.request)
        if not user:
            # ユーザーが特定できない場合は空のクエリセットを返す
            return Post.objects.none()
        
        # ユーザーが参加しているコミュニティ（承認済み）を取得
        memberships = CM.objects.filter(
            user=user,
            status=CM.Status.APPROVED
        ).values_list('community_id', flat=True)
        # それらのコミュニティの投稿のみを返す
        qs = Post.objects.filter(
            community_id__in=memberships,
            is_deleted=False
        ).select_related('community', 'author', 'author__profile', 'tag', 'poll').prefetch_related(
            'media',
            Prefetch('poll__options', queryset=PollOption.objects.all().order_by('id'))
        )
        muted_ids = list(UserMute.objects.filter(user=user).values_list('target_id', flat=True))
        if muted_ids:
            qs = qs.exclude(author_id__in=muted_ids)
        return qs.order_by('-created_at')


class PostDetailView(generics.RetrieveDestroyAPIView):
    queryset = Post.objects.select_related('community', 'author', 'author__profile', 'tag', 'poll').prefetch_related(
        'media',
        Prefetch('poll__options', queryset=PollOption.objects.all().order_by('id'))
    )
    permission_classes = [permissions.AllowAny]
    serializer_class = PostSerializer

    def get_serializer_context(self):
        return super().get_serializer_context()

    def _resolve_guest_user(self, request):
        """ゲストユーザーを解決（作成はしない、IPアドレスは保存しない）"""
        if request.user and request.user.is_authenticated:
            return request.user
        # 既存ユーザーのみ取得（新規作成はしない）
        return get_or_create_guest_user(request, create_if_not_exists=False)

    def get_object(self):
        """ポストを取得（非公開情報はシリアライザーでフィルタリング）"""
        return super().get_object()

    def delete(self, request, *args, **kwargs):
        post = get_object_or_404(Post, pk=kwargs.get('pk'))
        # Author can delete own post (unless blocked); otherwise OWNER or ADMIN_MODERATOR can delete
        if not request.user or not request.user.is_authenticated:
            raise PermissionDenied('権限がありません。')
        if request.user == post.author:
            # 投稿者本人の場合: ブロックされている場合は削除不可
            if CommunityBlock.objects.filter(community=post.community, user=request.user).exists():
                raise PermissionDenied('あなたはこのアノニウムにブロックされているため、投稿を削除できません。')
        else:
            # 他ユーザーの場合: オーナーまたは管理モデレーターのみ削除可能
            membership = CM.objects.filter(
                community=post.community,
                user=request.user,
                status=CM.Status.APPROVED,
            ).first()
            if not membership or membership.role not in (CM.Role.OWNER, CM.Role.ADMIN_MODERATOR):
                raise PermissionDenied('権限がありません。')
        # soft delete
        post.is_deleted = True
        post.deleted_at = timezone.now()
        post.deleted_by = request.user
        post.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by', 'updated_at'])
        
        # キャッシュ削除
        from app.utils import invalidate_cache
        invalidate_cache(key=f'/api/posts/{post.id}/')
        invalidate_cache(pattern='/api/posts/*')
        invalidate_cache(pattern='/api/posts/trending*')
        invalidate_cache(pattern=f'/api/communities/{post.community.id}/posts/*')
        
        return Response(status=status.HTTP_204_NO_CONTENT)

    def patch(self, request, *args, **kwargs):
        post = get_object_or_404(Post, pk=kwargs.get('pk'))
        if not request.user or not request.user.is_authenticated:
            raise PermissionDenied('権限がありません。')
        # Author can edit own post unless blocked
        if request.user != post.author:
            raise PermissionDenied('権限がありません。')
        if CommunityBlock.objects.filter(community=post.community, user=request.user).exists():
            raise PermissionDenied('あなたはこのアノニウムにブロックされているため、編集できません。')

        title = request.data.get('title')
        body = request.data.get('body')
        updates = {}
        # minimal validation (align with serializers)
        if title is not None:
            title = str(title)
            if len(title) > 200:
                return Response({'title': 'タイトルが長すぎます（最大200文字）'}, status=status.HTTP_400_BAD_REQUEST)
            updates['title'] = title
        if body is not None:
            body = str(body).replace('\r\n', '\n').replace('\r', '\n')
            max_len = 20000 if post.post_type == Post.PostType.TEXT else 20000
            if len(body) > max_len:
                return Response({'body': f'本文が長すぎます（最大{max_len}文字）'}, status=status.HTTP_400_BAD_REQUEST)
            updates['body'] = body

        if not updates:
            return Response({'detail': '変更内容がありません。'}, status=status.HTTP_400_BAD_REQUEST)

        # apply updates and set is_edited
        for k, v in updates.items():
            setattr(post, k, v)
        post.is_edited = True
        post.save(update_fields=[*updates.keys(), 'is_edited', 'updated_at'])
        
        # キャッシュ削除
        from app.utils import invalidate_cache
        invalidate_cache(key=f'/api/posts/{post.id}/')
        invalidate_cache(pattern='/api/posts/*')
        invalidate_cache(pattern='/api/posts/trending*')
        invalidate_cache(pattern=f'/api/communities/{post.community.id}/posts/*')
        
        return Response(PostSerializer(post, context={'request': request}).data)


class PostVoteView(generics.GenericAPIView):
    def get_permissions(self):
        # ゲストユーザーも許可（スコアチェックはpostメソッド内で実施）
        return [permissions.AllowAny()]

    serializer_class = PostSerializer

    def get_serializer_context(self):
        return super().get_serializer_context()

    def _resolve_guest_user(self, request):
        """ゲストユーザーを解決（作成はしない、IPアドレスは保存しない）"""
        if request.user and request.user.is_authenticated:
            return request.user
        # 既存ユーザーのみ取得（新規作成はしない）
        return get_or_create_guest_user(request, create_if_not_exists=False)

    def get(self, request, pk: int):
        """投票状態を取得"""
        post = get_object_or_404(Post, pk=pk)
        user = self._resolve_guest_user(request)
        if not user:
            return Response({'user_vote': None})
        
        vote = PostVote.objects.filter(post=post, user=user).first()
        user_vote = vote.value if vote else None
        return Response({'user_vote': user_vote})

    def post(self, request, pk: int):
        post = get_object_or_404(Post, pk=pk)
        user = self._resolve_guest_user(request)
        if not user:
            raise PermissionDenied('ユーザーを特定できません。')
        
        community = post.community
        
        # ログインユーザーの場合、メンバーシップチェック
        if request.user and request.user.is_authenticated:
            membership = CM.objects.filter(
                community=community,
                user=user,
                status=CM.Status.APPROVED
            ).first()
            if not membership:
                raise PermissionDenied('この投稿に投票するには、アノニウムに参加する必要があります。')
        else:
            # ゲストユーザーの場合、メンバーシップチェックとスコアチェック
            # まず、メンバーシップをチェック
            membership = CM.objects.filter(
                community=community,
                user=user,
                status=CM.Status.APPROVED
            ).first()
            if not membership:
                raise PermissionDenied('この投稿に投票するには、アノニウムに参加する必要があります。')
            # メンバーシップがある場合、スコアチェック
            user_profile, _ = UserProfile.objects.get_or_create(user=user)
            if user_profile.score < community.karma:
                raise PermissionDenied(f'このアノニウムで投票するには、スコア{community.karma}以上が必要です（現在のスコア: {user_profile.score}）。')
        
        value_raw = request.data.get('value')
        value: int
        if value_raw in ('good', '+', 1, '1'):
            value = PostVote.Value.UP
        elif value_raw in ('bad', '-', -1, '-1'):
            value = PostVote.Value.DOWN
        else:
            return Response({'detail': 'value must be good/bad or +/-1'}, status=status.HTTP_400_BAD_REQUEST)

        existing = PostVote.objects.filter(post=post, user=user).first()
        if existing and existing.value == value:
            # Toggle off (remove vote)
            existing.delete()
            Post.objects.filter(pk=post.pk).update(
                score=F('score') - value,
                votes_total=F('votes_total') - 1,
            )
            post.refresh_from_db(fields=['score', 'votes_total'])
            # 著者のスコアを減算（自己投票はスコア変動なし、ゲストユーザーの投票もスコア変動なし）
            if user != post.author and request.user and request.user.is_authenticated:
                author_profile, _ = UserProfile.objects.get_or_create(user=post.author)
                UserProfile.objects.filter(pk=author_profile.pk).update(score=F('score') - value)
            user_vote = None
        else:
            delta = value
            created = False
            if existing:
                # Switch vote
                delta = value - existing.value
                existing.value = value
                existing.save(update_fields=['value', 'updated_at'])
            else:
                PostVote.objects.create(post=post, user=user, value=value)
                created = True
            update_kwargs = {'score': F('score') + delta}
            if created:
                update_kwargs['votes_total'] = F('votes_total') + 1
            Post.objects.filter(pk=post.pk).update(**update_kwargs)
            post.refresh_from_db(fields=['score', 'votes_total'])
            # 著者のスコアを加算（自己投票はスコア変動なし、ゲストユーザーの投票もスコア変動なし）
            if user != post.author and request.user and request.user.is_authenticated:
                author_profile, _ = UserProfile.objects.get_or_create(user=post.author)
                UserProfile.objects.filter(pk=author_profile.pk).update(score=F('score') + delta)
            user_vote = int(value)

        # キャッシュ削除
        from app.utils import invalidate_cache
        invalidate_cache(key=f'/api/posts/{post.id}/')
        invalidate_cache(pattern='/api/posts/*')
        invalidate_cache(pattern='/api/posts/trending*')
        invalidate_cache(pattern=f'/api/communities/{post.community.id}/posts/*')
        # 著者のスコアが変動した場合はユーザープロフィールのキャッシュも削除
        if user != post.author and request.user and request.user.is_authenticated:
            invalidate_cache(pattern=f'/api/accounts/{post.author.username}/*')

        return Response({'score': post.score, 'votes_total': post.votes_total, 'user_vote': user_vote})


class PostFollowView(generics.GenericAPIView):
    def get_permissions(self):
        # GETは誰でも許可（認証不要）、POSTは認証が必要
        if self.request.method == 'GET':
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated()]

    serializer_class = PostSerializer

    def get_serializer_context(self):
        return super().get_serializer_context()

    def _resolve_guest_user(self, request):
        """ゲストユーザーを解決（作成はしない、IPアドレスは保存しない）"""
        if request.user and request.user.is_authenticated:
            return request.user
        # 既存ユーザーのみ取得（新規作成はしない）
        return get_or_create_guest_user(request, create_if_not_exists=False)

    def get(self, request, pk: int):
        """フォロー状態を取得"""
        post = get_object_or_404(Post, pk=pk)
        user = self._resolve_guest_user(request)
        if not user:
            return Response({'is_following': False})
        
        is_following = PostFollow.objects.filter(post=post, user=user).exists()
        return Response({'is_following': is_following})

    def post(self, request, pk: int):
        post = get_object_or_404(Post, pk=pk)
        existing = PostFollow.objects.filter(post=post, user=request.user).first()
        
        if existing:
            # アンフォロー（削除）
            existing.delete()
            is_following = False
        else:
            # フォロー（作成）
            PostFollow.objects.create(post=post, user=request.user)
            is_following = True
        
        serializer = self.get_serializer(post, context=self.get_serializer_context())
        return Response({'is_following': is_following, **serializer.data})


class CommentListCreateView(generics.ListCreateAPIView):
    serializer_class = CommentSerializer

    def get_permissions(self):
        # GET/POST は常に許可（参加ポリシーは参加時にのみ適用）
        return [permissions.AllowAny()]

    def get_queryset(self):
        post = get_object_or_404(Post, pk=self.kwargs['pk'])
        qs = Comment.objects.filter(post=post)
        # 親コメントのみを取得する場合（クエリパラメータで指定）
        parent_isnull = self.request.query_params.get('parent__isnull', '').lower()
        if parent_isnull in ('true', '1'):
            qs = qs.filter(parent__isnull=True)
        
        # ミュートフィルタをスキップするかどうか（デフォルト: false）
        skip_mute_filter = self.request.query_params.get('skip_mute_filter', 'false').lower() in ('true', '1')
        
        if not skip_mute_filter:
            user = getattr(self.request, 'user', None)
            if user and getattr(user, 'is_authenticated', False):
                muted_ids = list(UserMute.objects.filter(user=user).values_list('target_id', flat=True))
                if muted_ids:
                    # 1) ミュートユーザー本人のコメントを起点として取得
                    to_hide_ids = list(Comment.objects.filter(post=post, author_id__in=muted_ids).values_list('id', flat=True))
                    # 2) それらの子孫コメントも全て除外（BFS）
                    frontier = list(to_hide_ids)
                    while frontier:
                        children = list(Comment.objects.filter(post=post, parent_id__in=frontier).values_list('id', flat=True))
                        if not children:
                            break
                        to_hide_ids.extend(children)
                        frontier = children
                    if to_hide_ids:
                        qs = qs.exclude(id__in=to_hide_ids)
        return qs.order_by('created_at')

    def _resolve_guest_user(self, request):
        """ゲストユーザーを解決（作成はしない、IPアドレスは保存しない）"""
        if request.user and request.user.is_authenticated:
            return request.user
        # 既存ユーザーのみ取得（新規作成はしない）
        return get_or_create_guest_user(request, create_if_not_exists=False)

    def list(self, request, *args, **kwargs):
        post = get_object_or_404(Post, pk=self.kwargs['pk'])
        
        # ソート順を取得（デフォルト: popular）
        sort = request.query_params.get('sort', 'popular').lower()
        if sort not in ('popular', 'new', 'old'):
            sort = 'popular'
        
        # 親コメントの取得件数（デフォルト: 20件）
        try:
            parent_limit = int(request.query_params.get('limit', '20'))
            parent_limit = max(1, min(parent_limit, 100))  # 1-100件の範囲
        except (ValueError, TypeError):
            parent_limit = 20
        
        # 削除されたコメントを含めるかどうか（デフォルト: false）
        include_deleted = request.query_params.get('include_deleted', 'false').lower() in ('true', '1')
        
        # ミュートフィルタをスキップするかどうか（デフォルト: false）
        # skip_mute_filter=trueの場合、キャッシュ可能なデータを返すためにミュートフィルタを適用しない
        skip_mute_filter = request.query_params.get('skip_mute_filter', 'false').lower() in ('true', '1')
        
        # ユーザー情報とミュートユーザーIDを取得
        user = getattr(request, 'user', None)
        muted_ids = []
        if not skip_mute_filter and user and getattr(user, 'is_authenticated', False):
            muted_ids = list(UserMute.objects.filter(user=user).values_list('target_id', flat=True))
        
        # ミュートユーザーのコメントとその子孫を除外するIDを取得
        to_hide_ids = set()
        if not skip_mute_filter and muted_ids:
            muted_comment_ids = list(Comment.objects.filter(post=post, author_id__in=muted_ids).values_list('id', flat=True))
            to_hide_ids.update(muted_comment_ids)
            # BFSで子孫コメントも除外
            frontier = list(muted_comment_ids)
            while frontier:
                children = list(Comment.objects.filter(post=post, parent_id__in=frontier).values_list('id', flat=True))
                if not children:
                    break
                to_hide_ids.update(children)
                frontier = children
        
        # 親コメントを取得
        parent_qs = Comment.objects.filter(
            post=post,
            parent__isnull=True
        ).select_related('author', 'author__profile', 'community', 'post').prefetch_related('media')
        
        # 削除されたコメントを除外（include_deletedがfalseの場合）
        if not include_deleted:
            parent_qs = parent_qs.filter(is_deleted=False)
        
        if to_hide_ids:
            parent_qs = parent_qs.exclude(id__in=to_hide_ids)
        
        # ソート順に応じて並び替え
        if sort == 'popular':
            parent_qs = parent_qs.order_by('is_deleted', '-score', '-created_at')
        elif sort == 'new':
            parent_qs = parent_qs.order_by('is_deleted', '-created_at')
        else:  # old
            parent_qs = parent_qs.order_by('is_deleted', 'created_at')
        
        # 親コメントを制限件数まで取得
        parent_comments = list(parent_qs[:parent_limit])
        
        if not parent_comments:
            return Response([])
        
        # 親コメントのIDを取得
        parent_ids = [c.id for c in parent_comments]
        
        # 各親コメントの直接の子コメントを取得（1階層目）
        direct_children_qs = Comment.objects.filter(
            post=post,
            parent_id__in=parent_ids
        ).select_related('author', 'author__profile', 'community', 'post').prefetch_related('media')
        
        # 削除されたコメントを除外（include_deletedがfalseの場合）
        if not include_deleted:
            direct_children_qs = direct_children_qs.filter(is_deleted=False)
        
        if to_hide_ids:
            direct_children_qs = direct_children_qs.exclude(id__in=to_hide_ids)
        
        # ソート順に応じて並び替え
        if sort == 'popular':
            direct_children_qs = direct_children_qs.order_by('is_deleted', '-score', '-created_at')
        elif sort == 'new':
            direct_children_qs = direct_children_qs.order_by('is_deleted', '-created_at')
        else:  # old
            direct_children_qs = direct_children_qs.order_by('is_deleted', 'created_at')
        
        direct_children_list = list(direct_children_qs)
        child_ids = [c.id for c in direct_children_list]
        
        # 各子コメントの直接の孫コメントを取得（2階層目）
        grandchild_list = []
        if child_ids:
            grandchild_qs = Comment.objects.filter(
                post=post,
                parent_id__in=child_ids
            ).select_related('author', 'author__profile', 'community', 'post').prefetch_related('media')
            
            # 削除されたコメントを除外（include_deletedがfalseの場合）
            if not include_deleted:
                grandchild_qs = grandchild_qs.filter(is_deleted=False)
            
            if to_hide_ids:
                grandchild_qs = grandchild_qs.exclude(id__in=to_hide_ids)
            
            # ソート順に応じて並び替え（削除されたコメントは末尾に）
            if sort == 'popular':
                grandchild_qs = grandchild_qs.order_by('is_deleted', '-score', '-created_at')
            elif sort == 'new':
                grandchild_qs = grandchild_qs.order_by('is_deleted', '-created_at')
            else:  # old
                grandchild_qs = grandchild_qs.order_by('is_deleted', 'created_at')
            
            grandchild_list = list(grandchild_qs)
        
        # 親IDごとに子コメントをグループ化
        children_by_parent = {}
        for child in direct_children_list:
            parent_id = child.parent_id
            if parent_id not in children_by_parent:
                children_by_parent[parent_id] = []
            children_by_parent[parent_id].append(child)
        
        # 子IDごとに孫コメントをグループ化
        grandchildren_by_child = {}
        for grandchild in grandchild_list:
            parent_id = grandchild.parent_id
            if parent_id not in grandchildren_by_child:
                grandchildren_by_child[parent_id] = []
            grandchildren_by_child[parent_id].append(grandchild)
        
        # 各親の子コメント総数を取得（ミュート除外前の実際のリプ数）
        children_count_by_parent = {}
        for parent_id in parent_ids:
            count_qs = Comment.objects.filter(
                post=post,
                parent_id=parent_id
            )
            # 削除されたコメントを除外（include_deletedがfalseの場合）
            if not include_deleted:
                count_qs = count_qs.filter(is_deleted=False)
            children_count_by_parent[parent_id] = count_qs.count()
        
        # 各子コメントの孫コメント総数を取得（ミュート除外前の実際のリプ数）
        grandchildren_count_by_child = {}
        for child_id in child_ids:
            count_qs = Comment.objects.filter(
                post=post,
                parent_id=child_id
            )
            # 削除されたコメントを除外（include_deletedがfalseの場合）
            if not include_deleted:
                count_qs = count_qs.filter(is_deleted=False)
            grandchildren_count_by_child[child_id] = count_qs.count()
        
        # 各孫コメントの子コメント総数を取得（ミュート除外前の実際のリプ数）
        # 孫コメントにさらに子があるかを確認するため
        grandchild_ids = [gc.id for gc in grandchild_list]
        great_grandchildren_count_by_grandchild = {}
        if grandchild_ids:
            for grandchild_id in grandchild_ids:
                count_qs = Comment.objects.filter(
                    post=post,
                    parent_id=grandchild_id
                )
                # 削除されたコメントを除外（include_deletedがfalseの場合）
                if not include_deleted:
                    count_qs = count_qs.filter(is_deleted=False)
                great_grandchildren_count_by_grandchild[grandchild_id] = count_qs.count()
        
        # 各親コメントに子コメントを設定（再帰的に孫コメントも含める）
        for parent_comment in parent_comments:
            children_list = children_by_parent.get(parent_comment.id, [])
            # 各子コメントに孫コメントを設定
            for child in children_list:
                grandchildren_list = grandchildren_by_child.get(child.id, [])
                # 各孫コメントに子コメント数とhas_more_childrenを設定
                for grandchild in grandchildren_list:
                    great_grandchildren_count = great_grandchildren_count_by_grandchild.get(grandchild.id, 0)
                    setattr(grandchild, '_children_count', great_grandchildren_count)
                    setattr(grandchild, '_has_more_children', great_grandchildren_count > 0)
                    setattr(grandchild, '_prefetched_children', [])  # 孫コメントの子は初期取得しない
                
                # 子コメントに孫コメントを設定
                setattr(child, '_prefetched_children', grandchildren_list)
                # 子コメントの子コメント数とhas_more_childrenを設定
                grandchildren_count = grandchildren_count_by_child.get(child.id, 0)
                setattr(child, '_children_count', grandchildren_count)
                # 孫コメントが取得済みでも、さらに子がある場合はhas_more_children=True
                setattr(child, '_has_more_children', grandchildren_count > len(grandchildren_list))
            
            # 親コメントに子コメントを設定
            setattr(parent_comment, '_prefetched_children', children_list)
            # 親コメントの子コメント数とhas_more_childrenを設定
            total_count = children_count_by_parent.get(parent_comment.id, 0)
            setattr(parent_comment, '_children_count', total_count)
            # 子コメントが取得済みでも、さらに子がある場合はhas_more_children=True
            setattr(parent_comment, '_has_more_children', total_count > len(children_list))
        
        # シリアライザーのコンテキストを準備
        serializer_context = {
            'request': request,
            'comment_children': children_by_parent,
            'comment_children_count': {**children_count_by_parent, **grandchildren_count_by_child, **great_grandchildren_count_by_grandchild},
            'comment_has_more': {
                parent_id: count > len(children_by_parent.get(parent_id, []))
                for parent_id, count in children_count_by_parent.items()
            }
        }
        
        # シリアライズ
        serializer = self.get_serializer(parent_comments, many=True, context=serializer_context)
        return Response(serializer.data)

    def perform_create(self, serializer):
        post = get_object_or_404(Post, pk=self.kwargs['pk'])
        parent_id = self.request.data.get('parent')
        parent = None
        if parent_id:
            parent = get_object_or_404(Comment, pk=parent_id, post=post)
        # ゲスト許可: コミュニティが OPEN のときのみ
        community = post.community
        user = self.request.user if (self.request.user and self.request.user.is_authenticated) else None
        membership_created = False  # メンバーシップが作成されたかどうかのフラグ
        # Blocked login users may not comment
        if user and CommunityBlock.objects.filter(community=community, user=user).exists():
            raise PermissionDenied('あなたはこのアノニウムにブロックされています。')
        if not user:
            # ゲストユーザーの場合
            if community.join_policy != Community.JoinPolicy.OPEN:
                raise PermissionDenied('このアノニウムはログインまたは承認が必要です。')
            # ゲストユーザーを取得または作成（IPアドレスも保存）
            user = get_or_create_guest_user(self.request, create_if_not_exists=True)
            if not user:
                raise PermissionDenied('ゲスト識別子がありません。')
            existed = CM.objects.filter(community=community, user=user).first()
            if not existed:
                CM.objects.create(community=community, user=user, role=CM.Role.MEMBER, status=CM.Status.APPROVED)
                Community.objects.filter(pk=community.pk).update(members_count=F('members_count') + 1)
                membership_created = True
        else:
            # ログインユーザーの場合：参加状態をチェック
            membership = CM.objects.filter(
                community=community,
                user=user,
                status=CM.Status.APPROVED
            ).first()
            if not membership:
                # 参加していない場合
                if community.join_policy == Community.JoinPolicy.OPEN:
                    # OPENポリシーの場合は自動的にメンバーシップを作成
                    CM.objects.create(community=community, user=user, role=CM.Role.MEMBER, status=CM.Status.APPROVED)
                    Community.objects.filter(pk=community.pk).update(members_count=F('members_count') + 1)
                    membership_created = True
                else:
                    # それ以外のポリシーは拒否
                    raise PermissionDenied('このアノニウムに参加していないため、コメントできません。')
        # Blocked guest users may not comment (after guest resolution)
        if user and CommunityBlock.objects.filter(community=community, user=user).exists():
            raise PermissionDenied('あなたはこのアノニウムにブロックされています。')
        
        # IPアドレスを取得して保存
        client_ip = get_client_ip(self.request)
        # コミュニティ列も付与
        comment = serializer.save(post=post, community=community, author=user, parent=parent, created_ip=client_ip)
        # メンバーシップが作成された場合のフラグをcommentオブジェクトに保存（後でキャッシュ削除時に使用）
        comment._membership_created = membership_created
        
        # 通知を作成
        notifications_to_create = []
        notified_user_ids = set()
        
        # 1. ポストに直接コメントがついた場合: ポスト作成者に通知
        if not parent and post.author_id != user.id:
            notifications_to_create.append(
                Notification(
                    recipient=post.author,
                    notification_type=Notification.NotificationType.POST_COMMENT,
                    actor=user,
                    post=post,
                    comment=comment,
                    community=community,
                )
            )
            notified_user_ids.add(post.author_id)
        
        # 2. コメントに返信がついた場合: 親コメントの作成者に通知
        # ただし、親コメントの作成者が返信元のコメントの作成者をミュートしている場合は通知しない
        elif parent and parent.author_id != user.id:
            # 親コメントの作成者が返信元のコメントの作成者をミュートしていないかチェック
            is_muted = UserMute.objects.filter(
                user=parent.author,
                target=user
            ).exists()
            
            if not is_muted:
                notifications_to_create.append(
                    Notification(
                        recipient=parent.author,
                        notification_type=Notification.NotificationType.COMMENT_REPLY,
                        actor=user,
                        post=post,
                        comment=comment,
                        community=community,
                    )
                )
                notified_user_ids.add(parent.author_id)
        
        # 3. ポストをフォローしているユーザーに通知（自分自身、既に通知を送ったユーザーを除く）
        post_followers = PostFollow.objects.filter(post=post).exclude(user_id=user.id)
        # 既に通知を送ったユーザーを除外
        if notified_user_ids:
            post_followers = post_followers.exclude(user_id__in=notified_user_ids)
        
        for follower in post_followers:
            notifications_to_create.append(
                Notification(
                    recipient=follower.user,
                    notification_type=Notification.NotificationType.FOLLOWED_POST_COMMENT,
                    actor=user,
                    post=post,
                    comment=comment,
                    community=community,
                )
            )
        
        # バルクインサートで通知を作成
        if notifications_to_create:
            Notification.objects.bulk_create(notifications_to_create)
        
        # キャッシュ削除: コメント一覧、投稿詳細、投稿一覧、トレンド投稿一覧
        from app.utils import invalidate_cache
        invalidate_cache(key=f'/api/posts/{post.id}/')
        invalidate_cache(key=f'/api/posts/{post.id}/comments/')
        invalidate_cache(pattern=f'/api/communities/{community.id}/posts/*')
        invalidate_cache(pattern='/api/posts/*')
        invalidate_cache(pattern='/api/posts/trending*')
        # メンバーシップが作成された場合、コミュニティ関連のキャッシュも削除
        if hasattr(comment, '_membership_created') and comment._membership_created:
            invalidate_cache(pattern='/api/communities/*')  # コミュニティ一覧（メンバー数変更のため）
            invalidate_cache(key=f'/api/communities/{community.id}/')
            invalidate_cache(pattern=f'/api/communities/{community.id}/members/*')


class CommentVoteView(generics.GenericAPIView):
    def get_permissions(self):
        # ゲストユーザーも許可（スコアチェックはpostメソッド内で実施）
        return [permissions.AllowAny()]

    serializer_class = CommentSerializer

    def _resolve_guest_user(self, request):
        """ゲストユーザーを解決（作成はしない、IPアドレスは保存しない）"""
        if request.user and request.user.is_authenticated:
            return request.user
        # 既存ユーザーのみ取得（新規作成はしない）
        return get_or_create_guest_user(request, create_if_not_exists=False)

    def post(self, request, pk: int):
        comment = get_object_or_404(Comment, pk=pk)
        user = self._resolve_guest_user(request)
        if not user:
            raise PermissionDenied('ユーザーを特定できません。')
        
        community = comment.community
        
        # ログインユーザーの場合、メンバーシップチェック
        if request.user and request.user.is_authenticated:
            membership = CM.objects.filter(
                community=community,
                user=user,
                status=CM.Status.APPROVED
            ).first()
            if not membership:
                raise PermissionDenied('このコメントに投票するには、アノニウムに参加する必要があります。')
        else:
            # ゲストユーザーの場合、メンバーシップチェックとスコアチェック
            # まず、メンバーシップをチェック
            membership = CM.objects.filter(
                community=community,
                user=user,
                status=CM.Status.APPROVED
            ).first()
            if not membership:
                raise PermissionDenied('このコメントに投票するには、アノニウムに参加する必要があります。')
            # メンバーシップがある場合、スコアチェック
            user_profile, _ = UserProfile.objects.get_or_create(user=user)
            if user_profile.score < community.karma:
                raise PermissionDenied(f'このアノニウムで投票するには、スコア{community.karma}以上が必要です（現在のスコア: {user_profile.score}）。')
        
        value_raw = request.data.get('value')
        if value_raw in ('good', '+', 1, '1'):
            value = CommentVote.Value.UP
        elif value_raw in ('bad', '-', -1, '-1'):
            value = CommentVote.Value.DOWN
        else:
            return Response({'detail': 'value must be good/bad or +/-1'}, status=status.HTTP_400_BAD_REQUEST)

        existing = CommentVote.objects.filter(comment=comment, user=user).first()
        if existing and existing.value == value:
            existing.delete()
            Comment.objects.filter(pk=comment.pk).update(
                score=F('score') - value,
                votes_total=F('votes_total') - 1,
            )
            comment.refresh_from_db(fields=['score', 'votes_total'])
            # 著者のスコアを減算（自己投票はスコア変動なし、ゲストユーザーの投票もスコア変動なし）
            if user != comment.author and request.user and request.user.is_authenticated:
                author_profile, _ = UserProfile.objects.get_or_create(user=comment.author)
                UserProfile.objects.filter(pk=author_profile.pk).update(score=F('score') - value)
            user_vote = None
        else:
            delta = value
            created = False
            if existing:
                delta = value - existing.value
                existing.value = value
                existing.save(update_fields=['value', 'updated_at'])
            else:
                CommentVote.objects.create(comment=comment, user=user, value=value)
                created = True
            update_kwargs = {'score': F('score') + delta}
            if created:
                update_kwargs['votes_total'] = F('votes_total') + 1
            Comment.objects.filter(pk=comment.pk).update(**update_kwargs)
            comment.refresh_from_db(fields=['score', 'votes_total'])
            # 著者のスコアを加算（自己投票はスコア変動なし、ゲストユーザーの投票もスコア変動なし）
            if user != comment.author and request.user and request.user.is_authenticated:
                author_profile, _ = UserProfile.objects.get_or_create(user=comment.author)
                UserProfile.objects.filter(pk=author_profile.pk).update(score=F('score') + delta)
            user_vote = int(value)

        # キャッシュ削除
        from app.utils import invalidate_cache
        post = comment.post
        invalidate_cache(key=f'/api/comments/{comment.id}/')
        invalidate_cache(key=f'/api/posts/{post.id}/')
        invalidate_cache(key=f'/api/posts/{post.id}/comments/')
        invalidate_cache(pattern=f'/api/communities/{post.community.id}/posts/*')
        invalidate_cache(pattern='/api/posts/*')
        invalidate_cache(pattern='/api/posts/trending*')
        # 著者のスコアが変動した場合はユーザープロフィールのキャッシュも削除
        if user != comment.author and request.user and request.user.is_authenticated:
            invalidate_cache(pattern=f'/api/accounts/{comment.author.username}/*')

        return Response({'score': comment.score, 'votes_total': comment.votes_total, 'user_vote': user_vote})


class CommentImageUploadView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk: int):
        # Validate post exists
        get_object_or_404(Post, pk=pk)

        file = request.FILES.get('image')
        if not file:
            return Response({'detail': 'image file required'}, status=status.HTTP_400_BAD_REQUEST)

        # Try open image
        try:
            image = Image.open(file)
        except Exception:
            return Response({'detail': 'invalid image'}, status=status.HTTP_400_BAD_REQUEST)

        if image.mode not in ('RGB', 'L'):
            image = image.convert('RGB')

        # Resize to reasonable bounds while preserving aspect ratio (max 1600)
        max_side = 1600
        w, h = image.size
        scale = min(1.0, max_side / max(w, h))
        if scale < 1.0:
            nw, nh = int(w * scale), int(h * scale)
            image = image.resize((nw, nh), Image.LANCZOS)

        folder = 'comments/images'
        ts = int(time.time())
        filename = f"cimg-{pk}-{request.user.id}-{ts}.jpg"
        
        try:
            abs_url = save_image_locally_or_gcs(image, folder, filename, request)
        except Exception as e:
            error_type = type(e).__name__
            error_message = str(e)
            logger.error(
                f"Failed to save comment image: {error_type}: {error_message}",
                exc_info=True,
                extra={
                    'user_id': request.user.id,
                    'post_id': pk,
                    'folder': folder,
                    'filename': filename,
                    'error_type': error_type,
                    'error_message': error_message,
                }
            )
            # デバッグモードの場合は詳細なエラー情報を返す
            if settings.DEBUG:
                import traceback
                return Response({
                    'detail': 'failed to save',
                    'error_type': error_type,
                    'error_message': error_message,
                    'traceback': traceback.format_exc()
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            return Response({'detail': 'failed to save'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({'image_url': abs_url})


class PostBodyImageUploadView(APIView):
    permission_classes = [permissions.AllowAny]

    def _resolve_guest_user(self, request):
        """ゲストユーザーを解決"""
        if request.user and request.user.is_authenticated:
            return request.user
        # ゲストユーザーを取得または作成（IPアドレスも保存）
        return get_or_create_guest_user(request, create_if_not_exists=True)

    def post(self, request):
        file = request.FILES.get('image')
        if not file:
            return Response({'detail': 'image file required'}, status=status.HTTP_400_BAD_REQUEST)

        # ユーザーを解決（認証済みユーザーまたはゲストユーザー）
        user = self._resolve_guest_user(request)
        if not user:
            return Response({'detail': 'ユーザーを特定できません。'}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            image = Image.open(file)
        except Exception:
            return Response({'detail': 'invalid image'}, status=status.HTTP_400_BAD_REQUEST)

        if image.mode not in ('RGB', 'L'):
            image = image.convert('RGB')

        max_side = 1600
        w, h = image.size
        scale = min(1.0, max_side / max(w, h))
        if scale < 1.0:
            nw, nh = int(w * scale), int(h * scale)
            image = image.resize((nw, nh), Image.LANCZOS)

        folder = 'posts/images'
        ts = int(time.time())
        filename = f"pimg-{user.id}-{ts}.jpg"
        
        try:
            abs_url = save_image_locally_or_gcs(image, folder, filename, request)
        except Exception as e:
            error_type = type(e).__name__
            error_message = str(e)
            logger.error(
                f"Failed to save post image: {error_type}: {error_message}",
                exc_info=True,
                extra={
                    'user_id': user.id,
                    'folder': folder,
                    'filename': filename,
                    'error_type': error_type,
                    'error_message': error_message,
                }
            )
            # デバッグモードの場合は詳細なエラー情報を返す
            if settings.DEBUG:
                import traceback
                return Response({
                    'detail': 'failed to save',
                    'error_type': error_type,
                    'error_message': error_message,
                    'traceback': traceback.format_exc()
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            return Response({'detail': 'failed to save'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({'image_url': abs_url})


def _video_ext_from_name(name: str) -> str:
    lower = (name or '').lower()
    for ext in ('.mp4', '.webm', '.mov'):
        if lower.endswith(ext):
            return ext
    # fallback by mime
    mime, _ = mimetypes.guess_type(name)
    if mime == 'video/webm':
        return '.webm'
    if mime == 'video/quicktime':
        return '.mov'
    return '.mp4'


def _probe_duration_seconds(path: str) -> float | None:
    try:
        out = subprocess.check_output([
            'ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', path
        ], stderr=subprocess.STDOUT, timeout=10)
        s = (out.decode('utf-8', errors='ignore').strip())
        if not s:
            return None
        return float(s)
    except Exception:
        return None


class CommentVideoUploadView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk: int):
        # Validate post exists
        _ = get_object_or_404(Post, pk=pk)

        file = request.FILES.get('video')
        if not file:
            return Response({'detail': 'video file required'}, status=status.HTTP_400_BAD_REQUEST)

        # Save to temp first to probe duration
        suffix = _video_ext_from_name(getattr(file, 'name', ''))
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            for chunk in file.chunks():
                tmp.write(chunk)
            tmp_path = tmp.name

        try:
            dur = _probe_duration_seconds(tmp_path)
            if dur is not None and dur > 140.0:
                return Response({'detail': '動画は最大140秒までです。'}, status=status.HTTP_400_BAD_REQUEST)

            folder = 'comments/videos'
            ts = int(time.time())
            filename = f"cvid-{pk}-{request.user.id}-{ts}{suffix}"
            dir_path = os.path.join(settings.MEDIA_ROOT, folder)
            os.makedirs(dir_path, exist_ok=True)
            final_path = os.path.join(dir_path, filename)
            shutil.move(tmp_path, final_path)
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass

        rel_url = f"{settings.MEDIA_URL}{folder}/{filename}"
        abs_url = request.build_absolute_uri(rel_url)
        return Response({'video_url': abs_url, 'duration': dur})


class PostBodyVideoUploadView(APIView):
    permission_classes = [permissions.AllowAny]

    def _resolve_guest_user(self, request):
        """ゲストユーザーを解決"""
        if request.user and request.user.is_authenticated:
            return request.user
        # ゲストユーザーを取得または作成（IPアドレスも保存）
        return get_or_create_guest_user(request, create_if_not_exists=True)

    def post(self, request):
        file = request.FILES.get('video')
        if not file:
            return Response({'detail': 'video file required'}, status=status.HTTP_400_BAD_REQUEST)

        # ユーザーを解決（認証済みユーザーまたはゲストユーザー）
        user = self._resolve_guest_user(request)
        if not user:
            return Response({'detail': 'ユーザーを特定できません。'}, status=status.HTTP_401_UNAUTHORIZED)

        suffix = _video_ext_from_name(getattr(file, 'name', ''))
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            for chunk in file.chunks():
                tmp.write(chunk)
            tmp_path = tmp.name

        try:
            dur = _probe_duration_seconds(tmp_path)
            if dur is not None and dur > 140.0:
                return Response({'detail': '動画は最大140秒までです。'}, status=status.HTTP_400_BAD_REQUEST)

            folder = 'posts/videos'
            ts = int(time.time())
            filename = f"pvid-{user.id}-{ts}{suffix}"
            dir_path = os.path.join(settings.MEDIA_ROOT, folder)
            os.makedirs(dir_path, exist_ok=True)
            final_path = os.path.join(dir_path, filename)
            shutil.move(tmp_path, final_path)
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass

        rel_url = f"{settings.MEDIA_URL}{folder}/{filename}"
        abs_url = request.build_absolute_uri(rel_url)
        return Response({'video_url': abs_url, 'duration': dur})

class OGPPreviewView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        url = request.query_params.get('url', '').strip()
        if not url:
            return Response({'detail': 'url is required'}, status=status.HTTP_400_BAD_REQUEST)

        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            return Response({'detail': 'unsupported URL scheme'}, status=status.HTTP_400_BAD_REQUEST)

        # 1) キャッシュヒット確認（TTL: 24時間）
        ttl_seconds = 24 * 60 * 60
        cache = OGPCache.objects.filter(url=url).first()
        if cache:
            age = (timezone.now() - cache.fetched_at).total_seconds()
            if age <= ttl_seconds:
                return Response(cache.to_response_dict())

        # 2) 取得・解析
        headers = {
            'User-Agent': 'Mozilla/5.0 (compatible; BlackBoxBot/1.0; +https://example.invalid)'
        }
        try:
            resp = requests.get(url, headers=headers, timeout=6)
            resp.raise_for_status()
        except requests.RequestException:
            # キャッシュがあればフォールバック
            if cache:
                return Response(cache.to_response_dict())
            return Response({'detail': 'failed to fetch url'}, status=status.HTTP_400_BAD_REQUEST)

        html = resp.text or ''
        soup = BeautifulSoup(html, 'html.parser')

        def meta_property(prop: str) -> str:
            tag = soup.find('meta', attrs={'property': prop})
            return tag.get('content', '').strip() if tag and tag.has_attr('content') else ''

        def meta_name(name: str) -> str:
            tag = soup.find('meta', attrs={'name': name})
            return tag.get('content', '').strip() if tag and tag.has_attr('content') else ''

        og_title = meta_property('og:title') or soup.title.string.strip() if soup.title and soup.title.string else ''
        og_desc = meta_property('og:description') or meta_name('description')
        og_image_raw = meta_property('og:image')
        # 相対パス画像を絶対URLに
        og_image = urljoin(url, og_image_raw) if og_image_raw else ''
        og_site = meta_property('og:site_name')
        og_url = meta_property('og:url') or url

        data = {
            'url': url,
            'canonical_url': og_url,
            'title': og_title,
            'description': og_desc,
            'image': og_image,
            'site_name': og_site,
        }

        # 3) キャッシュ保存/更新
        if cache:
            OGPCache.objects.filter(pk=cache.pk).update(
                canonical_url=og_url or '',
                title=og_title or '',
                description=og_desc or '',
                image=og_image or '',
                site_name=og_site or '',
                fetched_at=timezone.now(),
            )
        else:
            OGPCache.objects.create(
                url=url,
                canonical_url=og_url or '',
                title=og_title or '',
                description=og_desc or '',
                image=og_image or '',
                site_name=og_site or '',
            )

        return Response(data)


class UserCommentedPostsView(generics.ListAPIView):
    serializer_class = PostSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # 本人以外の username を指定された場合は非公開
        username = self.kwargs.get('username')
        if not self.request.user or self.request.user.username != username:
            return Post.objects.none()
        user = self.request.user
        latest_comment = (
            Comment.objects
            .filter(author=user)
            .values('post')
            .annotate(last_at=Max('created_at'))
            .order_by('-last_at')
        )
        post_ids = [row['post'] for row in latest_comment]
        preserved = models.Case(*[models.When(pk=pk, then=pos) for pos, pk in enumerate(post_ids)]) if post_ids else None
        qs = Post.objects.filter(pk__in=post_ids, is_deleted=False).select_related('community', 'author', 'author__profile', 'tag', 'poll').prefetch_related(
            'media',
            Prefetch('poll__options', queryset=PollOption.objects.all().order_by('id'))
        )
        if preserved is not None:
            qs = qs.order_by(preserved)
        else:
            qs = qs.order_by('-created_at')
        return qs


class MeCommentedPostsView(generics.ListAPIView):
    serializer_class = PostSerializer
    permission_classes = [permissions.AllowAny]

    def _resolve_guest_user(self, request):
        """ゲストユーザーを解決（既存のみ、新規作成はしない）"""
        return get_or_create_guest_user(request, create_if_not_exists=False)

    def get_queryset(self):
        # ユーザーを取得（認証済みユーザーまたはゲストユーザー）
        user = self.request.user if (self.request.user and self.request.user.is_authenticated) else None
        if not user:
            user = self._resolve_guest_user(self.request)
        if not user:
            # ユーザーが特定できない場合は空のクエリセットを返す
            return Post.objects.none()
        
        latest_comment = (
            Comment.objects
            .filter(author=user)
            .values('post')
            .annotate(last_at=Max('created_at'))
            .order_by('-last_at')
        )
        post_ids = [row['post'] for row in latest_comment]
        preserved = models.Case(*[models.When(pk=pk, then=pos) for pos, pk in enumerate(post_ids)]) if post_ids else None
        qs = Post.objects.filter(pk__in=post_ids, is_deleted=False).select_related('community', 'author', 'author__profile', 'tag', 'poll').prefetch_related(
            'media',
            Prefetch('poll__options', queryset=PollOption.objects.all().order_by('id'))
        )
        if preserved is not None:
            qs = qs.order_by(preserved)
        else:
            qs = qs.order_by('-created_at')
        return qs


class MeFollowedPostsView(generics.ListAPIView):
    serializer_class = PostSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # ログインユーザーのみフォローした投稿を取得可能
        user = self.request.user
        if not user or not user.is_authenticated:
            return Post.objects.none()
        
        # フォローしている投稿のIDを取得（作成日時の降順で）
        followed_posts = PostFollow.objects.filter(user=user).order_by('-created_at').values_list('post_id', flat=True)
        post_ids = list(followed_posts)
        
        if not post_ids:
            return Post.objects.none()
        
        # 投稿を取得（削除されていないもの）
        qs = Post.objects.filter(pk__in=post_ids, is_deleted=False).select_related('community', 'author', 'author__profile', 'tag', 'poll').prefetch_related(
            'media',
            Prefetch('poll__options', queryset=PollOption.objects.all().order_by('id'))
        )
        
        # フォローした順に並び替え
        preserved = models.Case(*[models.When(pk=pk, then=pos) for pos, pk in enumerate(post_ids)])
        qs = qs.order_by(preserved)
        
        return qs


class PostReportView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk: int):
        # In MVP, just accept and return 202. In future, persist reports.
        post = get_object_or_404(Post, pk=pk)
        reason = (request.data.get('reason') or '').strip()
        
        # キャッシュ削除: 投稿詳細、報告一覧（将来的に実装される場合）
        from app.utils import invalidate_cache
        invalidate_cache(key=f'/api/posts/{post.id}/')
        if post.community:
            invalidate_cache(pattern=f'/api/messages/reports/community/{post.community.id}/*')
        
        return Response({'detail': '報告を受け付けました。', 'reason': reason}, status=status.HTTP_202_ACCEPTED)


class CommentDetailView(generics.DestroyAPIView):
    queryset = Comment.objects.all()
    serializer_class = CommentSerializer

    def _resolve_guest_user(self, request):
        """ゲストユーザーを解決（作成はしない、IPアドレスは保存しない）"""
        if request.user and request.user.is_authenticated:
            return request.user
        # 既存ユーザーのみ取得（新規作成はしない）
        return get_or_create_guest_user(request, create_if_not_exists=False)

    def get_permissions(self):
        # GETは誰でも許可、PATCH/DELETEは認証が必要
        if self.request.method == 'GET':
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated()]

    def get(self, request, *args, **kwargs):
        comment = get_object_or_404(Comment.objects.prefetch_related('media'), pk=kwargs.get('pk'))
        return Response(CommentSerializer(comment, context={'request': request}).data)

    def patch(self, request, *args, **kwargs):
        comment = get_object_or_404(Comment, pk=kwargs.get('pk'))
        if not request.user or not request.user.is_authenticated:
            raise PermissionDenied('権限がありません。')
        # Author can edit own comment unless blocked
        if request.user != comment.author:
            raise PermissionDenied('権限がありません。')
        if CommunityBlock.objects.filter(community=comment.community, user=request.user).exists():
            raise PermissionDenied('あなたはこのアノニウムにブロックされているため、編集できません。')

        body = request.data.get('body')
        if body is None:
            return Response({'detail': '変更内容がありません。'}, status=status.HTTP_400_BAD_REQUEST)

        body = str(body).replace('\r\n', '\n').replace('\r', '\n')
        max_len = 10000
        if len(body) > max_len:
            return Response({'body': f'本文が長すぎます（最大{max_len}文字）'}, status=status.HTTP_400_BAD_REQUEST)

        comment.body = body
        comment.is_edited = True
        comment.save(update_fields=['body', 'is_edited'])
        
        # キャッシュ削除: コメント詳細、投稿詳細、投稿のコメント一覧
        from app.utils import invalidate_cache
        post = comment.post
        invalidate_cache(key=f'/api/comments/{comment.id}/')
        invalidate_cache(key=f'/api/posts/{post.id}/')
        invalidate_cache(key=f'/api/posts/{post.id}/comments/')
        invalidate_cache(pattern=f'/api/communities/{post.community.id}/posts/*')
        invalidate_cache(pattern='/api/posts/*')
        invalidate_cache(pattern='/api/posts/trending*')
        
        return Response(CommentSerializer(comment, context={'request': request}).data)

    def get_permissions(self):
        # GETは誰でも許可、PATCH/DELETEは認証が必要
        if self.request.method == 'GET':
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated()]

    def delete(self, request, *args, **kwargs):
        comment = get_object_or_404(Comment, pk=kwargs.get('pk'))
        # Author can delete own comment (unless blocked); otherwise OWNER or ADMIN_MODERATOR can delete
        post = comment.post
        if request.user == comment.author:
            # コメント作成者本人の場合: ブロックされている場合は削除不可
            if CommunityBlock.objects.filter(community=comment.community, user=request.user).exists():
                raise PermissionDenied('あなたはこのアノニウムにブロックされているため、コメントを削除できません。')
        else:
            # 他ユーザーの場合: オーナーまたは管理モデレーターのみ削除可能
            membership = CM.objects.filter(
                community=comment.community,
                user=request.user,
                status=CM.Status.APPROVED,
            ).first()
            if not membership or membership.role not in (CM.Role.OWNER, CM.Role.ADMIN_MODERATOR):
                raise PermissionDenied('権限がありません。')
        # soft delete target and all descendants in the same post (tree delete)
        now = timezone.now()
        ids = [comment.id]
        frontier = [comment.id]
        # BFS to collect descendant ids (do NOT filter by is_deleted here)
        while frontier:
            children = list(Comment.objects.filter(post=post, parent_id__in=frontier).values_list('id', flat=True))
            if not children:
                break
            ids.extend(children)
            frontier = children
        deleted_count = Comment.objects.filter(id__in=ids).update(is_deleted=True, deleted_at=now, deleted_by=request.user)
        # 削除ログを出力
        logger.info(
            f"Comment deleted: comment_id={comment.id}, deleted_by={request.user.username} (user_id={request.user.id}), "
            f"deleted_count={deleted_count}, post_id={post.id}, community_slug={comment.community.slug}"
        )
        
        # キャッシュ削除: コメント詳細、投稿詳細、投稿のコメント一覧
        from app.utils import invalidate_cache
        invalidate_cache(key=f'/api/comments/{comment.id}/')
        invalidate_cache(key=f'/api/posts/{post.id}/')
        invalidate_cache(key=f'/api/posts/{post.id}/comments/')
        invalidate_cache(pattern=f'/api/communities/{post.community.id}/posts/*')
        invalidate_cache(pattern='/api/posts/*')
        invalidate_cache(pattern='/api/posts/trending*')
        # 削除された子コメントのキャッシュも削除
        for child_id in ids:
            if child_id != comment.id:
                invalidate_cache(key=f'/api/comments/{child_id}/')
        
        return Response(status=status.HTTP_204_NO_CONTENT)


class CommentReportView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk: int):
        comment = get_object_or_404(Comment, pk=pk)
        reason = (request.data.get('reason') or '').strip()
        
        # キャッシュ削除: コメント詳細、投稿のコメント一覧、報告一覧
        from app.utils import invalidate_cache
        post = comment.post
        invalidate_cache(key=f'/api/comments/{comment.id}/')
        invalidate_cache(key=f'/api/posts/{post.id}/')
        invalidate_cache(key=f'/api/posts/{post.id}/comments/')
        invalidate_cache(pattern=f'/api/communities/{post.community.id}/posts/*')
        invalidate_cache(pattern='/api/posts/*')
        invalidate_cache(pattern='/api/posts/trending*')
        if comment.community:
            invalidate_cache(pattern=f'/api/messages/reports/community/{comment.community.id}/*')
        
        return Response({'detail': '報告を受け付けました。', 'reason': reason}, status=status.HTTP_202_ACCEPTED)


class CommunityCommentsPurgeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, id: int):
        community = get_object_or_404(Community, id=id)
        membership = CM.objects.filter(
            community=community,
            user=request.user,
            status=CM.Status.APPROVED,
        ).first()
        if not membership or membership.role != CM.Role.OWNER:
            raise PermissionDenied('オーナーのみ実行できます。')

        qs = Comment.objects.filter(community=community, is_deleted=False)
        count = qs.update(is_deleted=True, deleted_at=timezone.now(), deleted_by=request.user)
        
        # キャッシュ削除: コミュニティの投稿一覧、投稿詳細、コメント一覧
        from app.utils import invalidate_cache
        invalidate_cache(pattern=f'/api/communities/{community.id}/posts/*')
        invalidate_cache(pattern='/api/posts/*/comments/*')
        invalidate_cache(pattern='/api/posts/*')
        invalidate_cache(pattern='/api/posts/trending*')
        
        return Response({'detail': 'コメントを削除しました。', 'count': count}, status=status.HTTP_200_OK)


class CommentDescendantsListView(APIView):
    permission_classes = [permissions.AllowAny]

    def _resolve_guest_user(self, request):
        """ゲストユーザーを解決（作成はしない、IPアドレスは保存しない）"""
        if request.user and request.user.is_authenticated:
            return request.user
        # 既存ユーザーのみ取得（新規作成はしない）
        return get_or_create_guest_user(request, create_if_not_exists=False)

    def get(self, request, pk: int):
        parent = get_object_or_404(Comment, pk=pk)
        post = parent.post
        try:
            limit = int(request.query_params.get('limit', '5'))
        except (ValueError, TypeError):
            limit = 5
        limit = max(1, min(limit, 50))

        # ソート順を取得（デフォルト: new）
        sort = request.query_params.get('sort', 'new').lower()
        if sort not in ('popular', 'new', 'old'):
            sort = 'new'

        # 削除されたコメントを含めるかどうか（デフォルト: false）
        include_deleted = request.query_params.get('include_deleted', 'false').lower() in ('true', '1')

        # ミュートフィルタをスキップするかどうか（デフォルト: false）
        # skip_mute_filter=trueの場合、キャッシュ可能なデータを返すためにミュートフィルタを適用しない
        skip_mute_filter = request.query_params.get('skip_mute_filter', 'false').lower() in ('true', '1')

        # 既に取得済みのコメントIDを取得（フロントエンドから送られてくる）
        # 注意: exclude_idsは既にレンダリング済みのノード集合のみ（子孫まで除外しない）
        exclude_ids_raw = request.query_params.get('exclude_ids', '')
        exclude_ids = set()
        if exclude_ids_raw:
            try:
                exclude_ids = set(map(int, exclude_ids_raw.split(',')))
            except (ValueError, TypeError):
                exclude_ids = set()
        
        print(f"[CommentDescendantsListView] parent.id={parent.id}, exclude_ids={exclude_ids}, limit={limit}, include_deleted={include_deleted}, skip_mute_filter={skip_mute_filter}")

        # Track muted user IDs for filtering
        user = getattr(request, 'user', None)
        muted_ids = []
        if not skip_mute_filter and user and getattr(user, 'is_authenticated', False):
            muted_ids = list(UserMute.objects.filter(user=user).values_list('target_id', flat=True))

        # シンプルな実装: 直接の子コメント（兄弟コメント）のみを取得
        # カーソルがある場合は、カーソルから続きを取得
        cursor_raw = request.query_params.get('cursor') or ''
        if cursor_raw:
            # カーソルがある場合は、カーソルから続きを取得（ページネーション用）
            try:
                data = json.loads(base64.urlsafe_b64decode(cursor_raw.encode('utf-8')).decode('utf-8'))
                offset = data.get('offset', 0)
            except Exception:
                offset = 0
        else:
            offset = 0
        
        # 親の直接の子コメントを取得
        all_children_qs = Comment.objects.filter(
            post=post,
            parent_id=parent.id
        ).select_related('author', 'author__profile', 'community', 'post').prefetch_related('media')
        # 削除されたコメントを除外（include_deletedがfalseの場合）
        if not include_deleted:
            all_children_qs = all_children_qs.filter(is_deleted=False)
        # ミュートユーザーのコメントを除外（skip_mute_filterがfalseの場合のみ）
        if not skip_mute_filter and muted_ids:
            all_children_qs = all_children_qs.exclude(author_id__in=muted_ids)
        # 既に取得済みのコメントを除外
        if exclude_ids:
            all_children_qs = all_children_qs.exclude(id__in=exclude_ids)
        # ソート順に応じて並び替え
        if sort == 'popular':
            all_children_qs = all_children_qs.order_by('is_deleted', '-score', '-created_at')
        elif sort == 'new':
            all_children_qs = all_children_qs.order_by('is_deleted', '-created_at')
        else:  # old
            all_children_qs = all_children_qs.order_by('is_deleted', 'created_at')
        
        # オフセットとリミットを適用
        total_count = all_children_qs.count()
        children_qs = all_children_qs[offset:offset + limit]
        children_list = list(children_qs)
        
        results: list[dict] = []
        for c in children_list:
            comment_data = {
                **CommentSerializer(c, context={'request': request}).data,
                'level_from_parent': 0,  # 直接の子コメントなので0
            }
            results.append(comment_data)

        # 各コメントの子コメント数とhas_more_childrenを計算（ミュート除外前の実際のリプ数）
        for result in results:
            comment_id = result.get('id')
            if comment_id:
                # このコメントの直接の子コメントを取得（ミュート除外前の実際のリプ数）
                all_child_ids_qs = Comment.objects.filter(
                    post=post,
                    parent_id=comment_id
                )
                # 削除されたコメントを除外（include_deletedがfalseの場合）
                if not include_deleted:
                    all_child_ids_qs = all_child_ids_qs.filter(is_deleted=False)
                # ミュートを除外せずにカウント（実際のリプ数）
                total_count = all_child_ids_qs.count()
                
                # children_count: ミュート除外前の実際のリプ数
                result['children_count'] = total_count
                
                # has_more_children: 子コメントがある場合はTrue（実際のリプ数が0より大きい場合）
                result['has_more_children'] = total_count > 0
            else:
                result['children_count'] = 0
                result['has_more_children'] = False

        # 親コメントを取得（既に取得済みのコメントは除外、削除されたコメントも含む）
        # 直接の子コメントなので、親は常にparent.id
        parent_comments = []
        if parent.id not in exclude_ids:
            parent_qs = Comment.objects.filter(
                post=post,
                id=parent.id
            )
            # ミュートユーザーのコメントを除外（skip_mute_filterがfalseの場合のみ）
            if not skip_mute_filter and muted_ids:
                parent_qs = parent_qs.exclude(author_id__in=muted_ids)
            parent_obj = parent_qs.first()
            if parent_obj:
                parent_comments = [CommentSerializer(parent_obj, context={'request': request}).data]

        # build next cursor if there is more to fetch
        next_cursor = None
        if offset + limit < total_count:
            try:
                payload = {'offset': offset + limit}
                b = base64.urlsafe_b64encode(json.dumps(payload).encode('utf-8')).decode('utf-8')
                next_cursor = b
            except Exception:
                next_cursor = None

        return Response({
            'items': results,
            'parents': parent_comments,  # 親コメントの情報も一緒に返す
            'next': next_cursor,
        })


class PollVoteView(generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk: int):
        poll = get_object_or_404(Poll, pk=pk)
        
        # 期限チェック
        if getattr(poll, 'expires_at', None) is not None:
            now = timezone.now()
            if poll.expires_at <= now:
                return Response({'detail': 'この投票は締め切られました。'}, status=status.HTTP_400_BAD_REQUEST)
        
        # 投票権限チェック（コミュニティのメンバーであるか）
        # ログインユーザーかつメンバーでないと投票できない
        community = poll.post.community
        membership = CM.objects.filter(
            community=community,
            user=request.user,
            status=CM.Status.APPROVED
        ).first()
        if not membership:
            raise PermissionDenied('この投票に投票するには、アノニウムに参加する必要があります。')
        
        option_id = request.data.get('option_id')
        if not option_id:
            return Response({'detail': 'option_id is required'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            option_id = int(option_id)
        except (ValueError, TypeError):
            return Response({'detail': 'invalid option_id'}, status=status.HTTP_400_BAD_REQUEST)
        
        option = get_object_or_404(PollOption, pk=option_id, poll=poll)
        
        existing = PollVote.objects.filter(poll=poll, user=request.user).first()
        if existing:
            # 既存の投票がある場合は変更
            old_option_id = existing.option_id
            if old_option_id == option_id:
                # 同じ選択肢を選んだ場合は投票を取り消す
                existing.delete()
                PollOption.objects.filter(pk=option_id).update(vote_count=F('vote_count') - 1)
                return Response({'detail': '投票を取り消しました。', 'selected_option_id': None})
            else:
                # 別の選択肢に変更
                existing.option = option
                existing.save(update_fields=['option', 'updated_at'])
                # 古い選択肢のカウントを減らす
                PollOption.objects.filter(pk=old_option_id).update(vote_count=F('vote_count') - 1)
                # 新しい選択肢のカウントを増やす
                PollOption.objects.filter(pk=option_id).update(vote_count=F('vote_count') + 1)
        else:
            # 新規投票
            PollVote.objects.create(poll=poll, option=option, user=request.user)
            PollOption.objects.filter(pk=option_id).update(vote_count=F('vote_count') + 1)
        
        # 更新された選択肢の情報を返す
        poll.refresh_from_db()
        option.refresh_from_db()
        return Response({
            'detail': '投票を記録しました。',
            'selected_option_id': option_id,
            'vote_count': option.vote_count
        })
