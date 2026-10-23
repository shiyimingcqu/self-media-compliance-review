import json

import pytest

from app.core.database import connection
from app.modules.workflow import standards
from app.modules.workflow.prompts import prompt, recommendation_prompt
from test_script_versions import task_with_rule, K


EXCLUDED = [
    '预热期必带 TAG：#给麦咖啡加点麦当劳 #麦咖啡 #麦当劳',
    '测评期及后续阶段必带 TAG：#麦咖啡 #麦咖啡芝士浓奶铁',
    '脚本&大纲需包含主题、切入点、产品植入逻辑简述',
    '如有负面或有疑问的评论，需及时反馈给PR',
    '笔记发布后3个月不可删除、隐藏或随意修改',
    '发布文案必须添加活动话题标签',
    '博主需置顶一条产品相关评论',
    '交稿时间须在周五之前',
]
KEPT = [
    '不得出现侮辱性言论',
    '需强调活动机制和套餐内容',
    '视频中须展示麦咖啡芝士浓奶铁',
    '前15秒完整露出产品且手持不遮挡logo',
    '成片与已通过脚本核心商业内容一致',
    '与已通过脚本基本匹配',
    '片尾字幕须显示 #麦咖啡 标签',
    '口播不得诱导观众私信或评论引流',
    '画面展示的评论不得包含禁用功效词',
    '视频封面标题须体现麦咖啡活动机制',
]


def candidate(title):
    return {'text':title,'criterion':title,'importance':'must','layer':'brief','enabled':False}


@pytest.mark.parametrize('title',EXCLUDED)
def test_non_video_candidates_are_not_added(title):
    raw={'rules':[candidate(title),candidate(KEPT[1])],'warnings':[]}
    result=standards.validate_recommendation(raw,[],scope='video')
    assert [r.text for r in result.rules]==[KEPT[1]]
    assert raw['rules'][0]['text']==title  # Original model output is not rewritten.
    assert any('已略过1条' in warning for warning in result.warnings)


@pytest.mark.parametrize('title',KEPT)
def test_observable_video_requirements_are_preserved(title):
    result=standards.validate_recommendation({'rules':[candidate(title)]},[],scope='video')
    assert result.rules[0].text==title and result.rules[0].importance=='must'


def test_script_scope_and_manual_saves_are_not_filtered():
    result=standards.validate_recommendation({'rules':[candidate(EXCLUDED[2])]},[],scope='script')
    assert len(result.rules)==1 and result.rules[0].text==EXCLUDED[2]
    manual=standards.StandardRule.model_validate(candidate(EXCLUDED[0]))
    assert manual.text==EXCLUDED[0]


def test_empty_video_output_is_valid_without_inventing_rules():
    for value in ({'rules':[]},{'rules':[candidate(title) for title in EXCLUDED]}):
        result=standards.validate_recommendation(value,[],scope='video')
        assert result.rules==[]
        assert any('本次没有新增适用' in warning for warning in result.warnings)
    with pytest.raises(ValueError):
        standards.validate_recommendation({'rules':[]},[],scope='script')


def test_both_video_generation_modes_define_phase_boundaries():
    for template in (prompt('brief-video-layered'),recommendation_prompt('brief-video-standards','strict')):
        assert '【先判定适用阶段' in template['content']
        assert '发布后的运营' in template['content']
        assert 'rules=[]' in template['content']
        assert '片尾或口播中' in template['content']


def test_existing_exclusion_hints_are_read_only_and_do_not_target_manual_or_baseline():
    from app.modules.workflow.video_rule_scope import existing_exclusions
    source=[{**candidate(EXCLUDED[0]),'origin':'ai'}, {**candidate(EXCLUDED[0]),'origin':'manual'},
            {**candidate(EXCLUDED[2]),'origin':'ai','layer':'baseline'}, {**candidate(KEPT[1]),'origin':'ai'}]
    frozen=json.dumps(source,ensure_ascii=False)
    assert existing_exclusions(source)==[EXCLUDED[0]]
    assert json.dumps(source,ensure_ascii=False)==frozen


def test_generation_filters_without_paid_retry_or_changing_saved_rules(client,monkeypatch):
    project,task=task_with_rule(client)
    with connection(client.app.state.settings.database_path) as db:
        source=db.execute('SELECT source_version_id FROM rule_versions WHERE id=?',(task['ruleVersionId'],)).fetchone()[0]
        # This legacy task fixture predates project-level Brief selection.
        from app.modules.foundation.service import now
        db.execute('INSERT INTO project_current_briefs VALUES (?,?,1,?)',(project['id'],source,now()))
    client.put('/api/v1/settings/models/decomposition',json={'endpoint':'https://models.example.com/v1','model':'model1',
                                                          'revision':0,'apiKey':'sk-test-only'})
    before=client.get(f"/api/v1/projects/{project['id']}/standards").json()['data']
    calls=[]
    raw={'rules':[candidate(title) for title in EXCLUDED]+[candidate(KEPT[1])],'warnings':[]}
    def model(*args):
        calls.append(1)
        return {'report':raw,'modelResponse':json.dumps(raw,ensure_ascii=False)}
    monkeypatch.setattr(standards,'call_recommendation',model)
    path=f"/api/v1/projects/{project['id']}/standard-recommendations"
    key=K();body={'sourceVersionId':source,'scope':'video','videoRuleMode':'layered','configRevision':1,'allowRealCall':True}
    response=client.post(path,json=body,headers={'Idempotency-Key':key}).json()['data']
    assert response['status']=='succeeded'
    assert [r['text'] for r in response['result']['report']['rules']]==[KEPT[1]]
    assert EXCLUDED[0] in response['result']['modelResponse']
    assert client.post(path,json=body,headers={'Idempotency-Key':key}).json()['data']['id']==response['id']
    assert calls==[1]
    assert client.get(f"/api/v1/projects/{project['id']}/standards").json()['data']==before
    recovered_response=client.post(path+'/'+response['id']+'/revalidate',headers={'Idempotency-Key':K()})
    assert recovered_response.status_code==200,recovered_response.text
    recovered=recovered_response.json()['data']
    assert recovered['status']=='succeeded' and len(recovered['result']['report']['rules'])==1
    assert calls==[1]
