from typing import Any
import secrets
import logging

from django.utils.text import slugify
from rest_framework import serializers

from .models import Community, CommunityMembership, CommunityBlock, CommunityTag
from django.core import signing
from django.contrib.auth.models import User
from accounts.serializers import UserSerializer
from accounts.utils import get_or_create_guest_user

logger = logging.getLogger(__name__)


class CommunitySerializer(serializers.ModelSerializer):
    is_member = serializers.SerializerMethodField()
    membership_status = serializers.SerializerMethodField()
    membership_role = serializers.SerializerMethodField()
    is_admin = serializers.SerializerMethodField()
    is_blocked = serializers.SerializerMethodField()
    is_favorite = serializers.SerializerMethodField()
    # 表示用（書き込みはupdate内でinitial_dataから処理）
    tags = serializers.SerializerMethodField()
    tag_permission_scope = serializers.ChoiceField(choices=[
        ('all', 'All Participants'), ('moderator', 'Moderators'), ('owner', 'Owner')
    ], required=False)

    clip_post_id = serializers.SerializerMethodField()

    class Meta:
        model = Community
        fields = [
            'id', 'name', 'slug', 'description', 'rules', 'icon_url', 'banner_url',
            'visibility', 'join_policy', 'is_nsfw', 'allow_repost', 'karma', 'creator', 'members_count',
            'created_at', 'updated_at',
            'is_member', 'membership_status', 'membership_role', 'is_admin', 'is_blocked', 'is_favorite',
            'tags', 'tag_permission_scope', 'clip_post_id',
        ]
        read_only_fields = ['id', 'slug', 'creator', 'members_count', 'created_at', 'updated_at']

    def _get_user(self):
        """ユーザーを取得（コンテキストから取得、なければ解決を試みる）"""
        # コンテキストから解決済みユーザーを取得（ビューで解決済み）
        resolved_user = self.context.get('resolved_user')
        if resolved_user is not None:
            return resolved_user
        
        # コンテキストにない場合は、従来の方法で解決を試みる
        request = self.context.get('request')
        if not request:
            return None
        
        if request.user and request.user.is_authenticated:
            return request.user
        
        # ゲストユーザーの解決を試みる
        try:
            return get_or_create_guest_user(request, create_if_not_exists=False)
        except Exception as e:
            logger.error(f"Error getting guest user in serializer: {e}", exc_info=True)
            return None

    def get_is_member(self, obj: Community) -> bool:
        user = self._get_user()
        if not user:
            return False
        
        try:
            return CommunityMembership.objects.filter(
                community=obj,
                user=user,
                status=CommunityMembership.Status.APPROVED,
            ).exists()
        except Exception as e:
            logger.error(f"Error checking membership for community {obj.id}: {e}", exc_info=True)
            return False

    def get_membership_status(self, obj: Community) -> str | None:
        user = self._get_user()
        if not user:
            return None
        
        try:
            m = CommunityMembership.objects.filter(community=obj, user=user).first()
            return m.status if m else None
        except Exception as e:
            logger.error(f"Error getting membership status for community {obj.id}: {e}", exc_info=True)
            return None

    def get_membership_role(self, obj: Community) -> str | None:
        user = self._get_user()
        if not user:
            return None
        
        try:
            m = CommunityMembership.objects.filter(community=obj, user=user, status=CommunityMembership.Status.APPROVED).first()
            return m.role if m else None
        except Exception as e:
            logger.error(f"Error getting membership role for community {obj.id}: {e}", exc_info=True)
            return None

    def get_is_admin(self, obj: Community) -> bool:
        user = self._get_user()
        if not user:
            return False
        
        try:
            m = CommunityMembership.objects.filter(community=obj, user=user, status=CommunityMembership.Status.APPROVED).first()
            if not m:
                return False
            return m.role in (
                CommunityMembership.Role.OWNER,
                CommunityMembership.Role.ADMIN_MODERATOR,
                CommunityMembership.Role.MODERATOR,
            )
        except Exception as e:
            logger.error(f"Error checking admin status for community {obj.id}: {e}", exc_info=True)
            return False

    def get_is_blocked(self, obj: Community) -> bool:
        user = self._get_user()
        if not user:
            return False
        
        try:
            return CommunityBlock.objects.filter(community=obj, user=user).exists()
        except Exception as e:
            logger.error(f"Error checking block status for community {obj.id}: {e}", exc_info=True)
            return False

    def get_is_favorite(self, obj: Community) -> bool:
        user = self._get_user()
        if not user:
            return False
        
        try:
            m = CommunityMembership.objects.filter(community=obj, user=user, status=CommunityMembership.Status.APPROVED).first()
            return bool(m and getattr(m, 'is_favorite', False))
        except Exception as e:
            logger.error(f"Error checking favorite status for community {obj.id}: {e}", exc_info=True)
            return False

    def get_tags(self, obj: Community) -> list[dict[str, str]]:
        try:
            return list(CommunityTag.objects.filter(community=obj).values('name', 'color'))
        except Exception:
            return []

    def get_clip_post_id(self, obj: Community) -> int | None:
        """clip_postが設定されている場合はそのIDを返す。Noneの場合はNoneを返す。"""
        try:
            if hasattr(obj, 'clip_post') and obj.clip_post:
                return obj.clip_post.id
        except Exception as e:
            logger.error(f"Error getting clip_post_id for community {obj.id}: {e}", exc_info=True)
        return None

    def to_representation(self, instance):
        """シリアライズ時にエラーが発生した場合の処理"""
        try:
            return super().to_representation(instance)
        except Exception as e:
            community_id = getattr(instance, 'id', 'unknown')
            logger.error(f"Error serializing community {community_id}: {e}", exc_info=True)
            # エラーをログに記録してから再発生（デバッグ用）
            # 本番環境では、エラーを隠すのではなく、根本原因を解決する必要がある
            raise

    def update(self, instance: Community, validated_data: dict[str, Any]) -> Community:
        tags = validated_data.pop('tags', None)
        community = super().update(instance, validated_data)
        if 'tag_permission_scope' in validated_data:
            scope = validated_data.pop('tag_permission_scope')
            try:
                Community.objects.filter(pk=community.pk).update(tag_permission_scope=scope)
                community.refresh_from_db(fields=['tag_permission_scope'])
            except Exception:
                pass
        # tags は SerializerMethodField なので validated_data には入らない。initial_data から取得する
        if tags is None:
            tags = self.initial_data.get('tags', None)
        if tags is not None:
            # permission: only OWNER/MODERATOR can update tags (checked in view.perform_update)
            names_seen = set()
            normed: list[dict[str, str]] = []
            for t in (tags or []):
                name = (t.get('name') or '').strip()
                if not name:
                    continue
                if name in names_seen:
                    continue
                names_seen.add(name)
                color = (t.get('color') or '#1e3a8a').strip()[:16]
                normed.append({'name': name, 'color': color})
            # replace all tags atomically（テーブル未作成でも落ちないようにガード）
            try:
                from django.db import transaction
                with transaction.atomic():
                    CommunityTag.objects.filter(community=community).delete()
                    for t in normed:
                        CommunityTag.objects.create(community=community, **t)
            except Exception:
                pass
        return community


