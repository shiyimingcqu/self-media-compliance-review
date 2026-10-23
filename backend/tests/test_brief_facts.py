import json

from app.core.database import connection
from app.modules.workflow import brief_facts, standards
from test_project_standards import K
from test_separate_standards import project


def upload_brief(client, p, text='品牌：测试品牌。产品全名：芝士浓奶铁。视频总时长不少于15秒。口播必须提到"浓郁芝士奶香"。禁用词：提神醒脑。', revision=0):
    file = client.post('/api/v1/files', files={'file': ('brief.txt', text.encode(), 'text/plain')},
                       data={'purpose': 'brief'}, headers={'Idempotency-Key': K()}).json()['data']
    response = client.post(f'/api/v1/projects/{p}/brief', json={'fileId': file['id'], 'title': '品牌项目 Brief', 'revision': revision},
                           headers={'Idempotency-Key': K()})
    assert response.status_code == 200, response.text
    return response.json()['data']['brief']


def configure(client):
    client.put('/api/v1/settings/models/decomposition',
               json={'endpoint': 'https://models.example.com/v1', 'model': 'model1', 'revision': 0, 'apiKey': 'sk-test-only'})


def facts_payload(blocks):
    b = blocks[0]['id']
    return {'brand': {'value': '测试品牌', 'block_id': b, 'quote': '品牌：测试品牌。'},
            'background': '年轻化种草投放。',
            'products': [{'value': '芝士浓奶铁', 'block_id': b, 'quote': '产品全名：芝士浓奶铁。'}],
            'sellingPoints': [], 'requiredTerms': [{'value': '浓郁芝士奶香', 'block_id': b, 'quote': '"浓郁芝士奶香"'}],
            'forbiddenTerms': [{'value': '提神醒脑', 'block_id': b, 'quote': '禁用词：提神醒脑。'}],
            'publishRules': [], 'thresholds': [{'value': '视频总时长≥15秒', 'block_id': b, 'quote': '视频总时长不少于15秒。'}],
            'freeform': [], 'warnings': []}


def test_facts_extract_validate_idempotency_and_current_version_only(client, monkeypatch):
    p = project(client)['id']
    brief = upload_brief(client, p)
    configure(client)
    calls = []
    def model(config, secret, template, blocks, uncovered):
        calls.append(len(blocks))
        return {'report': facts_payload(blocks), 'providerRequestIds': ['mock'], 'usage': [], 'modelResponse': '{}'}
    monkeypatch.setattr(brief_facts, 'call_facts', model)
    path = f'/api/v1/projects/{p}/brief/facts'
    body = {'sourceVersionId': brief['sourceVersionId'], 'configRevision': 1, 'allowRealCall': False}
    assert client.post(path, json=body, headers={'Idempotency-Key': K()}).status_code == 400
    assert calls == []
    body['allowRealCall'] = True
    key = K()
    first = client.post(path, json=body, headers={'Idempotency-Key': key})
    assert first.status_code == 201, first.text
    run = first.json()['data']
    assert run['status'] == 'succeeded', (run.get('errorMessage'), run.get('result'))
    assert run['result']['facts']['brand']['value'] == '测试品牌'
    assert client.post(path, json=body, headers={'Idempotency-Key': key}).json()['data']['id'] == run['id']
    assert len(calls) == 1
    view = client.get(path).json()['data']
    assert view['facts']['products'][0]['value'] == '芝士浓奶铁'
    # New Brief version: old facts never leak into the current source view.
    fresh = upload_brief(client, p, text='品牌：测试品牌。', revision=brief['revision'])
    assert client.get(path).json()['data'] == {'sourceVersionId': fresh['sourceVersionId'], 'facts': None, 'lastRun': None}
    # Fabricated citation fails the whole run without partial adoption.
    def fabricated(config, secret, template, blocks, uncovered):
        bad = facts_payload(blocks)
        bad['products'][0] = {'value': '芝士奶铁', 'block_id': 'fake', 'quote': '编造原文'}
        return {'report': bad, 'providerRequestIds': ['mock'], 'usage': [], 'modelResponse': '{}'}
    monkeypatch.setattr(brief_facts, 'call_facts', fabricated)
    failed = client.post(path, json={'sourceVersionId': fresh['sourceVersionId'], 'configRevision': 1, 'allowRealCall': True},
                         headers={'Idempotency-Key': K()}).json()['data']
    assert failed['status'] == 'failed' and 'facts' not in failed['result']
    assert failed['result']['errorCode'] == 'BRIEF_FACTS_CITATION_MISMATCH'
    assert client.get(path).json()['data']['facts'] is None


def test_facts_missing_key_and_busy_are_explicit(client):
    p = project(client)['id']
    brief = upload_brief(client, p)
    body = {'sourceVersionId': brief['sourceVersionId'], 'configRevision': 1, 'allowRealCall': True}
    assert client.post(f'/api/v1/projects/{p}/brief/facts', json=body, headers={'Idempotency-Key': K()}).status_code == 409
    configure(client)
    body['configRevision'] = 1
    with connection(client.app.state.settings.database_path) as db:
        db.execute("INSERT INTO brief_facts_runs VALUES ('r1',?,?,'processing','{}',NULL,NULL,NULL,'2026-01-01','2026-01-01')", (p, brief['sourceVersionId']))
    assert client.post(f'/api/v1/projects/{p}/brief/facts', json=body, headers={'Idempotency-Key': K()}).status_code == 409


