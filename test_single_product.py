#!/usr/bin/env python3
"""
Test script to verify image swap and kostpris update on a single Venture Design product
"""

import os
import json
import requests
from rich.console import Console

console = Console()

def get_env(name):
    v = os.getenv(name)
    if not v:
        raise Exception(f"Missing {name}")
    return v

domain = get_env("SHOPIFY_DOMAIN")
token = get_env("SHOPIFY_TOKEN")
url = f"https://{domain}/admin/api/2024-07/graphql.json"
headers = {
    "X-Shopify-Access-Token": token,
    "Content-Type": "application/json",
}

def graphql_request(query, variables=None):
    resp = requests.post(url, headers=headers, json={"query": query, "variables": variables or {}}, timeout=30)
    if not resp.ok:
        raise Exception(f"HTTP {resp.status_code}: {resp.text}")
    data = resp.json()
    if "errors" in data:
        raise Exception(f"GraphQL errors: {data['errors']}")
    return data

# 1. Find first Venture Design product with multiple images
find_query = '''
{
  products(first: 5, query: "vendor:\\"Venture Design\\"") {
    edges {
      node {
        id
        title
        vendor
        media(first: 5) {
          edges { 
            node { 
              id 
              alt
              mediaContentType
            } 
          }
        }
        variants(first: 1) {
          edges {
            node {
              id
              title
              inventoryItem { unitCost { amount } }
              metafield(namespace: "custom", key: "kostpris") {
                id
                value
              }
            }
          }
        }
      }
    }
  }
}
'''

console.print("[bold blue]Finding test product...[/bold blue]")
data = graphql_request(find_query)

test_product = None
for edge in data["data"]["products"]["edges"]:
    p = edge["node"]
    if len(p["media"]["edges"]) >= 2:
        test_product = p
        break

if not test_product:
    console.print("[red]No Venture Design product found with >=2 images[/red]")
    exit(1)

console.print(f"[green]Found test product:[/green] {test_product['title']}")
console.print(f"Vendor: {test_product['vendor']}")
console.print(f"Media count: {len(test_product['media']['edges'])}")

# Show current media order
console.print("\n[bold]Current media order:[/bold]")
for i, edge in enumerate(test_product["media"]["edges"]):
    m = edge["node"]
    console.print(f"  {i+1}. {m['id']} - {m.get('alt', 'No alt')} ({m['mediaContentType']})")

# Show current variant info
variant = test_product["variants"]["edges"][0]["node"]
current_cost = variant["inventoryItem"]["unitCost"]["amount"]
current_kostpris = variant.get("metafield", {}).get("value") if variant.get("metafield") else None

console.print(f"\n[bold]Current variant info:[/bold]")
console.print(f"  Cost per item: {current_cost}")
console.print(f"  Current kostpris: {current_kostpris}")
console.print(f"  Expected kostpris: {float(current_cost) * 1.75:.2f}")

# 2. Swap first two images
if len(test_product["media"]["edges"]) >= 2:
    first_id = test_product["media"]["edges"][0]["node"]["id"]
    second_id = test_product["media"]["edges"][1]["node"]["id"]
    
    console.print(f"\n[bold yellow]Swapping images...[/bold yellow]")
    console.print(f"Moving {first_id} to position 2")
    console.print(f"Moving {second_id} to position 1")
    
    swap_mutation = '''
    mutation SwapImages($id: ID!, $moves: [MoveInput!]!) {
      productReorderMedia(id: $id, moves: $moves) {
        userErrors { field message }
      }
    }
    '''
    
    moves = [
        {"id": first_id, "newPosition": "2"},
        {"id": second_id, "newPosition": "1"},
    ]
    
    swap_result = graphql_request(swap_mutation, {"id": test_product["id"], "moves": moves})
    errors = swap_result["data"]["productReorderMedia"]["userErrors"]
    
    if errors:
        console.print(f"[red]Swap errors: {errors}[/red]")
    else:
        console.print("[green]✓ Images swapped successfully[/green]")

# 3. Set kostpris metafield
new_kostpris = str(float(current_cost) * 1.75)

console.print(f"\n[bold yellow]Setting kostpris metafield...[/bold yellow]")
console.print(f"Setting kostpris to: {new_kostpris}")

meta_mutation = '''
mutation SetKostpris($metafields: [MetafieldsSetInput!]!) {
  metafieldsSet(metafields: $metafields) {
    metafields { id }
    userErrors { field message }
  }
}
'''

metafields = [{
    "ownerId": variant["id"],
    "namespace": "custom",
    "key": "kostpris",
    "type": "number_decimal",
    "value": new_kostpris
}]

meta_result = graphql_request(meta_mutation, {"metafields": metafields})
errors = meta_result["data"]["metafieldsSet"]["userErrors"]

if errors:
    console.print(f"[red]Metafield errors: {errors}[/red]")
else:
    console.print("[green]✓ Kostpris metafield set successfully[/green]")

# 4. Verify changes
console.print(f"\n[bold blue]Verifying changes...[/bold blue]")
verify_data = graphql_request(find_query)

for edge in verify_data["data"]["products"]["edges"]:
    p = edge["node"]
    if p["id"] == test_product["id"]:
        console.print("\n[bold]New media order:[/bold]")
        for i, edge in enumerate(p["media"]["edges"]):
            m = edge["node"]
            console.print(f"  {i+1}. {m['id']} - {m.get('alt', 'No alt')} ({m['mediaContentType']})")
        
        new_variant = p["variants"]["edges"][0]["node"]
        new_kostpris = new_variant.get("metafield", {}).get("value") if new_variant.get("metafield") else None
        console.print(f"\n[bold]New kostpris:[/bold] {new_kostpris}")
        break

console.print("\n[green]Test completed![/green]")