class CommunityCreateSerializer(serializers.ModelSerializer):
    tags = serializers.ListField(child=serializers.DictField(), required=False, write_only=True)
    tag_permission_scope = serializers.ChoiceField(
        choices=[
            ('all', 'All Participants'),
            ('moderator', 'Moderators'),
            ('owner', 'Owner')
        ],
        required=False,
        default='all',
        write_only=True
    )
    
    class Meta:
        model = Community
        fields = [
            'name', 'description', 'rules', 'icon_url', 'banner_url',
            'visibility', 'join_policy', 'is_nsfw', 'allow_repost', 'karma', 'tags', 'tag_permission_scope',
        ]

    def validate_name(self, value: str) -> str:
        if Community.objects.filter(name__iexact=value).exists():
            raise serializers.ValidationError('同名のアノニウムが既に存在します。')
        return value

    def _generate_unique_slug(self, base: str) -> str:
        base_slug = slugify(base)[:80]
        # 日本語などでslugifyが空になった場合のフォールバック（ASCII保証）
        if not base_slug:
            base_slug = f"c-{secrets.token_hex(3)}"  # 例: c-a1b2c3
            if not Community.objects.filter(slug=base_slug).exists():
                return base_slug
        if not Community.objects.filter(slug=base_slug).exists():
            return base_slug
        suffix = 2
        while True:
            candidate = f"{base_slug[:77]}-{suffix}"
            if not Community.objects.filter(slug=candidate).exists():
                return candidate
            suffix += 1

    def create(self, validated_data: dict[str, Any]) -> Community:
        request = self.context['request']
        creator = request.user
        tags = validated_data.pop('tags', None)
        scope = validated_data.pop('tag_permission_scope', 'all')  # デフォルト値は'all'
        
        # 作成時に設定すべきでないフィールドを除外
        excluded_fields = {'id', 'slug', 'creator', 'members_count', 'created_at', 'updated_at', 'tags', 'tag_permission_scope'}
        clean_data = {k: v for k, v in validated_data.items() if k not in excluded_fields}
        
        # karmaが含まれていない場合、デフォルト値0を設定（join_policyがopen以外の場合）
        if 'karma' not in clean_data:
            clean_data['karma'] = 0
        
        slug = self._generate_unique_slug(clean_data['name'])

        # tag_permission_scopeを明示的に設定
        create_kwargs = {
            'slug': slug,
            'creator': creator,
            'members_count': 1,
            'tag_permission_scope': scope,
            **clean_data,
        }

        try:
            community = Community.objects.create(**create_kwargs)
        except Exception as e:
            import traceback
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error creating community: {e}")
            logger.error(f"validated_data: {validated_data}")
            logger.error(f"clean_data: {clean_data}")
            logger.error(f"create_kwargs: {create_kwargs}")
            logger.error(traceback.format_exc())
            raise
        
        try:
            CommunityMembership.objects.create(
                community=community,
                user=creator,
                role=CommunityMembership.Role.OWNER,
                status=CommunityMembership.Status.APPROVED,
            )
        except Exception as e:
            # メンバーシップ作成に失敗した場合、作成したコミュニティを削除
            community.delete()
            import traceback
            print(f"Error creating membership: {e}")
            print(traceback.format_exc())
            raise
        
        if tags:
            try:
                names_seen = set()
                for t in tags:
                    name = (t.get('name') or '').strip()
                    if not name or name in names_seen:
                        continue
                    names_seen.add(name)
                    color = (t.get('color') or '#1e3a8a').strip()[:16]
                    CommunityTag.objects.create(community=community, name=name, color=color)
            except Exception:
                pass
        return community


