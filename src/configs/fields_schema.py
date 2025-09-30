"""Centralized field schema definitions for API objects."""

from __future__ import annotations

from typing import Dict, Iterable, Mapping, Optional


def _build_schema(
    label_map: Mapping[str, str],
    keys: Optional[Iterable[str]] = None,
) -> Dict[str, Dict[str, str]]:
    schema: Dict[str, Dict[str, str]] = {}
    target_keys = list(keys) if keys is not None else list(label_map.keys())
    for field in target_keys:
        if field not in label_map:
            raise KeyError(f"Missing zh_tw label for field '{field}'")
        schema[field] = {'zh_tw': label_map[field]}
    return schema


_CAMPAIGN_FIELD_LABELS = {
    'id': '行銷活動 ID',
    'account_id': '廣告帳戶 ID',
    'name': '行銷活動名稱',
    'status': '行銷活動狀態',
    'configured_status': '設定狀態',
    'effective_status': '實際狀態',
    'objective': '行銷目標',
    'start_time': '開始時間',
    'stop_time': '結束時間',
    'daily_budget': '每日預算',
    'lifetime_budget': '總預算',
    'spend_cap': '花費上限',
    'buying_type': '購買方式',
    'bid_strategy': '出價策略',
    'promoted_object': '推廣物件',
    'special_ad_categories': '特殊廣告類別',
    'created_time': '建立時間',
    'updated_time': '更新時間',
}


CAMPAIGN_GET_FIELDS = _build_schema(_CAMPAIGN_FIELD_LABELS)
CAMPAIGN_POST_FIELDS = _build_schema(
    _CAMPAIGN_FIELD_LABELS,
    keys=(
        'name',
        'objective',
        'status',
        'special_ad_categories',
        'buying_type',
        'bid_strategy',
        'start_time',
        'stop_time',
        'daily_budget',
        'lifetime_budget',
        'spend_cap',
        'promoted_object',
    ),
)


_ADSET_FIELD_LABELS = {
    'id': '廣告組 ID',
    'account_id': '廣告帳戶 ID',
    'campaign_id': '行銷活動 ID',
    'name': '廣告組名稱',
    'status': '廣告組狀態',
    'configured_status': '設定狀態',
    'effective_status': '實際狀態',
    'daily_budget': '每日預算',
    'lifetime_budget': '總預算',
    'budget_remaining': '剩餘預算',
    'start_time': '開始時間',
    'end_time': '結束時間',
    'pacing_type': '投放節奏',
    'adset_schedule': '廣告組排程',
    'bid_strategy': '出價策略',
    'bid_amount': '出價金額',
    'billing_event': '計費事件',
    'optimization_goal': '最佳化目標',
    'promoted_object': '推廣物件',
    'targeting': '受眾鎖定',
    'attribution_spec': '歸因設定',
    'is_dynamic_creative': '是否使用動態素材',
    'learning_stage_info': '學習階段資訊',
    'issues_info': '問題資訊',
    'recommendations': '系統建議',
    'created_time': '建立時間',
    'updated_time': '更新時間',
    'financial_services_declaration_section':'不知道',
}


ADSET_GET_FIELDS = _build_schema(_ADSET_FIELD_LABELS)
ADSET_POST_FIELDS = _build_schema(
    _ADSET_FIELD_LABELS,
    keys=(
        'name',
        'campaign_id',
        'status',
        'daily_budget',
        'lifetime_budget',
        'start_time',
        'end_time',
        'pacing_type',
        'bid_strategy',
        'bid_amount',
        'billing_event',
        'optimization_goal',
        'promoted_object',
        'targeting',
        'attribution_spec',
        'is_dynamic_creative',
        'financial_services_declaration_section',
    ),
)


_AD_FIELD_LABELS = {
    'id': '廣告 ID',
    'name': '廣告名稱',
    'status': '廣告狀態',
    'campaign_id': '行銷活動 ID',
    'adset_id': '廣告組 ID',
    'creative{id}': '素材 ID',
    'creative': '素材設定',
    'created_time': '建立時間',
    'updated_time': '更新時間',
}


AD_GET_FIELDS = _build_schema(
    _AD_FIELD_LABELS,
    keys=(
        'id',
        'name',
        'status',
        'campaign_id',
        'adset_id',
        'creative{id}',
        'created_time',
        'updated_time',
    ),
)
AD_POST_FIELDS = _build_schema(
    _AD_FIELD_LABELS,
    keys=(
        'name',
        'status',
        'adset_id',
        'creative',
    ),
)


