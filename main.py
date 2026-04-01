#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Set

import requests

from llm_enrichment import enrich_bundle, resolve_llm_config
from scene_planner import build_scene_plan


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


@dataclass
class PlannedResource:
    address: str
    rtype: str
    name: str
    provider_name: str
    values: Dict[str, Any] = field(default_factory=dict)
    depends_on: List[str] = field(default_factory=list)
    module_path: str = "root"


@dataclass
class RenderNode:
    logical_id: str
    label: str
    kind: str
    x: float
    y: float
    icon_key: Optional[str] = None


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
                "strokeColor": "#374151",
                "strokeWidth": 2.5,
                "endStrokeCap": "arrow",
            },
        }
        if caption:
            payload["captions"] = [{"content": caption}]
        result = self._request("POST", f"/boards/{board_id}/connectors", payload)
        if "id" not in result:
            raise RuntimeError(f"Connector creation returned no id: {result}")
        return result["id"]


def collect_planned_resources(module: Dict[str, Any], out: Dict[str, PlannedResource], module_path: str = "root") -> None:
    for r in module.get("resources", []) or []:
        address = r["address"]
        out[address] = PlannedResource(
            address=address,
            rtype=r["type"],
            name=r.get("name", address),
            provider_name=r.get("provider_name", ""),
            values=r.get("values") or {},
            depends_on=r.get("depends_on") or [],
            module_path=module_path,
        )

    for child in module.get("child_modules", []) or []:
        child_path = child.get("address", module_path)
        collect_planned_resources(child, out, child_path)


def parse_association_suffix(address: str) -> Tuple[Optional[str], Optional[str]]:
    match = re.search(r"\.([^.\[]+)\[\"([^\"]+)\"\]$", address)
    if not match:
        return None, None
    return match.group(1), match.group(2)


def build_aws_model(plan: Dict[str, Any]) -> Dict[str, Any]:
    root = (plan.get("planned_values") or {}).get("root_module")
    if not root:
        raise ValueError("Invalid tfplan.json: missing planned_values.root_module")

    resources: Dict[str, PlannedResource] = {}
    collect_planned_resources(root, resources)

    model: Dict[str, Any] = {
        "vpcs": {},
        "subnets": {},
        "route_tables": {},
        "route_table_associations": [],
        "route53_zones": [],
        "internet_gateways": [],
        "nat_gateways": [],
    }

    # First pass
    for addr, r in resources.items():
        vals = r.values
        rtype = r.rtype

        if rtype == "aws_vpc":
            vpc_id = vals.get("id") or addr
            model["vpcs"][addr] = {
                "address": addr,
                "id": vpc_id,
                "cidr_block": vals.get("cidr_block", ""),
                "name": (vals.get("tags") or {}).get("Name", r.name),
                "resource": r,
            }

        elif rtype == "aws_subnet":
            assoc_name, assoc_index = parse_association_suffix(addr)
            model["subnets"][addr] = {
                "address": addr,
                "vpc_id": vals.get("vpc_id"),
                "subnet_id": vals.get("id") or addr,
                "cidr_block": vals.get("cidr_block", ""),
                "availability_zone": vals.get("availability_zone", ""),
                "name": (vals.get("tags") or {}).get("Name", r.name),
                "tf_name": r.name,
                "assoc_name": assoc_name,
                "assoc_index": assoc_index,
                "map_public_ip_on_launch": bool(vals.get("map_public_ip_on_launch", False)),
                "resource": r,
            }

        elif rtype == "aws_route_table":
            model["route_tables"][addr] = {
                "address": addr,
                "route_table_id": vals.get("id") or addr,
                "vpc_id": vals.get("vpc_id"),
                "name": (vals.get("tags") or {}).get("Name", r.name),
                "tf_name": r.name,
                "routes": vals.get("route") or [],
                "resource": r,
            }

        elif rtype == "aws_route_table_association":
            assoc_name, assoc_index = parse_association_suffix(addr)
            model["route_table_associations"].append(
                {
                    "address": addr,
                    "assoc_name": assoc_name,
                    "assoc_index": assoc_index,
                    "resource": r,
                }
            )

        elif rtype == "aws_route53_zone":
            model["route53_zones"].append(
                {
                    "address": addr,
                    "name": vals.get("name", r.name),
                    "vpcs": vals.get("vpc") or [],
                    "resource": r,
                }
            )

        elif rtype == "aws_internet_gateway":
            model["internet_gateways"].append(
                {
                    "address": addr,
                    "vpc_id": vals.get("vpc_id"),
                    "name": (vals.get("tags") or {}).get("Name", r.name),
                    "resource": r,
                }
            )

        elif rtype == "aws_nat_gateway":
            model["nat_gateways"].append(
                {
                    "address": addr,
                    "subnet_id": vals.get("subnet_id"),
                    "name": (vals.get("tags") or {}).get("Name", r.name),
                    "resource": r,
                }
            )

    return model


