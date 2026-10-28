import json

import pytest

from app.core.database import connection
from app.modules.workflow import standards
from app.modules.workflow.platform_rule_skills import apply_platform_skills
from app.modules.workflow.prompts import prompt, recommendation_prompt
from test_script_versions import task_with_rule, K


KEYS = ('brief-video-standards','brief-video-layered','brief-video-template')


@pytest.mark.parametrize('key',KEYS)
def test_video_generation_shared_contract_loaded_once_and_not_used_for_review(key):
    value=prompt(key)
    content=value['content']
    assert content.count('【品牌方视频验收规则生成总则】')==1
    for required in ('第一组｜交付要求是否落实（50%', '第二组｜事实、合规和舆情风险（30%',
                     '第三组｜品牌呈现与基本观看质量（10%', '第四组｜传播效果预判（10%',
                     '不能抵消任何必须项失败', '不改写、倒序拼接或伪造',
                     '不要求逐字、逐镜头复刻', '不要求用一张截图证明全片不存在',
                     '不自行填默认秒数、次数或比例', '不额外增加评分字段'):
        assert required in content
    for platform in ('xiaohongshu','douyin'):
        combined,context=apply_platform_skills(value,[platform],'video')
        assert '【品牌方视频验收规则生成总则】' in combined['content']
        assert context['priorityProfile']['weightedPass'] is False
    for other in ('script-review','image-review','video-review','video-refine','brief-script-standards','brief-image-standards'):
        assert '【品牌方视频验收规则生成总则】' not in prompt(other)['content']


@pytest.mark.parametrize('level',['lenient','standard','strict'])
def test_existing_generation_profiles_keep_video_contract(level):
    assert '【品牌方视频验收规则生成总则】' in recommendation_prompt('brief-video-standards',level)['content']


@pytest.mark.parametrize('mode,key',[('legacy','brief-video-standards'),('layered','brief-video-layered'),('template','brief-video-template')])
def test_all_video_entry_modes_use_prompt_without_an_additional_model_call(client,monkeypatch,mode,key):
    project,task=task_with_rule(client)
    with connection(client.app.state.settings.database_path) as db:
        source=db.execute('SELECT source_version_id FROM rule_versions WHERE id=?',(task['ruleVersionId'],)).fetchone()[0]
    configured=client.put('/api/v1/settings/models/decomposition',json={'endpoint':'https://example.com/v1','model':'test-model','revision':0,'apiKey':'test-fixture-key'})
    assert configured.status_code==200
    calls=[]
    def fixed_model(config,secret,template,blocks,uncovered,facts=None,source_refs=None):
        calls.append(template)
        assert template['key']==key
        assert '【品牌方视频验收规则生成总则】' in template['content']
        assert '不虚构产品事实、竞品名单、平台政策' in template['content']
        rule={'text':'视频不得贬低竞品','criterion':'检查视频有无贬低竞品的表达。','category':'品牌安全','importance':'must',
              'sourceBlockId':blocks[0]['id'],'sourceQuote':blocks[0]['text']}
        if mode!='legacy':rule.update(layer='brief',enabled=False)
        raw={'rules':[rule],'warnings':[]}
        return {'report':raw,'modelResponse':json.dumps(raw),'providerRequestIds':['fixed-one-call']}
    monkeypatch.setattr(standards,'call_recommendation',fixed_model)
    path=f"/api/v1/projects/{project['id']}/standard-recommendations";idempotency=K()
    body={'sourceVersionId':source,'scope':'video','videoRuleMode':mode,'configRevision':1,'allowRealCall':True}
    response=client.post(path,json=body,headers={'Idempotency-Key':idempotency})
    assert response.status_code==200,response.text
    run=response.json()['data'];assert run['status']=='succeeded',run
    assert '【品牌方视频验收规则生成总则】' in run['snapshot']['prompt']['content']
    assert client.post(path,json=body,headers={'Idempotency-Key':idempotency}).json()['data']['id']==run['id']
    assert len(calls)==1
    if mode=='template':
        rules=run['result']['report']['rules']
        assert len([r for r in rules if r.get('layer')=='baseline'])==6
    elif mode=='layered':
        assert all(r['enabled'] is False and r['layer']!='baseline' for r in run['result']['report']['rules'])
