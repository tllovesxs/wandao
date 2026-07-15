#!/usr/bin/env python3
"""Run the lightweight quality gate used locally and in CI."""

from __future__ import annotations

import argparse
import py_compile
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.validate_providers import validate_repository
NODE_CHECK_FILES = [
    "wandao_electron/main.js",
    "wandao_electron/preload.js",
    "wandao_electron/plugin_format.js",
    "wandao_electron/plugin_manager.js",
    "wandao_electron/process_result.js",
    "wandao_electron/command_security.js",
    "wandao_electron/provider_script_routing.js",
    "wandao_electron/plugin_state_migration.js",
    "wandao_electron/renderer/app.js",
    "wandao_electron/provider_legacy_compat.js",
    "wandao_electron/renderer/providers.js",
    "wandao_electron/renderer/provider_runtime.js",
    "wandao_electron/renderer/task_report.js",
    "wandao_electron/renderer/task_history.js",
    "wandao_electron/renderer/recent_inputs.js",
    "wandao_electron/renderer/structured_logs.js",
    "wandao_electron/renderer/time_format.js",
    "wandao_electron/renderer/task_resume.js",
    "wandao_electron/renderer/toc_tree.js",
    "wandao_electron/scripts/npm_install_cn.js",
    "scripts/build_plugin.js",
    "scripts/build_plugin_registry.js",
    "scripts/validate_plugins.js",
    "scripts/check_plugin_versions.js",
    "scripts/plugin_release_policy.js",
    "wandao_electron/scripts/prepare_python_runtime.py",
]


def iter_python_files() -> list[Path]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.py"],
            cwd=REPO_ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
        return [REPO_ROOT / line.strip() for line in result.stdout.splitlines() if line.strip()]
    except Exception:
        return sorted(
            path
            for path in REPO_ROOT.rglob("*.py")
            if not any(part.startswith(".tmp") or part in {".git", ".venv", "__pycache__", "node_modules"} for part in path.parts)
        )


def run_py_compile() -> None:
    for path in iter_python_files():
        py_compile.compile(str(path), doraise=True)
    print(f"Python compile passed ({len(iter_python_files())} files).")


def run_unittest() -> None:
    suite = unittest.defaultTestLoader.discover(str(REPO_ROOT / "tests"))
    result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


def run_provider_validation() -> None:
    issues = validate_repository(REPO_ROOT)
    if issues:
        for issue in issues:
            print(issue.format(REPO_ROOT), file=sys.stderr)
        raise SystemExit(f"Provider validation failed: {len(issues)} issue(s).")
    print("Provider validation passed.")


