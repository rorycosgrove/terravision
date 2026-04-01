#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple, Set

import requests

from layout_ir import build_layout_ir, serialize_layout_ir
from llm_enrichment import enrich_bundle, resolve_llm_config
from topology import (
    build_topology,
    serialize_topology,
    topology_diagnostics,
    topology_summary,
    topology_to_render_bundles,
)


MIRO_API_BASE = "https://api.miro.com/v2"


# Public icon URLs. Replace with your own hosted copies if you want stricter reliability.
ICON_URLS = {
    "vpc": "https://raw.githubusercontent.com/awslabs/aws-icons-for-plantuml/main/dist/Groups/VPC.png",
    "public_subnet": "https://raw.githubusercontent.com/awslabs/aws-icons-for-plantuml/main/dist/Groups/PublicSubnet.png",
    "private_subnet": "https://raw.githubusercontent.com/awslabs/aws-icons-for-plantuml/main/dist/Groups/PrivateSubnet.png",
    "route_table": "https://raw.githubusercontent.com/awslabs/aws-icons-for-plantuml/main/dist/NetworkingContentDelivery/Route53RouteTable.png",
    "route53": "https://raw.githubusercontent.com/awslabs/aws-icons-for-plantuml/main/dist/NetworkingContentDelivery/Route53HostedZone.png",
    "igw": "https://raw.githubusercontent.com/awslabs/aws-icons-for-plantuml/main/dist/NetworkingContentDelivery/VPCInternetGateway.png",
    "nat": "https://raw.githubusercontent.com/awslabs/aws-icons-for-plantuml/main/dist/NetworkingContentDelivery/VPCNATGateway.png",
}


def log(msg: str) -> None:
    print(f"[teravision] {msg}")


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )

class MiroClient:
    def __init__(self, token: str, timeout: int = 30) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )
        self.timeout = timeout

    def _request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{MIRO_API_BASE}{path}"
        last_error: Optional[str] = None

        for attempt in range(4):
            resp = self.session.request(method, url, json=payload, timeout=self.timeout)

            if resp.status_code < 400:
                if resp.text.strip():
                    return resp.json()
                return {}

            last_error = f"{resp.status_code}: {resp.text[:2000]}"

            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(0.75 * (attempt + 1))
                continue

            break

        raise RuntimeError(f"Miro API request failed for {method} {path}: {last_error}")

    def create_frame(self, board_id: str, title: str, x: float, y: float, width: int, height: int) -> str:
        result = self._request(
            "POST",
            f"/boards/{board_id}/frames",
            {
                "data": {"title": title},
                "position": {"x": x, "y": y},
                "geometry": {"width": width, "height": height},
            },
        )
        if "id" not in result:
            raise RuntimeError(f"Frame creation returned no id: {result}")
        return result["id"]

    def create_shape(
        self,
        board_id: str,
        html: str,
        x: float,
        y: float,
        width: int = 220,
        height: int = 100,
        fill_color: Optional[str] = None,
        border_color: Optional[str] = None,
        border_width: int = 2,
    ) -> str:
        payload: Dict[str, Any] = {
            "data": {"shape": "round_rectangle", "content": html},
            "position": {"x": x, "y": y},
            "geometry": {"width": width, "height": height},
        }
        style: Dict[str, Any] = {}
        if fill_color:
            style["fillColor"] = fill_color
        if border_color:
            style["borderColor"] = border_color
            style["borderWidth"] = border_width
        if style:
            payload["style"] = style

        result = self._request(
            "POST",
            f"/boards/{board_id}/shapes",
            payload,
        )
        if "id" not in result:
            raise RuntimeError(f"Shape creation returned no id: {result}")
        return result["id"]

    def create_text(self, board_id: str, html: str, x: float, y: float) -> str:
        result = self._request(
            "POST",
            f"/boards/{board_id}/texts",
            {
                "data": {"content": html},
                "position": {"x": x, "y": y},
            },
        )
        if "id" not in result:
            raise RuntimeError(f"Text creation returned no id: {result}")
        return result["id"]

    def create_image(self, board_id: str, url: str, x: float, y: float, width: int = 80) -> str:
        result = self._request(
            "POST",
            f"/boards/{board_id}/images",
            {
                "data": {"url": url},
                "position": {"x": x, "y": y},
                "geometry": {"width": width},
            },
        )
        if "id" not in result:
            raise RuntimeError(f"Image creation returned no id: {result}")
        return result["id"]

    def create_connector(self, board_id: str, start_id: str, end_id: str, caption: Optional[str] = None) -> str:
        payload: Dict[str, Any] = {
            "startItem": {"id": start_id},
            "endItem": {"id": end_id},
            "shape": "straight",
            "style": {
                "strokeColor": "#64748B",
                "strokeWidth": 2.0,
                "endStrokeCap": "arrow",
            },
        }
        if caption:
            payload["captions"] = [{"content": caption}]
        result = self._request("POST", f"/boards/{board_id}/connectors", payload)
        if "id" not in result:
            raise RuntimeError(f"Connector creation returned no id: {result}")
        return result["id"]
