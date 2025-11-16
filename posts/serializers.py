from rest_framework import serializers

from .models import Post, PostVote, Comment, CommentVote, Poll, PollOption, PollVote, PostFollow, PostMedia, CommentMedia
from communities.models import CommunityMembership as CM, CommunityTag
from accounts.utils import get_or_create_guest_user


class PollOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PollOption
        fields = ['id', 'text', 'vote_count']


class PollSerializer(serializers.ModelSerializer):
    options = PollOptionSerializer(many=True, read_only=True)
    user_vote_id = serializers.SerializerMethodField()
    
    class Meta:
        model = Poll
        fields = ['id', 'title', 'options', 'user_vote_id', 'expires_at']
    
    def get_user_vote_id(self, obj: Poll) -> int | None:
        request = self.context.get('request')
        if not request:
            return None
        user = self._resolve_guest_user(request)
        if not user:
            return None
        vote = PollVote.objects.filter(poll=obj, user=user).only('option_id').first()
        return vote.option_id if vote else None
    
    def _resolve_guest_user(self, request):
        """ゲストユーザーを解決"""
        if request and request.user and request.user.is_authenticated:
            return request.user
        if not request:
            return None
        from accounts.utils import get_or_create_guest_user
        return get_or_create_guest_user(request, create_if_not_exists=False)


class PostMediaSerializer(serializers.ModelSerializer):
    class Meta:
        model = PostMedia
        fields = ['id', 'media_type', 'url', 'thumbnail_url', 'width', 'height', 'duration', 'file_size', 'order']


class CommentMediaSerializer(serializers.ModelSerializer):
    class Meta:
        model = CommentMedia
        fields = ['id', 'media_type', 'url', 'thumbnail_url', 'width', 'height', 'duration', 'file_size', 'order']