def route_table_is_public(route_table: Dict[str, Any]) -> bool:
    for route in route_table.get("routes", []):
        gateway_id = route.get("gateway_id")
        cidr = route.get("cidr_block")
        if gateway_id and cidr == "0.0.0.0/0":
            return True
    return False


def _resource_tags(resource_entry: Dict[str, Any]) -> Dict[str, Any]:
    resource_obj: Optional[PlannedResource] = resource_entry.get("resource")
    if not resource_obj:
        return {}
    vals = resource_obj.values or {}
    return (vals.get("tags") or {}) or (vals.get("tags_all") or {})


def infer_subnet_tier(subnet: Dict[str, Any], route_tables: List[Dict[str, Any]]) -> str:
    if subnet.get("map_public_ip_on_launch"):
        return "public"

    tags = _resource_tags(subnet)
    tier_tag = str(tags.get("Tier", "")).lower()
    if "public" in tier_tag:
        return "public"
    if "private" in tier_tag:
        return "private"

    subnet_name = str(subnet.get("name") or "").lower()
    if "public" in subnet_name:
        return "public"
    if "private" in subnet_name:
        return "private"

    for rt in route_tables:
        if route_table_is_public(rt):
            return "public"
    return "private"


def infer_route_table_tier(route_table: Dict[str, Any]) -> str:
    if route_table_is_public(route_table):
        return "public"

    tags = _resource_tags(route_table)
    tier_tag = str(tags.get("Tier", "")).lower()
    if "public" in tier_tag:
        return "public"
    if "private" in tier_tag:
        return "private"

    name = str(route_table.get("name") or "").lower()
    if "public" in name:
        return "public"
    if "private" in name:
        return "private"
    return "private"


def subnet_tier(subnet: Dict[str, Any], route_tables: List[Dict[str, Any]]) -> str:
    return infer_subnet_tier(subnet, route_tables)


