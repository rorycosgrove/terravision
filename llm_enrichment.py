from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


def _resource_tags(resource_entry: Dict[str, Any]) -> Dict[str, Any]:
    resource_obj = resource_entry.get("resource")
    if not resource_obj:
        return {}
    vals = resource_obj.values or {}
    return (vals.get("tags") or {}) or (vals.get("tags_all") or {})


def route_table_is_public(route_table: Dict[str, Any]) -> bool:
    for route in route_table.get("routes", []):
        gateway_id = route.get("gateway_id")
        cidr = route.get("cidr_block")
        if gateway_id and cidr == "0.0.0.0/0":
            return True
    return False


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


def build_bundle_snapshot(bundle: Dict[str, Any]) -> Dict[str, Any]:
    vpc = bundle["vpc"]
    subnets = bundle.get("subnets") or []
    route_tables = bundle.get("route_tables") or []
    igws = bundle.get("internet_gateways") or []
    nat_gws = bundle.get("nat_gateways") or []
    zones = bundle.get("route53_zones") or []

    azs = sorted({subnet.get("availability_zone") or "regional" for subnet in subnets})
    public_subnets = [subnet for subnet in subnets if infer_subnet_tier(subnet, route_tables) == "public"]
    private_subnets = [subnet for subnet in subnets if infer_subnet_tier(subnet, route_tables) == "private"]
    public_route_tables = [rt for rt in route_tables if route_table_is_public(rt)]

    return {
        "vpc": {
            "id": vpc.get("id"),
            "name": vpc.get("name"),
            "cidr_block": vpc.get("cidr_block"),
        },
        "availability_zones": azs,
        "subnets": {
            "total": len(subnets),
            "public": len(public_subnets),
            "private": len(private_subnets),
            "cidrs": [subnet.get("cidr_block") for subnet in subnets],
        },
        "routing": {
            "route_tables": len(route_tables),
            "public_route_tables": len(public_route_tables),
            "internet_gateways": len(igws),
            "nat_gateways": len(nat_gws),
        },
        "dns": {
            "route53_private_zones": len(zones),
            "zone_names": [zone.get("name") for zone in zones],
        },
    }


def load_skill_documents(skills_dir: Optional[str]) -> List[str]:
    if not skills_dir:
        return []

    skill_root = Path(skills_dir)
    if not skill_root.exists():
        return []

    documents: List[str] = []
    for skill_file in sorted(skill_root.rglob("SKILL.md")):
        try:
            documents.append(skill_file.read_text(encoding="utf-8"))
        except OSError:
            continue
    return documents


def generate_heuristic_enrichment(bundle: Dict[str, Any]) -> Dict[str, Any]:
    snapshot = build_bundle_snapshot(bundle)
    vpc = snapshot["vpc"]
    routing = snapshot["routing"]
    subnets = snapshot["subnets"]
    azs = snapshot["availability_zones"]
    dns = snapshot["dns"]

    nat_phrase = "NAT-backed private egress is configured" if routing["nat_gateways"] else "private egress control is not visibly NAT-backed"
    internet_phrase = "internet ingress/egress path is present" if routing["internet_gateways"] else "no explicit internet gateway is present"

    callouts = [
        f"VPC {vpc.get('cidr_block') or vpc.get('name') or vpc.get('id') or 'network'} is distributed across {len(azs)} availability zone(s) with {subnets['public']} public and {subnets['private']} private subnet(s).",
        f"Routing is modeled with {routing['route_tables']} route table(s); {internet_phrase}, and {nat_phrase}.",
        f"Private DNS footprint includes {dns['route53_private_zones']} Route 53 private zone(s), supporting internal name resolution where shown.",
    ]

    risks: List[str] = []
    if len(azs) < 2:
        risks.append("Single-AZ placement reduces fault tolerance and maintenance flexibility.")
    if subnets["private"] > 0 and routing["nat_gateways"] == 0:
        risks.append("Private subnets are present without a visible NAT gateway, which may constrain outbound dependency access.")
    if routing["internet_gateways"] == 0 and subnets["public"] > 0:
        risks.append("Public subnets exist without a visible internet gateway; validate whether internet exposure is intended or the plan is incomplete.")
    if dns["route53_private_zones"] == 0:
        risks.append("No private Route 53 zone is present; internal service discovery may rely on external conventions or unmanaged DNS.")
    if not risks:
        risks.append("Topology appears internally consistent from the available Terraform plan data; next review should focus on security controls outside this renderer's scope.")

    observations: List[str] = []
    observations.append(
        f"Terraform data shows {subnets['total']} subnet resource(s) across {len(azs)} availability zone(s), using CIDR ranges {', '.join(subnets['cidrs']) if subnets['cidrs'] else 'not specified'}."
    )
    observations.append(
        f"Route modeling includes {routing['route_tables']} route table(s), {routing['internet_gateways']} internet gateway resource(s), and {routing['nat_gateways']} NAT gateway resource(s)."
    )
    if dns["route53_private_zones"]:
        observations.append(
            f"Terraform declares {dns['route53_private_zones']} Route 53 private zone resource(s): {', '.join(dns['zone_names'])}."
        )
    else:
        observations.append("No Route 53 private hosted zone resources are present in the Terraform data for this VPC bundle.")

    summary = (
        f"This topology describes a {len(azs)}-AZ AWS network footprint for VPC {vpc.get('cidr_block') or vpc.get('name') or vpc.get('id')}, "
        f"with {subnets['total']} subnet(s), {routing['route_tables']} route table(s), and {dns['route53_private_zones']} private DNS zone(s)."
    )

    return {
        "mode": "heuristic",
        "summary": summary,
        "callouts": callouts[:3],
        "risks": risks[:4],
        "opportunities": observations[:3],
        "snapshot": snapshot,
    }