def create_labeled_resource(
    miro: MiroClient,
    board_id: str,
    label: str,
    x: float,
    y: float,
    icon_key: Optional[str],
    prefer_icons: bool,
) -> Tuple[str, str]:
    """
    Returns (primary_item_id, label_item_id_or_same).
    Connector should usually use primary_item_id.
    """
    safe_label = html_escape(label).replace("\n", "<br/>")
    text_html = f"<p style='font-size:12px;line-height:1.25;color:#1F2937;font-weight:700'>{safe_label}</p>"
    line_count = max(1, label.count("\n") + 1)
    label_height = 44 if line_count <= 2 else min(76, 40 + line_count * 12)

    if prefer_icons and icon_key and icon_key in ICON_URLS:
        try:
            icon_width_map = {
                "route_table": 92,
                "route53": 92,
                "igw": 98,
                "nat": 98,
                "public_subnet": 102,
                "private_subnet": 102,
            }
            label_width_map = {
                "route_table": 170,
                "route53": 186,
                "igw": 172,
                "nat": 172,
                "public_subnet": 182,
                "private_subnet": 182,
            }
            label_offset_map = {
                "route_table": 72,
                "route53": 72,
                "igw": 62,
                "nat": 62,
                "public_subnet": 82,
                "private_subnet": 82,
            }
            image_id = miro.create_image(board_id, ICON_URLS[icon_key], x, y, width=icon_width_map.get(icon_key, 98))
            label_id = miro.create_shape(
                board_id,
                text_html,
                x,
                y + label_offset_map.get(icon_key, 78),
                label_width_map.get(icon_key, 172),
                label_height,
                fill_color="#F8FAFC",
                border_color="#CBD5E1",
                border_width=1,
            )
            return image_id, label_id
        except Exception as e:
            log(f"Icon render failed for {icon_key}, falling back to shape: {e}")

    shape_id = miro.create_shape(
        board_id,
        text_html,
        x,
        y,
        300,
        max(108, 66 + line_count * 12),
        fill_color="#F9FAFB",
        border_color="#9CA3AF",
        border_width=2,
    )
    return shape_id, shape_id


def render_reference_diagram(
    miro: MiroClient,
    board_id: str,
    bundles: List[Dict[str, Any]],
    prefer_icons: bool,
    llm_config: Optional[Dict[str, Optional[str]]] = None,
    center_x: float = 0,
    center_y: float = 0,
) -> None:
    enrichments = [enrich_bundle(bundle, **(llm_config or {})) for bundle in bundles]
    layout_ir = build_layout_ir(
        bundles=bundles,
        enrichments=enrichments,
        center_x=center_x,
        center_y=center_y,
    )
    render_layout_ir(miro, board_id, layout_ir, prefer_icons)


def render_layout_ir(miro: MiroClient, board_id: str, layout_ir: Any, prefer_icons: bool) -> None:
    for page in layout_ir.pages:
        item_ids: Dict[str, str] = {}
        connectors_seen: Set[Tuple[str, str, str]] = set()

        for frame in page.frames:
            frame_id = miro.create_frame(board_id, frame.title, frame.x, frame.y, frame.width, frame.height)
            item_ids[frame.logical_id] = frame_id

        for shape in page.shapes:
            shape_id = miro.create_shape(
                board_id,
                shape.html,
                shape.x,
                shape.y,
                shape.width,
                shape.height,
                fill_color=shape.fill_color,
                border_color=shape.border_color,
                border_width=shape.border_width,
            )
            item_ids[shape.logical_id] = shape_id

        for resource in page.resources:
            resource_id, _ = create_labeled_resource(
                miro,
                board_id,
                resource.label,
                resource.x,
                resource.y,
                resource.icon_key,
                prefer_icons,
            )
            item_ids[resource.logical_id] = resource_id

        for connector in page.connectors:
            start_id = item_ids.get(connector.start_id)
            end_id = item_ids.get(connector.end_id)
            if not start_id or not end_id:
                log(f"Connector skipped ({connector.start_id} -> {connector.end_id}): unresolved IR element")
                continue
            key = (start_id, end_id, connector.caption or "")
            if key in connectors_seen:
                continue
            connectors_seen.add(key)
            try:
                miro.create_connector(board_id, start_id, end_id, connector.caption)
            except Exception as e:
                log(f"Connector skipped ({connector.start_id} -> {connector.end_id}): {e}")


