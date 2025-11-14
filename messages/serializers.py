from rest_framework import serializers
from .models import Message, GroupChatMessage, Report
from accounts.serializers import UserSerializer
from communities.serializers import CommunitySerializer


class MessageSerializer(serializers.ModelSerializer):
    """メッセージシリアライザー"""
    sender = UserSerializer(read_only=True)
    recipient = UserSerializer(read_only=True)
    community = CommunitySerializer(read_only=True)

    class Meta:
        model = Message
        fields = [
            'id', 'sender', 'recipient', 'community',
            'subject', 'body', 'is_read', 'read_at', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'sender', 'is_read', 'read_at', 'created_at', 'updated_at']


class MessageCreateSerializer(serializers.ModelSerializer):
    """メッセージ作成用シリアライザー"""
    community_id = serializers.IntegerField()
    recipient_id = serializers.IntegerField(write_only=True)

    class Meta:
        model = Message
        fields = ['recipient_id', 'community_id', 'subject', 'body']

    def validate_recipient_id(self, value):
        """受信者が存在するかチェック"""
        from django.contrib.auth import get_user_model
        User = get_user_model()
        try:
            User.objects.get(pk=value)
        except User.DoesNotExist:
            raise serializers.ValidationError('受信者が存在しません。')
        return value

    def validate(self, attrs):
        """送信者と受信者が同じコミュニティのモデレーターかチェック"""
        from communities.models import CommunityMembership as CM
        from django.contrib.auth import get_user_model
        User = get_user_model()
        
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            raise serializers.ValidationError('認証が必要です。')
        
        community_id = attrs.get('community_id')
        recipient_id = attrs.get('recipient_id')
        sender = request.user

        try:
            recipient = User.objects.get(pk=recipient_id)
        except User.DoesNotExist:
            raise serializers.ValidationError('受信者が存在しません。')

        # 送信者がモデレーターかチェック
        sender_membership = CM.objects.filter(
            community_id=community_id,
            user=sender,
            status=CM.Status.APPROVED,
            role__in=[CM.Role.OWNER, CM.Role.ADMIN_MODERATOR, CM.Role.MODERATOR]
        ).first()
        
        if not sender_membership:
            raise serializers.ValidationError('このコミュニティのモデレーターのみメッセージを送信できます。')

        # 受信者がモデレーターかチェック
        recipient_membership = CM.objects.filter(
            community_id=community_id,
            user=recipient,
            status=CM.Status.APPROVED,
            role__in=[CM.Role.OWNER, CM.Role.ADMIN_MODERATOR, CM.Role.MODERATOR]
        ).first()
        
        if not recipient_membership:
            raise serializers.ValidationError('受信者はこのコミュニティのモデレーターである必要があります。')

        # 自分自身に送信できないようにする
        if sender == recipient:
            raise serializers.ValidationError('自分自身にメッセージを送信することはできません。')

        return attrs

    def create(self, validated_data):
        """メッセージを作成"""
        from django.contrib.auth import get_user_model
        User = get_user_model()
        from communities.models import Community
        
        request = self.context.get('request')
        community_id = validated_data.pop('community_id')
        recipient_id = validated_data.pop('recipient_id')
        
        community = Community.objects.get(pk=community_id)
        recipient = User.objects.get(pk=recipient_id)
        
        return Message.objects.create(
            sender=request.user,
            recipient=recipient,
            community=community,
            **validated_data
        )


