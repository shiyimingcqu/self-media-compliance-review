import json
import pytest

from app.core.database import connection
from app.modules.workflow import standards, router as workflow
from app.modules.workflow.video_layers import defaults, runnable, normalize_candidates
from app.modules.video_model.evidence.component_runtime import evaluate
from test_project_standards import rules as project_payload, add
from test_separate_standards import project
from test_script_versions import task_with_rule, K


def text_rule(layer='brief', **extra):
    return {'text': '品牌信息传达', 'criterion': '核对商业信息，合理同义表达允许', 'importance': 'suggested',
            'layer': layer, 'enabled': True, **extra}


@pytest.mark.parametrize('key', ['brief-script-standards', 'brief-video-standards', 'brief-video-layered'])
def test_rule_generation_uses_readable_brief_specific_names(key):
    import hashlib
    from app.modules.workflow.prompts import prompt
    template = prompt(key)
    assert '【品牌可直接读懂的规则名称】' in template['content']
    assert '动作或要求 + 具体对象 + 必要条件' in template['content']
    assert '必须换成当前输入中的真实事实' in template['content']
    assert template['version'] == hashlib.sha256(template['content'].encode()).hexdigest()[:12]


def test_defaults_sort_disabled_storage_and_suggested_only_are_ready(client):
    p = project(client)['id']
    baseline = client.get('/api/v1/workflow/video-rule-defaults').json()['data']['rules']
    assert [r['baselineKey'] for r in baseline] == ['competitor', 'product', 'script_match', 'identity', 'information_accuracy', 'understandability']
    assert [r['enabled'] for r in baseline] == [False, True, False, True, True, True]
    assert baseline[-1]['importance'] == 'suggested'
    body = project_payload(client)
    body['videoRules'] = [text_rule('precise', enabled=False, params={'thresholds': [{'key': 'minTotalSeconds', 'value': 5}]}), text_rule(), baseline[1]]
    url = f'/api/v1/projects/{p}/standards/confirm'
    response = client.post(url, json=body, headers={'Idempotency-Key': K()})
    assert response.status_code == 200, response.text
    saved = client.get(f'/api/v1/projects/{p}/standards').json()['data']
    video = saved['video']['rules']
    assert [r['layer'] for r in video] == ['baseline', 'brief', 'precise']
    assert [r['ruleNumber'] for r in video] == [1, 2, 3]
    assert video[2]['enabled'] is False and video[2]['params']['thresholds'][0]['value'] == 5
    ready, pending = runnable(video)
    assert [r['ruleNumber'] for r in ready] == [2]
    assert pending[0]['ruleNumber'] == 1 and pending[0]['reason'] == '待品牌确认产品名称'
    body['revision'] = 1
    body['videoRules'] = [text_rule()]
    assert client.post(url, json=body, headers={'Idempotency-Key': K()}).status_code == 200
    assert client.get(f'/api/v1/projects/{p}/standards').json()['data']['ready'] is True
    task = add(client, p, '仅第二层建议任务')
    assert client.get(f'/api/v1/tasks/{task}/workflow').json()['data']['rulesReady'] is True
    body['revision'] = 2
    body['videoRules'][0]['enabled'] = False
    assert client.post(url, json=body, headers={'Idempotency-Key': K()}).status_code == 400


def test_enabled_precise_requires_plan_and_layers_do_not_apply_to_script(client):
    p = project(client)['id']; body = project_payload(client)
    body['videoRules'] = [text_rule('precise')]
    r = client.post(f'/api/v1/projects/{p}/standards/confirm', json=body, headers={'Idempotency-Key': K()})
    assert r.status_code == 400 and r.json()['error']['code'] == 'VIDEO_COMPONENT_REQUIRED'
    body['videoRules'] = [text_rule()]
    body['scriptRules'][0]['layer'] = 'baseline'
    assert client.post(f'/api/v1/projects/{p}/standards/confirm', json=body, headers={'Idempotency-Key': K()}).status_code == 400


