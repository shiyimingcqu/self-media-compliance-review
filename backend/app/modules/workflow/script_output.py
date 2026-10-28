"""Script output protocol. No network retries and no repair can grant acceptance."""
import copy
import hashlib
import json
import os
import re
from .script_rule_refs import reference_map, short_refs


class OutputError(ValueError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.details = {'errorType': code, 'validationError': message, **details}


def request_options(model):
    # Freeze this before sending. Custom gateways may override the documented
    # model capability; never probe by silently resending a paid request.
    mode = os.getenv('REVIEW_SCRIPT_OUTPUT_MODE', 'auto')
    if mode == 'auto':
        mode = 'json_schema' if re.match(r'^qwen3\.[78]-(?:plus|max|flash)(?:$|-)', model) else 'json_object'
    if mode not in {'json_schema', 'json_object', 'tool'}:
        raise ValueError('REVIEW_SCRIPT_OUTPUT_MODE must be auto/json_schema/json_object/tool')
    # 判定类调用默认不发温度，供应商按默认采样，同一输入多次结果会漂移；默认钉成 0。
    temperature = float(os.getenv('REVIEW_SCRIPT_TEMPERATURE', '0'))
    return {'outputMode': mode, 'maxTokens': 12000, 'maxAttempts': 1, 'temperature': temperature,
            'readTimeoutSeconds': 300, 'connectTimeoutSeconds': 15, 'reasoningEffort': 'none'}


def output_contract(schema, mode, snapshot=None):
    schema = copy.deepcopy(schema)
    # All generated fields required for strict providers; application models
    # retain defaults so old saved reports remain readable.
    def strict(node):
        if isinstance(node, dict):
            node.pop('default', None)
            if node.get('type') == 'object':
                node['additionalProperties'] = False
                node['required'] = list(node.get('properties', {}))
            for value in node.values():
                strict(value)
        elif isinstance(node, list):
            for value in node:
                strict(value)
    # New calls reference local text; legacy quote fields are read-only input
    # compatibility, not an invitation to recopy source material.
    for name in ('ScriptFinding', 'MandatoryCheck'):
        properties = schema['$defs'][name]['properties']
        properties.pop('quote', None)
        properties.pop('quote_segments', None)
        if not snapshot or not snapshot.get('paragraphMode'):
            properties.pop('requirement', None)
        if snapshot:
            properties['rule_id']['enum']=list(reference_map(snapshot)) if short_refs(snapshot) else [r['id'] for r in snapshot['rules']]
            if short_refs(snapshot):properties.pop('rule_number', None)
            else:properties['rule_number']={'type':'integer','enum':[r['ruleNumber'] for r in snapshot['rules']]}
            for field, values in (
                ('quote_refs',[q['id'] for q in snapshot.get('quoteSources',[])]),
                ('image_ids',[im['id'] for im in snapshot['multimodal']['images']]),
            ):
                if values:properties[field]['items']['enum']=values
                else:properties[field]['maxItems']=0
    if snapshot:
        image_schema=schema['$defs']['ScriptImageCheck']['properties']
        image_ids=[im['id'] for im in snapshot['multimodal']['images']]
        if image_ids:image_schema['image_id']['enum']=image_ids
        else:schema['properties']['image_checks']['maxItems']=0
        image_schema['rule_ids']['items']['enum']=list(reference_map(snapshot)) if short_refs(snapshot) else [r['id'] for r in snapshot['rules']]
        if short_refs(snapshot) and not snapshot.get('paragraphMode'):
            schema['properties']['findings'].update(minItems=len(snapshot['rules']),maxItems=len(snapshot['rules']))
    strict(schema)
    if mode == 'tool':
        return {'tools': [{'type': 'function', 'function': {'name': 'submit_script_report',
                 'description': '提交逐条脚本审核报告', 'parameters': schema}}],
                'tool_choice': {'type': 'function', 'function': {'name': 'submit_script_report'}}}
    if mode == 'json_schema':
        return {'response_format': {'type': 'json_schema', 'json_schema': {
            'name': 'script_report', 'strict': True, 'schema': schema}}}
    return {'response_format': {'type': 'json_object'}}


def prompt_hash(content):
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def normalize_report_envelope(value, result):
    """Unwrap one unambiguous report without changing any report fields.

    Original provider text stays in modelResponse/rawProviderResponse. This is
    envelope normalization, not JSON repair; all schema/reference checks follow.
    Empty, nested, multi-item or scalar arrays remain invalid input.
    """
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict):
        result['normalization'] = {'method': 'single_report_array_v1',
                                   'originalContainer': 'array', 'itemCount': 1,
                                   'contentChanged': False, 'providerResubmitted': False}
        return value[0]
    return value


def parse_output(raw, finish_reason=None):
    if finish_reason in {'length', 'max_tokens'}:
        raise OutputError('OUTPUT_TRUNCATED', '模型输出达到长度上限，不能按完整报告采纳',
                          finishReason=finish_reason, generatedLength=len(raw))
    if not raw.strip():
        raise OutputError('OUTPUT_EMPTY', '模型未返回报告内容', finishReason=finish_reason)
    text = raw.strip()
    if text.startswith('```') and text.endswith('```'):
        text = text.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise OutputError('OUTPUT_INVALID_JSON', '模型返回不是合法 JSON', offset=exc.pos,
                          line=exc.lineno, column=exc.colno, parserMessage=exc.msg,
                          context=text[max(0, exc.pos-50):exc.pos+50]) from exc


def repair_output(raw):
    """Narrow local repair only. Never complete truncation, fabricate or drop fields.

    Recover paired ASCII quotes embedded between Chinese characters, as in the
    reported incident. Other defects remain available for manual inspection.
    """
    if not raw.rstrip().endswith('}'):
        return None
    fixed, count = re.subn(r'(?<=[\u3400-\u9fff])"([^"\r\n\\]{1,120})"(?=[\u3400-\u9fff])',
                          lambda m: '\\"' + m[1] + '\\"', raw)
    if not count:
        return None
    try:
        return parse_output(fixed), fixed
    except OutputError:
        return None
