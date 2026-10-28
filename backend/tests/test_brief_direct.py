import pytest

from app.core.errors import ApiError
from app.modules.workflow import brief_direct, brief_vision
from test_brief_vision import prepare, wait
from test_script_versions import K


def test_all_original_pages_in_one_request_and_trusts_model(client, monkeypatch):
    project, _, body = prepare(client, page_count=27)
    body['extractFacts'] = True
    calls = []

    def model(config, secret, snapshot, images):
        calls.append(snapshot)
        assert len(images) == 27
        assert snapshot['visionStage'] == 'direct-summary'
        assert snapshot['nativeTextBlocks'] == []
        assert 'stagePrompts' not in snapshot
        return {'report': [{'group': group, 'text': group, 'pageNo': 20, 'quote': '原图中的要求'}
                           for group in ('brand', 'product', 'sellingPoint', 'common', 'video', 'image', 'copy')]}

    monkeypatch.setattr(brief_vision, 'call_vision', model)
    started = client.post(f'/api/v1/projects/{project}/brief/vision', json=body, headers={'Idempotency-Key': K()})
    assert started.status_code == 202
    ready = wait(client, project)
    run = ready['visualRun']
    assert run['status'] == 'succeeded', run
    assert len(calls) == 1
    assert run['snapshot']['pipelineVersion'] == brief_direct.PIPELINE_VERSION
    assert set(ready['directSummary']['rules']) == {'common', 'video', 'image', 'copy'}
    assert set(ready['directSummary']['productFacts']) == {'brand', 'product', 'sellingPoint'}
    assert ready['directSummary']['rules']['video'][0]['citationStatus'] == 'model'
    assert not any(b.get('origin') == 'ai-visual-extraction' for b in ready['blocks'])
    assert run['result']['rawResponse']['report'][0]['group'] == 'brand'
    cards = client.get(f'/api/v1/projects/{project}/brief/facts').json()['data']
    assert cards['lastRun']['result']['directSummary'] == ready['directSummary']
    assert cards['lastRun']['result']['factsComplete']
    assert cards['lastRun']['result']['pendingFacts'] == []
    response = client.post(f'/api/v1/projects/{project}/brief/summary', json={'sourceVersionId':ready['sourceVersionId'],'runId':run['id'],'expectedUpdatedAt':ready['summaryUpdatedAt'],'group':'product','index':0,'text':'修正后的产品名'})
    assert response.status_code == 200, response.text
    updated = client.get(f'/api/v1/projects/{project}/brief').json()['data']['brief']
    assert updated['directSummary']['productFacts']['product'][0]['text'] == '修正后的产品名'
    assert updated['visualRun']['result']['rawResponse']['report'][1]['text'] == 'product'
    assert updated['visualRun']['result']['facts']['products'][0]['value'] == '修正后的产品名'
    assert client.post(f'/api/v1/projects/{project}/brief/summary', json={'sourceVersionId':'old-source','runId':run['id'],'expectedUpdatedAt':ready['summaryUpdatedAt'],'group':'product','index':0,'text':'过期修改'}).status_code == 409


def test_no_quote_verification_but_page_number_must_exist():
    snapshot = {'pageCount': 2, 'nativeTextBlocks': [{'id': 'native', 'pageNo': 1, 'text': '品牌\n产品原名'}]}
    report = [{'group': 'brand', 'text': '品牌', 'pageNo': 1, 'quote': '品牌'},
              {'group': 'product', 'text': '产品原名', 'pageNo': 2, 'quote': '产品原名'}]
    result = brief_direct.summarize_response(snapshot, {'report': report})
    assert result['facts']['brand']['blockId'].startswith('summary:')
    assert result['facts']['products'][0]['value'] == '产品原名'
    assert result['pendingFacts'] == []
    assert result['directSummary']['productFacts']['product'][0]['citationStatus'] == 'model'
    report[1]['pageNo'] = 3
    with pytest.raises(ApiError): brief_direct.summarize_response(snapshot, {'report': report})


def test_invalid_reply_is_saved_and_never_retried(client, monkeypatch):
    project, _, body = prepare(client)
    body['extractFacts'] = True
    calls = []
    def model(*args):
        calls.append(True)
        return {'report': [{'group': 'video', 'text': '要求', 'pageNo': 99, 'quote': '引文'}]}
    monkeypatch.setattr(brief_vision, 'call_vision', model)
    client.post(f'/api/v1/projects/{project}/brief/vision', json=body, headers={'Idempotency-Key': K()})
    run = wait(client, project)['visualRun']
    assert run['status'] == 'failed'
    assert run['errorCode'] == 'BRIEF_VISION_PAGE_MISMATCH'
    assert run['result']['rawResponse']['report'][0]['pageNo'] == 99
    assert len(calls) == 1
