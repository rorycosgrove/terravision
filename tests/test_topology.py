from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from topology import (
    assess_route_table_tier,
    assess_subnet_tier,
    build_topology,
    infer_route_table_tier,
    infer_subnet_tier,
    topology_diagnostics,
    topology_summary,
    topology_to_render_bundles,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = REPO_ROOT / "tfplan.json"


def load_plan() -> dict:
    return json.loads(PLAN_PATH.read_text(encoding="utf-8"))


class TopologyTests(unittest.TestCase):
    def test_build_topology_resolves_all_associations_for_sample_plan(self) -> None:
        topology = build_topology(load_plan())
        summary = topology_summary(topology)

        self.assertEqual(summary["vpcs"], 2)
        self.assertEqual(summary["subnets"], 4)
        self.assertEqual(summary["route_table_associations"], 4)
        self.assertEqual(summary["unresolved_associations"], 0)

        for association in topology.route_table_associations:
            self.assertEqual(association.resolution_status, "resolved")
            self.assertIsNotNone(association.subnet_address)
            self.assertIsNotNone(association.route_table_address)

    def test_topology_bundles_surface_unresolved_associations(self) -> None:
        plan = copy.deepcopy(load_plan())
        resources = plan["configuration"]["root_module"]["module_calls"]["vpc_eu_west_1"]["module"]["resources"]
        plan["configuration"]["root_module"]["module_calls"]["vpc_eu_west_1"]["module"]["resources"] = [
            resource for resource in resources if resource.get("type") != "aws_route_table_association"
        ]

        topology = build_topology(plan)
        summary = topology_summary(topology)
        bundles = topology_to_render_bundles(topology)

        self.assertEqual(summary["unresolved_associations"], 2)
        unresolved_bundle = next(bundle for bundle in bundles if bundle["vpc"]["address"] == "module.vpc_eu_west_1.aws_vpc.this")
        self.assertEqual(len(unresolved_bundle["unresolved_associations"]), 2)
        self.assertTrue(
            all(assoc["resolution_status"] == "unresolved" for assoc in unresolved_bundle["route_table_associations"])
        )

    def test_tier_derivation_prefers_resolved_route_table_relationships(self) -> None:
        route_tables = [
            {
                "address": "module.example.aws_route_table.public",
                "name": "public-rt",
                "routes": [{"cidr_block": "0.0.0.0/0", "gateway_id": "igw-123"}],
                "resource": None,
            },
            {
                "address": "module.example.aws_route_table.private",
                "name": "private-rt",
                "routes": [],
                "resource": None,
            },
        ]
        public_subnet = {
            "name": "app-subnet",
            "map_public_ip_on_launch": False,
            "associated_route_table_address": "module.example.aws_route_table.public",
            "resource": None,
        }
        private_subnet = {
            "name": "db-subnet",
            "map_public_ip_on_launch": False,
            "associated_route_table_address": "module.example.aws_route_table.private",
            "resource": None,
        }

        public_route_table_assessment = assess_route_table_tier(route_tables[0])
        private_route_table_assessment = assess_route_table_tier(route_tables[1])
        public_subnet_assessment = assess_subnet_tier(public_subnet, route_tables)
        private_subnet_assessment = assess_subnet_tier(private_subnet, route_tables)

        self.assertEqual(infer_route_table_tier(route_tables[0]), "public")
        self.assertEqual(infer_route_table_tier(route_tables[1]), "private")
        self.assertEqual(infer_subnet_tier(public_subnet, route_tables), "public")
        self.assertEqual(infer_subnet_tier(private_subnet, route_tables), "private")
        self.assertEqual(public_route_table_assessment.confidence, "high")
        self.assertEqual(public_route_table_assessment.source, "default_route_via_gateway")
        self.assertEqual(private_route_table_assessment.confidence, "low")
        self.assertEqual(public_subnet_assessment.source, "associated_route_table:default_route_via_gateway")
        self.assertEqual(private_subnet_assessment.tier, "private")

    def test_topology_diagnostics_include_tier_confidence(self) -> None:
        topology = build_topology(load_plan())
        diagnostics = topology_diagnostics(topology)

        self.assertEqual(len(diagnostics["bundles"]), 2)
        first_bundle = diagnostics["bundles"][0]
        self.assertIn("subnet_tiers", first_bundle)
        self.assertIn("route_table_tiers", first_bundle)
        self.assertTrue(all("confidence" in entry for entry in first_bundle["subnet_tiers"]))
        self.assertTrue(all("source" in entry for entry in first_bundle["route_table_tiers"]))


if __name__ == "__main__":
    unittest.main()