class CommunityParticipantSerializer(serializers.Serializer):
    id = serializers.IntegerField(source='user.id', read_only=True)
    username = serializers.SerializerMethodField()
    username_id = serializers.SerializerMethodField()
    display_name = serializers.SerializerMethodField()
    icon_url = serializers.SerializerMethodField()
    score = serializers.SerializerMethodField()
    role = serializers.CharField(read_only=True)

    def get_username(self, obj: CommunityMembership) -> str:
        """表示名があれば表示名、なければユーザー名を返す（後方互換性のため）"""
        return self.get_display_name(obj)
    
    def get_username_id(self, obj: CommunityMembership) -> str:
        """実際のユーザー名（ID）を返す"""
        return obj.user.username if obj.user else ''
    
    def get_display_name(self, obj: CommunityMembership) -> str:
        """表示名を返す（表示名がない場合はユーザー名を返す）"""
        from django.db import DatabaseError
        try:
            profile = getattr(obj.user, 'profile', None)
            if profile and profile.display_name:
                return profile.display_name
        except DatabaseError:
            pass
        return obj.user.username if obj.user else ''

    def get_icon_url(self, obj: CommunityMembership) -> str:
        from django.db import DatabaseError
        try:
            profile = getattr(obj.user, 'profile', None)
            return getattr(profile, 'icon_url', '') if profile else ''
        except DatabaseError:
            return ''

    def get_score(self, obj: CommunityMembership) -> int:
        from django.db import DatabaseError
        try:
            profile = getattr(obj.user, 'profile', None)
            return getattr(profile, 'score', 0) if profile else 0
        except DatabaseError:
            return 0


class CommunityBlockedUserSerializer(serializers.Serializer):
    id = serializers.IntegerField(source='user.id', read_only=True)
    username = serializers.SerializerMethodField()
    icon_url = serializers.SerializerMethodField()
    reason = serializers.CharField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)

    def get_username(self, obj: CommunityBlock) -> str:
        """表示名があれば表示名、なければユーザー名を返す"""
        from django.db import DatabaseError
        try:
            profile = getattr(obj.user, 'profile', None)
            if profile and profile.display_name:
                return profile.display_name
        except DatabaseError:
            pass
        return obj.user.username if obj.user else ''

    def get_icon_url(self, obj: CommunityBlock) -> str:
        try:
            profile = getattr(obj.user, 'profile', None)
            return getattr(profile, 'icon_url', '') if profile else ''
        except Exception:
            return ''

