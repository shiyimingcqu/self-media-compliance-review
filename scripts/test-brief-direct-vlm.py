"""Explicit live experiment: 12 original PDF images -> concise facts per request.

Does not update project data. Raw replies, prompts and timings are saved locally.
Requires --run to make paid requests; failures are recorded and never retried.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
from typing import Literal

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.core.config import Settings
from app.core.contracts import ContractModel
from pydantic import Field, RootModel
from app.modules.video_model.evidence.secrets import cipher
from app.modules.workflow.brief_vision import call_vision,page_bytes,image_batches
from app.modules.workflow.vision_facts import PageFacts,source_quote


class DirectSummary(ContractModel):
    facts: PageFacts


class GroupedRule(ContractModel):
    text: str = Field(min_length=1,max_length=1000)
    page_no: int = Field(ge=1)
    quote: str = Field(min_length=1,max_length=3000)


class GroupedRules(ContractModel):
    common: list[GroupedRule] = Field(default_factory=list,max_length=60)
    video: list[GroupedRule] = Field(default_factory=list,max_length=60)
    image: list[GroupedRule] = Field(default_factory=list,max_length=60)
    copy_rules: list[GroupedRule] = Field(default_factory=list,max_length=60,alias='copy')
    warnings: list[str] = Field(default_factory=list,max_length=30)


class TaggedRule(GroupedRule):
    group: Literal['common','video','image','copy']


class TaggedRules(RootModel[list[TaggedRule]]):
    root: list[TaggedRule] = Field(max_length=240)


class ProductAndRuleItem(GroupedRule):
    group: Literal['brand','product','sellingPoint','common','video','image','copy']


class ProductAndRules(RootModel[list[ProductAndRuleItem]]):
    root: list[ProductAndRuleItem] = Field(max_length=240)


def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source-run-id',required=True)
    parser.add_argument('--output-dir',required=True)
    parser.add_argument('--batch-size',type=int,default=12)
    parser.add_argument('--format',choices=['facts','grouped','grouped-tags','product-rules'],default='facts')
    parser.add_argument('--run',action='store_true')
    args=parser.parse_args()
    if args.batch_size<1:raise SystemExit('batch-size must be positive')
    settings=Settings(data_dir=ROOT/'backend'/'.data')
    output=Path(args.output_dir).resolve();output.mkdir(parents=True,exist_ok=True)
    database=sqlite3.connect('file:'+settings.database_path.as_posix()+'?mode=ro',uri=True)
    database.row_factory=sqlite3.Row
    row=database.execute("SELECT * FROM brief_visual_runs WHERE id=? AND status='succeeded'",(args.source_run_id,)).fetchone()
    if not row:raise SystemExit('Source extraction not found')
    snapshot=json.loads(row['snapshot_json'])
    current=database.execute('SELECT source_version_id FROM project_current_briefs WHERE project_id=?',(row['project_id'],)).fetchone()
    if not current or current[0]!=row['source_version_id']:raise SystemExit('Source is no longer the current project Brief')
    config=dict(database.execute('SELECT * FROM evidence_model_settings WHERE id=1').fetchone())
    if not config.get('encrypted_key'):raise SystemExit('Saved multimodal model is not configured')
    filename={'facts':'brief-direct-facts.md','grouped':'brief-direct-grouped-rules.md','grouped-tags':'brief-direct-grouped-tags.md','product-rules':'brief-direct-product-rules.md'}[args.format]
    prompt=(ROOT/'backend/app/modules/workflow/prompts'/filename).read_text(encoding='utf-8')
    schema={'facts':DirectSummary,'grouped':GroupedRules,'grouped-tags':TaggedRules,'product-rules':ProductAndRules}[args.format]
    (output/'prompt.md').write_text(prompt,encoding='utf-8')
    batches=list(image_batches(settings,row['id'],snapshot['pages'],max_pages=args.batch_size))
    manifest={'mode':'direct-images-to-'+args.format,'sourceRunId':row['id'],'sourceVersionId':row['source_version_id'],
              'fileSha256':snapshot['fileSha256'],'model':config['model'],'batchSize':args.batch_size,
              'batches':[[p['pageNo'] for p,_ in batch] for batch in batches],
              'promptSha256':hashlib.sha256(prompt.encode()).hexdigest(),'maxAttemptsPerBatch':1,'writesProjectData':False}
    write(output/'manifest.json',manifest)
    print(json.dumps({'prepared':True,'model':config['model'],'pages':snapshot['pageCount'],'batches':manifest['batches'],'live':args.run},ensure_ascii=False),flush=True)
    if not args.run:return
    # Local text layer is used only to assess returned quotes, never sent as input.
    import pypdfium2 as pdfium
    from app.modules.foundation.files_service import get_asset
    _,pdf_path=get_asset(settings,snapshot['fileId'])
    native={}
    with pdfium.PdfDocument(pdf_path) as doc:
        for index in range(len(doc)):
            page=doc[index];text=page.get_textpage()
            try:native[index+1]=text.get_text_range()
            finally:text.close();page.close()
    secret=cipher(settings.data_dir).decrypt(config['encrypted_key'].encode()).decode()
    results=[];begin=time.monotonic()
    try:
        for index,images in enumerate(batches,1):
            numbers=[p['pageNo'] for p,_ in images];start=time.monotonic()
            print(json.dumps({'batch':index,'status':'requesting','pages':numbers},ensure_ascii=False),flush=True)
            child={'pageCount':len(images),'documentContext':snapshot.get('documentContext',{}),
                   'nativeTextBlocks':[],'extractFacts':False,'visionStage':'direct-summary',
                   'stagePayload':{'physicalPages':numbers},'responseSchema':schema.model_json_schema(),
                   'prompt':{'content':prompt}}
            entry={'batch':index,'pages':numbers}
            try:
                response=call_vision(config,secret,child,images)
                write(output/f'batch-{index}-raw.json',response)
                if response.get('httpStatus'):raise ValueError('Provider HTTP '+str(response['httpStatus']))
                report=response.get('report')
                if args.format=='facts' and isinstance(report,dict) and 'facts' not in report and any(key in report for key in ('products','brandEntities','publishRules')):
                    report={'facts':report}
                parsed=schema.model_validate(report)
                if args.format in ('grouped','grouped-tags','product-rules'):
                    if args.format in ('grouped-tags','product-rules'):
                        entry['rules']={group:[] for group in ('common','video','image','copy')}
                        if args.format=='product-rules':entry['productFacts']={group:[] for group in ('brand','product','sellingPoint')}
                        for rule in parsed.root:
                            data=rule.model_dump(by_alias=True);group=data.pop('group')
                            destination=entry['productFacts'] if group in ('brand','product','sellingPoint') else entry['rules']
                            destination[group].append(data)
                    else:entry['rules']=parsed.model_dump(by_alias=True)
                    groups=('common','video','image','copy')
                    values=entry['rules']
                    if args.format=='product-rules':
                        groups=('brand','product','sellingPoint',*groups)
                        values={**entry['productFacts'],**entry['rules']}
                        entry['productFactCount']=sum(map(len,entry['productFacts'].values()))
                        entry['ruleCount']=sum(map(len,entry['rules'].values()))
                else:
                    entry['facts']=parsed.facts.model_dump(by_alias=True)
                    groups=('brands','products','competitors','sellingPoints','requiredTerms','forbiddenTerms','publishRules','thresholds','freeform')
                    values=entry['facts']
                validations=[]
                for group in groups:
                    for fact in values.get(group,[]):
                        page=fact['pageNo'];text=native.get(page,'')
                        status='wrong-batch-page' if page not in numbers else 'no-native-text' if not text.strip() else 'matched' if source_quote(fact['quote'],text) else 'not-located-in-text-layer'
                        validations.append({'group':group,'value':fact.get('value',fact.get('text')),'pageNo':page,'status':status})
                entry['quoteChecks']=validations;entry['status']='succeeded'
                entry['factCount']=sum(len(values.get(g,[])) for g in groups if g!='brands')
            except Exception as exc:
                entry['status']='failed';entry['errorType']=type(exc).__name__
                # Never print provider text or exception inputs containing credentials.
            entry['durationSeconds']=round(time.monotonic()-start,2)
            results.append(entry);write(output/f'batch-{index}-result.json',entry)
            write(output/'results.json',{'manifest':manifest,'elapsedSeconds':round(time.monotonic()-begin,2),'results':results})
            print(json.dumps({k:entry[k] for k in ('batch','status','durationSeconds','factCount','errorType') if k in entry},ensure_ascii=False),flush=True)
            if entry['status']=='failed':break
    finally:secret='';database.close()
    print(json.dumps({'finished':True,'durationSeconds':round(time.monotonic()-begin,2),'successfulBatches':sum(e['status']=='succeeded' for e in results),'output':str(output)},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