def save_model(path: str, model: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(model, f, indent=2, default=str)


def resolve_live_config(args: argparse.Namespace) -> Tuple[Optional[str], Optional[str]]:
    board_id = args.board or os.environ.get("MIRO_BOARD_ID")
    token = os.environ.get("MIRO_TOKEN")
    return board_id, token


def main() -> int:
    parser = argparse.ArgumentParser(description="Render AWS Terraform plan into Miro")
    parser.add_argument("--plan", required=True, help="Path to tfplan.json generated by terraform show -json")
    parser.add_argument("--board", help="Miro board ID (or set MIRO_BOARD_ID)")
    parser.add_argument("--dry-run", action="store_true", help="Do not call Miro; just parse and print summary")
    parser.add_argument("--prefer-icons", dest="prefer_icons", action="store_true", default=True, help="Try AWS icons first; fall back to shapes on failure (default: enabled)")
    parser.add_argument("--no-icons", dest="prefer_icons", action="store_false", help="Disable icons and use only shapes")
    parser.add_argument("--dump-model", help="Write parsed AWS model JSON to this path")
    parser.add_argument("--llm-endpoint", help="OpenAI-compatible chat completions endpoint for architecture enrichment")
    parser.add_argument("--llm-model", help="LLM model name used for architecture enrichment")
    parser.add_argument("--llm-api-key-env", default="TERRAVISION_LLM_API_KEY", help="Environment variable name containing the LLM API key")
    parser.add_argument("--skills-dir", default="skills", help="Directory containing reusable skill prompts for LLM enrichment")
    args = parser.parse_args()

    board_id, token = resolve_live_config(args)

    if not args.dry_run and not board_id:
        print("Error: missing board id. Use --board or set MIRO_BOARD_ID", file=sys.stderr)
        return 2

    if not args.dry_run and not token:
        print("Error: MIRO_TOKEN is not set", file=sys.stderr)
        return 2

    plan = load_json(args.plan)
    topology = build_topology(plan)
    bundles = topology_to_render_bundles(topology)
    summary = topology_summary(topology)
    diagnostics = topology_diagnostics(topology)

    log(f"VPCs: {summary['vpcs']}")
    log(f"Subnets: {summary['subnets']}")
    log(f"Route tables: {summary['route_tables']}")
    log(f"Route53 zones: {summary['route53_zones']}")
    log(f"Route table associations: {summary['route_table_associations']}")
    log(f"Unresolved associations: {summary['unresolved_associations']}")
    for bundle_diagnostic in diagnostics["bundles"]:
        log(f"Bundle: {bundle_diagnostic['vpc_address']}")
        for subnet_tier in bundle_diagnostic["subnet_tiers"]:
            log(
                "  Subnet tier: "
                f"{subnet_tier['address']} -> {subnet_tier['tier']} "
                f"({subnet_tier['confidence']}, {subnet_tier['source']})"
            )
        for route_table_tier in bundle_diagnostic["route_table_tiers"]:
            log(
                "  Route table tier: "
                f"{route_table_tier['address']} -> {route_table_tier['tier']} "
                f"({route_table_tier['confidence']}, {route_table_tier['source']})"
            )

    llm_config = resolve_llm_config(args)
    llm_enabled = bool(llm_config.get("llm_endpoint") and llm_config.get("llm_model") and llm_config.get("llm_api_key"))
    log(f"Callout enrichment: {'LLM' if llm_enabled else 'heuristic'}")

    layout_ir = None
    if args.dump_model or not args.dry_run:
        enrichments = [enrich_bundle(bundle, **llm_config) for bundle in bundles]
        layout_ir = build_layout_ir(bundles=bundles, enrichments=enrichments, center_x=0, center_y=0)

    if args.dump_model:
        save_model(
            args.dump_model,
            {
                "topology": serialize_topology(topology),
                "diagnostics": diagnostics,
                "render_bundles": bundles,
                "layout_ir": serialize_layout_ir(layout_ir) if layout_ir else None,
            },
        )
        log(f"Wrote model to {args.dump_model}")

    if args.dry_run:
        return 0

    miro = MiroClient(token)

    try:
        render_reference_diagram(
            miro=miro,
            board_id=board_id,
            bundles=bundles,
            prefer_icons=args.prefer_icons,
            llm_config=llm_config,
            center_x=0,
            center_y=0,
        )
        time.sleep(0.1)
    except Exception as e:
        print(f"Error: live render failed: {e}", file=sys.stderr)
        return 1

    log("Render complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())