class PostSerializer(serializers.ModelSerializer):
    community_id = serializers.SerializerMethodField()
    community_slug = serializers.SerializerMethodField()
    community_name = serializers.SerializerMethodField()
    community_icon_url = serializers.SerializerMethodField()
    community_visibility = serializers.SerializerMethodField()
    community_join_policy = serializers.SerializerMethodField()
    community_karma = serializers.SerializerMethodField()
    community_is_member = serializers.SerializerMethodField()
    community_membership_role = serializers.SerializerMethodField()
    author_username = serializers.SerializerMethodField()
    author_icon_url = serializers.SerializerMethodField()
    score = serializers.IntegerField(read_only=True)
    votes_total = serializers.IntegerField(read_only=True)
    trending_score = serializers.SerializerMethodField()
    user_vote = serializers.SerializerMethodField()
    comments_count = serializers.SerializerMethodField()
    can_moderate = serializers.SerializerMethodField()
    is_deleted = serializers.BooleanField(read_only=True)
    tag = serializers.SerializerMethodField()
    poll = serializers.SerializerMethodField()
    is_following = serializers.SerializerMethodField()
    media = serializers.SerializerMethodField()
    
    author_username_id = serializers.SerializerMethodField()
    
    class Meta:
        model = Post
        fields = [
            'id', 'community', 'community_id', 'community_slug', 'community_name', 'community_icon_url', 'community_visibility', 
            'community_join_policy', 'community_karma',
            'community_is_member', 'community_membership_role',
            'author', 'author_username', 'author_username_id', 'author_icon_url', 'title', 'body', 'post_type', 'tag', 'poll', 'media',
            'score', 'votes_total', 'trending_score', 'user_vote', 'comments_count', 'can_moderate', 'is_deleted', 'is_edited', 'is_following', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'author', 'created_at', 'updated_at']

    def get_community_id(self, obj: Post) -> int:
        return obj.community.id

    def get_community_slug(self, obj: Post) -> str:
        return obj.community.slug

    def get_community_name(self, obj: Post) -> str:
        return obj.community.name

    def get_community_icon_url(self, obj: Post) -> str:
        return getattr(obj.community, 'icon_url', '') or ''

    def get_community_visibility(self, obj: Post) -> str:
        return getattr(obj.community, 'visibility', 'public') or 'public'

    def get_community_join_policy(self, obj: Post) -> str:
        return getattr(obj.community, 'join_policy', 'open') or 'open'

    def get_community_karma(self, obj: Post) -> int:
        return getattr(obj.community, 'karma', 0) or 0

    def _resolve_guest_user(self, request):
        """ゲストユーザーを解決"""
        if request and request.user and request.user.is_authenticated:
            return request.user
        if not request:
            return None
        # 既存ゲストのみ取得（未登録なら None）
        return get_or_create_guest_user(request, create_if_not_exists=False)

    def get_community_is_member(self, obj: Post) -> bool:
        request = self.context.get('request')
        user = self._resolve_guest_user(request)
        if not user:
            return False
        m = CM.objects.filter(community=obj.community, user=user, status=CM.Status.APPROVED).first()
        return bool(m)

    def get_community_membership_role(self, obj: Post) -> str | None:
        request = self.context.get('request')
        user = self._resolve_guest_user(request)
        if not user:
            return None
        m = CM.objects.filter(community=obj.community, user=user, status=CM.Status.APPROVED).first()
        return m.role if m else None

    def get_author_username(self, obj: Post) -> str:
        """表示名があれば表示名、なければユーザー名を返す"""
        if not obj.author:
            return ''
        try:
            profile = getattr(obj.author, 'profile', None)
            if profile and profile.display_name:
                return profile.display_name
        except Exception:
            pass
        return getattr(obj.author, 'username', '')
    
    def get_author_username_id(self, obj: Post) -> str:
        """実際のユーザー名（ID）を返す（表示名ではない）"""
        return getattr(obj.author, 'username', '') if obj.author else ''

    def get_author_icon_url(self, obj: Post) -> str:
        try:
            profile = getattr(obj.author, 'profile', None)
            return getattr(profile, 'icon_url', '') if profile else ''
        except Exception:
            return ''

    def get_trending_score(self, obj: Post) -> float | None:
        # まず_trending_score属性をチェック（動的に計算された場合）
        score = getattr(obj, '_trending_score', None)
        if score is not None:
            return round(float(score), 7)
        # _trending_scoreが設定されていない場合は、モデルのtrending_scoreフィールドを使用
        if hasattr(obj, 'trending_score'):
            db_score = obj.trending_score
            if db_score is not None:
                return round(float(db_score), 7)
        return None

    def get_user_vote(self, obj: Post) -> int | None:
        request = self.context.get('request')
        user = self._resolve_guest_user(request)
        if not user:
            return None
        vote = PostVote.objects.filter(post=obj, user=user).only('value').first()
        return vote.value if vote else None

    def get_comments_count(self, obj: Post) -> int:
        return getattr(obj, 'comments', None).count() if hasattr(obj, 'comments') else Comment.objects.filter(post=obj).count()

    def get_can_moderate(self, obj: Post) -> bool:
        request = self.context.get('request')
        user = self._resolve_guest_user(request)
        if not user:
            return False
        m = CM.objects.filter(community=obj.community, user=user, status=CM.Status.APPROVED).first()
        return bool(m and m.role in (CM.Role.OWNER, CM.Role.MODERATOR))

    def get_tag(self, obj: Post) -> dict | None:
        t = getattr(obj, 'tag', None)
        if not t:
            return None
        try:
            return { 'name': t.name, 'color': t.color }
        except Exception:
            return None

    def get_poll(self, obj: Post) -> dict | None:
        try:
            poll = getattr(obj, 'poll', None)
            if not poll:
                return None
            serializer = PollSerializer(poll, context=self.context)
            return serializer.data
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error in get_poll for post {obj.id}: {e}", exc_info=True)
            return None

    def get_is_following(self, obj: Post) -> bool:
        request = self.context.get('request')
        user = self._resolve_guest_user(request)
        if not user:
            return False
        return PostFollow.objects.filter(post=obj, user=user).exists()

    def get_media(self, obj: Post) -> list[dict] | None:
        """メディア情報を取得"""
        try:
            # prefetch_relatedで取得された場合はall()で取得
            if hasattr(obj, '_prefetched_objects_cache') and 'media' in obj._prefetched_objects_cache:
                media_list = obj._prefetched_objects_cache['media']
            elif hasattr(obj, 'media'):
                # RelatedManagerの場合はall()で取得
                try:
                    media_list = list(obj.media.all().order_by('order', 'created_at'))
                except Exception:
                    # Prefetchされていない場合はクエリを実行
                    media_list = list(PostMedia.objects.filter(post=obj).order_by('order', 'created_at'))
            else:
                # media属性が存在しない場合はクエリを実行
                media_list = list(PostMedia.objects.filter(post=obj).order_by('order', 'created_at'))
            
            if not media_list:
                return None
            
            return [PostMediaSerializer(media, context=self.context).data for media in media_list]
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error in get_media for post {obj.id}: {e}", exc_info=True)
            return None

    def to_representation(self, instance):
        """非公開コミュニティのメンバーでない場合は本文などを非公開にする"""
        data = super().to_representation(instance)
        community = instance.community
        
        # 非公開コミュニティの場合、メンバーシップをチェック
        if community.visibility == 'private':
            request = self.context.get('request')
            user = self._resolve_guest_user(request)
            is_member = False
            if user:
                is_member = CM.objects.filter(
                    community=community,
                    user=user,
                    status=CM.Status.APPROVED
                ).exists()
            
            if not is_member:
                # メンバーでない場合、本文などを非公開にする
                data['body'] = ''
                data['poll'] = None
                data['media'] = None
                data['title'] = 'このアノニウムは非公開です。'
        
        return data


