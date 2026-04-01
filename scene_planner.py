from __future__ import annotations

from typing import Any, Dict, List


def _count_private_subnets_by_az(subnets: List[Dict[str, Any]], route_tables: List[Dict[str, Any]], subnet_tier_fn: Any) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for subnet in subnets:
        az = subnet.get("availability_zone") or "regional"
        if subnet_tier_fn(subnet, route_tables) == "private":
            counts[az] = counts.get(az, 0) + 1
    return counts


def _count_public_subnets_by_az(subnets: List[Dict[str, Any]], route_tables: List[Dict[str, Any]], subnet_tier_fn: Any) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for subnet in subnets:
        az = subnet.get("availability_zone") or "regional"
        if subnet_tier_fn(subnet, route_tables) == "public":
            counts[az] = counts.get(az, 0) + 1
    return counts


def build_scene_plan(bundle: Dict[str, Any], enrichment: Dict[str, Any], subnet_tier_fn: Any) -> Dict[str, Any]:
    subnets = bundle.get("subnets") or []
    route_tables = bundle.get("route_tables") or []
    nat_gws = bundle.get("nat_gateways") or []
    zones = bundle.get("route53_zones") or []
    igws = bundle.get("internet_gateways") or []

    az_names = sorted({subnet.get("availability_zone") or "regional" for subnet in subnets}) or ["regional"]
    az_count = len(az_names)
    public_counts = _count_public_subnets_by_az(subnets, route_tables, subnet_tier_fn)
    private_counts = _count_private_subnets_by_az(subnets, route_tables, subnet_tier_fn)
    max_subnets_in_any_az = 1
    for az in az_names:
        max_subnets_in_any_az = max(max_subnets_in_any_az, public_counts.get(az, 0) + private_counts.get(az, 0))

    page = {
        "width": 3000,
        "height": 2040,
        "gap_y": 2300,
        "header_height": 120,
    }
    rails = {
        "review_width": 760,
        "canvas_width": 2100,
        "canvas_height": 1750,
        "review_card_height": 250,
        "review_card_gap": 34,
    }

    routing_lane_height = 240
    shared_services_height = 210 if zones else 0
    edge_lane_height = 190 if (igws or nat_gws) else 120
    az_height = max(780, min(1160, 560 + max_subnets_in_any_az * 220))
    vpc_height = edge_lane_height + az_height + routing_lane_height + shared_services_height + 190

    az_gap = 40
    az_width = max(380, min(620, int((rails["canvas_width"] - 260 - (az_gap * max(0, az_count - 1))) / az_count)))

    return {
        "page": page,
        "rails": rails,
        "vpc": {
            "height": vpc_height,
            "edge_lane_height": edge_lane_height,
            "az_height": az_height,
            "routing_lane_height": routing_lane_height,
            "shared_services_height": shared_services_height,
        },
        "az": {
            "names": az_names,
            "count": az_count,
            "width": az_width,
            "gap": az_gap,
            "public_counts": public_counts,
            "private_counts": private_counts,
        },
        "review": {
            "summary": enrichment.get("summary") or "Architecture summary unavailable.",
            "risks": enrichment.get("risks") or ["No material risks identified from the Terraform plan data."],
            "opportunities": enrichment.get("opportunities") or ["No additional opportunities captured."],
            "callouts": enrichment.get("callouts") or [],
            "mode": enrichment.get("mode") or "heuristic",
        },
    }
