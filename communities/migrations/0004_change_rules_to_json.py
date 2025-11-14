from django.db import migrations, models
import json


def convert_rules_to_json(apps, schema_editor):
    """既存のrulesテキストをJSON形式に変換"""
    Community = apps.get_model('communities', 'Community')
    # 既存のデータを取得（まだTextFieldなので）
    for community in Community.objects.all():
        if community.rules:
            # 既存のテキストがある場合、空でない場合は単一のルールとして変換
            rules_text = str(community.rules).strip()
            if rules_text:
                # 既存のテキストを1つのルール項目として保存
                rules_json = json.dumps([{"title": "ルール", "description": rules_text}], ensure_ascii=False)
            else:
                rules_json = json.dumps([], ensure_ascii=False)
        else:
            rules_json = json.dumps([], ensure_ascii=False)
        
        # schema_editor.executeを使う方法（デバッグSQLの問題を回避）
        # シングルクォートでJSON文字列をエスケープ
        rules_json_escaped = rules_json.replace("'", "''")
        schema_editor.execute(
            f"UPDATE communities_community SET rules = '{rules_json_escaped}' WHERE id = {community.id}"
        )


def convert_rules_to_text(apps, schema_editor):
    """JSON形式のrulesをテキストに戻す（ロールバック用）"""
    Community = apps.get_model('communities', 'Community')
    for community in Community.objects.all():
        rules_json = community.rules
        if rules_json:
            try:
                rules_list = json.loads(rules_json) if isinstance(rules_json, str) else rules_json
                if isinstance(rules_list, list) and rules_list:
                    text_parts = []
                    for i, rule in enumerate(rules_list, 1):
                        title = rule.get('title', f'ルール{i}')
                        desc = rule.get('description', '')
                        if desc:
                            text_parts.append(f"{i}. {title} -> {desc}")
                    rules_text = "\n".join(text_parts) if text_parts else ""
                else:
                    rules_text = ""
            except (json.JSONDecodeError, TypeError):
                rules_text = str(rules_json) if rules_json else ""
        else:
            rules_text = ""
        
        # schema_editor.executeを使う方法
        rules_text_escaped = rules_text.replace("'", "''")
        schema_editor.execute(
            f"UPDATE communities_community SET rules = '{rules_text_escaped}' WHERE id = {community.id}"
        )


class Migration(migrations.Migration):

    dependencies = [
        ('communities', '0003_communityblock'),
    ]

    operations = [
        # まずデータを変換（TextFieldのまま）
        migrations.RunPython(convert_rules_to_json, convert_rules_to_text, atomic=False),
        # その後フィールドをJSONFieldに変更
        migrations.AlterField(
            model_name='community',
            name='rules',
            field=models.JSONField(blank=True, default=list),
        ),
    ]

