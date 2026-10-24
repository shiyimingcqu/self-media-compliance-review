import copy
import json

from app.modules.workflow.router import video_component_acceptance, video_submission_ready
from app.modules.workflow.video_layers import defaults, legacy_skipped_competitors, runnable


def fixture():
    competitor=dict(next(r for r in defaults() if r['baselineKey']=='competitor'),id='competitor',enabled=True,ruleNumber=1)
    other={'id':'other','text':'产品展示','importance':'must','enabled':True,'layer':'brief'}
    snapshot={'rules':[competitor,other],'unconfiguredRules':[{'ruleId':'competitor','reason':'待品牌确认指定竞品名单','importance':'must'}]}
    result={'report':{'findings':[{'rule_id':'other','verdict':'compliant'}]}}
    return snapshot,result


def row(snapshot,result):
    return {'kind':'video','status':'succeeded','snapshot_json':json.dumps(snapshot),'result_json':json.dumps(result)}


def test_no_names_goes_to_model_instead_of_configuration_queue():
    snapshot,_=fixture();source=copy.deepcopy(snapshot['rules'][0])
    assert runnable([source])==([source],[])


def test_legacy_omitted_competitor_can_be_submitted_for_brand_review_without_fabricated_pass():
    snapshot,result=fixture();original=copy.deepcopy((snapshot,result))
    assert legacy_skipped_competitors(snapshot,result['report']['findings'])=={'competitor'}
    assert video_submission_ready(row(snapshot,result))
    acceptance=video_component_acceptance(row(snapshot,result))
    assert acceptance['blockingRules']==[] and not acceptance['configurationRequired']
    assert acceptance['reviewCount']==1 and '旧报告未执行' in acceptance['reviewRules'][0]['reason']
    assert (snapshot,result)==original


def test_other_missing_rules_named_competitors_and_actual_violations_still_block():
    snapshot,result=fixture()
    result['report']['findings']=[]
    assert not video_submission_ready(row(snapshot,result))
    snapshot,result=fixture();snapshot['rules'][0]['params']['names']=['竞品A']
    assert not video_submission_ready(row(snapshot,result))
    snapshot,result=fixture();snapshot['unconfiguredRules']=[]
    assert not video_submission_ready(row(snapshot,result))
    snapshot,result=fixture();result['report']['findings'].append({'rule_id':'competitor','verdict':'non_compliant'})
    assert not video_submission_ready(row(snapshot,result))
    assert not video_component_acceptance(row(snapshot,result))['passed']


def test_unknown_duplicate_findings_and_partial_results_do_not_gain_submission():
    snapshot,result=fixture()
    result['report']['findings'].append({'rule_id':'unknown','verdict':'compliant'})
    assert not video_submission_ready(row(snapshot,result))
    snapshot,result=fixture();result['report']['findings']*=2
    assert not video_submission_ready(row(snapshot,result))
    snapshot,result=fixture();result['partialReport']={'findings':[]}
    assert not video_submission_ready(row(snapshot,result))