def build_vpc_render_data(model: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    for _, vpc in model["vpcs"].items():
        vpc_id = vpc["id"]
        vpc_resource: PlannedResource = vpc["resource"]
        vpc_module_path = vpc_resource.module_path
        vpc_region = (
            (vpc_resource.values.get("tags") or {}).get("Region")
            or (vpc_resource.values.get("tags_all") or {}).get("Region")
        )

        def in_same_vpc(resource_entry: Dict[str, Any]) -> bool:
            resource_vpc_id = resource_entry.get("vpc_id")
            if resource_vpc_id and resource_vpc_id == vpc_id:
                return True
            resource_obj: PlannedResource = resource_entry.get("resource")
            return bool(resource_obj and resource_obj.module_path == vpc_module_path)

        subnets = [s for s in model["subnets"].values() if in_same_vpc(s)]
        route_tables = [rt for rt in model["route_tables"].values() if in_same_vpc(rt)]
        igws = [igw for igw in model["internet_gateways"] if in_same_vpc(igw)]
        nat_gws = []
        route_table_associations = []

        subnet_ids = {s["subnet_id"] for s in subnets}
        for nat in model["nat_gateways"]:
            if nat["subnet_id"] in subnet_ids:
                nat_gws.append(nat)

        for assoc in model["route_table_associations"]:
            assoc_resource: PlannedResource = assoc["resource"]
            if assoc_resource.module_path == vpc_module_path:
                route_table_associations.append(assoc)

        zones = []
        for zone in model["route53_zones"]:
            for assoc in zone["vpcs"]:
                if assoc.get("vpc_id") == vpc_id:
                    zones.append(zone)
                    break
                if vpc_region and assoc.get("vpc_region") == vpc_region:
                    zones.append(zone)
                    break

        out.append(
            {
                "vpc": vpc,
                "subnets": subnets,
                "route_tables": route_tables,
                "internet_gateways": igws,
                "nat_gateways": nat_gws,
                "route_table_associations": route_table_associations,
                "route53_zones": zones,
            }
        )

    return out


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
    text_html = f"<p style='font-size:12px;line-height:1.6;color:#374151;font-weight:600'>{safe_label}</p>"

    if prefer_icons and icon_key and icon_key in ICON_URLS:
        try:
            image_id = miro.create_image(board_id, ICON_URLS[icon_key], x, y, width=96)
            text_id = miro.create_text(board_id, text_html, x, y + 130)
            return image_id, text_id
        except Exception as e:
            log(f"Icon render failed for {icon_key}, falling back to shape: {e}")

    shape_id = miro.create_shape(board_id, text_html, x, y, 290, 135, fill_color="#F9FAFB", border_color="#9CA3AF", border_width=2)
    return shape_id, shape_id


def create_review_card(
    miro: MiroClient,
    board_id: str,
    x: float,
    y: float,
    width: int,
    height: int,
    title: str,
    body_lines: List[str],
    accent_fill: str,
    accent_border: str,
) -> None:
    safe_title = html_escape(title)
    safe_lines = [html_escape(line) for line in body_lines if line]
    content = "".join(f"<p style='font-size:13px;color:#1F2937;line-height:1.65;margin:0 0 10px 0'>{line}</p>" for line in safe_lines)
    miro.create_shape(
        board_id,
        f"<p style='font-size:15px;color:#0F172A;font-weight:900;letter-spacing:0.2px;margin:0 0 16px 0'><strong>{safe_title}</strong></p>{content}",
        x,
        y,
        width,
        height,
        fill_color="#FFFFFF",
        border_color="#CBD5E1",
        border_width=2,
    )
    miro.create_shape(
        board_id,
        "",
        x - (width / 2) + 10,
        y,
        20,
        height - 18,
        fill_color=accent_fill,
        border_color=accent_border,
        border_width=2,
    )


def render_vpc_frame(
    miro: MiroClient,
    board_id: str,
    vpc_bundle: Dict[str, Any],
    frame_center_x: float,
    frame_center_y: float,
    prefer_icons: bool,
) -> None:
    vpc = vpc_bundle["vpc"]
    subnets = sorted(vpc_bundle["subnets"], key=lambda s: (s.get("availability_zone", ""), s.get("cidr_block", "")))
    route_tables = sorted(vpc_bundle["route_tables"], key=lambda rt: rt.get("name", ""))
    igws = vpc_bundle["internet_gateways"]
    nat_gws = vpc_bundle["nat_gateways"]
    route_table_associations = vpc_bundle["route_table_associations"]
    zones = vpc_bundle["route53_zones"]

    az_names = sorted({s.get("availability_zone") or "regional" for s in subnets})
    if not az_names:
        az_names = ["regional"]

    az_count = len(az_names)
    vpc_frame_width = max(2200, 1050 + az_count * 520)
    vpc_frame_height = 1320

    vpc_resource: PlannedResource = vpc["resource"]
    vpc_region = (
        (vpc_resource.values.get("tags") or {}).get("Region")
        or (vpc_resource.values.get("tags_all") or {}).get("Region")
        or "unknown-region"
    )

    cloud_frame_width = vpc_frame_width + 320
    cloud_frame_height = vpc_frame_height + 360
    miro.create_frame(board_id, "AWS Cloud", frame_center_x, frame_center_y, cloud_frame_width, cloud_frame_height)

    miro.create_text(
        board_id,
        "<p style='font-size:26px'><strong>AWS Reference Architecture</strong></p>",
        frame_center_x - 360,
        frame_center_y - (cloud_frame_height / 2) + 40,
    )
    miro.create_text(
        board_id,
        f"<p style='font-size:16px'><strong>Region:</strong> {html_escape(vpc_region)}</p>",
        frame_center_x + 360,
        frame_center_y - (cloud_frame_height / 2) + 40,
    )

    miro.create_frame(
        board_id,
        f"AWS Account ({vpc_region})",
        frame_center_x,
        frame_center_y + 10,
        vpc_frame_width + 180,
        vpc_frame_height + 190,
    )

    miro.create_frame(
        board_id,
        f"Amazon VPC {vpc.get('cidr_block') or vpc.get('name') or vpc['id']}",
        frame_center_x - 120,
        frame_center_y + 60,
        vpc_frame_width - 360,
        vpc_frame_height,
    )

    connectors_seen: Set[Tuple[str, str, str]] = set()

    def connect(a: str, b: str, label: Optional[str] = None) -> None:
        key = (a, b, label or "")
        if key in connectors_seen:
            return
        connectors_seen.add(key)
        try:
            miro.create_connector(board_id, a, b, label)
        except Exception as e:
            log(f"Connector skipped ({a} -> {b}): {e}")

    item_ids: Dict[str, str] = {}

    has_public = any(subnet_tier(s, route_tables) == "public" for s in subnets)
    show_internet = bool(igws) or has_public
    internet_id: Optional[str] = None
    if show_internet:
        internet_id = miro.create_shape(
            board_id,
            "<p style='font-size:18px'><strong>Internet</strong></p>",
            frame_center_x - 120,
            frame_center_y - 620,
            220,
            80,
        )

    igw_item_ids: List[str] = []
    if igws:
        for idx, igw in enumerate(igws):
            igw_x = frame_center_x - 250 + idx * 250
            igw_y = frame_center_y - 500
            label = f"Internet Gateway\n{igw.get('name') or igw['address']}"
            item_id, _ = create_labeled_resource(miro, board_id, label, igw_x, igw_y, "igw", prefer_icons)
            igw_item_ids.append(item_id)
            if internet_id:
                connect(internet_id, item_id)

    legend_x = frame_center_x + (vpc_frame_width / 2) - 170
    legend_y = frame_center_y - 10
    legend_html = (
        "<p style='font-size:14px'><strong>Diagram Notes</strong><br/>"
        "1. Boundaries follow AWS Cloud -> Account -> VPC -> AZ.<br/>"
        "2. Subnet to route table links use Terraform associations.<br/>"
        "3. Only meaningful network paths are shown.</p>"
    )
    miro.create_shape(board_id, legend_html, legend_x, legend_y, 330, 250)

    vpc_left = (frame_center_x - 120) - ((vpc_frame_width - 360) / 2)
    az_lane_y = frame_center_y + 90
    az_lane_w = 430
    az_lane_h = 780
    az_spacing = 500
    lanes_start_x = vpc_left + 280

    subnets_by_az: Dict[str, List[Dict[str, Any]]] = {az: [] for az in az_names}
    for subnet in subnets:
        az = subnet.get("availability_zone") or "regional"
        subnets_by_az.setdefault(az, []).append(subnet)

    public_subnet_ids: List[str] = []
    private_subnet_ids: List[str] = []

    for az_idx, az in enumerate(az_names):
        az_x = lanes_start_x + az_idx * az_spacing
        miro.create_frame(board_id, f"Availability Zone {az}", az_x, az_lane_y, az_lane_w, az_lane_h)

        az_subnets = subnets_by_az.get(az, [])
        az_public = [s for s in az_subnets if subnet_tier(s, route_tables) == "public"]
        az_private = [s for s in az_subnets if subnet_tier(s, route_tables) == "private"]

        public_band_id: Optional[str] = None
        private_band_id: Optional[str] = None
        if az_public:
            public_band_id = miro.create_shape(
                board_id,
                "<p style='font-size:16px'><strong>Public Subnet Tier</strong></p>",
                az_x,
                az_lane_y - 220,
                350,
                80,
            )
        if az_private:
            private_band_id = miro.create_shape(
                board_id,
                "<p style='font-size:16px'><strong>Private Subnet Tier</strong></p>",
                az_x,
                az_lane_y + 160,
                350,
                80,
            )

        for idx, subnet in enumerate(az_public):
            x = az_x
            y = az_lane_y - 110 + idx * 140
            label = f"Public Subnet\n{subnet['cidr_block']}\n{az}"
            item_id, _ = create_labeled_resource(miro, board_id, label, x, y, "public_subnet", prefer_icons)
            item_ids[subnet["address"]] = item_id
            public_subnet_ids.append(item_id)
            if public_band_id:
                connect(public_band_id, item_id)

        for idx, subnet in enumerate(az_private):
            x = az_x
            y = az_lane_y + 300 + idx * 140
            label = f"Private Subnet\n{subnet['cidr_block']}\n{az}"
            item_id, _ = create_labeled_resource(miro, board_id, label, x, y, "private_subnet", prefer_icons)
            item_ids[subnet["address"]] = item_id
            private_subnet_ids.append(item_id)
            if private_band_id:
                connect(private_band_id, item_id)

    rt_x_start = frame_center_x - 260
    rt_y = frame_center_y + 600
    public_route_table_ids: List[str] = []
    private_route_table_ids: List[str] = []
    for idx, rt in enumerate(route_tables):
        x = rt_x_start + idx * 320
        tier = "Public" if infer_route_table_tier(rt) == "public" else "Private"
        label = f"{tier} Route Table\n{rt.get('name') or rt['route_table_id']}"
        item_id, _ = create_labeled_resource(miro, board_id, label, x, rt_y, "route_table", prefer_icons)
        item_ids[rt["address"]] = item_id
        if tier == "Public":
            public_route_table_ids.append(item_id)
        else:
            private_route_table_ids.append(item_id)

    explicit_assoc_count = 0
    subnet_by_assoc: Dict[Tuple[Optional[str], Optional[str]], Dict[str, Any]] = {}
    for subnet in subnets:
        subnet_by_assoc[(subnet.get("assoc_name"), subnet.get("assoc_index"))] = subnet

    route_table_by_tf_name: Dict[str, Dict[str, Any]] = {}
    for rt in route_tables:
        route_table_by_tf_name[rt.get("tf_name")] = rt

    for assoc in route_table_associations:
        subnet = subnet_by_assoc.get((assoc.get("assoc_name"), assoc.get("assoc_index")))
        route_table = route_table_by_tf_name.get(assoc.get("assoc_name"))
        if not subnet or not route_table:
            continue
        subnet_item_id = item_ids.get(subnet["address"])
        route_table_item_id = item_ids.get(route_table["address"])
        if subnet_item_id and route_table_item_id:
            connect(subnet_item_id, route_table_item_id)
            explicit_assoc_count += 1

    if explicit_assoc_count == 0:
        if public_route_table_ids:
            for subnet_id in public_subnet_ids:
                connect(subnet_id, public_route_table_ids[0])
        if private_route_table_ids:
            for subnet_id in private_subnet_ids:
                connect(subnet_id, private_route_table_ids[0])

    if public_route_table_ids and igw_item_ids:
        connect(public_route_table_ids[0], igw_item_ids[0])

    nat_x = frame_center_x + 450
    nat_start_y = frame_center_y + 320
    for idx, nat in enumerate(nat_gws):
        y = nat_start_y + idx * 170
        label = f"NAT Gateway\n{nat.get('name') or nat['address']}"
        item_id, _ = create_labeled_resource(miro, board_id, label, nat_x, y, "nat", prefer_icons)
        for rt_id in private_route_table_ids:
            connect(rt_id, item_id)
        if igw_item_ids:
            connect(item_id, igw_item_ids[0])

    zone_y = frame_center_y + 725
    for idx, zone in enumerate(zones):
        x = frame_center_x - 260 + (idx * 360)
        label = f"Amazon Route 53\nPrivate Hosted Zone\n{zone.get('name')}"
        create_labeled_resource(miro, board_id, label, x, zone_y, "route53", prefer_icons)


def render_reference_diagram(
    miro: MiroClient,
    board_id: str,
    bundles: List[Dict[str, Any]],
    prefer_icons: bool,
    llm_config: Optional[Dict[str, Optional[str]]] = None,
    center_x: float = 0,
    center_y: float = 0,
) -> None:
    for b_idx, bundle in enumerate(bundles):
        vpc = bundle["vpc"]
        subnets = sorted(bundle["subnets"], key=lambda s: (s.get("availability_zone", ""), s.get("cidr_block", "")))
        route_tables = sorted(bundle["route_tables"], key=lambda rt: rt.get("name", ""))
        igws = bundle["internet_gateways"]
        nat_gws = bundle["nat_gateways"]
        route_table_associations = bundle["route_table_associations"]
        zones = bundle["route53_zones"]

        vpc_resource: PlannedResource = vpc["resource"]
        vpc_region = (
            (vpc_resource.values.get("tags") or {}).get("Region")
            or (vpc_resource.values.get("tags_all") or {}).get("Region")
            or "unknown-region"
        )

        enrichment = enrich_bundle(bundle, **(llm_config or {}))
        scene = build_scene_plan(bundle, enrichment, subnet_tier)
        page_w = scene["page"]["width"]
        page_h = scene["page"]["height"]
        header_h = scene["page"]["header_height"]
        page_gap_y = scene["page"]["gap_y"]
        left_w = scene["rails"]["canvas_width"]
        right_w = scene["rails"]["review_width"]
        review_card_height = scene["rails"]["review_card_height"]
        review_card_gap = scene["rails"]["review_card_gap"]
        page_center_y = center_y + (b_idx * page_gap_y)

        miro.create_frame(board_id, f"AWS Reference Architecture ({vpc_region})", center_x, page_center_y, page_w, page_h)

        header_y = page_center_y - (page_h / 2) + (header_h / 2) + 24
        miro.create_shape(
            board_id,
            "<p style='font-size:38px;color:#FFFFFF;letter-spacing:-0.8px;font-weight:900'><strong>AWS Reference Architecture</strong></p>",
            center_x - 430,
            header_y,
            980,
            92,
            fill_color="#FF9900",
            border_color="#CC7700",
            border_width=3,
        )
        miro.create_shape(
            board_id,
            f"<p style='font-size:13px;color:#0F3B66;font-weight:600;line-height:1.6'><strong>Region:</strong> {html_escape(vpc_region)}<br/><strong>Structure:</strong> Cloud → Account → VPC → AZ</p>",
            center_x + 520,
            header_y,
            820,
            92,
            fill_color="#D0E8FF",
            border_color="#89BFFF",
            border_width=2,
        )

        left_x = center_x - 420
        left_y = page_center_y + 35
        left_h = page_h - 250
        miro.create_frame(board_id, "Architecture", left_x, left_y, left_w, left_h)

        right_x = center_x + 1020
        right_y = left_y
        miro.create_frame(board_id, "Architecture Review", right_x, right_y, right_w, left_h)
        review = scene["review"]
        create_review_card(
            miro,
            board_id,
            right_x,
            right_y - 470,
            640,
            review_card_height,
            f"Terraform Summary ({str(review['mode']).upper()})",
            [review["summary"]],
            "#FF9900",
            "#CC7700",
        )
        create_review_card(
            miro,
            board_id,
            right_x,
            right_y - 470 + review_card_height + review_card_gap,
            640,
            review_card_height,
            "Observed Risks",
            review["risks"][:4],
            "#DC2626",
            "#991B1B",
        )
        create_review_card(
            miro,
            board_id,
            right_x,
            right_y - 470 + ((review_card_height + review_card_gap) * 2),
            640,
            review_card_height,
            "Terraform Observations",
            review["opportunities"][:4],
            "#2563EB",
            "#1D4ED8",
        )

        connectors_seen: Set[Tuple[str, str, str]] = set()

        def connect(a: str, b: str, label: Optional[str] = None) -> None:
            key = (a, b, label or "")
            if key in connectors_seen:
                return
            connectors_seen.add(key)
            try:
                miro.create_connector(board_id, a, b, label)
            except Exception as e:
                log(f"Connector skipped ({a} -> {b}): {e}")

        section_w = left_w - 100
        section_inner_h = left_h - 90
        section_x = left_x
        section_y = left_y

        miro.create_frame(board_id, f"AWS Account ({vpc_region})", section_x, section_y, section_w, section_inner_h)
        vpc_w = section_w - 120
        vpc_h = min(section_inner_h - 130, scene["vpc"]["height"])
        vpc_x = section_x
        vpc_y = section_y + 24
        miro.create_frame(
            board_id,
            f"Amazon VPC {vpc.get('cidr_block') or vpc.get('name') or vpc['id']}",
            vpc_x,
            vpc_y,
            vpc_w,
            vpc_h,
        )
        vpc_name = vpc.get('cidr_block') or vpc.get('name') or vpc['id']
        miro.create_shape(
            board_id,
            f"<p style='font-size:12px;color:#FFFFFF;font-weight:900;letter-spacing:0.5px'><strong>VPC</strong></p>",
            vpc_x - (vpc_w / 2) + 110,
            vpc_y - (vpc_h / 2) + 25,
            220,
            40,
            fill_color="#FF9900",
            border_color="#CC7700",
            border_width=2,
        )

        az_names = sorted({s.get("availability_zone") or "regional" for s in subnets})
        if not az_names:
            az_names = ["regional"]

        az_w = scene["az"]["width"]
        az_h = scene["vpc"]["az_height"]
        az_gap = scene["az"]["gap"]
        az_start_x = vpc_x - ((len(az_names) - 1) * (az_w + az_gap) / 2)
        az_y = vpc_y + (scene["vpc"]["edge_lane_height"] / 2) + 40

        edge_lane_y = vpc_y - (vpc_h / 2) + 110
        routing_lane_y = az_y + (az_h / 2) + 120
        shared_services_y = routing_lane_y + (scene["vpc"]["routing_lane_height"] / 2) + 110

        miro.create_shape(
            board_id,
            "<p style='font-size:12px;color:#0F172A;font-weight:900;letter-spacing:0.4px'><strong>EDGE SERVICES</strong></p>",
            vpc_x,
            edge_lane_y - 38,
            vpc_w - 120,
            42,
            fill_color="#FFF7ED",
            border_color="#FDBA74",
            border_width=2,
        )

        subnets_by_az: Dict[str, List[Dict[str, Any]]] = {az: [] for az in az_names}
        for subnet in subnets:
            az = subnet.get("availability_zone") or "regional"
            subnets_by_az.setdefault(az, []).append(subnet)

        item_ids: Dict[str, str] = {}
        public_subnet_ids: List[str] = []
        private_subnet_ids: List[str] = []

        for az_idx, az in enumerate(az_names):
            az_x = az_start_x + az_idx * (az_w + az_gap)
            miro.create_frame(board_id, f"Availability Zone {az}", az_x, az_y, az_w, az_h)
            # AZ header styling
            miro.create_shape(
                board_id,
                f"<p style='font-size:11px;color:#FFFFFF;font-weight:900;letter-spacing:0.5px'><strong>AZ: {html_escape(az)}</strong></p>",
                az_x,
                az_y - (az_h / 2) + 22,
                az_w - 60,
                35,
                fill_color="#495569",
                border_color="#2D3545",
                border_width=2,
            )

            az_subnets = subnets_by_az.get(az, [])
            az_public = [s for s in az_subnets if subnet_tier(s, route_tables) == "public"]
            az_private = [s for s in az_subnets if subnet_tier(s, route_tables) == "private"]

            if az_public:
                public_band = miro.create_shape(
                    board_id,
                    "<p style='font-size:13px;color:#FFFFFF;font-weight:900;letter-spacing:0.5px'><strong>PUBLIC TIER</strong></p>",
                    az_x,
                    az_y - (az_h / 2) + 90,
                    az_w - 40,
                    60,
                    fill_color="#10B981",
                    border_color="#0B915C",
                    border_width=2,
                )
                for s_idx, subnet in enumerate(az_public):
                    label = f"Public Subnet\n{subnet['cidr_block']}"
                    node_id, _ = create_labeled_resource(
                        miro,
                        board_id,
                        label,
                        az_x,
                        az_y - (az_h / 2) + 215 + s_idx * 190,
                        "public_subnet",
                        prefer_icons,
                    )
                    item_ids[subnet["address"]] = node_id
                    public_subnet_ids.append(node_id)
                    connect(public_band, node_id)

            if az_private:
                private_band = miro.create_shape(
                    board_id,
                    "<p style='font-size:13px;color:#FFFFFF;font-weight:900;letter-spacing:0.5px'><strong>PRIVATE TIER</strong></p>",
                    az_x,
                    az_y + 15,
                    az_w - 40,
                    60,
                    fill_color="#1F51B6",
                    border_color="#163A91",
                    border_width=2,
                )
                for s_idx, subnet in enumerate(az_private):
                    label = f"Private Subnet\n{subnet['cidr_block']}"
                    node_id, _ = create_labeled_resource(
                        miro,
                        board_id,
                        label,
                        az_x,
                        az_y + 220 + s_idx * 190,
                        "private_subnet",
                        prefer_icons,
                    )
                    item_ids[subnet["address"]] = node_id
                    private_subnet_ids.append(node_id)
                    connect(private_band, node_id)

        rt_ids_by_name: Dict[str, str] = {}
        rt_x = vpc_x
        rt_y = routing_lane_y
        miro.create_shape(
            board_id,
            "<p style='font-size:12px;color:#FFFFFF;font-weight:900;letter-spacing:0.5px'><strong>ROUTE TABLES</strong></p>",
            rt_x,
            rt_y - 80,
            620,
            44,
            fill_color="#7C3AED",
            border_color="#5A1FB5",
            border_width=2,
        )
        rt_spacing = 340
        rt_start_x = vpc_x - ((max(0, len(route_tables) - 1)) * rt_spacing / 2)
        for index, rt in enumerate(route_tables):
            tier = "Public" if infer_route_table_tier(rt) == "public" else "Private"
            label = f"{tier} Route Table\n{rt.get('name') or rt['route_table_id']}"
            rt_id, _ = create_labeled_resource(miro, board_id, label, rt_start_x + index * rt_spacing, rt_y, "route_table", prefer_icons)
            rt_ids_by_name[rt.get("tf_name")] = rt_id

        subnet_by_assoc: Dict[Tuple[Optional[str], Optional[str]], Dict[str, Any]] = {}
        for subnet in subnets:
            subnet_by_assoc[(subnet.get("assoc_name"), subnet.get("assoc_index"))] = subnet

        explicit_assoc_count = 0
        for assoc in route_table_associations:
            subnet = subnet_by_assoc.get((assoc.get("assoc_name"), assoc.get("assoc_index")))
            if not subnet:
                continue
            subnet_item_id = item_ids.get(subnet["address"])
            rt_item_id = rt_ids_by_name.get(assoc.get("assoc_name"))
            if subnet_item_id and rt_item_id:
                connect(subnet_item_id, rt_item_id)
                explicit_assoc_count += 1

        if explicit_assoc_count == 0 and route_tables:
            default_rt_name = route_tables[0].get("tf_name")
            default_rt_id = rt_ids_by_name.get(default_rt_name)
            if default_rt_id:
                for subnet_id in public_subnet_ids + private_subnet_ids:
                    connect(subnet_id, default_rt_id)

        if igws:
            igw_id, _ = create_labeled_resource(
                miro,
                board_id,
                "Internet Gateway",
                vpc_x - 360,
                edge_lane_y + 30,
                "igw",
                prefer_icons,
            )
            if rt_ids_by_name:
                first_rt_id = list(rt_ids_by_name.values())[0]
                connect(first_rt_id, igw_id)

        if nat_gws:
            nat_start_x = vpc_x + 60
            for index, nat in enumerate(nat_gws):
                nat_id, _ = create_labeled_resource(
                    miro,
                    board_id,
                    f"NAT Gateway\n{nat.get('name') or nat['address']}",
                    nat_start_x + (index * 220),
                    edge_lane_y + 30,
                    "nat",
                    prefer_icons,
                )
                for rt_id in rt_ids_by_name.values():
                    connect(rt_id, nat_id)

        if zones:
            miro.create_shape(
                board_id,
                "<p style='font-size:12px;color:#0F172A;font-weight:900;letter-spacing:0.4px'><strong>SHARED SERVICES</strong></p>",
                vpc_x,
                shared_services_y - 70,
                620,
                44,
                fill_color="#EFF6FF",
                border_color="#93C5FD",
                border_width=2,
            )
            zone_start_x = vpc_x - ((max(0, len(zones) - 1)) * 280 / 2)
            for index, zone in enumerate(zones):
                create_labeled_resource(
                    miro,
                    board_id,
                    f"Amazon Route 53\nPrivate Hosted Zone\n{zone.get('name')}",
                    zone_start_x + (index * 280),
                    shared_services_y,
                    "route53",
                    prefer_icons,
                )


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
    model = build_aws_model(plan)
    bundles = build_vpc_render_data(model)

    total_vpcs = len(model["vpcs"])
    total_subnets = len(model["subnets"])
    total_route_tables = len(model["route_tables"])
    total_zones = len(model["route53_zones"])

    log(f"VPCs: {total_vpcs}")
    log(f"Subnets: {total_subnets}")
    log(f"Route tables: {total_route_tables}")
    log(f"Route53 zones: {total_zones}")

    if args.dump_model:
        save_model(args.dump_model, {"model": model, "bundles": bundles})
        log(f"Wrote model to {args.dump_model}")

    llm_config = resolve_llm_config(args)
    llm_enabled = bool(llm_config.get("llm_endpoint") and llm_config.get("llm_model") and llm_config.get("llm_api_key"))
    log(f"Callout enrichment: {'LLM' if llm_enabled else 'heuristic'}")

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