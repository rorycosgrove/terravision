from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from topology import infer_route_table_tier, infer_subnet_tier


@dataclass
class FrameElement:
    logical_id: str
    title: str
    x: float
    y: float
    width: int
    height: int


@dataclass
class ShapeElement:
    logical_id: str
    html: str
    x: float
    y: float
    width: int
    height: int
    fill_color: Optional[str] = None
    border_color: Optional[str] = None
    border_width: int = 2


@dataclass
class ResourceElement:
    logical_id: str
    label: str
    x: float
    y: float
    icon_key: Optional[str] = None


@dataclass
class ConnectorElement:
    start_id: str
    end_id: str
    caption: Optional[str] = None


@dataclass
class LayoutPage:
    logical_id: str
    title: str
    width: int
    height: int
    center_x: float
    center_y: float
    frames: List[FrameElement] = field(default_factory=list)
    shapes: List[ShapeElement] = field(default_factory=list)
    resources: List[ResourceElement] = field(default_factory=list)
    connectors: List[ConnectorElement] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class LayoutIR:
    pages: List[LayoutPage] = field(default_factory=list)


def _ellipsize(text: str, max_len: int = 30) -> str:
    normalized = (text or "").strip()
    if len(normalized) <= max_len:
        return normalized
    return f"{normalized[: max_len - 3]}..."


def _review_lines(review: Dict[str, Any], key: str, fallback: str, limit: int = 4) -> List[str]:
    lines = [str(line) for line in (review.get(key) or []) if line]
    if lines:
        clipped: List[str] = []
        for line in lines[:limit]:
            compact = line if len(line) <= 120 else f"{line[:117]}..."
            clipped.append(compact)
        return clipped
    return [fallback if len(fallback) <= 120 else f"{fallback[:117]}..."]


def _review_shape(logical_id: str, title: str, lines: List[str], x: float, y: float, accent_fill: str, accent_border: str) -> List[ShapeElement]:
    safe_lines = "".join(f"<p style='font-size:12px;color:#1F2937;line-height:1.4;margin:0 0 6px 0'>- {line}</p>" for line in lines)
    width = 500
    height = 138
    return [
        ShapeElement(
            logical_id=logical_id,
            html=f"<p style='font-size:14px;color:#0F172A;font-weight:900;letter-spacing:0.2px;margin:0 0 8px 0'><strong>{title}</strong></p>{safe_lines}",
            x=x,
            y=y,
            width=width,
            height=height,
            fill_color="#FFFFFF",
            border_color="#CBD5E1",
            border_width=2,
        ),
        ShapeElement(
            logical_id=f"{logical_id}:accent",
            html="",
            x=x - (width / 2) + 10,
            y=y,
            width=20,
            height=height - 18,
            fill_color=accent_fill,
            border_color=accent_border,
            border_width=2,
        ),
    ]


