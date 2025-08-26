#!/usr/bin/env python3
"""
FINAL Venture Design fix - combines REST API for images + GraphQL for kostpris
"""

import os
import json
import requests
import argparse
from rich.console import Console
from rich.table import Table
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
    args = parser.parse_args()

    domain = get_env("SHOPIFY_DOMAIN")
    token = get_env("SHOPIFY_TOKEN")
    
    graphql_url = f"https://{domain}/admin/api/2024-07/graphql.json"
    rest_headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    }
    graphql_headers = rest_headers.copy()

    # Fetch Venture Design products using GraphQL
    products_query = '''
    query VentureProducts($cursor: String) {
      products(first: 100, after: $cursor, query: "vendor:\\"Venture Design\\"") {
        pageInfo { hasNextPage endCursor }
        edges {
          node {
            id
            legacyResourceId
            title
            vendor
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
        data = graphql_request(graphql_url, graphql_headers, products_query, {"cursor": cursor})
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

    # Plan changes
    to_swap_images = []
    to_update_kostpris = []
    skipped_kostpris = 0

    for p in products:
        product_id = p["legacyResourceId"]
        
        # Check for image swap potential using REST API
        rest_url = f"https://{domain}/admin/api/2024-07/products/{product_id}.json"
        try:
            resp = requests.get(rest_url, headers=rest_headers, timeout=10)
            if resp.ok:
                product_data = resp.json()["product"]
                images = product_data.get("images", [])
                if len(images) >= 2:
                    first_img = images[0]
                    second_img = images[1]
                    to_swap_images.append((product_id, p["title"], first_img["id"], second_img["id"]))
        except:
            continue  # Skip if REST call fails

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

    # Summary
    table = Table(title="Venture Design Changes Plan")
    table.add_column("Action", style="bold")
    table.add_column("Count", justify="right")
    table.add_row("Products to swap images", str(len(to_swap_images)))
    table.add_row("Variants to update kostpris", str(len(to_update_kostpris)))
    table.add_row("Variants already correct kostpris", str(skipped_kostpris))
    console.print(table)

    if args.dry_run or not args.apply:
        console.print("[yellow]Dry-run mode - no changes applied[/yellow]")
        if to_swap_images:
            console.print("\nSample image swaps:")
            for i, (pid, title, first, second) in enumerate(to_swap_images[:3]):
                console.print(f"  {title}: {first} <-> {second}")
        return

    # Apply changes
    console.print("\n[bold yellow]Applying changes...[/bold yellow]")

    # 1. Swap images using REST API
    swapped = 0
    failed_swaps = 0
    
    for product_id, title, first_img_id, second_img_id in tqdm(to_swap_images, desc="Swapping images"):
        try:
            # Update first image to position 2
            update_url1 = f"https://{domain}/admin/api/2024-07/products/{product_id}/images/{first_img_id}.json"
            update_data1 = {"image": {"id": first_img_id, "position": 2}}
            resp1 = requests.put(update_url1, headers=rest_headers, json=update_data1, timeout=10)
            
            # Update second image to position 1  
            update_url2 = f"https://{domain}/admin/api/2024-07/products/{product_id}/images/{second_img_id}.json"
            update_data2 = {"image": {"id": second_img_id, "position": 1}}
            resp2 = requests.put(update_url2, headers=rest_headers, json=update_data2, timeout=10)
            
            if resp1.ok and resp2.ok:
                swapped += 1
            else:
                failed_swaps += 1
                if failed_swaps <= 3:  # Only log first few failures
                    console.print(f"[red]Swap failed for {title}: {resp1.status_code}/{resp2.status_code}[/red]")
                    
        except Exception as e:
            failed_swaps += 1
            if failed_swaps <= 3:
                console.print(f"[red]Swap error for {title}: {e}[/red]")

    # 2. Update kostpris metafields using GraphQL
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
            result = graphql_request(graphql_url, graphql_headers, meta_mutation, {"metafields": metafields})
            errors = result["data"]["metafieldsSet"]["userErrors"]
            if not errors:
                updated_kostpris += len(metafields)
            else:
                console.print(f"[red]Kostpris batch errors: {errors}[/red]")
        except Exception as e:
            console.print(f"[red]Kostpris batch failed: {e}[/red]")

    # Final results
    results_table = Table(title="Results")
    results_table.add_column("Action", style="bold")
    results_table.add_column("Success", style="green")
    results_table.add_column("Failed", style="red")
    results_table.add_row("Image swaps", str(swapped), str(failed_swaps))
    results_table.add_row("Kostpris updates", str(updated_kostpris), str(len(to_update_kostpris) - updated_kostpris))
    console.print(results_table)

    if swapped > 0 and updated_kostpris > 0:
        console.print(f"\n[bold green]🎉 SUCCESS! Updated {swapped} image swaps and {updated_kostpris} kostpris values! 🎉[/bold green]")
    elif swapped > 0 or updated_kostpris > 0:
        console.print(f"\n[bold yellow]⚠️ PARTIAL SUCCESS: {swapped} swaps, {updated_kostpris} kostpris[/bold yellow]")
    else:
        console.print(f"\n[bold red]❌ NO CHANGES APPLIED[/bold red]")

if __name__ == "__main__":
    main()
