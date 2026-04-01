from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any, Dict, List, Optional, Tuple


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
class ConfigResourceSpec:
    full_address: str
    expressions: Dict[str, Any] = field(default_factory=dict)
    for_each_expression: Dict[str, Any] = field(default_factory=dict)


@dataclass
class VpcRecord:
    address: str
    vpc_id: str
    cidr_block: str
    name: str
    resource: PlannedResource


@dataclass
class SubnetRecord:
    address: str
    subnet_id: str
    vpc_id: Optional[str]
    cidr_block: str
    availability_zone: str
    name: str
    tf_name: str
    index_key: Optional[str]
    map_public_ip_on_launch: bool
    resource: PlannedResource
    associated_route_table_address: Optional[str] = None


@dataclass
class RouteTableRecord:
    address: str
    route_table_id: str
    vpc_id: Optional[str]
    name: str
    tf_name: str
    resource: PlannedResource
    routes: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class RouteTableAssociationRecord:
    address: str
    tf_name: str
    index_key: Optional[str]
    subnet_address: Optional[str]
    route_table_address: Optional[str]
    resolution_status: str
    unresolved_reason: Optional[str]
    resource: PlannedResource


@dataclass
class Route53ZoneRecord:
    address: str
    name: str
    vpcs: List[Dict[str, Any]]
    resource: PlannedResource


@dataclass
class InternetGatewayRecord:
    address: str
    vpc_id: Optional[str]
    name: str
    resource: PlannedResource


@dataclass
class NatGatewayRecord:
    address: str
    subnet_id: Optional[str]
    name: str
    resource: PlannedResource


@dataclass
class TopologyBundle:
    vpc: VpcRecord
    subnets: List[SubnetRecord] = field(default_factory=list)
    route_tables: List[RouteTableRecord] = field(default_factory=list)
    route_table_associations: List[RouteTableAssociationRecord] = field(default_factory=list)
    route53_zones: List[Route53ZoneRecord] = field(default_factory=list)
    internet_gateways: List[InternetGatewayRecord] = field(default_factory=list)
    nat_gateways: List[NatGatewayRecord] = field(default_factory=list)
    unresolved_associations: List[str] = field(default_factory=list)


@dataclass
class TopologyModel:
    vpcs: Dict[str, VpcRecord] = field(default_factory=dict)
    subnets: Dict[str, SubnetRecord] = field(default_factory=dict)
    route_tables: Dict[str, RouteTableRecord] = field(default_factory=dict)
    route_table_associations: List[RouteTableAssociationRecord] = field(default_factory=list)
    route53_zones: List[Route53ZoneRecord] = field(default_factory=list)
    internet_gateways: List[InternetGatewayRecord] = field(default_factory=list)
    nat_gateways: List[NatGatewayRecord] = field(default_factory=list)
    bundles: List[TopologyBundle] = field(default_factory=list)


@dataclass
class TierAssessment:
    tier: str
    confidence: str
    source: str


def parse_association_suffix(address: str) -> Tuple[Optional[str], Optional[str]]:
    match = re.search(r"\.([^.\[]+)\[\"([^\"]+)\"\]$", address)
    if not match:
        return None, None
    return match.group(1), match.group(2)


def _strip_instance_suffix(address: str) -> str:
    return re.sub(r"\[[^\]]+\]$", "", address)


def _module_prefix(address: str) -> str:
    base_address = _strip_instance_suffix(address)
    parts = base_address.split(".")
    if len(parts) <= 2:
        return ""
    return ".".join(parts[:-2])


def _normalize_resource_reference(reference: str) -> Optional[str]:
    if reference.startswith("each.value"):
        return None
    parts = reference.split(".")
    normalized: List[str] = []
    index = 0
    while index + 1 < len(parts) and parts[index] == "module":
        normalized.extend(parts[index : index + 2])
        index += 2
    if index + 1 >= len(parts):
        return None
    normalized.extend(parts[index : index + 2])
    return ".".join(normalized)


def _qualify_reference(module_prefix: str, reference: str) -> str:
    if reference.startswith("module.") or not module_prefix:
        return reference
    return f"{module_prefix}.{reference}"


def _expression_references(expression: Dict[str, Any]) -> List[str]:
    if not expression:
        return []
    references = expression.get("references") or []
    return [reference for reference in references if isinstance(reference, str)]


def _candidate_instance_addresses(base_address: str, index_key: Optional[str]) -> List[str]:
    candidates = [base_address]
    if index_key is not None:
        candidates.insert(0, f'{base_address}["{index_key}"]')
        if index_key.isdigit():
            candidates.append(f"{base_address}[{index_key}]")
    return candidates