class CommentSerializer(serializers.ModelSerializer):
    author_username = serializers.SerializerMethodField()
    author_icon_url = serializers.SerializerMethodField()
    score = serializers.IntegerField(read_only=True)
    votes_total = serializers.IntegerField(read_only=True)
    user_vote = serializers.SerializerMethodField()
    can_moderate = serializers.SerializerMethodField()
    community_id = serializers.SerializerMethodField()
    community_slug = serializers.SerializerMethodField()
    is_deleted = serializers.BooleanField(read_only=True)
    deleted_by_username = serializers.SerializerMethodField()
    deleted_at = serializers.DateTimeField(read_only=True)
    children = serializers.SerializerMethodField()
    children_count = serializers.SerializerMethodField()
    has_more_children = serializers.SerializerMethodField()
    media = serializers.SerializerMethodField()

    author_username_id = serializers.SerializerMethodField()
    
    # コメント作成時にmedia_urlsを受け取る（write_only）
    media_urls = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        allow_empty=False,
        write_only=True
    )
    
    class Meta:
        model = Comment
        fields = ['id', 'post', 'author', 'author_username', 'author_username_id', 'author_icon_url', 'parent', 'body', 'score', 'votes_total', 'user_vote', 'can_moderate', 'community_id', 'community_slug', 'is_deleted', 'is_edited', 'deleted_by_username', 'deleted_at', 'created_at', 'children', 'children_count', 'has_more_children', 'media', 'media_urls']
        read_only_fields = ['id', 'author', 'created_at', 'post']

    def get_author_username(self, obj: Comment) -> str:
        """表示名があれば表示名、なければユーザー名を返す"""
        if not obj.author:
            return ''
        try:
            profile = getattr(obj.author, 'profile', None)
            if profile and profile.display_name:
                return profile.display_name
        except Exception:
            pass
        return getattr(obj.author, 'username', '')
    
    def get_author_username_id(self, obj: Comment) -> str:
        """実際のユーザー名（ID）を返す（表示名ではない）"""
        return getattr(obj.author, 'username', '') if obj.author else ''

    def _resolve_guest_user(self, request):
        """ゲストユーザーを解決"""
        if request and request.user and request.user.is_authenticated:
            return request.user
        if not request:
            return None
        # 既存ゲストのみ取得（未登録なら None）
        return get_or_create_guest_user(request, create_if_not_exists=False)

    def get_user_vote(self, obj: Comment) -> int | None:
        request = self.context.get('request')
        user = self._resolve_guest_user(request)
        if not user:
            return None
        vote = CommentVote.objects.filter(comment=obj, user=user).only('value').first()
        return vote.value if vote else None

    def get_author_icon_url(self, obj: Comment) -> str:
        try:
            profile = getattr(obj.author, 'profile', None)
            return getattr(profile, 'icon_url', '') if profile else ''
        except Exception:
            return ''

    def get_can_moderate(self, obj: Comment) -> bool:
        request = self.context.get('request')
        user = self._resolve_guest_user(request)
        if not user:
            return False
        m = CM.objects.filter(community=obj.community, user=user, status=CM.Status.APPROVED).first()
        return bool(m and m.role in (CM.Role.OWNER, CM.Role.MODERATOR))

    def get_community_id(self, obj: Comment) -> int:
        return obj.community.id

    def get_community_slug(self, obj: Comment) -> str:
        return obj.community.slug

    def get_deleted_by_username(self, obj: Comment) -> str | None:
        """削除者の表示名があれば表示名、なければユーザー名を返す"""
        if obj.is_deleted and obj.deleted_by:
            try:
                profile = getattr(obj.deleted_by, 'profile', None)
                if profile and profile.display_name:
                    return profile.display_name
            except Exception:
                pass
            return getattr(obj.deleted_by, 'username', None)
        return None

    def get_children(self, obj: Comment) -> list:
        # まず_prefetched_children属性をチェック（再帰的に設定された子コメント）
        prefetched = getattr(obj, '_prefetched_children', None)
        if prefetched is not None:
            # 再帰的にシリアライズ（子コメントの子コメントも含む）
            # 各子コメントには既に_prefetched_childrenが設定されているので、再帰的にシリアライズされる
            return [self.__class__(child, context=self.context).data for child in prefetched]
        
        # コンテキストから直接の子コメントを取得（親コメント用、フォールバック）
        context_children = self.context.get('comment_children', {})
        children_list = context_children.get(obj.id, [])
        if children_list:
            # 再帰的にシリアライズ（子コメントの子コメントも含む）
            return [self.__class__(child, context=self.context).data for child in children_list]
        
        return []

    def get_children_count(self, obj: Comment) -> int:
        # まず_children_count属性をチェック（再帰的に設定された子コメント数）
        prefetched_count = getattr(obj, '_children_count', None)
        if prefetched_count is not None:
            return prefetched_count
        
        # コンテキストから子コメント数を取得
        context_counts = self.context.get('comment_children_count', {})
        if obj.id in context_counts:
            return context_counts[obj.id]
        
        # フォールバック: 直接カウント（パフォーマンスが悪いので避ける）
        return 0

    def get_has_more_children(self, obj: Comment) -> bool:
        # まず_has_more_children属性をチェック（再帰的に設定されたフラグ）
        # hasattrでチェックしてからgetattrで取得（FalseとNoneを区別するため）
        if hasattr(obj, '_has_more_children'):
            prefetched_has_more = getattr(obj, '_has_more_children')
            # Falseの場合も有効な値として扱う
            return bool(prefetched_has_more)
        
        # コンテキストから取得（親コメント用、フォールバック）
        context_has_more = self.context.get('comment_has_more', {})
        if obj.id in context_has_more:
            return bool(context_has_more[obj.id])
        
        # フォールバック: 子コメント数と取得済みの子コメント数を比較
        # これは最後の手段で、通常は_prefetched_childrenやコンテキストから取得される
        children_count = self.get_children_count(obj)
        children = self.get_children(obj)
        return children_count > len(children)

    def get_media(self, obj: Comment) -> list:
        """コメントに添付されたメディア（画像/動画）のリストを返す"""
        try:
            # prefetch_relatedで取得済みの場合はそれを使用
            if hasattr(obj, 'media'):
                media_list = obj.media.all()
            else:
                # フォールバック: 直接取得
                media_list = CommentMedia.objects.filter(comment=obj).order_by('order', 'created_at')
            return [CommentMediaSerializer(media, context=self.context).data for media in media_list]
        except Exception:
            return []

    def to_representation(self, instance):
        """非公開コミュニティのメンバーでない場合は本文を非公開にする"""
        data = super().to_representation(instance)
        community = instance.community
        
        # 非公開コミュニティの場合、メンバーシップをチェック
        if community.visibility == 'private':
            request = self.context.get('request')
            user = self._resolve_guest_user(request)
            is_member = False
            if user:
                is_member = CM.objects.filter(
                    community=community,
                    user=user,
                    status=CM.Status.APPROVED
                ).exists()
            
            if not is_member:
                # メンバーでない場合、本文を非公開にする
                data['body'] = 'このアノニウムは非公開です。'
        
        return data

    def validate_body(self, value: str) -> str:
        if value is None:
            return value
        v = value.replace('\r\n', '\n').replace('\r', '\n')
        max_len = 10000
        if len(v) > max_len:
            raise serializers.ValidationError(f'本文が長すぎます（最大{max_len}文字）')
        return v

    def validate(self, attrs):
        """本文が空でもメディアがあれば許可"""
        body = attrs.get('body', '').strip() if attrs.get('body') else ''
        media_urls = attrs.get('media_urls', [])
        
        # 本文が空でメディアもない場合はエラー
        if not body and not media_urls:
            raise serializers.ValidationError({'body': '本文またはメディアのいずれかが必要です。'})
        
        return attrs

    def create(self, validated_data):
        """コメントを作成し、media_urlsがあればCommentMediaも作成"""
        media_urls = validated_data.pop('media_urls', []) if 'media_urls' in validated_data else []
        
        # コメントを作成
        comment = super().create(validated_data)
        
        # メディアデータの作成
        if media_urls:
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"Creating CommentMedia for comment {comment.id}, media_urls count={len(media_urls)}")
            try:
                for order, media_data in enumerate(media_urls):
                    media_type = media_data.get('media_type', 'image')
                    url = media_data.get('url', '').strip()
                    if not url:
                        continue
                    logger.info(f"Creating CommentMedia: comment_id={comment.id}, media_type={media_type}, url={url}, order={order}")
                    CommentMedia.objects.create(
                        comment=comment,
                        media_type=media_type,
                        url=url,
                        thumbnail_url=media_data.get('thumbnail_url', '').strip() or '',
                        width=media_data.get('width'),
                        height=media_data.get('height'),
                        duration=media_data.get('duration'),
                        file_size=media_data.get('file_size'),
                        order=order
                    )
            except Exception as e:
                logger.error(f"Error creating CommentMedia: {e}", exc_info=True)
        
        return comment