class GroupChatMessageSerializer(serializers.ModelSerializer):
    """グループチャットメッセージシリアライザー"""
    sender = serializers.SerializerMethodField()
    community = CommunitySerializer(read_only=True)
    reply_to = serializers.SerializerMethodField()
    report = serializers.SerializerMethodField()
    
    def get_sender(self, obj):
        """送信者情報を取得（役職情報を含む）"""
        sender_data = UserSerializer(obj.sender).data
        # コミュニティでの役職を取得
        from communities.models import CommunityMembership as CM
        membership = CM.objects.filter(
            community=obj.community,
            user=obj.sender,
            status=CM.Status.APPROVED
        ).first()
        if membership:
            sender_data['role'] = membership.role
        return sender_data
    
    def get_reply_to(self, obj):
        """引用元メッセージの情報を返す"""
        if obj.reply_to:
            # 送信者のアイコンURLを取得
            icon_url = None
            try:
                if hasattr(obj.reply_to.sender, 'userprofile') and obj.reply_to.sender.userprofile.icon_url:
                    icon_url = obj.reply_to.sender.userprofile.icon_url.url
            except Exception:
                pass
            
            # コミュニティでの役職を取得
            from communities.models import CommunityMembership as CM
            role = None
            membership = CM.objects.filter(
                community=obj.reply_to.community,
                user=obj.reply_to.sender,
                status=CM.Status.APPROVED
            ).first()
            if membership:
                role = membership.role
            
            return {
                'id': obj.reply_to.id,
                'sender': {
                    'id': obj.reply_to.sender.id,
                    'username': obj.reply_to.sender.username,
                    'icon_url': icon_url,
                    'role': role,
                },
                'body': obj.reply_to.body,
                'created_at': obj.reply_to.created_at.isoformat(),
            }
        return None
    
    def get_report(self, obj):
        """引用元報告の情報を返す"""
        if obj.report:
            # post_idを取得（コメントの場合はコメントが属する投稿のID）
            post_id = None
            if obj.report.content_type == Report.ContentType.POST:
                post_id = obj.report.content_object_id
            elif obj.report.content_type == Report.ContentType.COMMENT:
                comment = obj.report.comment
                if comment:
                    post_id = comment.post_id
            
            return {
                'id': obj.report.id,
                'reporter': {
                    'id': obj.report.reporter.id,
                    'username': obj.report.reporter.username,
                },
                'content_type': obj.report.content_type,
                'post_id': post_id,
                'comment_id': obj.report.content_object_id if obj.report.content_type == Report.ContentType.COMMENT else None,
                'body': obj.report.body,
                'status': obj.report.status,
                'created_at': obj.report.created_at.isoformat(),
            }
        return None

    class Meta:
        model = GroupChatMessage
        fields = [
            'id', 'sender', 'community', 'body', 'reply_to', 'report', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'sender', 'created_at', 'updated_at']


class GroupChatMessageCreateSerializer(serializers.ModelSerializer):
    """グループチャットメッセージ作成用シリアライザー"""
    community_id = serializers.IntegerField()
    reply_to_id = serializers.IntegerField(required=False, allow_null=True)
    report_id = serializers.IntegerField(required=False, allow_null=True)

    class Meta:
        model = GroupChatMessage
        fields = ['community_id', 'body', 'reply_to_id', 'report_id']

    def validate(self, attrs):
        """送信者がコミュニティのモデレーター以上かチェック"""
        from communities.models import CommunityMembership as CM
        
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            raise serializers.ValidationError('認証が必要です。')
        
        community_id = attrs.get('community_id')
        reply_to_id = attrs.get('reply_to_id')
        report_id = attrs.get('report_id')
        sender = request.user

        # 送信者がモデレーター以上かチェック
        sender_membership = CM.objects.filter(
            community_id=community_id,
            user=sender,
            status=CM.Status.APPROVED,
            role__in=[CM.Role.OWNER, CM.Role.ADMIN_MODERATOR, CM.Role.MODERATOR]
        ).first()
        
        if not sender_membership:
            raise serializers.ValidationError('このコミュニティのモデレーター以上の権限が必要です。')
        
        # 引用元メッセージの検証
        if reply_to_id:
            try:
                reply_to_message = GroupChatMessage.objects.get(
                    pk=reply_to_id,
                    community_id=community_id
                )
                attrs['reply_to'] = reply_to_message
            except GroupChatMessage.DoesNotExist:
                raise serializers.ValidationError({
                    'reply_to_id': '引用元のメッセージが見つかりません。'
                })
        
        # 引用元報告の検証
        if report_id:
            try:
                report = Report.objects.get(
                    pk=report_id,
                    community_id=community_id
                )
                attrs['report'] = report
            except Report.DoesNotExist:
                raise serializers.ValidationError({
                    'report_id': '引用元の報告が見つかりません。'
                })

        return attrs

    def create(self, validated_data):
        """グループチャットメッセージを作成"""
        from communities.models import Community
        
        request = self.context.get('request')
        community_id = validated_data.pop('community_id')
        reply_to = validated_data.pop('reply_to', None)
        report = validated_data.pop('report', None)
        
        community = Community.objects.get(pk=community_id)
        
        return GroupChatMessage.objects.create(
            sender=request.user,
            community=community,
            reply_to=reply_to,
            report=report,
            **validated_data
        )


