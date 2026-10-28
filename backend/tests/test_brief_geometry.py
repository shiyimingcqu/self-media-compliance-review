import pytest
from app.modules.workflow.brief_layout_pipeline import LayoutPage
from app.modules.workflow.brief_geometry import refine_page
from app.modules.workflow.brief_summary_sources import summary_sources,ReferencedSummary,resolve_summary
from app.modules.workflow.brief_vision import VisualPage


def geometry():
    # Native geometry for a three-row table with a merged lower right cell.
    return {20:{'spans':[],'panels':[
        {'id':'yes-head','bbox':[158,167,533,224],'text':'请遵守√'},
        {'id':'no-head','bbox':[554,167,923,224],'text':'不要做×'},
        {'id':'copy-label','bbox':[71,232,130,407],'text':'文案'},
        {'id':'image-label','bbox':[71,412,130,645],'text':'图片'},
        {'id':'video-label','bbox':[71,653,130,892],'text':'视频'},
        {'id':'copy','bbox':[158,232,533,407],'text':'• 符合内容要求'},
        {'id':'copy-ban','bbox':[554,232,923,488],'text':'不可拉踩竞品'},
        {'id':'highlight','bbox':[554,231,923,281],'text':'注意！不提及功效词'},
        {'id':'image','bbox':[158,412,533,645],'text':'封面、画面、内容'},
        {'id':'video','bbox':[158,651,533,892],'text':'产品露出：视频前15s'},
        {'id':'merged','bbox':[554,505,923,896],'text':'不可产品画面调色饱和度低，使食物无食欲'},
    ]}}


def test_rounded_model_boxes_snap_to_real_borders_and_shared_cell_stays_merged():
    page=LayoutPage(pageNo=20,readingMode='regions',regions=[
        {'id':'copy','label':'文案','role':'requirement','bbox':[200,200,500,400]},
        {'id':'image','label':'图片','role':'requirement','bbox':[200,400,500,650]},
        {'id':'video','label':'视频','role':'requirement','bbox':[200,650,500,900]},
        {'id':'image-ban','label':'图片禁令','role':'requirement','bbox':[520,400,900,650]},
        {'id':'video-ban','label':'视频禁令','role':'requirement','bbox':[520,650,900,900]},
    ])
    before=page.model_dump()
    corrected,changes=refine_page(page,geometry())
    assert page.model_dump()==before
    assert len(corrected.regions)==4
    assert corrected.regions[0].bbox==[158,232,533,407]
    assert corrected.regions[0].row_header=='文案'
    assert corrected.regions[0].column_header=='请遵守√'
    shared=corrected.regions[-1]
    assert shared.bbox==[554,505,923,896]
    assert shared.row_header=='图片／视频' and shared.column_header=='不要做×'
    assert shared.applicability.materials==['image','video']
    assert any(c.get('mergedInto')=='image-ban' for c in changes)


def test_native_text_boundary_guard_preserves_whole_lines_when_no_panel_exists():
    page=LayoutPage(pageNo=3,readingMode='regions',regions=[{'id':'a','label':'正文','role':'context','bbox':[200,200,500,400]}])
    corrected,_=refine_page(page,{3:{'panels':[],'spans':[{'bbox':[170,220,520,300],'text':'完整的一行文字'}]}})
    assert corrected.regions[0].bbox==[170,200,520,400]


def test_summary_chooses_source_ids_and_program_supplies_contiguous_multiline_quote():
    page=VisualPage(pageNo=20,text='• 前15s需清晰且完整露出产品\nlogo正对镜头\n• 软植前30s至少出现一次',
                    visualDescription='视频要求',readability='readable')
    sources=summary_sources([page])
    assert len(sources)==2
    assert sources[0]['quote']=='• 前15s需清晰且完整露出产品\nlogo正对镜头'
    summary=ReferencedSummary.model_validate({'facts':{'thresholds':[
        {'value':'前15s完整露出','sourceId':sources[0]['id']},
        {'value':'软植前30s至少露出一次','sourceId':sources[1]['id']}]}})
    facts=resolve_summary(summary,sources)
    assert len(facts.thresholds)==2
    assert all(item.quote in page.text for item in facts.thresholds)
    assert all('...' not in item.quote for item in facts.thresholds)
    with pytest.raises(Exception):
        resolve_summary(ReferencedSummary.model_validate({'facts':{'thresholds':[{'value':'伪造','sourceId':'bad-id'}]}}),sources)
