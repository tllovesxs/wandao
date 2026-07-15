import unittest
from unittest.mock import patch

from wandao_report import derive_outcome, finalize_report


class WandaoReportTests(unittest.TestCase):
    def test_finalize_export_report_adds_common_fields(self) -> None:
        report = finalize_report(
            {
                "platform": "wiz",
                "total": 3,
                "exported": 2,
                "failures": [{"title": "broken", "error": "timeout"}],
            },
            mode="export",
            report_file="00-导出报告.json",
            output="exports/wiz",
        )

        self.assertEqual(report["reportSchemaVersion"], 1)
        self.assertEqual(report["provider"], "wiz")
        self.assertEqual(report["mode"], "export")
        self.assertEqual(report["totalDocs"], 3)
        self.assertEqual(report["successCount"], 2)
        self.assertEqual(report["failureCount"], 1)
        self.assertEqual(report["outcome"], "partial")
        self.assertEqual(report["reportFile"], "00-导出报告.json")
        self.assertEqual(report["output"], "exports/wiz")

    def test_finalize_import_report_combines_created_and_updated(self) -> None:
        report = finalize_report(
            {
                "provider": "yuque-import",
                "totalDocs": 5,
                "createdDocs": 2,
                "updatedDocs": 1,
                "skippedDocs": 1,
                "imageFailures": [{"document": "a", "failures": [{"url": "x"}]}],
            },
            mode="import",
        )

        self.assertEqual(report["successCount"], 3)
        self.assertEqual(report["failureCount"], 0)
        self.assertEqual(report["resourceFailures"][0]["type"], "image")
        self.assertEqual(report["outcome"], "partial")

    def test_finalize_legacy_import_count_fields(self) -> None:
        report = finalize_report(
            {
                "provider": "yinxiang-import",
                "sourceDocCount": 4,
                "importedCount": 3,
                "failures": [{"path": "broken.md", "error": "timeout"}],
            },
            mode="import",
        )

        self.assertEqual(report["totalDocs"], 4)
        self.assertEqual(report["successCount"], 3)
        self.assertEqual(report["failureCount"], 1)
        self.assertEqual(report["outcome"], "partial")

    def test_finalize_report_emits_task_result_v1_with_run_lineage(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "WANDAO_RUN_ID": "run-123",
                "WANDAO_JOB_ID": "job-456",
                "WANDAO_PARENT_RUN_ID": "run-122",
            },
            clear=False,
        ):
            report = finalize_report({"totalDocs": 0}, provider="wiz", mode="export")

        self.assertEqual(report["kind"], "wandao.result")
        self.assertEqual(report["schemaVersion"], 1)
        self.assertEqual(report["runId"], "run-123")
        self.assertEqual(report["jobId"], "job-456")
        self.assertEqual(report["parentRunId"], "run-122")
        self.assertEqual(report["outcome"], "completed")
        for field in ("provider", "mode", "totalDocs", "successCount", "failureCount", "failures", "resourceFailures", "outcome"):
            self.assertIn(field, report)

    def test_report_outcome_corrects_contradictory_failure_count(self) -> None:
        report = finalize_report(
            {
                "totalDocs": 2,
                "successCount": 1,
                "failureCount": 0,
                "failures": [{"title": "missing", "error": "timeout"}],
            }
        )

        self.assertEqual(report["failureCount"], 1)
        self.assertEqual(report["outcome"], "partial")
        self.assertEqual(derive_outcome({"stopped": True}), "stopped")


if __name__ == "__main__":
    unittest.main()
