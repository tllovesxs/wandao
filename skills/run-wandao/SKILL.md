---
name: run-wandao
description: "Use this skill when the user wants to run Wandao without reading project docs: ask for the knowledge base URL if it is missing, infer the provider, recommend safe parameters, and directly call the bundled launch_wandao.py script to start the GUI or run an authorized export."
---

# Run Wandao

This skill directly calls the bundled launcher script:

```bash
python <this-skill-dir>/scripts/launch_wandao.py
```

The launcher script then locates or downloads the Wandao repository and calls `wandao.py`.

## Required Behavior

When the user invokes `$run-wandao`, do not stop at explanation.

- If the user did not provide a URL, ask for the knowledge base URL first. Do not open an empty GUI as the first response.
- If the user provided a URL and asks to export, crawl, fetch, or start, call the launcher script with `--url "<url>" --export`.
- If the user provided a URL but only asks for parameter advice, call the launcher script with `--url "<url>" --dry-run`.
- If the user explicitly wants a GUI, call the launcher script with `--url "<url>"` when a URL is available.

Use the absolute path of this skill directory when running the script. If the skill is installed at `~/.codex/skills/run-wandao`, run:

```bash
python ~/.codex/skills/run-wandao/scripts/launch_wandao.py
```

If the current working directory is the Wandao repository, this also works:

```bash
python skills/run-wandao/scripts/launch_wandao.py
```

## Normal Flow

1. Check that the user is exporting content they are allowed to access.
2. If there is no URL in the user message, ask: "Please send the knowledge base URL you want to export."
3. If the user gave a URL, run a dry run first only when they ask for recommendations or when the URL/provider is uncertain:

   ```bash
   python <this-skill-dir>/scripts/launch_wandao.py --url "<url>" --dry-run
   ```

4. If the user wants a GUI, run:

   ```bash
   python <this-skill-dir>/scripts/launch_wandao.py --url "<url>"
   ```

5. If the user wants the AI to run the export/crawl directly, run:

   ```bash
   python <this-skill-dir>/scripts/launch_wandao.py --url "<url>" --export
   ```

6. If the user explicitly says they just want to open the tool without a URL, open the unified GUI:

   ```bash
   python <this-skill-dir>/scripts/launch_wandao.py
   ```

## Provider Detection

The launcher can infer providers from URL hosts:

- `zsxq.com` -> `zsxq`
- `yuque.com` -> `yuque`
- `feishu.cn/wiki` -> `feishu`
- `thoughts.aliyun.com/workspaces` -> `aliyun-thoughts`

If the user says "Alibaba Cloud Yunxiao" or "Alibaba Cloud DevOps", inspect the actual URL first. Use `aliyun-thoughts` only for `thoughts.aliyun.com/workspaces/...` URLs. Generic `devops.aliyun.com` pages are not supported by the current exporter.

If a URL is ambiguous, ask the user for the provider or pass `--provider`.

## Recommended Parameters

For `zsxq` project or column URLs:

- Default depth: `--max-depth 2`
- Folder threshold: `--folder-link-threshold 9`
- Skip video-only pages unless requested: `--skip-video-topics`
- Comments are skipped by default; add `--include-comments` only when the user asks to export page comments.
- Safer request pace: `--request-delay 1.5 --request-jitter 0.6`
- If nested links were missed in an earlier run, add `--update-existing`

For `yuque`, `feishu`, and `aliyun-thoughts` URLs:

- Use incremental export by default
- Normal request pace: `--request-delay 0.8 --request-jitter 0.4`
- Add `--update-existing` only when refreshing existing local docs

For single article URLs:

- Use low depth unless the article is clearly an index page
- Skip video-only pages when the user wants text documents

## Plain-Language Explanations

Use these short explanations when the user is unsure:

- URL depth: how many layers of internal links should be opened.
- Request delay: how long the tool waits between document/page requests.
- Incremental export: only add missing documents by default.
- Update existing: refresh documents that already exist locally.
- Skip video pages: avoid creating empty Markdown for video-only pages.
- Include comments: append the visible page comment area to the Markdown after the main content.

The tool stores cookies only, not account passwords. If login is needed, tell the user to complete login in the opened browser and then save credentials in the GUI.

If command-line export fails because the site requires login or directory selection, switch to GUI mode with the same URL and tell the user to login/read the directory in the opened window.
