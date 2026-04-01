from __future__ import annotations

import json
from pathlib import Path
import unittest

from layout_ir import build_layout_ir, serialize_layout_ir
from llm_enrichment import generate_heuristic_enrichment
from topology import build_topology, topology_to_render_bundles


REPO_ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = REPO_ROOT / "tfplan.json"


def load_plan() -> dict:
    return json.loads(PLAN_PATH.read_text(encoding="utf-8"))


class LayoutIRTests(unittest.TestCase):
    def test_build_layout_ir_creates_one_page_per_bundle(self) -> None:
        topology = build_topology(load_plan())
        bundles = topology_to_render_bundles(topology)
        enrichments = [generate_heuristic_enrichment(bundle) for bundle in bundles]

        layout_ir = build_layout_ir(bundles, enrichments)

        self.assertEqual(len(layout_ir.pages), len(bundles))
        for page in layout_ir.pages:
            self.assertGreater(len(page.frames), 0)
            self.assertGreater(len(page.resources), 0)
            self.assertGreaterEqual(len(page.connectors), 2)
            self.assertIn("Architecture Review", [frame.title for frame in page.frames])

    def test_serialized_layout_ir_contains_warnings_and_pages(self) -> None:
        topology = build_topology(load_plan())
        bundles = topology_to_render_bundles(topology)
        enrichments = [generate_heuristic_enrichment(bundle) for bundle in bundles]

        layout_ir = build_layout_ir(bundles, enrichments)
        payload = serialize_layout_ir(layout_ir)

        self.assertIn("pages", payload)
        self.assertEqual(len(payload["pages"]), 2)
        self.assertIn("warnings", payload["pages"][0])
        warning_shapes = [shape for shape in payload["pages"][0]["shapes"] if shape["logical_id"].startswith("review:warnings")]
        self.assertTrue(warning_shapes)


if __name__ == "__main__":
    unittest.main()