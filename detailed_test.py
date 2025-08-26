#!/usr/bin/env python3
"""
Detailed test - show before/after for images and kostpris on a specific product
"""

import os
import json
import requests
from rich.console import Console
from rich.table import Table

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

def get_product_details(url, headers, product_title):
    """Get complete product details including images and variant info"""
    query = f'''
    {{
      products(first: 1, query: "title:\\"{product_title}\\"") {{
        edges {{
          node {{
            id
            title
            vendor
            media(first: 5) {{
              edges {{ 
                node {{ 
                  id 
                  alt
                  mediaContentType
                  preview {{ image {{ url }} }}
                }} 
              }}
            }}
            variants(first: 1) {{
              edges {{
                node {{
                  id
                  title
                  inventoryItem {{ unitCost {{ amount currencyCode }} }}
                  metafield(namespace: "custom", key: "kostpris") {{
                    id
                    value
                  }}
                }}
              }}
            }}
          }}
        }}
      }}
    }}
    '''
    
    data = graphql_request(url, headers, query)
    if not data["data"]["products"]["edges"]:
        return None
    return data["data"]["products"]["edges"][0]["node"]

def main():
    domain = get_env("SHOPIFY_DOMAIN")
    token = get_env("SHOPIFY_TOKEN")
    url = f"https://{domain}/admin/api/2024-07/graphql.json"
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    }

    # Test product - we know this one exists
    test_product_title = "Dakar Sofa Sæt Sort Sort"
    
    console.print(f"[bold blue]BEFORE - Testing product: {test_product_title}[/bold blue]")
    
    # Get initial state
    product = get_product_details(url, headers, test_product_title)
    if not product:
        console.print("[red]Product not found![/red]")
        return
    
    console.print(f"Product ID: {product['id']}")
    console.print(f"Vendor: {product['vendor']}")
    
    # Show current images
    console.print("\n[bold]Current Image Order:[/bold]")
    image_table = Table()
    image_table.add_column("Position", style="bold")
    image_table.add_column("Image ID", style="cyan")
    image_table.add_column("Type")
    
    media_ids = []
    for i, edge in enumerate(product["media"]["edges"]):
        m = edge["node"]
        media_ids.append(m["id"])
        image_table.add_row(str(i+1), m["id"], m["mediaContentType"])
    
    console.print(image_table)
    
    # Show current variant info
    if product["variants"]["edges"]:
        variant = product["variants"]["edges"][0]["node"]
        cost = variant["inventoryItem"]["unitCost"]["amount"]
        currency = variant["inventoryItem"]["unitCost"]["currencyCode"]
        current_kostpris = variant.get("metafield", {}).get("value") if variant.get("metafield") else None
        expected_kostpris = float(cost) * 1.75
        
        console.print(f"\n[bold]Current Variant Info:[/bold]")
        console.print(f"Variant ID: {variant['id']}")
        console.print(f"Cost per item: {cost} {currency}")
        console.print(f"Current kostpris: {current_kostpris}")
        console.print(f"Expected kostpris: {expected_kostpris:.2f}")
        console.print(f"Needs kostpris update: {current_kostpris != f'{expected_kostpris:.2f}'}")
    
    # Now apply our fixes
    console.print(f"\n[bold yellow]APPLYING CHANGES...[/bold yellow]")
    
    # 1. Swap images if we have at least 2
    if len(media_ids) >= 2:
        first_id = media_ids[0]
        second_id = media_ids[1]
        
        console.print(f"Swapping: Moving {second_id} to position 1")
        
        swap_mutation = '''
        mutation SwapFirst($id: ID!, $mediaId: ID!) {
          productReorderMedia(id: $id, moves: [{id: $mediaId, newPosition: "1"}]) {
            userErrors { field message }
          }
        }
        '''
        
        result = graphql_request(url, headers, swap_mutation, {
            "id": product["id"], 
            "mediaId": second_id
        })
        errors = result["data"]["productReorderMedia"]["userErrors"]
        if errors:
            console.print(f"[red]Image swap failed: {errors}[/red]")
        else:
            console.print("[green]✓ Images swapped[/green]")
    
    # 2. Update kostpris
    if product["variants"]["edges"]:
        variant = product["variants"]["edges"][0]["node"]
        cost = variant["inventoryItem"]["unitCost"]["amount"]
        new_kostpris = f"{float(cost) * 1.75:.2f}"
        
        console.print(f"Setting kostpris to: {new_kostpris}")
        
        meta_mutation = '''
        mutation SetKostpris($metafields: [MetafieldsSetInput!]!) {
          metafieldsSet(metafields: $metafields) {
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
        
        result = graphql_request(url, headers, meta_mutation, {"metafields": metafields})
        errors = result["data"]["metafieldsSet"]["userErrors"]
        if errors:
            console.print(f"[red]Kostpris update failed: {errors}[/red]")
        else:
            console.print("[green]✓ Kostpris updated[/green]")
    
    # 3. Verify changes
    console.print(f"\n[bold blue]AFTER - Verifying changes...[/bold blue]")
    
    # Wait a moment for changes to propagate
    import time
    time.sleep(2)
    
    updated_product = get_product_details(url, headers, test_product_title)
    
    # Show new image order
    console.print("\n[bold]New Image Order:[/bold]")
    new_image_table = Table()
    new_image_table.add_column("Position", style="bold")
    new_image_table.add_column("Image ID", style="cyan")
    new_image_table.add_column("Changed?", style="bold")
    
    new_media_ids = []
    for i, edge in enumerate(updated_product["media"]["edges"]):
        m = edge["node"]
        new_media_ids.append(m["id"])
        changed = "✓" if (i < len(media_ids) and m["id"] != media_ids[i]) else ""
        new_image_table.add_row(str(i+1), m["id"], changed)
    
    console.print(new_image_table)
    
    # Show updated variant info
    if updated_product["variants"]["edges"]:
        updated_variant = updated_product["variants"]["edges"][0]["node"]
        updated_kostpris = updated_variant.get("metafield", {}).get("value") if updated_variant.get("metafield") else None
        
        console.print(f"\n[bold]Updated Variant Info:[/bold]")
        console.print(f"New kostpris: {updated_kostpris}")
        
        # Verify the swap worked correctly
        if len(media_ids) >= 2 and len(new_media_ids) >= 2:
            original_first = media_ids[0]
            original_second = media_ids[1]
            new_first = new_media_ids[0]
            new_second = new_media_ids[1]
            
            swap_success = (new_first == original_second)
            console.print(f"\n[bold]Image Swap Verification:[/bold]")
            console.print(f"Original first image: {original_first}")
            console.print(f"Original second image: {original_second}")
            console.print(f"New first image: {new_first}")
            console.print(f"Swap successful: {'✓' if swap_success else '✗'}")
        
        # Verify kostpris
        expected = f"{float(cost) * 1.75:.2f}"
        kostpris_success = (updated_kostpris == expected)
        console.print(f"\n[bold]Kostpris Verification:[/bold]")
        console.print(f"Expected: {expected}")
        console.print(f"Actual: {updated_kostpris}")
        console.print(f"Kostpris correct: {'✓' if kostpris_success else '✗'}")
        
        # Final summary
        if len(media_ids) >= 2:
            console.print(f"\n[bold green]SUMMARY:[/bold green]")
            console.print(f"✓ Images swapped: {swap_success}")
            console.print(f"✓ Kostpris updated: {kostpris_success}")
            if swap_success and kostpris_success:
                console.print("[bold green]🎉 ALL CHANGES APPLIED SUCCESSFULLY! 🎉[/bold green]")
            else:
                console.print("[bold red]❌ SOME CHANGES FAILED[/bold red]")

if __name__ == "__main__":
    main()