class PostCreateSerializer(serializers.ModelSerializer):
    # name of community tag to attach (single)
    tag = serializers.CharField(max_length=32, required=False, allow_blank=True)
    post_type = serializers.ChoiceField(choices=Post.PostType.choices, required=False)
    poll_title = serializers.CharField(max_length=200, required=False, allow_blank=True)
    poll_options = serializers.ListField(
        child=serializers.CharField(max_length=500),
        required=False,
        allow_empty=False
    )
    poll_expires_at = serializers.DateTimeField(required=False, allow_null=True)
    # メディア情報（画像/動画タイプの投稿用）
    media_urls = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        allow_empty=False
    )
    
    class Meta:
        model = Post
        fields = ['title', 'body', 'tag', 'post_type', 'poll_title', 'poll_options', 'poll_expires_at', 'media_urls']

    def validate(self, attrs):
        post_type = attrs.get('post_type', Post.PostType.TEXT)
        body = attrs.get('body', '').strip() if attrs.get('body') else ''
        
        if post_type == Post.PostType.POLL:
            poll_title = attrs.get('poll_title', '').strip()
            poll_options = attrs.get('poll_options', [])
            
            if not poll_title:
                raise serializers.ValidationError({'poll_title': '投票のタイトルを入力してください。'})
            
            if not poll_options or len(poll_options) < 2:
                raise serializers.ValidationError({'poll_options': '投票項目は最低2つ必要です。'})
            
            if len(poll_options) > 20:
                raise serializers.ValidationError({'poll_options': '投票項目は最大20個までです。'})
            
            # 項目の重複チェック
            if len(poll_options) != len(set(poll_options)):
                raise serializers.ValidationError({'poll_options': '投票項目に重複があります。'})
            
            # 項目の空文字チェック
            for opt in poll_options:
                if not opt.strip():
                    raise serializers.ValidationError({'poll_options': '空の投票項目は登録できません。'})
        
        elif post_type in (Post.PostType.IMAGE, Post.PostType.VIDEO):
            media_urls = attrs.get('media_urls', [])
            if not media_urls:
                raise serializers.ValidationError({'media_urls': 'メディアが指定されていません。'})
            
            # メディアURLの検証
            for idx, media_data in enumerate(media_urls):
                if not isinstance(media_data, dict):
                    raise serializers.ValidationError({'media_urls': f'メディアデータ{idx+1}の形式が不正です。'})
                
                url = media_data.get('url', '').strip()
                if not url:
                    raise serializers.ValidationError({'media_urls': f'メディア{idx+1}のURLが指定されていません。'})
                
                # URLの形式チェック
                if not url.startswith(('http://', 'https://')):
                    raise serializers.ValidationError({'media_urls': f'メディア{idx+1}のURLが不正です。'})
            
            # メディア投稿の場合は本文が空でも許可
            if not body:
                attrs['body'] = ''
        
        return attrs

    def validate_body(self, value: str) -> str:
        if value is None:
            return value
        # 改行を統一（CRLF/CR → LF）し、極端に長い本文を制限
        v = value.replace('\r\n', '\n').replace('\r', '\n')
        max_len = 20000
        if len(v) > max_len:
            raise serializers.ValidationError(f'本文が長すぎます（最大{max_len}文字）')
        return v

    def create(self, validated_data):
        tag_name = validated_data.pop('tag', '').strip() if 'tag' in validated_data else ''
        post_type = validated_data.pop('post_type', Post.PostType.TEXT)
        poll_title = validated_data.pop('poll_title', '').strip() if 'poll_title' in validated_data else ''
        poll_options = validated_data.pop('poll_options', []) if 'poll_options' in validated_data else []
        poll_expires_at = validated_data.pop('poll_expires_at', None) if 'poll_expires_at' in validated_data else None
        media_urls = validated_data.pop('media_urls', []) if 'media_urls' in validated_data else []
        
        validated_data['post_type'] = post_type
        post: Post = super().create(validated_data)
        
        # attach single tag if provided
        try:
            community = post.community
            if tag_name and community:
                t = CommunityTag.objects.filter(community=community, name=tag_name).first()
                if t:
                    post.tag = t
                    post.save(update_fields=['tag', 'updated_at'])
        except Exception:
            pass
        
        # 投票データの作成
        if post_type == Post.PostType.POLL and poll_title and poll_options:
            try:
                poll = Poll.objects.create(post=post, title=poll_title, expires_at=poll_expires_at)
                for option_text in poll_options:
                    PollOption.objects.create(poll=poll, text=option_text, vote_count=0)
            except Exception:
                pass
        
        # メディアデータの作成
        if post_type in (Post.PostType.IMAGE, Post.PostType.VIDEO) and media_urls:
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"Creating PostMedia for post {post.id}, post_type={post_type}, media_urls count={len(media_urls)}")
            try:
                created_media = []
                for order, media_data in enumerate(media_urls):
                    media_type = PostMedia.MediaType.IMAGE if post_type == Post.PostType.IMAGE else PostMedia.MediaType.VIDEO
                    url = media_data.get('url', '').strip()
                    logger.info(f"Creating PostMedia: post_id={post.id}, media_type={media_type}, url={url}, order={order}")
                    media = PostMedia.objects.create(
                        post=post,
                        media_type=media_type,
                        url=url,
                        thumbnail_url=media_data.get('thumbnail_url', '').strip() or '',
                        width=media_data.get('width'),
                        height=media_data.get('height'),
                        duration=media_data.get('duration'),
                        file_size=media_data.get('file_size'),
                        order=order
                    )
                    created_media.append(media)
                    logger.info(f"PostMedia created successfully: id={media.id}, url={media.url}")
                
                # 作成されたメディアの数を確認
                media_count = PostMedia.objects.filter(post=post).count()
                logger.info(f"Total PostMedia count for post {post.id}: {media_count}")
            except Exception as e:
                # エラーが発生した場合はログに記録するが、投稿自体は作成される
                logger.error(f"Failed to create PostMedia for post {post.id}: {e}", exc_info=True)
                import traceback
                logger.error(traceback.format_exc())
        
        return post




