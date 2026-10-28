"""Summary outputs source IDs; the server supplies exact, contiguous quotes."""
import re
from pydantic import Field
from app.core.contracts import ContractModel
from app.core.errors import ApiError
from .fact_scope import FactApplicability
from .vision_facts import PageFacts,source_quote


class ReferencedFact(ContractModel):
    value: str = Field(min_length=1,max_length=500)
    source_id: str = Field(min_length=1,max_length=100)
    applicability: FactApplicability | None = None


class ReferencedBrand(ContractModel):
    name: ReferencedFact
    aliases: list[ReferencedFact] = Field(default_factory=list,max_length=30)
    role: str = Field(pattern='^(primary|related)$')


class ReferencedFacts(ContractModel):
    brand_entities: list[ReferencedBrand] = Field(default_factory=list,max_length=30)
    background: str = Field(default='',max_length=2000)
    products: list[ReferencedFact] = Field(default_factory=list,max_length=30)
    competitors: list[ReferencedFact] = Field(default_factory=list,max_length=30)
    selling_points: list[ReferencedFact] = Field(default_factory=list,max_length=30)
    required_terms: list[ReferencedFact] = Field(default_factory=list,max_length=30)
    forbidden_terms: list[ReferencedFact] = Field(default_factory=list,max_length=30)
    publish_rules: list[ReferencedFact] = Field(default_factory=list,max_length=30)
    thresholds: list[ReferencedFact] = Field(default_factory=list,max_length=30)
    freeform: list[ReferencedFact] = Field(default_factory=list,max_length=30)
    warnings: list[str] = Field(default_factory=list,max_length=40)


class ReferencedSummary(ContractModel):
    facts: ReferencedFacts


def _segments(text):
    # Keep wrapped bullet paragraphs intact and preserve exact source bytes.
    starts=[m.start() for m in re.finditer(r'(?m)^\s*[•●▪·★⭐]|\n\s*\n',text)]
    positions=sorted(set([0,*starts,len(text)]))
    for left,right in zip(positions,positions[1:]):
        fragment=text[left:right].strip()
        if not fragment:continue
        if len(fragment)<=3000:yield fragment
        else:
            # Break very long paragraphs only at original newline boundaries.
            offset=0
            while offset<len(fragment):
                end=min(len(fragment),offset+3000)
                boundary=fragment.rfind('\n',offset,end)
                if end<len(fragment) and boundary>offset:end=boundary
                yield fragment[offset:end].strip();offset=end


def summary_sources(pages):
    result=[]
    for page in pages:
        regions=page.regions or [None]
        for region in regions:
            text=region.text if region else page.text
            for quote in _segments(text):
                canonical=source_quote(quote,page.text)
                if canonical is None:continue
                result.append({'id':f'p{page.page_no}-s{len(result)+1}','pageNo':page.page_no,'source':'text',
                               'quote':canonical,'role':region.role if region else 'unknown',
                               'applicability':region.applicability.model_dump(by_alias=True) if region else None})
    return result


def resolve_summary(summary,sources):
    lookup={source['id']:source for source in sources}
    def resolved(item):
        source=lookup.get(item.source_id)
        if not source:raise ApiError(400,'BRIEF_FACT_SOURCE_UNKNOWN','重点汇总引用了不存在的来源编号，识读结果已保留')
        scope=source.get('applicability')
        if not scope or scope.get('materials')==['unknown']:
            scope=item.applicability.model_dump(by_alias=True) if item.applicability else scope
        return {'value':item.value,'pageNo':source['pageNo'],'source':source['source'],'quote':source['quote'],'applicability':scope}
    value={'background':summary.facts.background,'warnings':summary.facts.warnings,
           'brandEntities':[{'name':resolved(e.name),'aliases':[resolved(a) for a in e.aliases],'role':e.role} for e in summary.facts.brand_entities]}
    for key in ('products','competitors','selling_points','required_terms','forbidden_terms','publish_rules','thresholds','freeform'):
        value[key]=[resolved(item) for item in getattr(summary.facts,key)]
    return PageFacts.model_validate(value)