def _resolve_resource_address(
    base_address: str,
    index_key: Optional[str],
    resource_lookup: Dict[str, PlannedResource],
) -> Optional[str]:
    for candidate in _candidate_instance_addresses(base_address, index_key):
        if candidate in resource_lookup:
            return candidate

    prefix = f"{base_address}["
    indexed_matches = [address for address in resource_lookup if address.startswith(prefix)]
    if len(indexed_matches) == 1:
        return indexed_matches[0]
    return None


def collect_planned_resources(module: Dict[str, Any], out: Dict[str, PlannedResource], module_path: str = "root") -> None:
    for resource in module.get("resources", []) or []:
        address = resource["address"]
        out[address] = PlannedResource(
            address=address,
            rtype=resource["type"],
            name=resource.get("name", address),
            provider_name=resource.get("provider_name", ""),
            values=resource.get("values") or {},
            depends_on=resource.get("depends_on") or [],
            module_path=module_path,
        )

    for child in module.get("child_modules", []) or []:
        child_path = child.get("address", module_path)
        collect_planned_resources(child, out, child_path)


def collect_config_resource_specs(module: Dict[str, Any], out: Dict[str, ConfigResourceSpec], module_prefix: str = "") -> None:
    for resource in module.get("resources", []) or []:
        local_address = resource.get("address")
        if not local_address:
            continue
        full_address = f"{module_prefix}.{local_address}" if module_prefix else local_address
        out[full_address] = ConfigResourceSpec(
            full_address=full_address,
            expressions=resource.get("expressions") or {},
            for_each_expression=resource.get("for_each_expression") or {},
        )

    for module_name, module_call in (module.get("module_calls") or {}).items():
        child_module = module_call.get("module") or {}
        child_prefix = f"{module_prefix}.module.{module_name}" if module_prefix else f"module.{module_name}"
        collect_config_resource_specs(child_module, out, child_prefix)


def _resource_tags(resource_entry: Dict[str, Any]) -> Dict[str, Any]:
    resource_obj = resource_entry.get("resource")
    if not resource_obj:
        return {}
    values = resource_obj.values or {}
    return (values.get("tags") or {}) or (values.get("tags_all") or {})


def route_table_is_public(route_table: Dict[str, Any]) -> bool:
    for route in route_table.get("routes", []):
        gateway_id = route.get("gateway_id")
        cidr_block = route.get("cidr_block")
        if gateway_id and cidr_block == "0.0.0.0/0":
            return True
    return False


def assess_route_table_tier(route_table: Dict[str, Any]) -> TierAssessment:
    if route_table_is_public(route_table):
        return TierAssessment(tier="public", confidence="high", source="default_route_via_gateway")

    tags = _resource_tags(route_table)
    tier_tag = str(tags.get("Tier", "")).lower()
    if "public" in tier_tag:
        return TierAssessment(tier="public", confidence="medium", source="tier_tag")
    if "private" in tier_tag:
        return TierAssessment(tier="private", confidence="medium", source="tier_tag")

    name = str(route_table.get("name") or "").lower()
    if "public" in name:
        return TierAssessment(tier="public", confidence="low", source="name_heuristic")
    if "private" in name:
        return TierAssessment(tier="private", confidence="low", source="name_heuristic")

    return TierAssessment(tier="private", confidence="low", source="default_private")


def infer_route_table_tier(route_table: Dict[str, Any]) -> str:
    return assess_route_table_tier(route_table).tier


def assess_subnet_tier(subnet: Dict[str, Any], route_tables: List[Dict[str, Any]]) -> TierAssessment:
    if subnet.get("map_public_ip_on_launch"):
        return TierAssessment(tier="public", confidence="high", source="map_public_ip_on_launch")

    tags = _resource_tags(subnet)
    tier_tag = str(tags.get("Tier", "")).lower()
    if "public" in tier_tag:
        return TierAssessment(tier="public", confidence="medium", source="tier_tag")
    if "private" in tier_tag:
        return TierAssessment(tier="private", confidence="medium", source="tier_tag")

    associated_route_table_address = subnet.get("associated_route_table_address")
    if associated_route_table_address:
        for route_table in route_tables:
            if route_table.get("address") == associated_route_table_address:
                route_table_assessment = assess_route_table_tier(route_table)
                return TierAssessment(
                    tier=route_table_assessment.tier,
                    confidence="medium" if route_table_assessment.confidence == "low" else "high",
                    source=f"associated_route_table:{route_table_assessment.source}",
                )

    subnet_name = str(subnet.get("name") or "").lower()
    if "public" in subnet_name:
        return TierAssessment(tier="public", confidence="low", source="name_heuristic")
    if "private" in subnet_name:
        return TierAssessment(tier="private", confidence="low", source="name_heuristic")

    return TierAssessment(tier="private", confidence="low", source="default_private")


