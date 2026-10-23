"""从当前工作区导出源码，不包含 Git 历史、忽略文件和本地运行数据。"""
from pathlib import Path
import subprocess
import shutil
import json
from datetime import datetime, timezone
from zipfile import ZipFile, ZIP_DEFLATED


ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = {"backend", "frontend", "contracts", "scripts", "e2e", ".github", "docs", "openspec", "team", "deploy"}
ROOT_FILES = {".editorconfig", ".gitattributes", ".gitignore", ".dockerignore", ".env.example", "compose.yaml", "README.md", "package.json", "package-lock.json", "playwright.config.ts"}


def main():
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT, check=True, capture_output=True,
    )
    names = sorted(set(name for name in result.stdout.decode("utf-8").split("\0") if name))
    ignored = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin", "-z"],
        input="\0".join(names).encode("utf-8") + b"\0", cwd=ROOT, capture_output=True,
    )
    if ignored.returncode not in (0, 1):
        raise RuntimeError("无法读取 Git 忽略规则，停止导出")
    excluded = set(ignored.stdout.decode("utf-8").split("\0"))
    files = []
    for name in names:
        path = ROOT / name
        if name in excluded or (name.split("/")[0] not in DIRECTORIES and name not in ROOT_FILES):
            continue
        if not path.is_file():
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
            raise RuntimeError(f"拒绝导出符号链接或越界文件：{name}")
        if path.name == ".secrets.key" or (path.name.startswith(".env") and path.name != ".env.example"):
            raise RuntimeError(f"发现敏感配置，停止导出：{name}")
        if path.stat().st_size >= 90 * 1024 * 1024:
            raise RuntimeError(f"文件过大，请先检查：{name}")
        files.append((name, path))
    if not files:
        raise RuntimeError("没有可导出的源码")
    required = {"compose.yaml", ".env.example", ".dockerignore", "deploy/backend.Dockerfile", "deploy/frontend.Dockerfile", "deploy/nginx/default.conf", "deploy/nginx/api.conf", "deploy/relocate_uploads.py", "backend/requirements-docker.txt"}
    if missing := required - {name for name, _ in files}:
        raise RuntimeError(f"部署文件缺失，停止导出：{sorted(missing)}")
    output = ROOT / ".local/exports/creator-review-github.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    ready = output.parent / ("github-ready-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f"))
    ready.mkdir()
    for name, path in files:
        destination = ready / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, path in files:
            archive.write(ready / name, name)
    with ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("压缩包完整性检查失败")
    print(f"已导出 {len(files)} 个文件，{output.stat().st_size / 1048576:.2f} MiB")
    print(output)
    (output.parent / 'github-export-manifest.json').write_text(json.dumps({
        'sourceDirectory': str(ready), 'archive': str(output), 'fileCount': len(files),
        'files': [name for name, _ in files], 'includesGitHistory': False,
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"可直接初始化新 Git 仓库的干净目录：{ready}")


if __name__ == "__main__":
    main()
