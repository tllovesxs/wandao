# Plugin Contract Recovery Design

## Scope

This first recovery batch covers `yuque`, `aliyun`, and `feishu-export`. It also carries the already-tested cooperative-stop changes for `feishu-import` and `yuque-import`. `youdao` remains in the second batch required by Issue #33 and is not included in this PR.

## Contract

Each scan action has one explicit, tested contract:

`scan JSON -> provider toc mapping -> normalized tree -> selectable document export IDs -> selection CLI args -> backend document filter`.

The renderer must use the manifest mapping without relying on fallback paths to make a tree appear. A folder is visible in the tree but never default-selectable. A document is default-selectable only when the provider contract says it is exportable.

## First-Batch Mappings

| Provider | Scan array | Tree ID / parent | Export ID | Selectable documents | CLI argument |
| --- | --- | --- | --- | --- | --- |
| Yuque | `toc` | `uuid` / `parent_uuid` | `doc_id` | `type == DOC` | `--doc-id` |
| Aliyun Thoughts | `nodes` | backend node ID / `parent_id` | backend export ID | backend `selectable` document nodes | `--doc-id` |
| Feishu Wiki | `ordered` | `wiki_token` / `parent_wiki_token` | `wiki_token` | document nodes with an exportable URL/token, excluding folders | `--doc-id` |

## Stop And Resume

The existing Feishu and Yuque import fixes are integrated as first-batch regressions: a user stop is a controlled `130` result, is rendered as `stopped`, persists item state, and resumes only incomplete or failed items. No stopped result may be recategorized as a resource or permission failure.

## Testing

Sanitized scan fixtures live in JavaScript tests. Tests assert the manifest path, parent/child tree, document-only default selection, export IDs, and exact repeated CLI arguments. Backend unit tests assert selected document IDs use the same field as the manifest. Provider-schema validation accepts the type-selection fields that the renderer consumes.

## Verification

Run `node --test tests_js/toc_tree.test.js`, `python -m unittest`, `python scripts/quality_check.py`, provider validation, and `git diff --check`. Manual login validation remains required for Yuque, Aliyun, and Feishu after the Draft PR is created.
