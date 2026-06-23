#!/usr/bin/env python3
"""
Launch Wandao from a Codex Skill.

Author: tllovesxs
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


REPO_URL = "https://github.com/tllovesxs/wandao.git"
DEFAULT_REPO_DIR = Path.home() / ".wandao" / "wandao"

PROVIDERS = {
    "zsxq": {
        "domains": ("zsxq.com",),
        "url_arg": "--entry-url",
        "default_output": "exports/zsxq",
    },
    "yuque": {
        "domains": ("yuque.com",),
        "url_arg": "--book-url",
        "default_output": "exports/yuque",
    },
    "feishu": {
        "domains": ("feishu.cn",),
        "url_arg": "--wiki-url",
        "default_output": "exports/feishu",
    },
    "aliyun-thoughts": {
        "domains": ("thoughts.aliyun.com",),
        "url_arg": "--workspace-url",
        "default_output": "exports/aliyun-thoughts",
    },
}


def is_wandao_repo(path: Path) -> bool:
    return (path / "wandao.py").is_file() and (path / "README.md").is_file()


def ancestor_repos(start: Path) -> list[Path]:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    return [current, *current.parents]


def find_repo(explicit: str | None) -> Path | None:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    if os.environ.get("WANDAO_HOME"):
        candidates.append(Path(os.environ["WANDAO_HOME"]).expanduser())

    candidates.extend(ancestor_repos(Path.cwd()))
    candidates.extend(ancestor_repos(Path(__file__)))

    home = Path.home()
    candidates.extend(
        [
            home / "wandao",
            home / "Downloads" / "wandao",
            home / "Desktop" / "wandao",
            DEFAULT_REPO_DIR,
        ]
    )

    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if is_wandao_repo(resolved):
            return resolved
    return None


def ensure_repo(repo_dir: Path | None, update_repo: bool) -> Path:
    if repo_dir and is_wandao_repo(repo_dir):
        if update_repo:
            run_checked(["git", "-C", str(repo_dir), "pull", "--ff-only"])
        return repo_dir

    git = shutil.which("git")
    if not git:
        raise SystemExit(
            "Wandao was not found and git is not available. Install git, or download "
            f"{REPO_URL} manually and pass --repo-dir."
        )

    DEFAULT_REPO_DIR.parent.mkdir(parents=True, exist_ok=True)
    if DEFAULT_REPO_DIR.exists() and not is_wandao_repo(DEFAULT_REPO_DIR):
        raise SystemExit(f"{DEFAULT_REPO_DIR} exists but is not a Wandao repo. Pass --repo-dir manually.")

    if DEFAULT_REPO_DIR.exists():
        if update_repo:
            run_checked([git, "-C", str(DEFAULT_REPO_DIR), "pull", "--ff-only"])
        return DEFAULT_REPO_DIR

    run_checked([git, "clone", REPO_URL, str(DEFAULT_REPO_DIR)])
    return DEFAULT_REPO_DIR


def run_checked(command: list[str]) -> None:
    print("+ " + " ".join(command))
    subprocess.check_call(command)


def detect_provider(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if "zsxq.com" in host:
        return "zsxq"
    if host.endswith("yuque.com"):
        return "yuque"
    if host.endswith("feishu.cn") and "/wiki/" in path:
        return "feishu"
    if host == "thoughts.aliyun.com" and "/workspaces/" in path:
        return "aliyun-thoughts"
    return None


def unsupported_url_hint(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.endswith("devops.aliyun.com") or host.endswith("yunxiao.aliyun.com"):
        return (
            "This looks like an Alibaba Cloud Yunxiao/DevOps entry. Wandao currently supports "
            "Aliyun Thoughts workspace URLs such as https://thoughts.aliyun.com/workspaces/<id>/overview, "
            "not generic devops.aliyun.com pages."
        )
    return None


def install_requirements(repo: Path, python: str, skip_install: bool) -> None:
    requirements = repo / "requirements.txt"
    if skip_install or not requirements.exists():
        return
    active_requirements = [
        line.strip()
        for line in requirements.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if active_requirements:
        run_checked([python, "-m", "pip", "install", "-r", str(requirements)])


def default_output(repo: Path, provider: str, output: str | None) -> str:
    if output:
        return output
    return str((repo / PROVIDERS[provider]["default_output"]).resolve())


def build_export_args(args: argparse.Namespace, repo: Path, provider: str) -> list[str]:
    if not args.url:
        raise SystemExit("Command-line export needs --url. Use the GUI when no URL is available.")

    config = PROVIDERS[provider]
    export_args = [
        "--provider",
        provider,
        "--",
        config["url_arg"],
        args.url,
        "--output",
        default_output(repo, provider, args.output),
        "--incremental",
    ]

    if args.update_existing:
        export_args.append("--update-existing")
    if args.wait_login:
        export_args.append("--wait-login")
    if args.browser_path:
        export_args.extend(["--browser-path", args.browser_path])
    if args.request_delay is not None:
        export_args.extend(["--request-delay", str(args.request_delay)])
    if args.request_jitter is not None:
        export_args.extend(["--request-jitter", str(args.request_jitter)])

    if provider == "zsxq":
        max_depth = args.max_depth if args.max_depth is not None else 2
        folder_threshold = args.folder_link_threshold if args.folder_link_threshold is not None else 9
        delay = args.request_delay if args.request_delay is not None else 1.5
        jitter = args.request_jitter if args.request_jitter is not None else 0.6

        export_args.extend(["--max-depth", str(max_depth)])
        export_args.extend(["--folder-link-threshold", str(folder_threshold)])
        if args.request_delay is None:
            export_args.extend(["--request-delay", str(delay)])
        if args.request_jitter is None:
            export_args.extend(["--request-jitter", str(jitter)])
        if args.skip_video_topics:
            export_args.append("--skip-video-topics")
        if args.include_comments:
            export_args.append("--include-comments")
    else:
        delay = args.request_delay if args.request_delay is not None else 0.8
        jitter = args.request_jitter if args.request_jitter is not None else 0.4
        if args.request_delay is None:
            export_args.extend(["--request-delay", str(delay)])
        if args.request_jitter is None:
            export_args.extend(["--request-jitter", str(jitter)])

    return export_args


def print_recommendations(provider: str | None, args: argparse.Namespace, export_mode: bool) -> None:
    if not provider:
        print("Recommendation: open the unified GUI and choose the provider manually.")
        return
    print("Recommendations:")
    print("- Mode: GUI" if not export_mode else "- Mode: incremental command-line export")
    if provider == "zsxq":
        depth = args.max_depth if args.max_depth is not None else 2
        threshold = args.folder_link_threshold if args.folder_link_threshold is not None else 9
        delay = args.request_delay if args.request_delay is not None else 1.5
        jitter = args.request_jitter if args.request_jitter is not None else 0.6
        print(f"- URL depth: {depth}")
        print(f"- Folder link threshold: {threshold}")
        print(f"- Request delay: {delay}s + 0~{jitter}s jitter")
        print("- Video-only pages: included" if args.include_video_topics else "- Video-only pages: skipped")
        print("- Comments: included" if args.include_comments else "- Comments: skipped")
    else:
        delay = args.request_delay if args.request_delay is not None else 0.8
        jitter = args.request_jitter if args.request_jitter is not None else 0.4
        print(f"- Request delay: {delay}s + 0~{jitter}s jitter")
    if args.update_existing:
        print("- Existing docs: refresh during incremental export")
    else:
        print("- Existing docs: keep unless missing docs need to be added")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Wandao from an imported Codex Skill")
    parser.add_argument("--repo-dir", help="Existing Wandao repository directory. Defaults to auto-detect.")
    parser.add_argument("--url", help="Knowledge base entry URL.")
    parser.add_argument("--provider", choices=sorted(PROVIDERS), help="Provider override.")
    parser.add_argument("--export", action="store_true", help="Run command-line export instead of opening GUI.")
    parser.add_argument("--dry-run", action="store_true", help="Print the command and recommendations without running it.")
    parser.add_argument("--output", help="Output directory for command-line export.")
    parser.add_argument("--browser-path", help="Chrome/Edge/Chromium executable path.")
    parser.add_argument("--max-depth", type=int, help="ZSXQ URL recursion depth.")
    parser.add_argument("--folder-link-threshold", type=int, help="ZSXQ folderization threshold.")
    parser.add_argument("--request-delay", type=float, help="Fixed seconds to wait before requests.")
    parser.add_argument("--request-jitter", type=float, help="Extra random seconds added before requests.")
    parser.add_argument("--update-existing", action="store_true", help="Refresh existing documents during incremental export.")
    parser.add_argument("--wait-login", action="store_true", help="Pause command-line export so the user can login manually.")
    parser.add_argument("--include-video-topics", action="store_true", help="Do not skip video-only ZSXQ pages.")
    parser.add_argument("--include-comments", action="store_true", help="Append visible ZSXQ comments to exported Markdown.")
    parser.add_argument("--skip-install", action="store_true", help="Skip pip install -r requirements.txt.")
    parser.add_argument("--update-repo", action="store_true", help="Run git pull --ff-only when an existing repo is found.")
    parsed = parser.parse_args(argv)
    parsed.skip_video_topics = not parsed.include_video_topics
    return parsed


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    repo = ensure_repo(find_repo(args.repo_dir), args.update_repo)
    python = sys.executable
    install_requirements(repo, python, args.skip_install)

    provider = args.provider or detect_provider(args.url)
    if not provider and args.url:
        hint = unsupported_url_hint(args.url)
        if hint:
            raise SystemExit(hint)
        raise SystemExit("Cannot infer provider from URL. Pass --provider explicitly.")

    export_mode = bool(args.export or (args.dry_run and args.url))

    if export_mode:
        if not provider:
            raise SystemExit("Command-line export needs --provider or a recognizable --url.")
        command = [python, str(repo / "wandao.py"), *build_export_args(args, repo, provider)]
    else:
        command = [python, str(repo / "wandao.py")]
        if provider:
            command.extend(["--provider", provider, "--gui"])

    print(f"Wandao repo: {repo}")
    if provider:
        print(f"Provider: {provider}")
    if args.url:
        print(f"URL: {args.url}")
    print_recommendations(provider, args, export_mode)
    print("Command:")
    print(" ".join(f'"{part}"' if " " in part else part for part in command))

    if args.dry_run:
        return 0
    return subprocess.call(command, cwd=str(repo))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
