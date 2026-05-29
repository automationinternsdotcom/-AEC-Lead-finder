from __future__ import annotations

import sys

from pipeline.config import load_settings
from pipeline.crm import DryRunPipedriveMcpClient
from pipeline.enrich import GrokEnricher, NoOpEnricher
from pipeline.extract import AnthropicLeadExtractor, MissingLeadExtractor
from pipeline.orchestrator import LeadPipeline
from pipeline.sheets import CsvSheetGateway
from pipeline import util


def build_pipeline() -> LeadPipeline:
    settings = load_settings()
    sheets = CsvSheetGateway(settings.source_registry_csv, settings.review_queue_csv)

    extractor = (
        AnthropicLeadExtractor(settings)
        if settings.anthropic_api_key
        else MissingLeadExtractor()
    )
    enricher = GrokEnricher(settings) if settings.grok_api_key else NoOpEnricher()

    # Pipedrive MCP is intentionally behind this small adapter boundary.
    # Dry-run is the default so reviewed leads cannot be written accidentally.
    crm = DryRunPipedriveMcpClient()

    if not settings.source_registry_csv:
        util.json_log("configuration_note", message="SOURCE_REGISTRY_CSV is not set; no sources will load")
    if not settings.review_queue_csv:
        util.json_log("configuration_note", message="REVIEW_QUEUE_CSV is not set; review output will be skipped")

    return LeadPipeline(settings, sheets, extractor, enricher, crm)


if __name__ == "__main__":
    sys.exit(build_pipeline().run())

