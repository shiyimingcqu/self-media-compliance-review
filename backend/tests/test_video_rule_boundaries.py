import copy
from app.modules.workflow.video_rule_boundaries import assessment_boundaries


def rules():
    return [
        {'id': 'name', 'text': '须准确口播产品名称芝士浓奶铁', 'criterion': '认可简称', 'importance': 'must'},
        {'id': 'presence', 'text': '产品展示可辨认', 'importance': 'must'},
        {'id': 'script', 'text': '已通过脚本的执行一致性', 'importance': 'must'},
        {'id': 'subtitle', 'text': '口播与字幕一致且卖点落实', 'importance': 'must'},
    ]


def test_scopes_route_name_concerns_without_rewriting_frozen_requirements():
    source = rules(); original = copy.deepcopy(source)
    result = assessment_boundaries(source)['rules']
    assert source == original
    assert result['name']['nameChecksHandledBy'] == []
    for rid in ('presence', 'script', 'subtitle'):
        assert result[rid]['nameChecksHandledBy'] == [{'rule_id': 'name', 'text': source[0]['text']}]
        assert '本条明确写出的条件' in result[rid]['literalRequirements']
    assert '实际出现' in result['presence']['focus']
    assert '核心环节' in result['script']['focus']


def test_clip_scope_keeps_owner_reference_without_adding_owner_to_clip_rules():
    scoped = assessment_boundaries(rules(), {'subtitle'})
    assert set(scoped['rules']) == {'subtitle'}
    assert scoped['rules']['subtitle']['nameChecksHandledBy'][0]['rule_id'] == 'name'


def test_no_name_owner_means_no_invented_exemption_or_changed_thresholds():
    source = [{'id':'custom', 'text':'产品实际露出至少3秒且标识清晰', 'componentPlan':{'operator':'all'}}]
    assert assessment_boundaries(source)['rules']['custom']['nameChecksHandledBy'] == []


def test_unnamed_competitor_policy_reaches_whole_video_and_clip_boundaries():
    source=[{'id':'competitor','text':'不出现指定竞品','baselineKey':'competitor','params':{'names':[]}}]
    for selected in (None,{'competitor'}):
        policy=assessment_boundaries(source,selected)['rules']['competitor']['competitorPolicy']
        assert '名单是可选资料' in policy
        assert '不自动算竞品' in policy
