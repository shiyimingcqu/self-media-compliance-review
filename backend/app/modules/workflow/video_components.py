"""Brand-owned parameters and versioned VLM instructions; no model-selected thresholds."""
import hashlib
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from app.core.contracts import ContractModel

Kind = Literal['product_presence','logo_presence','product_mirroring','exposure_duration','exposure_count','forbidden_expression','required_speech','specified_action']
VISUAL = {'product_presence','logo_presence','product_mirroring','exposure_duration','exposure_count','specified_action'}
REFERENCE_REQUIRED = VISUAL - {'specified_action'}
NAMES = {'product_presence':'产品露出检查','logo_presence':'商标露出检查','product_mirroring':'产品镜像检查','exposure_duration':'露出时长检查','exposure_count':'露出次数检查','forbidden_expression':'禁用表达检查','required_speech':'必须口播检查','specified_action':'指定动作检查'}


class ComponentParameters(ContractModel):
    reference_image_ids: list[str] = Field(default_factory=list,max_length=4)
    start_seconds: float = Field(default=0,ge=0,le=600,allow_inf_nan=False)
    end_seconds: float | None = Field(default=None,gt=0,le=600,allow_inf_nan=False)
    require_whole_product: bool = False
    require_clear_logo: bool = False
    allow_occlusion: bool = False
    min_continuous_seconds: float | None = Field(default=1,gt=0,le=600,allow_inf_nan=False)
    min_total_seconds: float | None = Field(default=1,gt=0,le=600,allow_inf_nan=False)
    min_occurrences: int | None = Field(default=1,ge=1,le=30)
    occurrence_gap_seconds: float = Field(default=1,ge=0,le=60,allow_inf_nan=False)
    terms: list[str] = Field(default_factory=list,max_length=20)
    action: str = Field(default='',max_length=500)

    @model_validator(mode='after')
    def ranges(self):
        if self.end_seconds is not None and self.end_seconds<=self.start_seconds:
            raise ValueError('结束时间须大于开始时间')
        if len(set(self.reference_image_ids))!=len(self.reference_image_ids) or any(not t.strip() or len(t)>200 for t in self.terms):
            raise ValueError('参考图不能重复，词句必须非空且不超过200字')
        return self


class ComponentNode(ContractModel):
    id: str = Field(min_length=1,max_length=80,pattern=r'^[a-zA-Z0-9_-]+$')
    kind: Kind
    parameters: ComponentParameters

    @model_validator(mode='after')
    def required_material(self):
        if self.kind=='exposure_duration' and self.parameters.min_continuous_seconds is None and self.parameters.min_total_seconds is None:
            raise ValueError('请填写至少一项明确的时长阈值')
        if self.kind=='exposure_count' and self.parameters.min_occurrences is None:
            raise ValueError('请填写明确的最少露出次数')
        if self.kind in REFERENCE_REQUIRED and not self.parameters.reference_image_ids:
            raise ValueError('该检查组件必须选择品牌参考图')
        if self.kind in ('forbidden_expression','required_speech') and not self.parameters.terms:
            raise ValueError('请填写要检查的词句')
        if self.kind=='specified_action' and not self.parameters.action.strip():
            raise ValueError('请填写指定动作')
        return self


class ComponentPlan(ContractModel):
    operator: Literal['all','any']='all'
    same_segment: bool=False
    nodes: list[ComponentNode] = Field(min_length=1,max_length=8)

    @model_validator(mode='after')
    def unique(self):
        if len({n.id for n in self.nodes})!=len(self.nodes):
            raise ValueError('组件ID不能重复')
        if self.same_segment and (self.operator!='all' or any(n.kind not in VISUAL for n in self.nodes)):
            raise ValueError('同一画面组合仅适用于全部满足的视觉组件')
        return self


def instruction(kind):
    text=(Path(__file__).parent/'component_prompts'/f'{kind}.md').read_text(encoding='utf-8')
    return {'kind':kind,'title':NAMES[kind],'version':hashlib.sha256(text.encode()).hexdigest()[:12],'content':text}


def freeze_plan(plan):
    value=plan.model_dump(by_alias=True)
    for node in value['nodes']:
        node['prompt']=instruction(node['kind'])
    return value


def catalog():
    return [{'kind':kind,'name':name,'requiresReference':kind in REFERENCE_REQUIRED,'visual':kind in VISUAL,'prompt':instruction(kind)} for kind,name in NAMES.items()]
