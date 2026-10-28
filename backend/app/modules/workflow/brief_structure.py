"""VLM-owned spatial regions; text checks detect omissions but never infer table scope."""
import re
from typing import Literal

from pydantic import Field

from app.core.contracts import ContractModel
from .fact_scope import FactApplicability

STRUCTURE_VERSION = 'visual-regions-v1'


class RegionItem(ContractModel):
    quote: str = Field(min_length=1, max_length=3000)
    value: str = Field(min_length=1, max_length=500)
    group: Literal['products','competitors','sellingPoints','requiredTerms','forbiddenTerms','publishRules','thresholds','freeform'] = 'freeform'
    source: Literal['text','visual'] = 'text'


class VisualRegion(ContractModel):
    id: str = Field(min_length=1, max_length=80)
    row_header: str = Field(default='', max_length=200)
    column_header: str = Field(default='', max_length=200)
    role: Literal['requirement','reference','context']
    applicability: FactApplicability = Field(default_factory=FactApplicability)
    items: list[RegionItem] = Field(default_factory=list, max_length=100)
    text: str = Field(default='', max_length=25000)
    bbox: list[int] | None = Field(default=None, min_length=4, max_length=4)


def unmapped_bullets(text, quotes):
    """Coverage only: a missing bullet becomes a review note, never an inferred rule."""
    compact = lambda value: ''.join(value.split())
    spans = [compact(quote) for quote in quotes]
    return [line.strip() for line in text.splitlines()
            if (match := re.match(r'^\s*[•●▪·]\s*(.+)', line))
            and len(compact(match[1])) >= 4 and not any(compact(match[1]) in span for span in spans)]
