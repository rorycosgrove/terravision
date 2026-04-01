# Terravision

Terravision renders AWS network architecture diagrams in Miro from a Terraform plan JSON file.

It parses Terraform resources (VPCs, subnets, route tables, gateways, Route 53 zones), builds a model, and draws a documentation-style architecture layout per VPC/region.

## What It Does

- Reads Terraform output from `terraform show -json`.
- Extracts AWS network resources and associations.
- Infers subnet and route-table tiers (`public` / `private`) when needed.
- Draws AWS-style nested boundaries in Miro (Cloud, Account, VPC, AZ).
- Renders service icons from official AWS icon assets (with fallback to shapes).
- Uses straight, directional connectors for network relationships.
- Adds architecture callouts based on the actual parsed resources.

## Repository Layout

- `main.py`: CLI entrypoint and all parsing/rendering logic.
- `tfplan.json`: Example Terraform plan input.

## Requirements

- Python 3.9+ (tested with Python 3.12)
- `requests` Python package
- A Miro board ID and Miro API token for live rendering

Install dependency:

```bash
pip install requests
```

## Input: Terraform Plan JSON

Generate the input plan JSON from your Terraform configuration:

```bash
terraform plan -out=tfplan.bin
terraform show -json tfplan.bin > tfplan.json
```

Terravision expects the standard Terraform JSON structure with:

- `planned_values.root_module`
- `resources` and optional `child_modules`

## Supported AWS Resource Types

The parser currently extracts these Terraform resource types:

- `aws_vpc`
- `aws_subnet`
- `aws_route_table`
- `aws_route_table_association`
- `aws_route53_zone`
- `aws_internet_gateway`
- `aws_nat_gateway`

## CLI Usage

Basic dry-run (parse only, no Miro API calls):

```bash
python main.py --plan tfplan.json --dry-run
```

Live render to a Miro board:

```bash
python main.py --plan tfplan.json --board "<MIRO_BOARD_ID>"
```

Live render with icons disabled:

```bash
python main.py --plan tfplan.json --board "<MIRO_BOARD_ID>" --no-icons
```

Dump parsed model/bundles for inspection:

```bash
python main.py --plan tfplan.json --dry-run --dump-model model_dump.json
```

### CLI Arguments

- `--plan` (required): Path to Terraform plan JSON.
- `--board`: Miro board ID. Optional in dry-run mode.
- `--dry-run`: Parse and summarize only; do not call Miro.
- `--prefer-icons`: Enable icons (default).
- `--no-icons`: Disable icons and use shape fallback.
- `--dump-model`: Write parsed model and render bundles to a JSON file.

## Environment Variables

- `MIRO_TOKEN`: Required for live rendering.
- `MIRO_BOARD_ID`: Optional if `--board` is provided.

Example (PowerShell):

```powershell
$env:MIRO_TOKEN="<token>"
$env:MIRO_BOARD_ID="<board-id>"
python main.py --plan tfplan.json
```

## Rendered Diagram Structure

For each VPC bundle, the renderer creates a full page with:

1. Header area (title and region/format metadata)
2. Left panel: architecture boundaries and resources
3. Right panel: callouts derived from parsed data

Within architecture boundaries, it draws:

- AWS Account frame
- Amazon VPC frame
- Availability Zone frames
- Public/Private subnet tier bands
- Subnet nodes
- Route table section and nodes
- IGW/NAT nodes when present
- Route 53 zone nodes when present

### Connection Rules

- Uses explicit `aws_route_table_association` mappings when available.
- Falls back to default route table linking if no explicit associations are found.
- Connects public route table to IGW when present.
- Connects private route tables to NAT gateways when present.

## Tier Inference Logic

Subnet tier is inferred in this order:

1. `map_public_ip_on_launch` (true => public)
2. Resource tags (`Tier` contains `public` / `private`)
3. Name heuristic (`public` / `private` in name)
4. Route-table heuristic fallback

Route table tier is inferred in this order:

1. Presence of `0.0.0.0/0` route via gateway (public)
2. Resource tags (`Tier`)
3. Name heuristic
4. Defaults to private

## Icons

Icon URLs are configured in `ICON_URLS` in `main.py` and point to official AWS icon assets from the awslabs icon repository.

Current icon keys:

- `vpc`
- `public_subnet`
- `private_subnet`
- `route_table`
- `route53`
- `igw`
- `nat`

If icon render fails for any reason, Terravision logs the failure and falls back to a styled shape.

## Output and Logs

Dry run prints summary counts:

- VPCs
- Subnets
- Route tables
- Route53 zones

On live render success, it logs `Render complete`.

Typical log prefix:

- `[teravision] ...`

## Error Handling

- Missing board ID in live mode: exits with code `2`
- Missing `MIRO_TOKEN` in live mode: exits with code `2`
- Miro API or render failures: exits with code `1`

Miro requests include retry behavior for transient status codes:

- 429, 500, 502, 503, 504

## Troubleshooting

### 1) "missing board id"

Provide `--board` or set `MIRO_BOARD_ID`.

### 2) "MIRO_TOKEN is not set"

Export `MIRO_TOKEN` in your shell/session.

### 3) Live render fails with API errors

- Confirm token permissions for the target board.
- Verify board ID is correct.
- Retry after short delay for rate-limit responses.

### 4) Icons are not shown

- Check internet access to AWS icon URLs.
- Use `--no-icons` for deterministic shape-only rendering.

### 5) Layout overlaps

Layout constants are in `render_reference_diagram` and can be tuned by adjusting:

- page/header dimensions
- AZ panel geometry
- vertical spacing for resource placement
- callout card geometry

## Development Notes

- `main.py` currently contains parsing and rendering in one module for portability.
- `RenderNode` dataclass exists but is not currently central to rendering flow.
- Model extraction is recursive through Terraform child modules.
- VPC grouping combines direct `vpc_id` matching and module-path fallback.

## Security Notes

- Do not hard-code Miro tokens in source files.
- Prefer environment variables or a secure secret manager.
- Treat plan JSON as potentially sensitive infrastructure metadata.

## Suggested Next Improvements

- Split parser, model, and renderer into separate modules.
- Add unit tests for tier inference and association mapping.
- Add optional SVG/PDF export path (outside Miro) for CI artifacts.
- Add schema validation for Terraform input.

## License and Attribution

AWS service icons are sourced from publicly available AWS icon assets via the awslabs repository URLs configured in code.
