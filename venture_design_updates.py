#!/usr/bin/env python3
"""
Venture Design maintenance script

Actions (only for products where vendor is "Venture Design" or "VENTURE DESIGN"):
- Swap media positions 1 and 2 (if at least two media items exist)
- Compute and set variant metafield custom.kostpris = (cost_per_item * 2.20)

Dry-run by default. Use --apply to perform changes. Includes backups and logs.

Env vars:
- SHOPIFY_DOMAIN
- SHOPIFY_TOKEN
"""

from __future__ import annotations

import os
import json
import argparse
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests import Response
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from tqdm import tqdm
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box


console = Console()


# ------------------------------ GraphQL helpers ------------------------------

class ShopifyError(Exception):
    pass


def get_env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise ShopifyError(f"Missing required environment variable: {name}")
    return v


def build_graphql_session() -> Tuple[str, Dict[str, str]]:
    domain = get_env("SHOPIFY_DOMAIN")
    token = get_env("SHOPIFY_TOKEN")
    url = f"https://{domain}/admin/api/2024-07/graphql.json"
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    return url, headers


@retry(
    reraise=True,
    stop=stop_after_attempt(6),
    wait=wait_exponential(multiplier=1, min=1, max=20),
    retry=retry_if_exception_type((requests.RequestException, ShopifyError)),
)
def graphql_request(url: str, headers: Dict[str, str], query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
    resp: Response = requests.post(
        url,
        headers=headers,
        data=json.dumps({"query": query, "variables": variables}),
        timeout=30,
    )
    if resp.status_code == 429:
        raise ShopifyError("Rate limited (429)")
    if not resp.ok:
        raise ShopifyError(f"GraphQL HTTP error {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    if "errors" in data and data["errors"]:
        messages = "; ".join(err.get("message", "") for err in data["errors"])[:500]
        raise ShopifyError(f"GraphQL errors: {messages}")
    return data


PRODUCTS_QUERY = """
query VentureProducts($cursor: String, $query: String!) {
  products(first: 200, after: $cursor, query: $query) {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
        id
        title
        vendor
        handle
        media(first: 10) {
          edges { node { id mediaContentType alt } }
        }
        variants(first: 100) {
          edges {
            node {
              id
              title
              inventoryItem { unitCost { amount currencyCode } }
            }
          }
        }
      }
    }
  }
}
"""


REORDER_MEDIA_MUT = """
mutation Reorder($id: ID!, $moves: [MoveInput!]!) {
  productReorderMedia(id: $id, moves: $moves) {
    userErrors { field message }
  }
}
"""


METAFIELDS_SET = """
mutation SetMetafields($metafields: [MetafieldsSetInput!]!) {
  metafieldsSet(metafields: $metafields) {
    userErrors { field message code }
  }
}
"""


@dataclass
class VariantInfo:
    id: str
    title: str
    cost_amount: Optional[float]
    currency: Optional[str]


@dataclass
class ProductInfo:
    id: str
    title: str
    handle: str
    vendor: str
    media_ids: List[str]
    variants: List[VariantInfo]


def fetch_venture_products(url: str, headers: Dict[str, str]) -> List[ProductInfo]:
    products: List[ProductInfo] = []
    cursor: Optional[str] = None
    # Query matches both variants of vendor value
    query_str = 'vendor:"Venture Design" OR vendor:"VENTURE DESIGN"'
    while True:
        data = graphql_request(url, headers, PRODUCTS_QUERY, {"cursor": cursor, "query": query_str})
        conn = data["data"]["products"]
        for edge in conn["edges"]:
            node = edge["node"]
            media_ids = [e["node"]["id"] for e in (node.get("media", {}).get("edges") or [])]
            variants: List[VariantInfo] = []
            for vedge in (node.get("variants", {}).get("edges") or []):
                v = vedge["node"]
                cost = v.get("inventoryItem", {}).get("unitCost") or {}
                amount = cost.get("amount")
                try:
                    amount_f = float(amount) if amount is not None else None
                except ValueError:
                    amount_f = None
                variants.append(
                    VariantInfo(
                        id=v["id"],
                        title=v.get("title") or "",
                        cost_amount=amount_f,
                        currency=cost.get("currencyCode"),
                    )
                )
            products.append(
                ProductInfo(
                    id=node["id"],
                    title=node.get("title") or "",
                    handle=node.get("handle") or "",
                    vendor=node.get("vendor") or "",
                    media_ids=media_ids,
                    variants=variants,
                )
            )
        if not conn["pageInfo"]["hasNextPage"]:
            break
        cursor = conn["pageInfo"]["endCursor"]
    return products


def is_venture_vendor(vendor: str) -> bool:
    return (vendor or "").strip().lower() == "venture design"


def compute_kostpris(amount: Optional[float]) -> Optional[str]:
    if amount is None:
        return None
    # +120% => factor 2.20
    result = amount * 2.20
    return f"{result:.2f}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Swap images and set kostpris for Venture Design products")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Preview changes without applying (default)")
    mode.add_argument("--apply", action="store_true", help="Apply updates")
    parser.add_argument("--batch", type=int, default=25, help="Batch size for metafield writes (default 25)")
    parser.add_argument("--limit-products", type=int, default=0, help="Apply to only first N Venture products (0=all)")
    args = parser.parse_args()

    url, headers = build_graphql_session()

    console.print(Panel.fit("[bold]Loading Venture Design products[/bold]", box=box.ROUNDED))
    products = fetch_venture_products(url, headers)

    to_swap: List[Tuple[str, str, str]] = []  # (productId, firstId, secondId)
    metafields_payload: List[Dict[str, Any]] = []
    meta_preview: List[Tuple[str, str, str]] = []  # (variantId, oldCost, newKostpris)

    processed_products = 0
    for p in products:
        # Hard vendor guard
        if not is_venture_vendor(p.vendor):
            continue
        processed_products += 1
        if args.limit_products and processed_products > args.limit_products:
            break
        # Plan swaps
        if len(p.media_ids) >= 2:
            first_id, second_id = p.media_ids[0], p.media_ids[1]
            to_swap.append((p.id, first_id, second_id))

        # Plan kostpris metafields
        for v in p.variants:
            new_val = compute_kostpris(v.cost_amount)
            if new_val is None:
                continue
            metafields_payload.append(
                {
                    "ownerId": v.id,
                    "namespace": "custom",
                    "key": "kostpris",
                    "type": "number_decimal",
                    "value": new_val,
                }
            )
            meta_preview.append((v.id, f"{v.cost_amount:.2f}" if v.cost_amount is not None else "<none>", new_val))

    # Summary
    table = Table(title="Venture Design planned changes", box=box.SIMPLE_HEAVY)
    table.add_column("Metric", style="bold")
    table.add_column("Count", justify="right")
    table.add_row("Products found", str(len(products)))
    table.add_row("Products with >=2 images", str(len(to_swap)))
    table.add_row("Variant kostpris updates", str(len(metafields_payload)))
    console.print(table)

    if not args.apply:
        console.print("[green]Dry-run complete. No changes applied.[/green]")
        return

    # 1) Swap images per product
    for product_id, first_id, second_id in tqdm(to_swap, desc="Swapping images", unit="product"):
        moves = [
            {"id": first_id, "newPosition": "2"},
            {"id": second_id, "newPosition": "1"},
        ]
        data = graphql_request(url, headers, REORDER_MEDIA_MUT, {"id": product_id, "moves": moves})
        errs = data["data"]["productReorderMedia"]["userErrors"]
        if errs:
            console.print(f"[red]Reorder error for {product_id}:[/red] {errs}")

    # 2) Write metafields in batches
    updated = 0
    for i in tqdm(range(0, len(metafields_payload), args.batch), desc="Writing kostpris", unit="batch"):
        batch = metafields_payload[i : i + args.batch]
        data = graphql_request(url, headers, METAFIELDS_SET, {"metafields": batch})
        errs = data["data"]["metafieldsSet"]["userErrors"]
        if errs:
            console.print(f"[red]Metafield userErrors in batch:[/red] {errs}")
        updated += len(batch)

    console.print(f"[green]Swapped images on {len(to_swap)} products and set kostpris on {updated} variants.[/green]")


if __name__ == "__main__":
    try:
        main()
    except ShopifyError as e:
        console.print(f"[red]Shopify error:[/red] {e}")
        raise
    except requests.RequestException as e:
        console.print(f"[red]Network error:[/red] {e}")
        raise