def test_recommendation_receives_saved_facts(client, monkeypatch):
    from test_script_versions import task_with_rule
    project_view, task = task_with_rule(client)
    p = project_view['id']
    brief = upload_brief(client, p)
    configure(client)
    def model(config, secret, template, blocks, uncovered):
        return {'report': facts_payload(blocks), 'providerRequestIds': ['mock'], 'usage': [], 'modelResponse': '{}'}
    monkeypatch.setattr(brief_facts, 'call_facts', model)
    client.post(f'/api/v1/projects/{p}/brief/facts',
                json={'sourceVersionId': brief['sourceVersionId'], 'configRevision': 1, 'allowRealCall': True},
                headers={'Idempotency-Key': K()})
    captured = []
    def recommend_model(config, secret, template, blocks, uncovered, facts=None):
        captured.append((template['content'], blocks, facts))
        brand = facts['brand']
        return {'report': {'rules': [{'text': '检查产品名', 'criterion': '台词须逐字包含「芝士浓奶铁」', 'category': '品牌',
                   'importance': 'must', 'source_block_id': brand['blockId'], 'source_quote': brand['quote'],
                   'params': {'names': ['芝士浓奶铁'], 'terms': [], 'thresholds': []}}], 'warnings': []},
                'providerRequestIds': ['mock'], 'usage': []}
    monkeypatch.setattr(standards, 'call_recommendation', recommend_model)
    response = client.post(f'/api/v1/projects/{p}/standard-recommendations',
                           json={'sourceVersionId': brief['sourceVersionId'], 'scope': 'script', 'configRevision': 1, 'allowRealCall': True},
                           headers={'Idempotency-Key': K()})
    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['status'] == 'succeeded'
    content, blocks, facts = captured[0]
    # 事实卡完整且无待核对：只发卡片，Brief 正文不下发（省 token），但仍在服务端做引文校验。
    assert blocks is None
    assert facts and facts['brand']['value'] == '测试品牌'
    assert '【Brief 事实卡】' in content and '芝士浓奶铁' in content and '本次只提供事实卡' in content
    assert data['snapshot']['factsUsed'] is True and data['snapshot']['inputMode'] == 'facts'
    assert data['snapshot']['blocks'] == []


def test_recommendation_falls_back_to_blocks_when_facts_have_pending(client, monkeypatch):
    p = project(client)['id']
    brief = upload_brief(client, p)
    configure(client)
    with connection(client.app.state.settings.database_path) as db:
        blocks = db.execute('SELECT id,page_no,text FROM brief_text_blocks ORDER BY ordinal').fetchall()
        facts = facts_payload([{'id': row['id'], 'pageNo': row['page_no'], 'text': row['text']} for row in blocks])
        result = {'facts': facts, 'pendingFacts': [{'group': 'products', 'value': '待核对新品', 'pageNo': 1, 'source': 'text',
                                                    'quote': '待核对新品', 'reason': '引文存在省略'}], 'factsComplete': False}
        db.execute("INSERT INTO brief_facts_runs VALUES ('r-pending',?,?,'succeeded','{}',?,NULL,NULL,'2026-01-01','2026-01-01')",
                   (p, brief['sourceVersionId'], json.dumps(result, ensure_ascii=False)))
    captured = []
    def recommend_model(config, secret, template, blocks, uncovered, facts=None):
        captured.append((template['content'], blocks, facts))
        return {'report': {'rules': [{'text': '检查产品名', 'criterion': '台词须逐字包含「芝士浓奶铁」', 'category': '品牌',
                   'importance': 'must', 'source_block_id': blocks[0]['id'], 'source_quote': blocks[0]['text'][:20],
                   'params': {'names': ['芝士浓奶铁'], 'terms': [], 'thresholds': []}}], 'warnings': []},
                'providerRequestIds': ['mock'], 'usage': []}
    monkeypatch.setattr(standards, 'call_recommendation', recommend_model)
    response = client.post(f'/api/v1/projects/{p}/standard-recommendations',
                           json={'sourceVersionId': brief['sourceVersionId'], 'scope': 'script', 'configRevision': 1, 'allowRealCall': True},
                           headers={'Idempotency-Key': K()})
    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['status'] == 'succeeded'
    content, blocks, facts = captured[0]
    assert blocks, '存在待核对事实时必须下发 Brief 正文'
    assert '本次只提供事实卡' not in content
    assert data['snapshot']['inputMode'] == 'blocks' and data['snapshot']['factsPendingCount'] == 1


def test_fact_input_compaction_only_drops_proven_same_page_duplicates():
    blocks=[
        {'id':'a','pageNo':1,'text':'产品全名：芝士浓奶铁。'},
        {'id':'keep-number','pageNo':1,'text':'露出至少15秒'},
        {'id':'keep-other-page','pageNo':2,'text':'产品全名：芝士浓奶铁。'},
        {'id':'keep-no-page','pageNo':None,'text':'产品全名：芝士浓奶铁。'},
        {'id':'vision:run:1:text','pageNo':1,'text':'品牌要求：产品全名：芝士浓奶铁。请勿镜像。','origin':'ai-visual-extraction'},
        {'id':'vision:run:1:visual','pageNo':1,'text':'产品正面包装参考图','origin':'ai-visual-extraction'},
    ]
    compact=brief_facts.compact_fact_blocks(blocks)
    assert [b['id'] for b in compact]==['keep-number','keep-other-page','keep-no-page','vision:run:1:text','vision:run:1:visual']
    assert blocks[0]['id']=='a'  # immutable source remains intact
    assert brief_facts.compact_fact_blocks(compact)==compact
    for item in compact:assert item in blocks