def run_node_checks() -> None:
    checked = 0
    for rel in NODE_CHECK_FILES:
        path = REPO_ROOT / rel
        if path.suffix != ".js" or not path.exists():
            continue
        subprocess.run(["node", "--check", str(path)], cwd=REPO_ROOT, check=True)
        checked += 1
    subprocess.run(["node", "scripts/validate_plugins.js"], cwd=REPO_ROOT, check=True)
    subprocess.run(
        [
            "node", "--test",
            "tests_js/plugin_manager.test.js",
            "tests_js/process_result.test.js",
            "tests_js/command_security.test.js",
            "tests_js/provider_script_routing.test.js",
            "tests_js/plugin_release_policy.test.js",
            "tests_js/plugin_state_migration.test.js",
            "tests_js/provider_legacy_compat.test.js",
            "tests_js/time_format.test.js",
            "tests_js/form_drafts.test.js",
            "tests_js/recent_inputs.test.js",
            "tests_js/manifest_form_validation.test.js",
            "tests_js/import_write_guidance.test.js",
            "tests_js/task_accessibility.test.js",
            "tests_js/task_report.test.js",
            "tests_js/task_history.test.js",
            "tests_js/task_history_persistence.test.js",
            "tests_js/task_resume.test.js",
            "tests_js/provider_toc_contract.test.js",
            "tests_js/toc_tree.test.js",
            "tests_js/toc_rendering.test.js",
        ],
        cwd=REPO_ROOT,
        check=True,
    )
    subprocess.run(
        [
            "node",
            "-e",
            (
                "global.window=global;"
                "require('./wandao_electron/renderer/providers.js');"
                "global.WandaoProviders.register(require('./plugins/yuque/providers/yuque-import/provider.json'));"
                "const yuque=global.WandaoProviders.get('yuque-import');"
                "if(!yuque.capabilities.retryFailures||yuque.retryFailures.arg!=='--retry-failures') process.exit(1);"
            ),
        ],
        cwd=REPO_ROOT,
        check=True,
    )
    subprocess.run(
        [
            "node",
            "-e",
            (
                "const p=require('./wandao_electron/renderer/provider_runtime.js');"
                "if(p.shouldConfirmExecution({trustLevel:'official',script:'a.py'})) process.exit(1);"
                "if(!p.shouldConfirmExecution({trustLevel:'community',script:'a.py'})) process.exit(1);"
                "if(p.shouldConfirmExecution({trustLevel:'community',type:'guide'})) process.exit(1);"
                "if(p.providerTrustClass({status:'experimental'})!=='experimental') process.exit(1);"
            ),
        ],
        cwd=REPO_ROOT,
        check=True,
    )
    subprocess.run(
        [
            "node",
            "-e",
            (
                "const r=require('./wandao_electron/renderer/task_report.js');"
                "const report=r.normalizeTaskReport({totalDocs:2,exportedDocs:1,failures:[{title:'a',error:'x'}]});"
                "if(report.stats.total!==2||report.stats.exported!==1||report.stats.failed!==1) process.exit(1);"
                "const text=r.createMarkdownTaskReport({id:'t1',providerId:'demo',status:'completed',args:['--app-secret','abc'],resultData:{totalDocs:1,exportedDocs:1},logs:[]},{provider:{title:'Demo'},maskSensitiveText:(v)=>v});"
                "if(text.indexOf('Demo')<0||text.indexOf('***')<0||text.indexOf('导出 1')<0) process.exit(1);"
                "const task={report:r.normalizeTaskReport({output:'out',reportFile:'report.json',failures:[{title:'a',error:'bad'}]})};"
                "if(r.taskArtifactPaths(task).reportFile!=='report.json'||r.taskFailurePreview(task,1)[0].indexOf('bad')<0) process.exit(1);"
                "const resourceTask={report:r.normalizeTaskReport({imageFailureCount:2,resourceFailures:[{error:'x'},{error:'y'}]})};"
                "if(r.taskFailureCount(resourceTask)!==2) process.exit(1);"
                "const warningReport=r.normalizeTaskReport({totalDocs:1,resourceWarnings:[{target:'missing.png',reason:'local_file_missing'}]});"
                "if(warningReport.stats.failed!==0||warningReport.failures.length!==0) process.exit(1);"
            ),
        ],
        cwd=REPO_ROOT,
        check=True,
    )
    subprocess.run(
        [
            "node",
            "-e",
            (
                "const logs=require('./wandao_electron/renderer/structured_logs.js');"
                "let user=[]; let detail=[]; let progress=null;"
                "const p=logs.createProcessor({appendDetailedLog:(s,t,m,o)=>detail.push({s,t,m,o}),"
                "appendUserLog:(m,t)=>user.push({m,t}),updateProgress:(c,t,d)=>progress={c,t,d}});"
                "p.handleLine(logs.STRUCTURED_LOG_PREFIX+JSON.stringify({event:'task.progress',level:'info',provider:'demo',progress:{current:1,total:2},stats:{exportedDocs:1}}));"
                "if(user[0].m.indexOf('进度 1/2')<0||detail[0].o.event!=='task.progress'||progress.c!==1) process.exit(1);"
            ),
        ],
        cwd=REPO_ROOT,
        check=True,
    )
    print(f"Node syntax check passed ({checked} files).")


def run_diff_check() -> None:
    result = subprocess.run(
        ["git", "diff", "--check"],
        cwd=REPO_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    noise = "LF will be replaced by CRLF"
    output = (result.stdout or "") + (result.stderr or "")
    lines = [line for line in output.splitlines() if noise not in line]
    if result.returncode != 0:
        for line in lines:
            print(line, file=sys.stderr)
        raise SystemExit(result.returncode)
    for line in lines:
        print(line)
    print("Git diff whitespace check passed.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Wandao quality checks.")
    parser.add_argument("--skip-node", action="store_true", help="Skip node --check, useful on Python-only machines.")
    args = parser.parse_args(argv)

    run_provider_validation()
    run_py_compile()
    run_unittest()
    if not args.skip_node:
        run_node_checks()
    run_diff_check()
    print("Quality check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
