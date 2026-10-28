"""Prompt wiring and status handling, not a mock-based model accuracy benchmark."""
import json
import pytest

from app.modules.workflow.prompts import prompt
from app.modules.workflow.router import script_acceptance, script_submission_ready
from app.modules.workflow.script_defaults import default_script_rules


@pytest.mark.parametrize('key',[
    'script-review','image-review','video-review','video-localize','video-refine','video-final-report',
    'brief-script-standards','brief-script-rules-text','brief-image-standards',
    'brief-video-standards','brief-video-layered','brief-video-template'])
def test_all_judgment_and_generation_paths_receive_practical_tolerance(key):
    text=prompt(key)['content']
    assert '【创意表达的实务容忍口径】' in text
    assert '最多待确定' in text
    assert '逐字引文只证明说了什么' in text
    assert '把咖啡当女朋友' in text
    assert '咖喱味儿' in text
    assert '普通角色表演' in text
    assert '明确辱骂' in text
    assert '品牌已明确写出的具体禁令' in text
    assert '不输出审核结论或新增输出字段' in text


def test_observation_and_calculation_prompts_do_not_make_social_judgments():
    for key in ('brief-facts','video-component-pass','connection-test'):
        assert '【创意表达的实务容忍口径】' not in prompt(key)['content']


def test_new_brand_safety_template_does_not_invent_creative_bans():
    rule=next(r for r in default_script_rules() if r['category']=='品牌安全')
    assert rule['importance']=='must'
    assert '仅有具体争议而无法确认贬损时最多待确定' in rule['criterion']
    assert '品牌另行明确的具体禁令按原判据执行' in rule['criterion']


@pytest.mark.parametrize('verdict,status,submit_ready',[
    ('compliant','passed',True),('needs_confirmation','needs_confirmation',True),
    ('non_compliant','needs_changes',False)])
def test_uncertainty_can_be_sent_to_brand_without_becoming_a_failure(verdict,status,submit_ready):
    snapshot={'rules':[{'id':'r1','text':'不得歧视','importance':'must','ruleNumber':1}]}
    report={'findings':[{'rule_id':'r1','verdict':verdict,'description':'固定结果，仅验证状态流转'}]}
    row={'status':'succeeded','snapshot_json':json.dumps(snapshot),'result_json':json.dumps({'report':report})}
    acceptance=script_acceptance(row)
    assert acceptance['status']==status
    assert acceptance['checks'][0]['verdict']==verdict
    assert script_submission_ready(row)==submit_ready
    assert acceptance['passed']==(verdict=='compliant')