def test_all_enabled_must_layers_gate_approval_and_missing_configuration_is_not_content_failure():
    base = {'id': 'base', 'ruleNumber': 1, **defaults()[1]}
    brief = {'id': 'brief', 'ruleNumber': 2, **text_rule(importance='must')}
    optional = {'id': 'optional', 'ruleNumber': 3, **text_rule()}
    disabled = {'id': 'disabled', 'ruleNumber': 4, **text_rule(importance='must', enabled=False)}
    snapshot = {'rules': [base, brief, optional, disabled], 'unconfiguredRules': runnable([base])[1]}
    result = {'report': {'findings': [{'rule_id': 'brief', 'verdict': 'compliant'}, {'rule_id': 'optional', 'verdict': 'non_compliant'}]}}
    row = {'status': 'succeeded', 'snapshot_json': json.dumps(snapshot), 'result_json': json.dumps(result)}
    acceptance = workflow.video_component_acceptance(row)
    assert acceptance['passed'] is False and acceptance['configurationRequired'] is True
    assert [r['ruleId'] for r in acceptance['blockingRules']] == ['base']
    base['enabled'] = False; snapshot['unconfiguredRules'] = []
    row['snapshot_json'] = json.dumps(snapshot)
    assert workflow.video_component_acceptance(row)['passed'] is True
    result['report']['findings'][0]['verdict'] = 'needs_confirmation'
    row['result_json'] = json.dumps(result)
    pending = workflow.video_component_acceptance(row)
    assert pending['passed'] is True and pending['reviewCount'] == 1  # 待确认交人工，不再阻断
    result['report']['findings'][0]['verdict'] = 'non_compliant'
    row['result_json'] = json.dumps(result)
    assert workflow.video_component_acceptance(row)['blockingRules'][0]['layer'] == 'brief'


def test_layered_generation_is_disabled_candidates_and_idempotent(client, monkeypatch):
    p, task = task_with_rule(client)
    with connection(client.app.state.settings.database_path) as db:
        source = db.execute('SELECT source_version_id FROM rule_versions WHERE id=?', (task['ruleVersionId'],)).fetchone()[0]
    client.put('/api/v1/settings/models/decomposition', json={'endpoint': 'https://models.example.com/v1', 'model': 'model1', 'revision': 0, 'apiKey': 'sk-test-only'})
    calls = []
    def model(config, key, prompt, blocks, uncovered):
        calls.append(prompt['key'])
        assert '默认 importance=must' in prompt['content']
        assert '只有原文明示' in prompt['content']
        return {'report': {'rules': [text_rule(enabled=True, importance='must'), text_rule('precise', enabled=True, params={'thresholds': [{'key': 'minTotalSeconds', 'value': 5}, {'key': 'minOccurrences', 'value': 3}]})], 'warnings': []}}
    monkeypatch.setattr(standards, 'call_recommendation', model)
    body = {'sourceVersionId': source, 'scope': 'video', 'videoRuleMode': 'layered', 'configRevision': 1, 'allowRealCall': True}
    url = f"/api/v1/projects/{p['id']}/standard-recommendations"; key = K()
    first = client.post(url, json=body, headers={'Idempotency-Key': key}).json()['data']
    assert first['status'] == 'succeeded'
    candidates = first['result']['report']['rules']
    assert [(r['layer'], r['enabled'], r['importance']) for r in candidates] == [('brief', False, 'must'), ('precise', False, 'must')]
    assert candidates[1]['componentPlan'] is None
    assert candidates[1]['params']['thresholds'][0]['value'] == 5
    assert client.post(url, json=body, headers={'Idempotency-Key': key}).json()['data']['id'] == first['id']
    assert calls == ['brief-video-layered']
    assert client.get(f"/api/v1/projects/{p['id']}/standards").json()['data']['video'] is None
    unsupported = standards.RecommendedStandards.model_validate({'rules': [text_rule('precise', params={'thresholds': [{'key': 'moodScore', 'value': 9}]})]})
    assert normalize_candidates(unsupported).rules[0].layer == 'brief'


@pytest.mark.parametrize('importance', ['must', 'suggested'])
@pytest.mark.parametrize('rule_layer', ['brief', 'precise'])
def test_candidate_importance_is_preserved_for_text_and_unsupported_components(importance, rule_layer):
    report = standards.RecommendedStandards.model_validate({'rules': [text_rule(
        rule_layer, importance=importance, params={'thresholds': [{'key': 'moodScore', 'value': 9}]})]})
    candidate = normalize_candidates(report).rules[0]
    assert candidate.layer == 'brief'
    assert candidate.importance == importance
    assert candidate.enabled is False and candidate.component_plan is None


