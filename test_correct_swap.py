#!/usr/bin/env python3
"""
Test correct image swap logic
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

# Find Dakar product specifically
find_query = '''
{
  products(first: 1, query: "title:\\"Dakar Sofa Sæt Sort Sort\\"") {
    edges {
      node {
        id
        title
        media(first: 5) {
          edges { 
            node { 
              id 
              alt
            } 
          }
        }
      }
    }
  }
}
'''

console.print("[bold blue]Getting current Dakar product state...[/bold blue]")
data = graphql_request(find_query)
product = data["data"]["products"]["edges"][0]["node"]

console.print(f"Product: {product['title']}")
console.print("\n[bold]Current media order:[/bold]")
for i, edge in enumerate(product["media"]["edges"]):
    m = edge["node"]
    console.print(f"  {i+1}. {m['id']}")

if len(product["media"]["edges"]) >= 2:
    first_id = product["media"]["edges"][0]["node"]["id"]
    second_id = product["media"]["edges"][1]["node"]["id"]
    
    console.print(f"\n[bold yellow]Correct swap strategy:[/bold yellow]")
    console.print(f"Move {first_id} (position 1) to position 2")
    console.print(f"Move {second_id} (position 2) to position 1")
    
    # The correct way: we need to ensure both moves happen atomically
    swap_mutation = '''
    mutation SwapImages($id: ID!, $moves: [MoveInput!]!) {
      productReorderMedia(id: $id, moves: $moves) {
        userErrors { field message }
      }
    }
    '''
    
    # Strategy: Move first to position 3 temporarily, then second to 1, then first to 2
    moves = [
        {"id": first_id, "newPosition": "3"},   # Move first out of the way
        {"id": second_id, "newPosition": "1"},  # Move second to first position  
        {"id": first_id, "newPosition": "2"},   # Move original first to second position
    ]
    
    console.print("\n[bold]Executing three-step swap...[/bold]")
    
    swap_result = graphql_request(swap_mutation, {"id": product["id"], "moves": moves})
    errors = swap_result["data"]["productReorderMedia"]["userErrors"]
    
    if errors:
        console.print(f"[red]Swap errors: {errors}[/red]")
    else:
        console.print("[green]✓ Swap completed[/green]")
        
        # Verify
        console.print("\n[bold blue]Verifying...[/bold blue]")
        verify_data = graphql_request(find_query)
        new_product = verify_data["data"]["products"]["edges"][0]["node"]
        
        console.print("\n[bold]New media order:[/bold]")
        for i, edge in enumerate(new_product["media"]["edges"]):
            m = edge["node"]
            console.print(f"  {i+1}. {m['id']}")
            
        # Check if swap worked
        new_first = new_product["media"]["edges"][0]["node"]["id"]
        new_second = new_product["media"]["edges"][1]["node"]["id"]
        
        if new_first == second_id and new_second == first_id:
            console.print("\n[green]✓ Perfect! Images 1 and 2 successfully swapped![/green]")
        else:
            console.print(f"\n[red]Swap didn't work as expected[/red]")
            console.print(f"Expected first: {second_id}, got: {new_first}")
            console.print(f"Expected second: {first_id}, got: {new_second}")

console.print("\nTest completed!")
