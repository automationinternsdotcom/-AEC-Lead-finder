"""Tests for Pipedrive cleanup/schema/pipeline v2 planning."""

from __future__ import annotations

import unittest


class TestSchemaV2Plan(unittest.TestCase):
    def test_missing_required_fields_are_marked_for_creation(self):
        from pipeline import pipedrive_v2

        plan = pipedrive_v2.schema_plan(existing_fields=[
            {"name": "Article URL", "field_type": "varchar", "key": "abc"},
        ])

        creates = [row["name"] for row in plan if row["action"] == "create"]
        self.assertIn("Lead 1", creates)
        self.assertIn("Lead 1 LinkedIn", creates)
        self.assertIn("Next Follow-up Due", creates)
        self.assertFalse(any(row["name"] == "Article URL" and row["action"] == "create" for row in plan))


class TestCleanupReport(unittest.TestCase):
    def test_detects_duplicate_urls_missing_urls_and_normalizes_labels(self):
        from pipeline import pipedrive_v2

        report = pipedrive_v2.cleanup_report([
            {"id": "L1", "title": "A", "article_url": "https://ex.com/a", "labels": [" not relevant ", "HOT"]},
            {"id": "L2", "title": "B", "article_url": "https://ex.com/a", "labels": ["Hot"]},
            {"id": "L3", "title": "C", "article_url": "", "labels": []},
        ])

        self.assertEqual(report["total"], 3)
        self.assertEqual(report["missing_article_url_ids"], ["L3"])
        self.assertEqual(report["duplicate_article_url_groups"], [{"article_url": "https://ex.com/a", "lead_ids": ["L1", "L2"]}])
        self.assertEqual(report["normalized_labels"]["L1"], ["HOT", "NOT RELEVANT"])


class TestPipelineV2Plan(unittest.TestCase):
    def test_missing_pipeline_and_stages_are_planned(self):
        from pipeline import pipedrive_v2

        plan = pipedrive_v2.pipeline_plan(existing_pipelines=[], existing_stages=[])

        self.assertEqual(plan["pipeline"]["action"], "create")
        self.assertEqual(plan["pipeline"]["name"], "Aether Lead Review")
        self.assertEqual(
            [stage["name"] for stage in plan["stages"]],
            [
                "AI Scored",
                "Enriched",
                "Jordan Review",
                "Follow-up Scheduled",
                "Contacted",
                "Qualified Opportunity",
            ],
        )


if __name__ == "__main__":
    unittest.main()
