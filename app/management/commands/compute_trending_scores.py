"""
トレンドスコアを計算してDBに保存するDjango管理コマンド

使用方法:
    python manage.py compute_trending_scores
    python manage.py compute_trending_scores --lookback-hours 168
    python manage.py compute_trending_scores --half-life-hours 6.0
"""

from django.core.management.base import BaseCommand
from django.db.models import Q, Count
from django.utils import timezone
from datetime import timedelta
from posts.models import Post
from posts.views import calculate_trending_score
from communities.models import Community


class Command(BaseCommand):
    help = '全投稿のトレンドスコアを計算してDBに保存します'

    def add_arguments(self, parser):
        parser.add_argument(
            '--lookback-hours',
            type=float,
            default=168.0,  # デフォルト7日間
            help='何時間前までの投稿を対象にするか（デフォルト: 168時間 = 7日間）'
        )
        parser.add_argument(
            '--half-life-hours',
            type=float,
            default=6.0,
            help='トレンドスコアの半減期（時間）（デフォルト: 6.0時間）'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=1000,
            help='一度に処理する投稿数（デフォルト: 1000）'
        )

    def handle(self, *args, **options):
        lookback_hours = options['lookback_hours']
        half_life_hours = options['half_life_hours']
        batch_size = options['batch_size']

        now = timezone.now()
        cutoff_time = now - timedelta(hours=lookback_hours)

        self.stdout.write(f'トレンドスコア計算を開始します...')
        self.stdout.write(f'対象期間: {cutoff_time} 以降（{lookback_hours}時間前まで）')
        self.stdout.write(f'半減期: {half_life_hours}時間')
        self.stdout.write(f'バッチサイズ: {batch_size}')

        # 対象となる投稿を取得（公開コミュニティの投稿のみ、削除されていないもの）
        qs = Post.objects.filter(
            is_deleted=False,
            community__visibility=Community.Visibility.PUBLIC,
            created_at__gte=cutoff_time
        ).annotate(
            active_comments=Count('comments', filter=Q(comments__is_deleted=False))
        ).select_related('community', 'author').only(
            'id', 'score', 'votes_total', 'created_at', 'trending_score'
        )

        total_count = qs.count()
        self.stdout.write(f'対象投稿数: {total_count}件')

        if total_count == 0:
            self.stdout.write(self.style.WARNING('対象となる投稿がありません。'))
            return

        updated_count = 0
        processed_count = 0

        # バッチ処理でスコアを計算して更新
        for offset in range(0, total_count, batch_size):
            batch = list(qs[offset:offset + batch_size])
            
            updates = []
            for post in batch:
                upvotes = max(int((post.votes_total + post.score) / 2), 0)
                downvotes = max(post.votes_total - upvotes, 0)
                comment_count = getattr(post, 'active_comments', 0)
                
                trending_score = calculate_trending_score(
                    upvotes,
                    downvotes,
                    post.created_at,
                    comment_count=comment_count,
                    now=now,
                    half_life_hours=half_life_hours,
                )
                
                # スコアが変更された場合のみ更新リストに追加
                if abs(post.trending_score - trending_score) > 0.0001:  # 浮動小数点の誤差を考慮
                    updates.append((post.id, trending_score))
            
            # バルク更新
            if updates:
                from django.db import transaction
                with transaction.atomic():
                    for post_id, score in updates:
                        Post.objects.filter(pk=post_id).update(trending_score=score)
                updated_count += len(updates)
            
            processed_count += len(batch)
            
            # 進捗を表示
            if processed_count % 100 == 0 or processed_count == total_count:
                self.stdout.write(
                    f'進捗: {processed_count}/{total_count}件処理完了 '
                    f'({updated_count}件更新)'
                )

        # 対象期間外の投稿のスコアを0にリセット（オプション）
        # これにより、古い投稿のスコアが残り続けることを防ぐ
        old_posts_count = Post.objects.filter(
            is_deleted=False,
            community__visibility=Community.Visibility.PUBLIC,
            created_at__lt=cutoff_time,
            trending_score__gt=0.0
        ).update(trending_score=0.0)

        if old_posts_count > 0:
            self.stdout.write(
                self.style.WARNING(
                    f'対象期間外の投稿 {old_posts_count}件のスコアを0にリセットしました。'
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                f'\n完了: {processed_count}件処理、{updated_count}件更新'
            )
        )

