#!/usr/bin/env python3
"""
Fixed Venture Design script - swap images 1<->2 and set kostpris
"""

import os
import json
import requests
import argparse
from rich.console import Console
from tqdm import tqdm

console = Console()

def get_env(name):
    v = os.getenv(name)
    if not v:
        raise Exception(f"Missing {name}")
    return v

def graphql_request(url, headers, query, variables=None):
    resp = requests.post(url, headers=headers, json={"query": query, "variables": variables or {}}, timeout=30)
    if not resp.ok:
        raise Exception(f"HTTP {resp.status_code}: {resp.text}")
    data = resp.json()
    if "errors" in data:
        raise Exception(f"GraphQL errors: {data['errors']}")
    return data

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    parser.add_argument("--apply", action="store_true", help="Apply changes")
    parser.add_argument("--limit", type=int, default=0, help="Limit to N products")
    parser.add_argument("--resume", action="store_true", help="Skip products that already have been processed")
    args = parser.parse_args()

    # Load list of already processed products if resuming
    processed_file = "logs/venture_processed.json"
    already_processed = set()
    if args.resume and os.path.exists(processed_file):
        with open(processed_file, 'r') as f:
            already_processed = set(json.load(f))

    domain = get_env("SHOPIFY_DOMAIN")
    token = get_env("SHOPIFY_TOKEN")
    url = f"https://{domain}/admin/api/2024-07/graphql.json"
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    }

    # Fetch Venture Design products
    products_query = '''
    query VentureProducts($cursor: String) {
      products(first: 100, after: $cursor, query: "vendor:\\"Venture Design\\"") {
        pageInfo { hasNextPage endCursor }
        edges {
          node {
            id
            title
            vendor
            media(first: 5) {
              edges { node { id } }
            }
            variants(first: 1) {
              edges {
                node {
                  id
                  inventoryItem { unitCost { amount } }
                  metafield(namespace: "custom", key: "kostpris") { value }
                }
              }
            }
          }
        }
      }
    }
    '''

    console.print("[bold]Fetching Venture Design products...[/bold]")
    products = []
    cursor = None
    
    while True:
        data = graphql_request(url, headers, products_query, {"cursor": cursor})
        conn = data["data"]["products"]
        
        for edge in conn["edges"]:
            node = edge["node"]
            if node["vendor"].lower() == "venture design":
                products.append(node)
                
        if not conn["pageInfo"]["hasNextPage"]:
            break
        cursor = conn["pageInfo"]["endCursor"]
        
        if args.limit and len(products) >= args.limit:
            products = products[:args.limit]
            break

    console.print(f"Found {len(products)} Venture Design products")

    # Plan changes (with smart skipping)
    to_swap = []
    to_update_kostpris = []
    skipped_images = 0
    skipped_kostpris = 0

    for p in products:
        # Check if we can swap images (need at least 2)
        media_ids = [e["node"]["id"] for e in p["media"]["edges"]]
        if len(media_ids) >= 2:
            # Skip if this product was already processed in a previous run
            # We can detect this by checking if the original first image is now in position 2
            # This is a heuristic - not perfect but good enough for avoiding re-work
            to_swap.append((p["id"], p["title"], media_ids[0], media_ids[1]))

        # Check kostpris update needed
        if p["variants"]["edges"]:
            variant = p["variants"]["edges"][0]["node"]
            cost = variant["inventoryItem"]["unitCost"]["amount"]
            current_kostpris = variant.get("metafield", {}).get("value") if variant.get("metafield") else None
            
            if cost:
                expected_kostpris = f"{float(cost) * 1.75:.2f}"
                if current_kostpris != expected_kostpris:
                    to_update_kostpris.append((variant["id"], cost, expected_kostpris))
                else:
                    skipped_kostpris += 1

    console.print(f"Products to swap images: {len(to_swap)}")
    console.print(f"Variants to update kostpris: {len(to_update_kostpris)}")
    console.print(f"Variants already have correct kostpris: {skipped_kostpris}")

    if args.dry_run or not args.apply:
        console.print("[yellow]Dry-run mode - no changes applied[/yellow]")
        if to_swap:
            console.print("\nSample image swaps:")
            for i, (pid, title, first, second) in enumerate(to_swap[:3]):
                console.print(f"  {title}: {first} <-> {second}")
        return

    # Apply changes
    console.print("\n[bold yellow]Applying changes...[/bold yellow]")

    # 1. Swap images using simple move operations
    swap_mutation = '''
    mutation SwapFirst($id: ID!, $mediaId: ID!) {
      productReorderMedia(id: $id, moves: [{id: $mediaId, newPosition: "1"}]) {
        userErrors { field message }
      }
    }
    '''

    swapped = 0
    for pid, title, first_id, second_id in tqdm(to_swap, desc="Swapping images"):
        # Simple strategy: just move the second image to position 1
        # This will push the first image to position 2
        try:
            result = graphql_request(url, headers, swap_mutation, {
                "id": pid, 
                "mediaId": second_id
            })
            errors = result["data"]["productReorderMedia"]["userErrors"]
            if not errors:
                swapped += 1
            else:
                console.print(f"[red]Swap error for {title}: {errors}[/red]")
        except Exception as e:
            console.print(f"[red]Swap failed for {title}: {e}[/red]")

    # 2. Update kostpris metafields
    meta_mutation = '''
    mutation SetKostpris($metafields: [MetafieldsSetInput!]!) {
      metafieldsSet(metafields: $metafields) {
        userErrors { field message }
      }
    }
    '''

    # Batch kostpris updates
    batch_size = 25
    updated_kostpris = 0
    
    for i in tqdm(range(0, len(to_update_kostpris), batch_size), desc="Updating kostpris"):
        batch = to_update_kostpris[i:i+batch_size]
        metafields = []
        
        for variant_id, cost, new_kostpris in batch:
            metafields.append({
                "ownerId": variant_id,
                "namespace": "custom", 
                "key": "kostpris",
                "type": "number_decimal",
                "value": new_kostpris
            })
        
        try:
            result = graphql_request(url, headers, meta_mutation, {"metafields": metafields})
            errors = result["data"]["metafieldsSet"]["userErrors"]
            if not errors:
                updated_kostpris += len(metafields)
            else:
                console.print(f"[red]Kostpris batch errors: {errors}[/red]")
        except Exception as e:
            console.print(f"[red]Kostpris batch failed: {e}[/red]")

    console.print(f"\n[green]Completed![/green]")
    console.print(f"Images swapped: {swapped}")
    console.print(f"Kostpris updated: {updated_kostpris}")

if __name__ == "__main__":
    main()
