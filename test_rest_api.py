#!/usr/bin/env python3
"""
Test image swap using REST API instead of GraphQL
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

def main():
    domain = get_env("SHOPIFY_DOMAIN")
    token = get_env("SHOPIFY_TOKEN")
    
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    }

    # First get product ID using GraphQL
    graphql_url = f"https://{domain}/admin/api/2024-07/graphql.json"
    
    find_query = '''
    {
      products(first: 1, query: "title:\\"Dakar Sofa Sæt Sort Sort\\"") {
        edges {
          node {
            id
            legacyResourceId
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
    
    resp = requests.post(graphql_url, headers=headers, json={"query": find_query})
    data = resp.json()
    
    if not data["data"]["products"]["edges"]:
        console.print("Product not found")
        return
    
    product = data["data"]["products"]["edges"][0]["node"]
    product_id = product["legacyResourceId"]  # REST API needs numeric ID
    
    console.print(f"Product ID (REST): {product_id}")
    console.print(f"Product ID (GraphQL): {product['id']}")
    
    # Get product using REST API to see current image order
    rest_url = f"https://{domain}/admin/api/2024-07/products/{product_id}.json"
    resp = requests.get(rest_url, headers=headers)
    
    if not resp.ok:
        console.print(f"REST GET failed: {resp.status_code} {resp.text}")
        return
    
    product_data = resp.json()["product"]
    images = product_data.get("images", [])
    
    console.print(f"\n[bold]Current images (REST API):[/bold]")
    for i, img in enumerate(images):
        console.print(f"  {i+1}. ID: {img['id']}, Position: {img['position']}, Alt: {img.get('alt', 'No alt')}")
    
    if len(images) < 2:
        console.print("Not enough images to swap")
        return
    
    # Swap positions of first two images
    first_img = images[0]
    second_img = images[1]
    
    console.print(f"\n[bold yellow]Swapping images...[/bold yellow]")
    console.print(f"Moving image {first_img['id']} from position {first_img['position']} to position 2")
    console.print(f"Moving image {second_img['id']} from position {second_img['position']} to position 1")
    
    # Update first image to position 2
    update_url1 = f"https://{domain}/admin/api/2024-07/products/{product_id}/images/{first_img['id']}.json"
    update_data1 = {"image": {"id": first_img['id'], "position": 2}}
    resp1 = requests.put(update_url1, headers=headers, json=update_data1)
    
    # Update second image to position 1  
    update_url2 = f"https://{domain}/admin/api/2024-07/products/{product_id}/images/{second_img['id']}.json"
    update_data2 = {"image": {"id": second_img['id'], "position": 1}}
    resp2 = requests.put(update_url2, headers=headers, json=update_data2)
    
    if resp1.ok and resp2.ok:
        console.print("[green]✓ Position updates sent[/green]")
    else:
        console.print(f"[red]Update failed: {resp1.status_code} / {resp2.status_code}[/red]")
        console.print(f"Response 1: {resp1.text}")
        console.print(f"Response 2: {resp2.text}")
        return
    
    # Verify the change
    import time
    time.sleep(3)
    
    resp = requests.get(rest_url, headers=headers)
    updated_product = resp.json()["product"]
    updated_images = updated_product.get("images", [])
    
    console.print(f"\n[bold]Updated images (REST API):[/bold]")
    for i, img in enumerate(updated_images):
        console.print(f"  {i+1}. ID: {img['id']}, Position: {img['position']}, Alt: {img.get('alt', 'No alt')}")
    
    # Check if swap worked
    if len(updated_images) >= 2:
        new_first = updated_images[0]
        new_second = updated_images[1]
        
        success = (new_first['id'] == second_img['id'] and new_second['id'] == first_img['id'])
        
        console.print(f"\n[bold]Verification:[/bold]")
        console.print(f"Original first: {first_img['id']}")
        console.print(f"Original second: {second_img['id']}")
        console.print(f"New first: {new_first['id']}")
        console.print(f"New second: {new_second['id']}")
        console.print(f"Swap successful: {'✓' if success else '✗'}")
        
        if success:
            console.print("[bold green]🎉 REST API swap worked! 🎉[/bold green]")
        else:
            console.print("[bold red]❌ REST API swap failed too[/bold red]")

if __name__ == "__main__":
    main()