def test_duration_does_not_invent_unspecified_continuous_threshold():
    from app.modules.workflow.video_components import ComponentNode
    from app.modules.video_model.evidence.component_runtime import ComponentObservationInput
    node = ComponentNode.model_validate({'id': 'node', 'kind': 'exposure_duration', 'parameters': {'referenceImageIds': ['ref'], 'minTotalSeconds': 5, 'minContinuousSeconds': None}})
    assert node.parameters.min_continuous_seconds is None
    with pytest.raises(ValueError):
        ComponentNode.model_validate({'id': 'node', 'kind': 'exposure_duration', 'parameters': {'referenceImageIds': ['ref'], 'minTotalSeconds': None, 'minContinuousSeconds': None}})
    class Session:
        duration_ms = 20_000; has_audio = True; component_results = {}
        def rule_source(self, identity):
            return {'id': 'r', 'componentPlan': {'operator': 'all', 'sameSegment': False, 'nodes': [{**node.model_dump(by_alias=True), 'prompt': {'version': 'test'}}]}}
        def save_manifest(self): pass
    observations = [{'start_ms': n * 2000, 'end_ms': n * 2000 + 500, 'matched': True, 'certain': True, 'occluded': False, 'description': '可辨认产品'} for n in range(10)]
    value = evaluate(Session(), 'exposure_duration', ComponentObservationInput(rule_id='r', component_id='node', issue_id='i', coverage_complete=True, observations=observations))
    assert value['verdict'] == 'compliant' and value['metrics']['longestContinuousMs'] == 500


def test_prepare_run_only_sends_configured_enabled_rules_but_freezes_all(client, monkeypatch, tmp_path):
    from test_workflow import configure, state, analyze, decide, mock_script, submit
    from app.modules.video_model.evidence import console_service as evidence
    from app.modules.video_model.evidence.console_schemas import EvidenceRunInput
    from app.core.errors import ApiError
    import imageio_ffmpeg
    import subprocess

    project_data, task = task_with_rule(client)
    configure(client, task)
    # Save layered rules before script approval, so normal invalidation still applies.
    body = project_payload(client)
    body['revision'] = 1
    body['videoRules'] = [defaults()[1], text_rule(importance='must'), text_rule(enabled=False)]
    response = client.post(f"/api/v1/projects/{project_data['id']}/standards/confirm", json=body, headers={'Idempotency-Key': K()})
    assert response.status_code == 200, response.text
    submit(client, task['id'], {'inputType': 'text', 'text': '展示产品的核心商业内容'})
    monkeypatch.setattr(workflow, 'call_script_model', mock_script)
    assert analyze(client, task, state(client, task)).json()['data']['status'] == 'succeeded'
    assert decide(client, task, state(client, task)).status_code == 200
    source = tmp_path / 'layered.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-f', 'lavfi', '-i', 'color=s=160x120:d=1', '-c:v', 'libx264', str(source)], check=True, timeout=20)
    uploaded = client.post('/api/v1/files', files={'file': ('layered.mp4', source.read_bytes(), 'video/mp4')}, data={'purpose': 'video'}, headers={'Idempotency-Key': K()}).json()['data']
    payload = EvidenceRunInput(kind='video', config_revision=1, allow_real_call=True, task_id=task['id'], file_id=uploaded['id'])
    run_id, trusted = evidence.prepare_run(client.app.state.settings, payload, K())
    snapshot = evidence.get_run(client.app.state.settings, run_id).snapshot
    assert len(snapshot['rules']) == 3
    assert [r['ruleNumber'] for r in trusted['rules']] == [2]
    assert snapshot['unconfiguredRules'][0]['ruleNumber'] == 1
    assert snapshot['executableRuleIds'] == [trusted['rules'][0]['id']]
    import httpx
    def transport(request):
        # A local fixed response, never a vendor request.
        from test_localized_video import reply, row
        return httpx.Response(200, json=reply([row(trusted['rules'][0]['id'])]))
    evidence.execute_run(client.app.state.settings, run_id, trusted, transport=httpx.MockTransport(transport))
    completed = evidence.get_run(client.app.state.settings, run_id)
    assert completed.status == 'succeeded', completed.error_message
    assert completed.result['report']['findings'][0]['layer'] == 'brief'
    assert completed.result['report']['findings'][0]['rule_number'] == 2
    assert completed.result['ruleCoverage']['evaluatedRuleCount'] == 1
    assert completed.result['ruleCoverage']['enabledRuleCount'] == 2
    assert state(client, task)['videoComponentAcceptance']['configurationRequired'] is True
    # Missing configuration must short circuit before queueing any billable work.
    with connection(client.app.state.settings.database_path) as db:
        db.execute("UPDATE evidence_runs SET status='failed' WHERE id=?", (run_id,))
    monkeypatch.setattr(workflow, 'applicable_rules', lambda *_: [{**snapshot['rules'][0], 'enabled': True}])
    with pytest.raises(ApiError) as error:
        evidence.prepare_run(client.app.state.settings, payload, K())
    assert error.value.code == 'VIDEO_CONFIGURATION_REQUIRED'
    with connection(client.app.state.settings.database_path) as db:
        assert db.execute('SELECT count(*) FROM evidence_runs').fetchone()[0] == 1


