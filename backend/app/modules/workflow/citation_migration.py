"""Move citations only after verifying both owned old source and same-page new text."""
import hashlib
import json
import re


def migrate_citations(db, project_id, source_id, rules, blocks):
    from .standards import quote_located
    cache={}; changes={}

    def move(block_id, quote):
        if not quote or quote_located(quote,block_id,blocks):
            return block_id
        match=re.fullmatch(r'vision:([0-9a-f]{32}):([1-9][0-9]*):(text|visual)',block_id)
        if not match:return block_id
        run_id,page_no,kind=match.groups();page_no=int(page_no)
        if run_id not in cache:
            row=db.execute("SELECT source_version_id,result_json FROM brief_visual_runs WHERE id=? AND project_id=? AND status='succeeded'",(run_id,project_id)).fetchone()
            cache[run_id]=dict(row) if row else None
        old=cache[run_id]
        if not old or old['source_version_id']==source_id:return block_id
        pages=json.loads(old['result_json']).get('report',{}).get('pages',[])
        key='text' if kind=='text' else 'visualDescription'
        previous=next((p for p in pages if p['pageNo']==page_no),None)
        if not previous or quote not in previous.get(key,''):return block_id
        # Never search other pages, fuzzy-match words, use another project, or
        # expand a visual claim into a text citation. Ambiguity stays an error.
        matches=[b for b in blocks if b.get('pageNo')==page_no and b['id'].startswith('vision:')
                 and b['id'].endswith(':'+kind) and quote in b['text']]
        if len(matches)!=1:return block_id
        return matches[0]['id']

    for rule in rules:
        trace=[]
        old=rule.source_block_id
        new=move(old,rule.source_quote)
        if new!=old:
            trace.append({'fromBlockId':old,'toBlockId':new,'quote':rule.source_quote})
            rule.source_block_id=new
        for ref in rule.source_references:
            old=ref.block_id;new=move(old,ref.quote)
            if new!=old:
                trace.append({'fromBlockId':old,'toBlockId':new,'quote':ref.quote})
                ref.block_id=new
                ref.ref='src_'+hashlib.sha256((new+'\0'+ref.quote).encode()).hexdigest()[:24]
        if trace:
            changes[id(rule)]={'method':'same_page_exact_quote_v1','targetSourceVersionId':source_id,'bindings':trace}
    return changes
