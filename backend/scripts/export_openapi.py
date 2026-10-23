"""不启动服务、不创建数据库，导出 OpenAPI 供前端生成类型。"""
import json
import sys
from pathlib import Path

backend = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(backend))
from app.main import create_app

destination = backend.parent / "contracts" / "openapi.json"
destination.parent.mkdir(exist_ok=True)
destination.write_text(json.dumps(create_app().openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
print(f"Exported {destination}")