def test_product_presets_freeze_disabled_drafts_and_validate_component_kind(client):
    p = project(client)['id']; body = project_payload(client)
    preset = text_rule('precise', presetKey='product_count', enabled=False, params={'thresholds': [{'key': 'minOccurrences', 'value': 3}]})
    body['videoRules'] = [text_rule(), preset]
    url = f'/api/v1/projects/{p}/standards/confirm'
    response = client.post(url, json=body, headers={'Idempotency-Key': K()})
    assert response.status_code == 200, response.text
    saved = client.get(f'/api/v1/projects/{p}/standards').json()['data']['video']['rules'][1]
    assert saved['presetKey'] == 'product_count' and saved['enabled'] is False
    assert saved['params']['thresholds'][0]['value'] == 3
    assert saved['componentPlan'] is None
    body['revision'] = 1
    body['videoRules'] = [text_rule(), {**preset, 'enabled': True}]
    assert client.post(url, json=body, headers={'Idempotency-Key': K()}).json()['error']['code'] == 'VIDEO_COMPONENT_REQUIRED'
    body['videoRules'] = [text_rule(), preset, preset]
    assert client.post(url, json=body, headers={'Idempotency-Key': K()}).json()['error']['code'] == 'VIDEO_PRESET_DUPLICATE'
    body['videoRules'] = [{**preset, 'layer': 'brief', 'enabled': True}]
    assert client.post(url, json=body, headers={'Idempotency-Key': K()}).json()['error']['code'] == 'VIDEO_PRESET_INVALID'


@pytest.mark.parametrize('level,count', [('basic', 1), ('custom', 2), ('strict', 3)])
def test_review_level_freezes_and_filters_execution_and_must_gate(client, level, count):
    from app.modules.workflow.video_layers import active_rules
    p = project(client)['id']; body = project_payload(client)
    base = {**defaults()[1], 'params': {'names': ['指定产品']}}
    precise = text_rule('precise', importance='must', presetKey='forbidden_expression', componentPlan={
        'operator': 'all', 'sameSegment': False, 'nodes': [{'id': 'forbidden', 'kind': 'forbidden_expression', 'parameters': {'terms': ['禁词']}}]})
    body.update(videoReviewLevel=level, videoRules=[base, text_rule(importance='must'), precise])
    response = client.post(f'/api/v1/projects/{p}/standards/confirm', json=body, headers={'Idempotency-Key': K()})
    assert response.status_code == 200, response.text
    video = client.get(f'/api/v1/projects/{p}/standards').json()['data']['video']
    assert video['videoReviewLevel'] == level
    assert all(rule['videoReviewLevel'] == level and rule['enabled'] for rule in video['rules'])
    assert len(active_rules(video['rules'])) == count
    ready, pending = runnable(video['rules'])
    assert len(ready) == count and pending == []
    report = {'findings': [{'rule_id': rule['id'], 'verdict': 'compliant' if i == 0 else 'non_compliant'} for i, rule in enumerate(video['rules'])]}
    row = {'status': 'succeeded', 'snapshot_json': json.dumps({'rules': video['rules']}), 'result_json': json.dumps({'report': report})}
    assert workflow.video_component_acceptance(row)['passed'] is (level == 'basic')
    assert len(workflow.video_component_acceptance(row)['blockingRules']) == count - 1
    if level == 'basic':
        # Inactive detailed drafts may be incomplete without preventing basic checks.
        body['revision'] = 1; body['videoRules'][2]['componentPlan'] = None
        assert client.post(f'/api/v1/projects/{p}/standards/confirm', json=body, headers={'Idempotency-Key': K()}).status_code == 200
        body['revision'] = 2; body['videoReviewLevel'] = 'strict'
        assert client.post(f'/api/v1/projects/{p}/standards/confirm', json=body, headers={'Idempotency-Key': K()}).json()['error']['code'] == 'VIDEO_COMPONENT_REQUIRED'


def test_selected_review_level_cannot_save_empty_active_scope(client):
    p = project(client)['id']; body = project_payload(client)
    body.update(videoReviewLevel='basic', videoRules=[text_rule()])
    response = client.post(f'/api/v1/projects/{p}/standards/confirm', json=body, headers={'Idempotency-Key': K()})
    assert response.json()['error']['code'] == 'VIDEO_RULES_DISABLED'

