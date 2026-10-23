"""Playwright 专用隔离服务；退出后仅清理自身 TemporaryDirectory。"""
import os
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

if __name__ == "__main__":
    # Opt-in fixture for browser tests only. Production never imports this script;
    # the fixture makes no supplier request and never claims real model quality.
    if os.getenv('E2E_IMAGE_MODEL_FIXTURE') == '1':
        from app.modules.workflow import router as workflow
        real_call = workflow.call_script_model
        def image_fixture(config, secret, snapshot):
            if snapshot['prompt']['key'] != 'image-review':return real_call(config, secret, snapshot)
            images = snapshot['multimodal']['images']; rules = snapshot['rules']
            report = {'summary': '浏览器测试固定输出：已逐条核对文案与全部配图，等待品牌复核。',
                      'findings': [{'rule_id': r['id'], 'rule_number': r['ruleNumber'], 'verdict': 'compliant',
                                    'quote_refs': [snapshot['quoteSources'][0]['id']], 'image_ids': [im['id'] for im in images],
                                    'description': '固定测试结论，仅验证流程和展示。', 'suggestion': ''} for r in rules],
                      'mandatory_checks': [], 'image_checks': [{'image_id': im['id'], 'description': '固定测试图片说明，已覆盖本张图片。',
                                                                'rule_ids': [r['id'] for r in rules]} for im in images]}
            value = {'report': report, 'modelResponse': json.dumps(report, ensure_ascii=False),
                     'providerRequestIds': ['e2e-image-fixture'], 'usage': [], 'attemptCount': 1}
            snapshot.get('_persistOutput', lambda value: None)(value)
            return value
        workflow.call_script_model = image_fixture
    temporary_root = Path(__file__).resolve().parents[1] / '.local' / 'tmp' / 'e2e'
    temporary_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="creator-review-e2e-", dir=temporary_root) as directory:
        os.environ["REVIEW_DATA_DIR"] = directory
        uvicorn.run("app.main:app", host="127.0.0.1", port=8001)
