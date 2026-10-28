"""Short provider rule references; frozen IDs remain server-owned."""
import copy
import re
from collections import Counter

VERSION = 'short_rule_refs_v1'


def short_refs(snapshot):
    return snapshot.get('ruleReferenceVersion') == VERSION


def reference_map(snapshot):
    return {f"R{rule['ruleNumber']}": rule['id'] for rule in snapshot['rules']}


def freeze_references(snapshot):
    snapshot['ruleReferenceVersion'] = VERSION
    snapshot['ruleReferenceMap'] = reference_map(snapshot)


def map_report(value, mapping):
    """Rewrite only schema-defined reference positions, never descriptions/quotes."""
    result = copy.deepcopy(value)
    if not isinstance(result, dict):
        return result
    for name in ('findings', 'mandatory_checks'):
        for item in result.get(name, []) if isinstance(result.get(name), list) else []:
            if isinstance(item, dict) and isinstance(item.get('rule_id'), str):
                item['rule_id'] = mapping.get(item['rule_id'], item['rule_id'])
    for item in result.get('image_checks', []) if isinstance(result.get('image_checks'), list) else []:
        if isinstance(item, dict) and isinstance(item.get('rule_ids'), list):
            item['rule_ids'] = [mapping.get(ref, ref) if isinstance(ref, str) else ref for ref in item['rule_ids']]
    return result


def normalize_references(value, snapshot, result=None):
    if not short_refs(snapshot):
        return value
    mapping = reference_map(snapshot)
    normalized = map_report(value, mapping)
    if result is not None:
        result['ruleReferenceMapping'] = {'version': VERSION, 'bindings': mapping,
                                          'providerResubmitted': False, 'repaired': False}
    return normalized


def provider_material(snapshot, material):
    """Strip rule provenance and translate all structural rule references to R IDs."""
    if not short_refs(snapshot):
        return material
    reverse = {real: ref for ref, real in reference_map(snapshot).items()}
    def replace(value):
        if isinstance(value, str):
            return reverse.get(value, value)
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        return value
    material = copy.deepcopy(material)
    material['rules'] = [{key: value for key, value in rule.items()
                          if key in ('id','ruleNumber','text','criterion','category','importance','params','exceptions')}
                         for rule in snapshot['rules']]
    material = replace(material)
    material['ruleReferenceVersion'] = VERSION
    return material


def recover_legacy_reference(value, snapshot):
    """One malformed legacy UUID + one uniquely matching frozen number only.

    No edit-distance/fuzzy matching. A valid-length foreign UUID, short-ref run,
    multiple missing IDs, duplicate findings or contradictory numbers cannot be
    recovered. Even a recovered candidate requires human review.
    """
    if short_refs(snapshot) or snapshot.get('paragraphMode') or not isinstance(value, dict):
        return value, None
    rules = snapshot['rules']
    findings = value.get('findings')
    if not isinstance(findings, list) or len(findings) != len(rules):
        return value, None
    if any(not isinstance(item, dict) or not isinstance(item.get('rule_id'), str) for item in findings):
        return value, None
    known = {rule['id']: rule for rule in rules}
    ids = [item['rule_id'] for item in findings]
    if len(ids) != len(set(ids)):
        return value, None
    missing = set(known) - set(ids)
    unknown = set(ids) - set(known)
    if len(missing) != 1 or len(unknown) != 1:
        return value, None
    target, source = next(iter(missing)), next(iter(unknown))
    match = re.fullmatch(r'(.*?)([0-9a-f]{32})', target)
    if not match or not re.fullmatch(re.escape(match[1])+r'(?:[0-9a-f]{31}|[0-9a-f]{33})', source):
        return value, None
    numbers = [item.get('rule_number') for item in findings]
    expected = [rule['ruleNumber'] for rule in rules]
    if len(known)!=len(rules) or len(set(expected))!=len(rules):
        return value, None
    if any(type(number) is not int for number in numbers) or Counter(numbers) != Counter(expected):
        return value, None
    for item in findings:
        rule = known[target] if item['rule_id'] == source else known.get(item['rule_id'])
        if not rule or item.get('rule_number') != rule['ruleNumber']:
            return value, None
    # A second numbered section must not contradict this sole binding.
    checks=value.get('mandatory_checks', [])
    if not isinstance(checks,list):return value,None
    for item in checks:
        if isinstance(item, dict) and item.get('rule_id') == source and item.get('rule_number') not in (None, known[target]['ruleNumber']):
            return value, None
    recovery = {'repaired': True, 'requiresManualReview': True, 'status': 'NEEDS_MANUAL_REVIEW',
                'method': 'unique_missing_rule_number_v1', 'providerResubmitted': False,
                'bindings': [{'returnedRuleId': source, 'ruleId': target, 'ruleNumber': known[target]['ruleNumber']}]}
    return map_report(value, {source: target}), recovery