def infer_subnet_tier(subnet: Dict[str, Any], route_tables: List[Dict[str, Any]]) -> str:
    return assess_subnet_tier(subnet, route_tables).tier


def _resolve_association_targets(
    association: PlannedResource,
    config_specs: Dict[str, ConfigResourceSpec],
    resource_lookup: Dict[str, PlannedResource],
) -> Tuple[Optional[str], Optional[str], str, Optional[str]]:
    base_address = _strip_instance_suffix(association.address)
    config_spec = config_specs.get(base_address)
    if not config_spec:
        return None, None, "unresolved", "No Terraform configuration resource found for association"

    module_prefix = _module_prefix(association.address)
    _, index_key = parse_association_suffix(association.address)

    route_reference: Optional[str] = None
    for reference in _expression_references(config_spec.expressions.get("route_table_id") or {}):
        normalized = _normalize_resource_reference(reference)
        if normalized:
            route_reference = _qualify_reference(module_prefix, normalized)
            break

    route_table_address = None
    if route_reference:
        route_table_address = _resolve_resource_address(route_reference, index_key, resource_lookup)

    subnet_reference: Optional[str] = None
    subnet_expression_references = _expression_references(config_spec.expressions.get("subnet_id") or {})
    for reference in subnet_expression_references:
        normalized = _normalize_resource_reference(reference)
        if normalized:
            subnet_reference = _qualify_reference(module_prefix, normalized)
            break

    if not subnet_reference and any(reference.startswith("each.value") for reference in subnet_expression_references):
        for reference in _expression_references(config_spec.for_each_expression):
            normalized = _normalize_resource_reference(reference)
            if normalized:
                subnet_reference = _qualify_reference(module_prefix, normalized)
                break

    subnet_address = None
    if subnet_reference:
        subnet_address = _resolve_resource_address(subnet_reference, index_key, resource_lookup)

    if route_table_address and subnet_address:
        return subnet_address, route_table_address, "resolved", None

    reasons: List[str] = []
    if not route_table_address:
        reasons.append("route table target could not be resolved from configuration references")
    if not subnet_address:
        reasons.append("subnet target could not be resolved from configuration references")
    return subnet_address, route_table_address, "unresolved", "; ".join(reasons)


def build_topology(plan: Dict[str, Any]) -> TopologyModel:
    root_module = (plan.get("planned_values") or {}).get("root_module")
    if not root_module:
        raise ValueError("Invalid tfplan.json: missing planned_values.root_module")

    resources: Dict[str, PlannedResource] = {}
    collect_planned_resources(root_module, resources)

    config_specs: Dict[str, ConfigResourceSpec] = {}
    configuration_root = (plan.get("configuration") or {}).get("root_module") or {}
    collect_config_resource_specs(configuration_root, config_specs)

    topology = TopologyModel()

    for address, resource in resources.items():
        values = resource.values
        if resource.rtype == "aws_vpc":
            topology.vpcs[address] = VpcRecord(
                address=address,
                vpc_id=values.get("id") or address,
                cidr_block=values.get("cidr_block", ""),
                name=(values.get("tags") or {}).get("Name", resource.name),
                resource=resource,
            )
        elif resource.rtype == "aws_subnet":
            _, index_key = parse_association_suffix(address)
            topology.subnets[address] = SubnetRecord(
                address=address,
                subnet_id=values.get("id") or address,
                vpc_id=values.get("vpc_id"),
                cidr_block=values.get("cidr_block", ""),
                availability_zone=values.get("availability_zone", ""),
                name=(values.get("tags") or {}).get("Name", resource.name),
                tf_name=resource.name,
                index_key=index_key,
                map_public_ip_on_launch=bool(values.get("map_public_ip_on_launch", False)),
                resource=resource,
            )
        elif resource.rtype == "aws_route_table":
            topology.route_tables[address] = RouteTableRecord(
                address=address,
                route_table_id=values.get("id") or address,
                vpc_id=values.get("vpc_id"),
                name=(values.get("tags") or {}).get("Name", resource.name),
                tf_name=resource.name,
                routes=values.get("route") or [],
                resource=resource,
            )
        elif resource.rtype == "aws_route_table_association":
            _, index_key = parse_association_suffix(address)
            subnet_address, route_table_address, status, unresolved_reason = _resolve_association_targets(
                association=resource,
                config_specs=config_specs,
                resource_lookup=resources,
            )
            topology.route_table_associations.append(
                RouteTableAssociationRecord(
                    address=address,
                    tf_name=resource.name,
                    index_key=index_key,
                    subnet_address=subnet_address,
                    route_table_address=route_table_address,
                    resolution_status=status,
                    unresolved_reason=unresolved_reason,
                    resource=resource,
                )
            )
        elif resource.rtype == "aws_route53_zone":
            topology.route53_zones.append(
                Route53ZoneRecord(
                    address=address,
                    name=values.get("name", resource.name),
                    vpcs=values.get("vpc") or [],
                    resource=resource,
                )
            )
        elif resource.rtype == "aws_internet_gateway":
            topology.internet_gateways.append(
                InternetGatewayRecord(
                    address=address,
                    vpc_id=values.get("vpc_id"),
                    name=(values.get("tags") or {}).get("Name", resource.name),
                    resource=resource,
                )
            )
        elif resource.rtype == "aws_nat_gateway":
            topology.nat_gateways.append(
                NatGatewayRecord(
                    address=address,
                    subnet_id=values.get("subnet_id"),
                    name=(values.get("tags") or {}).get("Name", resource.name),
                    resource=resource,
                )
            )

    for association in topology.route_table_associations:
        if association.subnet_address and association.subnet_address in topology.subnets:
            topology.subnets[association.subnet_address].associated_route_table_address = association.route_table_address

    topology.bundles = build_topology_bundles(topology)
    return topology