def _extract_json_object(text: str) -> Dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in LLM response")
    return json.loads(text[start : end + 1])


def generate_llm_enrichment(
    bundle: Dict[str, Any],
    endpoint: str,
    model: str,
    api_key: str,
    skill_documents: Optional[List[str]] = None,
    timeout: int = 45,
) -> Dict[str, Any]:
    snapshot = build_bundle_snapshot(bundle)
    skills_text = "\n\n".join(skill_documents or [])

    system_prompt = (
        "You are an expert AWS architecture reviewer producing concise, high-signal insights for a rendered diagram. "
        "Every statement must be grounded in the provided Terraform-derived topology data. "
        "Do not mention the project, renderer, future features, workflow changes, or generic recommendations unless directly implied by the topology. "
        "Return strict JSON with keys: summary, callouts, risks, opportunities. "
        "callouts must be an array of exactly 3 strings. risks must be an array of up to 4 strings. opportunities must be an array of up to 4 strings containing factual Terraform observations rather than product suggestions."
    )
    if skills_text:
        system_prompt += "\n\nReusable skill guidance:\n" + skills_text

    user_prompt = (
        "Review this AWS network architecture snapshot derived from Terraform and produce diagram-ready insights. "
        "Be specific, avoid generic cloud advice, avoid project commentary, and do not invent resources that are not present.\n\n"
        + json.dumps(snapshot, indent=2)
    )

    response = requests.post(
        endpoint,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.3,
            "response_format": {"type": "json_object"},
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    content = payload["choices"][0]["message"]["content"]
    result = _extract_json_object(content)
    result["mode"] = "llm"
    result["snapshot"] = snapshot
    return result


def enrich_bundle(
    bundle: Dict[str, Any],
    llm_endpoint: Optional[str] = None,
    llm_model: Optional[str] = None,
    llm_api_key: Optional[str] = None,
    skills_dir: Optional[str] = None,
) -> Dict[str, Any]:
    heuristic = generate_heuristic_enrichment(bundle)

    if not llm_endpoint or not llm_model or not llm_api_key:
        return heuristic

    try:
        skill_documents = load_skill_documents(skills_dir)
        result = generate_llm_enrichment(
            bundle=bundle,
            endpoint=llm_endpoint,
            model=llm_model,
            api_key=llm_api_key,
            skill_documents=skill_documents,
        )
        if len(result.get("callouts") or []) != 3:
            return heuristic
        return result
    except Exception:
        return heuristic


def resolve_llm_config(args: Any) -> Dict[str, Optional[str]]:
    return {
        "llm_endpoint": args.llm_endpoint or os.environ.get("TERRAVISION_LLM_ENDPOINT"),
        "llm_model": args.llm_model or os.environ.get("TERRAVISION_LLM_MODEL"),
        "llm_api_key": os.environ.get(args.llm_api_key_env or "TERRAVISION_LLM_API_KEY"),
        "skills_dir": args.skills_dir,
    }
