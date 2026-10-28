import copy
import json
import shutil

import pytest

from app.modules.workflow import standards
from app.modules.workflow.platform_rule_skills import ROOT, apply_platform_skills, filter_stage_candidates
from app.modules.workflow.script_defaults import SCRIPT_DIMENSIONS
from test_project_brief import upload
from test_script_versions import K


def cases():
    return [(platform,case) for platform in ('xiaohongshu','douyin')
            for case in json.loads((ROOT/platform/'cases.json').read_text(encoding='utf8'))]


@pytest.mark.parametrize('platform,case',cases())
def test_fixed_platform_stage_cases(platform,case):
    _,context=apply_platform_skills({'content':'base','key':'fixed'},[platform],case['scope'])
    value={'rules':[{'text':case['text'],'criterion':'固定测试'}],'warnings':[]}
    original=copy.deepcopy(value)
    filtered,excluded=filter_stage_candidates(value,case['scope'],context)
    assert bool(filtered['rules'])==(case['expected']=='keep'),case
    assert bool(excluded)==(case['expected']=='omit')
    assert original==value


def test_platform_selection_scope_version_and_unknown_fallback():
    base={'content':'existing prompt','key':'brief-script-standards','version':'old'}
    xhs,xctx=apply_platform_skills(base,['xiaohongshu'],'script')
    dy,dctx=apply_platform_skills(base,['douyin'],'video')
    assert [p['id'] for p in xctx['packs']]==['xiaohongshu']
    assert [p['id'] for p in dctx['packs']]==['douyin']
    assert xctx['stage']=='planning' and xctx['contentType']=='script_plan'
    assert dctx['stage']=='content' and dctx['contentType']=='video'
    assert xhs['version']!=dy['version'] and base['version']=='old'
    assert xhs==apply_platform_skills(base,['xiaohongshu'],'script')[0]
    assert 'entry_only' in xhs['content'] and xctx['packs'][0]['sources'][0]['checkedAt']=='2026-10-25'
    fallback,ctx=apply_platform_skills(base,['bilibili'],'image')
    assert ctx['packs']==[] and ctx['contentType']=='image_post'
    assert '暂无专属规则包' in fallback['content']


@pytest.mark.parametrize('platform',['xiaohongshu','douyin'])
def test_brand_priorities_are_injected_for_each_platform_and_stage(platform):
    for scope in ('script','video','image'):
        prompt,context=apply_platform_skills({'content':'已有输出契约'},[platform],scope)
        content=prompt['content']
        groups=['第一组｜我要的东西给到了吗','第二组｜会不会给我惹麻烦',
                '第三组｜像不像我的品牌','第四组｜有没有人看']
        positions=[content.index(group) for group in groups]
        assert positions==sorted(positions)
        assert '50%' in content and '30%' in content and '10%' in content
        assert '不允许用其他组的高分抵消必须项失败' in content
        assert '只有 Brief 明确约定才转为第一组的交付条件' in content
        assert '不替换八维基础模板' in content
        assert context['priorityProfile']=={'version':'brand_priorities_v1','delivery':50,'risk':30,
                                            'brandStyle':10,'effectiveness':10,'weightedPass':False}


def test_shared_priority_changes_update_both_pack_versions(tmp_path,monkeypatch):
    from app.modules.workflow import platform_rule_skills as module
    fixture=tmp_path/'packs';shutil.copytree(ROOT,fixture)
    before={platform:apply_platform_skills({'content':'base'},[platform],'script')[1]['packs'][0]['version']
            for platform in ('xiaohongshu','douyin')}
    path=fixture/'brand-priorities.md';path.write_text(path.read_text(encoding='utf8')+'\n固定测试新增说明。',encoding='utf8')
    monkeypatch.setattr(module,'ROOT',fixture)
    for platform,version in before.items():
        assert apply_platform_skills({'content':'base'},[platform],'script')[1]['packs'][0]['version']!=version


def test_multi_platform_shared_mode_does_not_guess_primary_platform():
    _,ctx=apply_platform_skills({'content':'base'},['xiaohongshu','douyin'],'video')
    assert ctx['mode']=='shared' and len(ctx['packs'])==2
    value={'rules':[{'text':'小红书视频须展示专属标识'},{'text':'口播不得贬低竞品'},
                    {'text':'视频中不得导流到小红书'}]}
    report,excluded=filter_stage_candidates(value,'video',ctx)
    assert [r['text'] for r in report['rules']]==['口播不得贬低竞品','视频中不得导流到小红书']
    assert len(excluded)==1


@pytest.mark.parametrize('platform,scope',[('xiaohongshu','script'),('douyin','script'),('xiaohongshu','image'),('douyin','video')])
def test_generation_uses_project_platform_single_call_and_retains_raw_reply(client,monkeypatch,platform,scope):
    project=client.post('/api/v1/projects',json={'name':'平台生成测试','brandName':'测试品牌','platforms':[platform]},headers={'Idempotency-Key':K()}).json()['data']
    uploaded,_=upload(client,project['id'],'品牌名为测试品牌。不得拉踩竞品。')
    source=uploaded.json()['data']['brief']['sourceVersionId']
    configured=client.put('/api/v1/settings/models/decomposition',json={'endpoint':'https://example.com/v1','model':'test-model','revision':0,'apiKey':'test-fixture-key'})
    assert configured.status_code==200
    calls=[]
    wrong='发布配文必须添加话题标签' if scope!='image' else '口播必须持续5秒'
    raw={'rules':[{'text':wrong,'criterion':'本条不属于当前阶段','category':'合作要求达成','importance':'must','sourceBlockId':'','sourceQuote':''}],'warnings':[]}
    def model(config,key,template,blocks,uncovered,facts=None,source_refs=None):
        calls.append(template)
        assert ('# 小红书规则生成' in template['content'])==(platform=='xiaohongshu')
        assert ('# 抖音规则生成' in template['content'])==(platform=='douyin')
        assert '第一组｜我要的东西给到了吗（50%）' in template['content']
        assert '第三组｜像不像我的品牌（10%）' in template['content']
        return {'report':copy.deepcopy(raw),'modelResponse':json.dumps(raw,ensure_ascii=False),'providerRequestIds':['fixed-once']}
    monkeypatch.setattr(standards,'call_recommendation',model)
    path=f"/api/v1/projects/{project['id']}/standard-recommendations";key=K()
    body={'sourceVersionId':source,'scope':scope,'configRevision':1,'allowRealCall':True}
    response=client.post(path,json=body,headers={'Idempotency-Key':key})
    assert response.status_code==200,response.text
    data=response.json()['data'];assert data['status']=='succeeded',data
    assert data['snapshot']['platformSkills']['platforms']==[platform]
    assert data['snapshot']['platformSkills']['priorityProfile']['weightedPass'] is False
    assert data['result']['modelResponse']==json.dumps(raw,ensure_ascii=False)
    assert data['result']['excludedCandidates'][0]['text']==wrong
    if scope=='script':
        assert [r['category'] for r in data['result']['report']['rules']]==[r[0] for r in SCRIPT_DIMENSIONS]
    else:
        assert data['result']['report']['rules']==[]
    client.post(path,json=body,headers={'Idempotency-Key':key})
    assert len(calls)==1
    def no_call(*args,**kwargs):raise AssertionError('本地恢复不得重调模型')
    monkeypatch.setattr(standards,'call_recommendation',no_call)
    restored=client.post(path+'/'+data['id']+'/revalidate',headers={'Idempotency-Key':K()})
    assert restored.status_code==200,restored.text
    assert restored.json()['data']['snapshot']['platformSkills']==data['snapshot']['platformSkills']