def build_topology_bundles(topology: TopologyModel) -> List[TopologyBundle]:
    bundles: List[TopologyBundle] = []

    for vpc in topology.vpcs.values():
        vpc_resource = vpc.resource
        vpc_module_path = vpc_resource.module_path
        vpc_region = (
            (vpc_resource.values.get("tags") or {}).get("Region")
            or (vpc_resource.values.get("tags_all") or {}).get("Region")
        )

        def in_same_vpc(entry_vpc_id: Optional[str], entry_resource: PlannedResource) -> bool:
            if entry_vpc_id and entry_vpc_id == vpc.vpc_id:
                return True
            return entry_resource.module_path == vpc_module_path

        subnets = [
            subnet for subnet in topology.subnets.values() if in_same_vpc(subnet.vpc_id, subnet.resource)
        ]
        route_tables = [
            route_table for route_table in topology.route_tables.values() if in_same_vpc(route_table.vpc_id, route_table.resource)
        ]
        internet_gateways = [
            gateway for gateway in topology.internet_gateways if in_same_vpc(gateway.vpc_id, gateway.resource)
        ]

        subnet_ids = {subnet.subnet_id for subnet in subnets}
        nat_gateways = [gateway for gateway in topology.nat_gateways if gateway.subnet_id in subnet_ids]
        route_table_associations = [
            association for association in topology.route_table_associations if association.resource.module_path == vpc_module_path
        ]

        route53_zones: List[Route53ZoneRecord] = []
        for zone in topology.route53_zones:
            for zone_vpc in zone.vpcs:
                if zone_vpc.get("vpc_id") == vpc.vpc_id:
                    route53_zones.append(zone)
                    break
                if vpc_region and zone_vpc.get("vpc_region") == vpc_region:
                    route53_zones.append(zone)
                    break

        bundles.append(
            TopologyBundle(
                vpc=vpc,
                subnets=subnets,
                route_tables=route_tables,
                route_table_associations=route_table_associations,
                route53_zones=route53_zones,
                internet_gateways=internet_gateways,
                nat_gateways=nat_gateways,
                unresolved_associations=[
                    association.address
                    for association in route_table_associations
                    if association.resolution_status != "resolved"
                ],
            )
        )

    return bundles