def test_competitor_baseline_requires_enablement_but_names_are_optional():
    competitor = next(r for r in defaults() if r['baselineKey'] == 'competitor')
    competitor['id'] = 'b-competitor'
    product = next(r for r in defaults() if r['baselineKey'] == 'product')
    product['id'] = 'b-product'
    ready, pending = runnable([competitor, product])
    assert ready == []
    assert [r['ruleId'] for r in pending] == ['b-product'] and pending[0]['reason'] == '待品牌确认产品名称'
    competitor['enabled'] = True
    assert runnable([competitor]) == ([competitor], [])
    named = dict(competitor, params={'names': ['瑞幸'], 'terms': [], 'thresholds': []})
    ready2, pending2 = runnable([named])
    assert ready2 == [named] and pending2 == []


def test_baseline_fill_maps_facts_without_overwriting_brand_edits():
    from app.modules.workflow.baseline_fill import baseline_fill
    from app.modules.workflow.brief_facts import BriefFacts
    rules = [dict(r, id=r['baselineKey']) for r in defaults()]
    facts = BriefFacts.model_validate({
        'brand': {'value': '麦咖啡', 'block_id': 'b1', 'quote': '品牌：麦咖啡'},
        'products': [{'value': '芝士浓奶铁', 'block_id': 'b1', 'quote': 'q'}],
        'competitors': [{'value': '瑞幸', 'block_id': 'b1', 'quote': 'q'}],
        'forbiddenTerms': [{'value': '提神醒脑', 'block_id': 'b1', 'quote': 'q'}],
    }).model_dump(by_alias=True)
    rules[0]['params'] = {'names': ['库迪'], 'terms': [], 'thresholds': []}  # 品牌已手改竞品名单
    assert baseline_fill(rules, facts) is True
    assert rules[0]['params']['names'] == ['库迪'] and 'paramsSource' not in rules[0]
    assert rules[1]['params']['names'] == ['芝士浓奶铁'] and rules[1]['paramsSource'] == {'origin': 'briefFacts', 'groups': ['products']}
    forbidden = next((r for r in rules if r['baselineKey'] == 'forbidden'), None)
    if forbidden is None:
        forbidden = {'id': 'forbidden', 'layer': 'baseline', 'baselineKey': 'forbidden', 'enabled': True,
                     'params': {'names': [], 'terms': [], 'thresholds': []}}
        rules.append(forbidden)
    baseline_fill(rules, facts)
    assert forbidden['params']['terms'] == ['提神醒脑']
    assert next(r for r in rules if r['baselineKey'] == 'identity')['params']['names'] == ['麦咖啡', '芝士浓奶铁']
    # 未点名竞品不猜测，也不启用该项。
    no_comp = [dict(r, params={'names': [], 'terms': [], 'thresholds': []}) for r in defaults()]
    baseline_fill(no_comp, {**facts, 'competitors': []})
    assert next(r for r in no_comp if r['baselineKey'] == 'competitor')['params']['names'] == []


def test_whole_video_only_rules_are_detected():
    """脚本一致性类规则（基础 script_match 与生成的文字规则）只做全片判断，不进逐段精判。"""
    from app.modules.workflow.video_layers import whole_video_only
    assert whole_video_only({'baselineKey': 'script_match', 'text': '与已通过脚本基本匹配'}) is True
    assert whole_video_only({'text': '已通过脚本的执行一致性'}) is True
    assert whole_video_only({'text': '实际品牌与产品信息正确'}) is False
    assert whole_video_only({'text': '口播一致性', 'criterion': '对照已通过脚本核对关键镜头'}) is True


def test_script_rules_become_not_applicable_without_approved_script():
    """脚本未提交/未确认时，"脚本一致性"类规则直接判不适用，理由说清是哪一种。"""
    from app.modules.workflow.video_layers import mark_script_rules_unavailable
    rules = [{'id': 'r1', 'text': '实际品牌与产品信息正确'},
             {'id': 'r7', 'text': '已通过脚本的执行一致性'}]
    findings = [{'rule_id': 'r1', 'verdict': 'compliant', 'description': '原描述'},
                {'rule_id': 'r7', 'verdict': 'needs_confirmation', 'description': '原描述'}]
    changed = mark_script_rules_unavailable(findings, rules, 'pending_approval')
    assert changed == ['r7']
    assert findings[0]['verdict'] == 'compliant' and findings[0]['description'] == '原描述'
    assert findings[1]['verdict'] == 'not_applicable'
    assert '已提交' in findings[1]['description'] and '尚未确认通过' in findings[1]['description']

    findings = [{'rule_id': 'r7', 'verdict': 'compliant', 'description': '原描述'}]
    mark_script_rules_unavailable(findings, rules, '')
    assert findings[0]['verdict'] == 'not_applicable' and '未提交脚本' in findings[0]['description']