_CREATIVE_FIELD_LABELS = {
    'id': '素材 ID',
    'name': '素材名稱',
    'status': '素材狀態',
    'object_story_spec': '故事素材設定',
    'asset_feed_spec': '素材資源設定',
    'image_url': '圖片網址',
    'video_id': '影片 ID',
    'thumbnail_url': '縮圖網址',
    'effective_object_story_id': '實際故事 ID',
    'body': '廣告內文',
    'title': '素材標題',
    'call_to_action_type': '行動號召類型',
    'instagram_actor_id': 'Instagram 角色 ID',
    'url_tags': '網址參數',
    'message': '廣告訊息',
    'headline': '廣告標題',
}


CREATIVE_GET_FIELDS = _build_schema(
    _CREATIVE_FIELD_LABELS,
    keys=(
        'id',
        'name',
        'status',
        'object_story_spec',
        'asset_feed_spec',
        'image_url',
        'video_id',
        'thumbnail_url',
        'effective_object_story_id',
    ),
)
CREATIVE_POST_FIELDS = _build_schema(
    _CREATIVE_FIELD_LABELS,
    keys=(
        'name',
        'object_story_spec',
        'body',
        'title',
        'image_url',
        'thumbnail_url',
        'video_id',
        'call_to_action_type',
        'instagram_actor_id',
        'url_tags',
        'message',
        'headline',
    ),
)


_INSIGHT_FIELD_LABELS = {
    'ad_id': '廣告 ID',
    'adset_id': '廣告組 ID',
    'campaign_id': '行銷活動 ID',
    'date_start': '起始日期',
    'date_stop': '結束日期',
    'spend': '花費',
    'impressions': '曝光數',
    'reach': '觸及人數',
    'frequency': '觸及頻率',
    'clicks': '點擊數',
    'unique_clicks': '唯一點擊數',
    'inline_link_clicks': '連結點擊數',
    'inline_post_engagement': '貼文互動數',
    'cpc': '平均每次點擊成本',
    'cpm': '平均每千次曝光成本',
    'ctr': '點擊率',
    'unique_ctr': '唯一點擊率',
    'website_ctr': '網站點擊率',
    'actions': '動作數據',
    'action_values': '動作價值',
    'cost_per_action_type': '各動作成本',
    'purchase_roas': '購買投資報酬率',
}


INSIGHT_GET_FIELDS = _build_schema(_INSIGHT_FIELD_LABELS)
INSIGHT_DEMOGRAPHIC_GET_FIELDS = _build_schema(
    _INSIGHT_FIELD_LABELS,
    keys=(
        'ad_id',
        'date_start',
        'date_stop',
        'spend',
        'impressions',
        'reach',
        'frequency',
        'clicks',
        'unique_clicks',
        'inline_link_clicks',
        'inline_post_engagement',
        'cpc',
        'cpm',
        'ctr',
        'unique_ctr',
        'website_ctr',
    ),
)
INSIGHT_ACTION_TYPE_GET_FIELDS = _build_schema(
    _INSIGHT_FIELD_LABELS,
    keys=(
        'ad_id',
        'date_start',
        'date_stop',
        'spend',
        'impressions',
        'reach',
        'clicks',
        'unique_clicks',
        'actions',
        'action_values',
        'cost_per_action_type',
        'purchase_roas',
    ),
)
INSIGHT_AD_SUMMARY_FIELDS = _build_schema(
    _INSIGHT_FIELD_LABELS,
    keys=(
        'ad_id',
        'adset_id',
        'campaign_id',
        'reach',
        'frequency',
    ),
)
INSIGHT_ADSET_SUMMARY_FIELDS = _build_schema(
    _INSIGHT_FIELD_LABELS,
    keys=(
        'adset_id',
        'reach',
        'frequency',
    ),
)
INSIGHT_CAMPAIGN_SUMMARY_FIELDS = _build_schema(
    _INSIGHT_FIELD_LABELS,
    keys=(
        'campaign_id',
        'reach',
        'frequency',
    ),
)
INSIGHT_POST_FIELDS: Dict[str, Dict[str, str]] = {}


__all__ = [
    'CAMPAIGN_GET_FIELDS',
    'CAMPAIGN_POST_FIELDS',
    'ADSET_GET_FIELDS',
    'ADSET_POST_FIELDS',
    'AD_GET_FIELDS',
    'AD_POST_FIELDS',
    'CREATIVE_GET_FIELDS',
    'CREATIVE_POST_FIELDS',
    'INSIGHT_GET_FIELDS',
    'INSIGHT_DEMOGRAPHIC_GET_FIELDS',
    'INSIGHT_ACTION_TYPE_GET_FIELDS',
    'INSIGHT_AD_SUMMARY_FIELDS',
    'INSIGHT_ADSET_SUMMARY_FIELDS',
    'INSIGHT_CAMPAIGN_SUMMARY_FIELDS',
    'INSIGHT_POST_FIELDS',
]
