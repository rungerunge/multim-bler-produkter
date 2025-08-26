#!/usr/bin/env python3
"""
Test different image swap strategies
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

def graphql_request(url, headers, query, variables=None):
    resp = requests.post(url, headers=headers, json={"query": query, "variables": variables or {}}, timeout=30)
    if not resp.ok:
        raise Exception(f"HTTP {resp.status_code}: {resp.text}")
    data = resp.json()
    if "errors" in data:
        raise Exception(f"GraphQL errors: {data['errors']}")
    return data

def get_media_order(url, headers, product_title):
    query = f'''
    {{
      products(first: 1, query: "title:\\"{product_title}\\"") {{
        edges {{
          node {{
            id
            media(first: 5) {{
              edges {{ node {{ id }} }}
            }}
          }}
        }}
      }}
    }}
    '''
    data = graphql_request(url, headers, query)
    if not data["data"]["products"]["edges"]:
        return None, []
    
    product = data["data"]["products"]["edges"][0]["node"]
    media_ids = [e["node"]["id"] for e in product["media"]["edges"]]
    return product["id"], media_ids

def main():
    domain = get_env("SHOPIFY_DOMAIN")
    token = get_env("SHOPIFY_TOKEN")
    url = f"https://{domain}/admin/api/2024-07/graphql.json"
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    }

    test_product = "Dakar Sofa Sæt Sort Sort"
    
    # Get current state
    product_id, media_ids = get_media_order(url, headers, test_product)
    if not media_ids or len(media_ids) < 2:
        console.print("Not enough media items")
        return
    
    console.print(f"[bold]BEFORE:[/bold]")
    for i, mid in enumerate(media_ids):
        console.print(f"  {i+1}. {mid}")
    
    first_id = media_ids[0]
    second_id = media_ids[1]
    
    # Strategy: Use explicit position moves for both items
    console.print(f"\n[bold yellow]Strategy: Move both items explicitly[/bold yellow]")
    console.print(f"Move {first_id} to position 2")
    console.print(f"Move {second_id} to position 1")
    
    swap_mutation = '''
    mutation SwapBoth($id: ID!, $moves: [MoveInput!]!) {
      productReorderMedia(id: $id, moves: $moves) {
        userErrors { field message }
      }
    }
    '''
    
    # Try moving both at once with explicit positions
    moves = [
        {"id": first_id, "newPosition": "2"},
        {"id": second_id, "newPosition": "1"}
    ]
    
    result = graphql_request(url, headers, swap_mutation, {
        "id": product_id, 
        "moves": moves
    })
    errors = result["data"]["productReorderMedia"]["userErrors"]
    
    if errors:
        console.print(f"[red]Swap failed: {errors}[/red]")
        return
    
    console.print("[green]✓ Swap attempted[/green]")
    
    # Check result
    import time
    time.sleep(2)
    
    _, new_media_ids = get_media_order(url, headers, test_product)
    
    console.print(f"\n[bold]AFTER:[/bold]")
    for i, mid in enumerate(new_media_ids):
        console.print(f"  {i+1}. {mid}")
    
    # Verify swap
    if len(new_media_ids) >= 2:
        new_first = new_media_ids[0]
        new_second = new_media_ids[1]
        
        success = (new_first == second_id and new_second == first_id)
        console.print(f"\n[bold]Swap verification:[/bold]")
        console.print(f"Expected first: {second_id}")
        console.print(f"Actual first: {new_first}")
        console.print(f"Expected second: {first_id}")
        console.print(f"Actual second: {new_second}")
        console.print(f"Success: {'✓' if success else '✗'}")
        
        if not success:
            console.print("\n[yellow]Trying alternative strategy...[/yellow]")
            # Alternative: move first to position 3, then second to 1, then first to 2
            moves2 = [
                {"id": first_id, "newPosition": "3"},
                {"id": second_id, "newPosition": "1"},
                {"id": first_id, "newPosition": "2"}
            ]
            
            result2 = graphql_request(url, headers, swap_mutation, {
                "id": product_id, 
                "moves": moves2
            })
            
            time.sleep(2)
            _, final_media_ids = get_media_order(url, headers, test_product)
            
            console.print(f"\n[bold]FINAL ATTEMPT:[/bold]")
            for i, mid in enumerate(final_media_ids):
                console.print(f"  {i+1}. {mid}")
            
            if len(final_media_ids) >= 2:
                final_success = (final_media_ids[0] == second_id and final_media_ids[1] == first_id)
                console.print(f"Final success: {'✓' if final_success else '✗'}")

if __name__ == "__main__":
    main()