def build_layout_ir(
    bundles: List[Dict[str, Any]],
    enrichments: List[Dict[str, Any]],
    center_x: float = 0,
    center_y: float = 0,
) -> LayoutIR:
    pages: List[LayoutPage] = []

    page_width = 2080
    page_height = 1220
    page_gap_y = 1360
    left_width = 1440
    right_width = 540
    header_height = 86

    for index, bundle in enumerate(bundles):
        review = enrichments[index] if index < len(enrichments) else {"mode": "heuristic"}
        vpc = bundle["vpc"]
        subnets = sorted(bundle["subnets"], key=lambda item: (item.get("availability_zone", ""), item.get("cidr_block", "")))
        route_tables = sorted(bundle["route_tables"], key=lambda item: item.get("name", ""))
        zones = bundle["route53_zones"]
        igws = bundle["internet_gateways"]
        nat_gateways = bundle["nat_gateways"]
        associations = bundle["route_table_associations"]
        unresolved_associations = bundle.get("unresolved_associations") or []
        association_count_by_route_table: Dict[str, int] = {}
        for association in associations:
            route_table_address = association.get("route_table_address")
            if not route_table_address:
                continue
            association_count_by_route_table[route_table_address] = association_count_by_route_table.get(route_table_address, 0) + 1

        vpc_resource = vpc["resource"]
        region = (
            (vpc_resource.values.get("tags") or {}).get("Region")
            or (vpc_resource.values.get("tags_all") or {}).get("Region")
            or "unknown-region"
        )
        page_center_y = center_y + (index * page_gap_y)
        page = LayoutPage(
            logical_id=f"page:{index}",
            title=f"AWS Reference Architecture ({region})",
            width=page_width,
            height=page_height,
            center_x=center_x,
            center_y=page_center_y,
        )

        left_x = center_x - 230
        left_y = page_center_y + 6
        left_height = page_height - 104
        right_x = center_x + 590
        right_y = left_y
        section_width = left_width - 48
        section_height = left_height - 30
        vpc_width = section_width - 120

        az_names = sorted({subnet.get("availability_zone") or "regional" for subnet in subnets}) or ["regional"]
        max_subnets_in_az = 1
        for az_name in az_names:
            az_subnets = [subnet for subnet in subnets if (subnet.get("availability_zone") or "regional") == az_name]
            max_subnets_in_az = max(max_subnets_in_az, len(az_subnets))

        has_edge_services = bool(igws or nat_gateways)
        az_height = max(340, min(500, 300 + max_subnets_in_az * 92))
        core_content_height = 74 + (104 if has_edge_services else 24) + az_height + 116 + (112 if zones else 34)
        vpc_height = min(section_height - 16, max(640, core_content_height))

        page.frames.extend(
            [
                FrameElement(page.logical_id, page.title, center_x, page_center_y, page_width, page_height),
                FrameElement(f"architecture:{index}", "Architecture", left_x, left_y, left_width, left_height),
                FrameElement(f"review:{index}", "Architecture Review", right_x, right_y, right_width, left_height),
                FrameElement(f"account:{index}", f"AWS Account ({region})", left_x, left_y, section_width, section_height),
                FrameElement(
                    f"vpc:{vpc['address']}",
                    f"Amazon VPC {vpc.get('cidr_block') or vpc.get('name') or vpc['id']}",
                    left_x,
                    left_y + 24,
                    vpc_width,
                    vpc_height,
                ),
            ]
        )

        header_y = page_center_y - (page_height / 2) + (header_height / 2) + 10
        page.shapes.extend(
            [
                ShapeElement(
                    logical_id=f"header:title:{index}",
                    html="<p style='font-size:24px;color:#FFFFFF;letter-spacing:-0.2px;font-weight:900'><strong>AWS Reference Architecture</strong></p>",
                    x=center_x - 248,
                    y=header_y,
                    width=620,
                    height=58,
                    fill_color="#FF9900",
                    border_color="#CC7700",
                    border_width=3,
                ),
                ShapeElement(
                    logical_id=f"header:meta:{index}",
                    html=(
                        f"<p style='font-size:12px;color:#0F3B66;font-weight:600;line-height:1.45'>"
                        f"<strong>Region:</strong> {region}<br/>"
                        f"<strong>Resources:</strong> AZ {len(az_names)} | Subnets {len(subnets)} | RTs {len(route_tables)} | NAT {len(nat_gateways)} | DNS {len(zones)}<br/>"
                        f"<strong>Associations:</strong> {len(associations)} resolved, {len(unresolved_associations)} unresolved"
                        f"</p>"
                    ),
                    x=center_x + 286,
                    y=header_y,
                    width=470,
                    height=74,
                    fill_color="#D0E8FF",
                    border_color="#89BFFF",
                    border_width=2,
                ),
                ShapeElement(
                    logical_id=f"vpc:badge:{index}",
                    html="<p style='font-size:12px;color:#FFFFFF;font-weight:900;letter-spacing:0.5px'><strong>VPC</strong></p>",
                    x=left_x - (vpc_width / 2) + 110,
                    y=(left_y + 24) - (vpc_height / 2) + 25,
                    width=220,
                    height=40,
                    fill_color="#FF9900",
                    border_color="#CC7700",
                    border_width=2,
                ),
            ]
        )

        review_top = right_y - (left_height / 2) + 94
        review_y = review_top + 68
        page.shapes.extend(
            _review_shape(
                f"review:summary:{index}",
                f"Terraform Summary ({str(review.get('mode') or 'heuristic').upper()})",
                [str(review.get("summary") or "Architecture summary unavailable.")],
                right_x,
                review_y,
                "#FF9900",
                "#CC7700",
            )
        )
        page.shapes.extend(
            _review_shape(
                f"review:risks:{index}",
                "Observed Risks",
                _review_lines(review, "risks", "No material risks identified from the Terraform plan data.", limit=2),
                right_x,
                review_y + 150,
                "#DC2626",
                "#991B1B",
            )
        )
        observations_plus_warnings = _review_lines(review, "opportunities", "No additional topology observations captured.", limit=2)
        warning_lines = [
            f"{len(unresolved_associations)} route-table associations could not be resolved from Terraform config references."
            if unresolved_associations
            else "All route-table associations were resolved from Terraform config references."
        ]
        if unresolved_associations:
            observations_plus_warnings.append("Unresolved route-table associations are shown in diagnostics output.")
        page.shapes.extend(
            _review_shape(
                f"review:warnings:{index}",
                "Resolution Warnings",
                warning_lines,
                right_x,
                review_y + 300,
                "#F59E0B",
                "#B45309",
            )
        )
        observations_y = review_y + 450
        page.shapes.extend(
            _review_shape(
                f"review:observations:{index}",
                "Terraform Observations",
                observations_plus_warnings[:3],
                right_x,
                observations_y,
                "#2563EB",
                "#1D4ED8",
            )
        )
        page.warnings.extend(unresolved_associations)

        vpc_x = left_x
        vpc_y = left_y + 24
        vpc_top = vpc_y - (vpc_height / 2)
        lane_width = vpc_width - 84
        edge_lane_label_y = vpc_top + 24
        edge_node_y = edge_lane_label_y + 26
        az_width = max(280, min(400, int((left_width - 200 - (18 * max(0, len(az_names) - 1))) / len(az_names))))
        az_gap = 18
        az_start_x = vpc_x - ((len(az_names) - 1) * (az_width + az_gap) / 2)
        az_top = vpc_top + (170 if has_edge_services else 78)
        az_y = az_top + (az_height / 2)
        routing_lane_label_y = az_top + az_height + 32
        route_table_node_y = routing_lane_label_y + 56
        shared_services_label_y = route_table_node_y + 102
        shared_services_y = shared_services_label_y + 40

        if has_edge_services:
            page.shapes.append(
                ShapeElement(
                    logical_id=f"lane:edge:{index}",
                    html="<p style='font-size:12px;color:#0F172A;font-weight:900;letter-spacing:0.3px'><strong>EDGE SERVICES</strong></p>",
                    x=vpc_x,
                    y=edge_lane_label_y,
                    width=lane_width,
                    height=32,
                    fill_color="#FFF7ED",
                    border_color="#FDBA74",
                    border_width=2,
                )
            )

        page.shapes.append(
            ShapeElement(
                logical_id=f"chip:vpc:{index}",
                html="<p style='font-size:12px;color:#FFFFFF;font-weight:900;letter-spacing:0.4px'><strong>VPC</strong></p>",
                x=vpc_x - (vpc_width / 2) + 94,
                y=vpc_top + 16,
                width=170,
                height=34,
                fill_color="#FF9900",
                border_color="#CC7700",
                border_width=2,
            )
        )

        subnets_by_az: Dict[str, List[Dict[str, Any]]] = {az_name: [] for az_name in az_names}
        for subnet in subnets:
            subnets_by_az.setdefault(subnet.get("availability_zone") or "regional", []).append(subnet)

        for az_index, az_name in enumerate(az_names):
            az_x = az_start_x + az_index * (az_width + az_gap)
            page.frames.append(
                FrameElement(
                    logical_id=f"az:{index}:{az_name}",
                    title=f"Availability Zone {az_name}",
                    x=az_x,
                    y=az_y,
                    width=az_width,
                    height=az_height,
                )
            )
            page.shapes.append(
                ShapeElement(
                    logical_id=f"az:label:{index}:{az_name}",
                    html=f"<p style='font-size:11px;color:#FFFFFF;font-weight:900;letter-spacing:0.5px'><strong>AZ: {az_name}</strong></p>",
                    x=az_x,
                    y=az_y - (az_height / 2) + 14,
                    width=az_width - 40,
                    height=26,
                    fill_color="#495569",
                    border_color="#2D3545",
                    border_width=2,
                )
            )

            az_subnets = subnets_by_az.get(az_name, [])
            public_subnets = [subnet for subnet in az_subnets if infer_subnet_tier(subnet, route_tables) == "public"]
            private_subnets = [subnet for subnet in az_subnets if infer_subnet_tier(subnet, route_tables) == "private"]

            if public_subnets:
                page.shapes.append(
                    ShapeElement(
                        logical_id=f"band:public:{index}:{az_name}",
                        html="<p style='font-size:12px;color:#FFFFFF;font-weight:900;letter-spacing:0.4px'><strong>PUBLIC TIER</strong></p>",
                        x=az_x,
                        y=az_y - (az_height / 2) + 42,
                        width=az_width - 40,
                        height=40,
                        fill_color="#10B981",
                        border_color="#0B915C",
                        border_width=2,
                    )
                )
                for subnet_index, subnet in enumerate(public_subnets):
                    subnet_name = _ellipsize(subnet.get("name") or subnet.get("tf_name") or subnet["subnet_id"], max_len=28)
                    page.resources.append(
                        ResourceElement(
                            logical_id=f"resource:{subnet['address']}",
                            label=f"Public Subnet\n{subnet_name}\n{subnet['cidr_block']}",
                            x=az_x,
                            y=az_y - (az_height / 2) + 98 + subnet_index * 104,
                            icon_key="public_subnet",
                        )
                    )

            if private_subnets:
                page.shapes.append(
                    ShapeElement(
                        logical_id=f"band:private:{index}:{az_name}",
                        html="<p style='font-size:12px;color:#FFFFFF;font-weight:900;letter-spacing:0.4px'><strong>PRIVATE TIER</strong></p>",
                        x=az_x,
                        y=az_y,
                        width=az_width - 40,
                        height=40,
                        fill_color="#1F51B6",
                        border_color="#163A91",
                        border_width=2,
                    )
                )
                for subnet_index, subnet in enumerate(private_subnets):
                    subnet_name = _ellipsize(subnet.get("name") or subnet.get("tf_name") or subnet["subnet_id"], max_len=28)
                    page.resources.append(
                        ResourceElement(
                            logical_id=f"resource:{subnet['address']}",
                            label=f"Private Subnet\n{subnet_name}\n{subnet['cidr_block']}",
                            x=az_x,
                            y=az_y + 92 + subnet_index * 104,
                            icon_key="private_subnet",
                        )
                    )

        page.shapes.append(
            ShapeElement(
                logical_id=f"lane:routing:{index}",
                html="<p style='font-size:12px;color:#FFFFFF;font-weight:900;letter-spacing:0.4px'><strong>ROUTE TABLES</strong></p>",
                x=vpc_x,
                y=routing_lane_label_y,
                width=lane_width,
                height=30,
                fill_color="#7C3AED",
                border_color="#5A1FB5",
                border_width=2,
            )
        )
        route_table_spacing = max(176, min(240, int((lane_width - 120) / max(1, len(route_tables)))))
        route_table_start_x = vpc_x - ((max(0, len(route_tables) - 1)) * route_table_spacing / 2)
        private_route_table_ids: List[str] = []
        private_route_table_positions: List[float] = []
        public_route_table_positions: List[float] = []
        for route_index, route_table in enumerate(route_tables):
            route_logical_id = f"resource:{route_table['address']}"
            route_name = _ellipsize(route_table.get("name") or route_table["route_table_id"], max_len=26)
            assoc_count = association_count_by_route_table.get(route_table["address"], 0)
            route_x = route_table_start_x + route_index * route_table_spacing
            if infer_route_table_tier(route_table) != "public":
                private_route_table_ids.append(route_logical_id)
                private_route_table_positions.append(route_x)
            else:
                public_route_table_positions.append(route_x)
            page.resources.append(
                ResourceElement(
                    logical_id=route_logical_id,
                    label=(
                        f"{'Public' if infer_route_table_tier(route_table) == 'public' else 'Private'} Route Table"
                        f"\n{route_name}"
                        f"\n{assoc_count} subnet associations"
                    ),
                    x=route_x,
                    y=route_table_node_y,
                    icon_key=None,
                )
            )

        for association in associations:
            subnet_address = association.get("subnet_address")
            route_table_address = association.get("route_table_address")
            if subnet_address and route_table_address:
                page.connectors.append(
                    ConnectorElement(
                        start_id=f"resource:{subnet_address}",
                        end_id=f"resource:{route_table_address}",
                    )
                )

        for gateway in igws:
            gateway_id = f"resource:{gateway['address']}"
            igw_x = public_route_table_positions[0] if public_route_table_positions else (vpc_x - (lane_width / 2) + 120)
            page.resources.append(
                ResourceElement(
                    logical_id=gateway_id,
                    label=f"Internet Gateway\n{gateway.get('name') or gateway['address']}",
                    x=igw_x,
                    y=edge_node_y,
                    icon_key="igw",
                )
            )
            public_route_tables = [route_table for route_table in route_tables if infer_route_table_tier(route_table) == "public"]
            if public_route_tables:
                page.connectors.append(
                    ConnectorElement(
                        start_id=f"resource:{public_route_tables[0]['address']}",
                        end_id=gateway_id,
                    )
                )

        nat_start_x = vpc_x - ((max(0, len(nat_gateways) - 1)) * 184 / 2)
        nat_positions: List[float] = []
        for nat_index, gateway in enumerate(nat_gateways):
            nat_id = f"resource:{gateway['address']}"
            nat_x = nat_start_x + (nat_index * 184)
            nat_positions.append(nat_x)
            page.resources.append(
                ResourceElement(
                    logical_id=nat_id,
                    label=f"NAT Gateway\n{gateway.get('name') or gateway['address']}",
                    x=nat_x,
                    y=edge_node_y,
                    icon_key="nat",
                )
            )

        if nat_positions and private_route_table_ids:
            for table_id, table_x in zip(private_route_table_ids, private_route_table_positions):
                nearest_nat_index = min(range(len(nat_positions)), key=lambda idx: abs(nat_positions[idx] - table_x))
                page.connectors.append(
                    ConnectorElement(
                        start_id=table_id,
                        end_id=f"resource:{nat_gateways[nearest_nat_index]['address']}",
                    )
                )

        if zones:
            page.shapes.append(
                ShapeElement(
                    logical_id=f"lane:shared:{index}",
                    html="<p style='font-size:12px;color:#0F172A;font-weight:900;letter-spacing:0.4px'><strong>SHARED SERVICES</strong></p>",
                    x=vpc_x,
                    y=shared_services_label_y,
                    width=lane_width,
                    height=30,
                    fill_color="#EFF6FF",
                    border_color="#93C5FD",
                    border_width=2,
                )
            )
            zone_start_x = vpc_x - ((max(0, len(zones) - 1)) * 172 / 2)
            for zone_index, zone in enumerate(zones):
                zone_name = _ellipsize(zone.get("name") or "Private Zone", max_len=24)
                page.resources.append(
                    ResourceElement(
                        logical_id=f"resource:{zone['address']}",
                        label=f"Route 53 Private Zone\n{zone_name}\nlinked to VPC",
                        x=zone_start_x + (zone_index * 172),
                        y=shared_services_y,
                        icon_key=None,
                    )
                )

        pages.append(page)

    return LayoutIR(pages=pages)


def serialize_layout_ir(layout_ir: LayoutIR) -> Dict[str, Any]:
    return asdict(layout_ir)