class ReportSerializer(serializers.ModelSerializer):
    """報告シリアライザー"""
    reporter = UserSerializer(read_only=True)
    community = CommunitySerializer(read_only=True)
    post_id = serializers.SerializerMethodField()
    comment_id = serializers.SerializerMethodField()

    class Meta:
        model = Report
        fields = [
            'id', 'reporter', 'community', 'content_type', 'content_object_id',
            'post_id', 'comment_id', 'body', 'status', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'reporter', 'created_at', 'updated_at']

    def get_post_id(self, obj):
        """投稿IDを取得（コメントの場合はコメントが属する投稿のID）"""
        if obj.content_type == Report.ContentType.POST:
            return obj.content_object_id
        elif obj.content_type == Report.ContentType.COMMENT:
            # コメントの場合は、コメントから投稿IDを取得
            comment = obj.comment
            if comment:
                return comment.post_id
        return None

    def get_comment_id(self, obj):
        """コメントIDを取得"""
        return obj.content_object_id if obj.content_type == Report.ContentType.COMMENT else None


class ReportCreateSerializer(serializers.ModelSerializer):
    """報告作成用シリアライザー"""
    community_id = serializers.IntegerField()
    post_id = serializers.IntegerField(required=False, allow_null=True)
    comment_id = serializers.IntegerField(required=False, allow_null=True)

    class Meta:
        model = Report
        fields = ['community_id', 'post_id', 'comment_id', 'body']

    def validate(self, attrs):
        """バリデーション"""
        from posts.models import Post, Comment
        from communities.models import Community
        
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            raise serializers.ValidationError('認証が必要です。')
        
        community_id = attrs.get('community_id')
        post_id = attrs.get('post_id')
        comment_id = attrs.get('comment_id')
        
        # 投稿またはコメントのどちらか一方が必須
        if not post_id and not comment_id:
            raise serializers.ValidationError('投稿IDまたはコメントIDのどちらか一方を指定してください。')
        
        if post_id and comment_id:
            raise serializers.ValidationError('投稿とコメントを同時に指定することはできません。')
        
        # コミュニティが存在するかチェック
        try:
            community = Community.objects.get(pk=community_id)
        except Community.DoesNotExist:
            raise serializers.ValidationError('コミュニティが存在しません。')
        
        # 投稿またはコメントが存在し、指定されたコミュニティに属しているかチェック
        if post_id:
            try:
                post = Post.objects.get(pk=post_id, is_deleted=False)
                if post.community_id != community_id:
                    raise serializers.ValidationError('投稿が指定されたコミュニティに属していません。')
                attrs['content_type'] = Report.ContentType.POST
                attrs['content_object_id'] = post_id
            except Post.DoesNotExist:
                raise serializers.ValidationError('投稿が存在しません。')
        
        if comment_id:
            try:
                comment = Comment.objects.get(pk=comment_id, is_deleted=False)
                if comment.community_id != community_id:
                    raise serializers.ValidationError('コメントが指定されたコミュニティに属していません。')
                attrs['content_type'] = Report.ContentType.COMMENT
                attrs['content_object_id'] = comment_id
            except Comment.DoesNotExist:
                raise serializers.ValidationError('コメントが存在しません。')
        
        # post_idとcomment_idを削除（content_typeとcontent_object_idを使用）
        attrs.pop('post_id', None)
        attrs.pop('comment_id', None)
        
        return attrs

    def create(self, validated_data):
        """報告を作成"""
        from communities.models import Community
        
        request = self.context.get('request')
        community_id = validated_data.pop('community_id')
        content_type = validated_data.pop('content_type')
        content_object_id = validated_data.pop('content_object_id')
        
        community = Community.objects.get(pk=community_id)
        
        return Report.objects.create(
            reporter=request.user,
            community=community,
            content_type=content_type,
            content_object_id=content_object_id,
            **validated_data
        )