def topology_to_render_bundles(topology: TopologyModel) -> List[Dict[str, Any]]:
    bundles: List[Dict[str, Any]] = []
    for bundle in topology.bundles:
        bundles.append(
            {
                "vpc": {
                    "address": bundle.vpc.address,
                    "id": bundle.vpc.vpc_id,
                    "cidr_block": bundle.vpc.cidr_block,
                    "name": bundle.vpc.name,
                    "resource": bundle.vpc.resource,
                },
                "subnets": [
                    {
                        "address": subnet.address,
                        "vpc_id": subnet.vpc_id,
                        "subnet_id": subnet.subnet_id,
                        "cidr_block": subnet.cidr_block,
                        "availability_zone": subnet.availability_zone,
                        "name": subnet.name,
                        "tf_name": subnet.tf_name,
                        "assoc_index": subnet.index_key,
                        "map_public_ip_on_launch": subnet.map_public_ip_on_launch,
                        "associated_route_table_address": subnet.associated_route_table_address,
                        "resource": subnet.resource,
                    }
                    for subnet in bundle.subnets
                ],
                "route_tables": [
                    {
                        "address": route_table.address,
                        "route_table_id": route_table.route_table_id,
                        "vpc_id": route_table.vpc_id,
                        "name": route_table.name,
                        "tf_name": route_table.tf_name,
                        "routes": route_table.routes,
                        "resource": route_table.resource,
                    }
                    for route_table in bundle.route_tables
                ],
                "route_table_associations": [
                    {
                        "address": association.address,
                        "assoc_name": association.tf_name,
                        "assoc_index": association.index_key,
                        "subnet_address": association.subnet_address,
                        "route_table_address": association.route_table_address,
                        "resolution_status": association.resolution_status,
                        "unresolved_reason": association.unresolved_reason,
                        "resource": association.resource,
                    }
                    for association in bundle.route_table_associations
                ],
                "route53_zones": [
                    {
                        "address": zone.address,
                        "name": zone.name,
                        "vpcs": zone.vpcs,
                        "resource": zone.resource,
                    }
                    for zone in bundle.route53_zones
                ],
                "internet_gateways": [
                    {
                        "address": gateway.address,
                        "vpc_id": gateway.vpc_id,
                        "name": gateway.name,
                        "resource": gateway.resource,
                    }
                    for gateway in bundle.internet_gateways
                ],
                "nat_gateways": [
                    {
                        "address": gateway.address,
                        "subnet_id": gateway.subnet_id,
                        "name": gateway.name,
                        "resource": gateway.resource,
                    }
                    for gateway in bundle.nat_gateways
                ],
                "unresolved_associations": list(bundle.unresolved_associations),
            }
        )
    return bundles


def serialize_topology(topology: TopologyModel) -> Dict[str, Any]:
    return {
        "vpcs": {address: asdict(vpc) for address, vpc in topology.vpcs.items()},
        "subnets": {address: asdict(subnet) for address, subnet in topology.subnets.items()},
        "route_tables": {address: asdict(route_table) for address, route_table in topology.route_tables.items()},
        "route_table_associations": [asdict(association) for association in topology.route_table_associations],
        "route53_zones": [asdict(zone) for zone in topology.route53_zones],
        "internet_gateways": [asdict(gateway) for gateway in topology.internet_gateways],
        "nat_gateways": [asdict(gateway) for gateway in topology.nat_gateways],
        "bundles": [asdict(bundle) for bundle in topology.bundles],
    }


def topology_diagnostics(topology: TopologyModel) -> Dict[str, Any]:
    bundle_diagnostics: List[Dict[str, Any]] = []

    for bundle in topology.bundles:
        route_table_entries = [
            {
                "address": route_table.address,
                "name": route_table.name,
                **asdict(assess_route_table_tier({
                    "address": route_table.address,
                    "name": route_table.name,
                    "routes": route_table.routes,
                    "resource": route_table.resource,
                })),
            }
            for route_table in bundle.route_tables
        ]
        subnet_entries = [
            {
                "address": subnet.address,
                "name": subnet.name,
                "associated_route_table_address": subnet.associated_route_table_address,
                **asdict(assess_subnet_tier({
                    "address": subnet.address,
                    "name": subnet.name,
                    "map_public_ip_on_launch": subnet.map_public_ip_on_launch,
                    "associated_route_table_address": subnet.associated_route_table_address,
                    "resource": subnet.resource,
                }, [
                    {
                        "address": route_table.address,
                        "name": route_table.name,
                        "routes": route_table.routes,
                        "resource": route_table.resource,
                    }
                    for route_table in bundle.route_tables
                ])),
            }
            for subnet in bundle.subnets
        ]
        bundle_diagnostics.append(
            {
                "vpc_address": bundle.vpc.address,
                "subnet_tiers": subnet_entries,
                "route_table_tiers": route_table_entries,
                "unresolved_associations": list(bundle.unresolved_associations),
            }
        )

    return {"bundles": bundle_diagnostics}


def topology_summary(topology: TopologyModel) -> Dict[str, int]:
    unresolved_associations = sum(
        1 for association in topology.route_table_associations if association.resolution_status != "resolved"
    )
    return {
        "vpcs": len(topology.vpcs),
        "subnets": len(topology.subnets),
        "route_tables": len(topology.route_tables),
        "route53_zones": len(topology.route53_zones),
        "route_table_associations": len(topology.route_table_associations),
        "unresolved_associations": unresolved_associations,
    }