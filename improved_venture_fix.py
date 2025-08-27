#!/usr/bin/env python3
"""
Improved Venture Design fix with better error handling and GraphQL for prices
"""

import os
import json
import requests
import argparse
import time
from datetime import datetime
from typing import Dict, List, Set, Tuple
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from tqdm import tqdm

console = Console()

def get_env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise Exception(f"Missing environment variable: {name}")
    return v

class ImprovedVentureUpdater:
    def __init__(self, domain: str, token: str, dry_run: bool = False):
        self.domain = domain
        self.token = token
        self.dry_run = dry_run
        self.headers = {
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json"
        }
        self.graphql_url = f"https://{domain}/admin/api/2024-07/graphql.json"
        
        # Progress tracking
        self.processed = set()
        self.progress_file = "logs/improved_progress.json"
        self.load_progress()
    
    def load_progress(self):
        """Load previous progress"""
        os.makedirs("logs", exist_ok=True)
        if os.path.exists(self.progress_file):
            with open(self.progress_file, 'r') as f:
                data = json.load(f)
                self.processed = set(data.get("processed", []))
                console.print(f"[blue]Loaded progress: {len(self.processed)} products already processed[/blue]")
    
    def save_progress(self, product_id: str, success: bool, error: str = ""):
        """Save progress"""
        if success:
            self.processed.add(product_id)
        
        # Save to file
        data = {
            "last_update": datetime.now().isoformat(),
            "processed": list(self.processed),
            "total_processed": len(self.processed)
        }
        
        with open(self.progress_file, 'w') as f:
            json.dump(data, f, indent=2)
    
    def fetch_venture_products(self) -> List[Dict]:
        """Fetch all Venture Design products"""
        query = '''
        query VentureProducts($cursor: String) {
          products(first: 50, after: $cursor, query: "vendor:\\"Venture Design\\"") {
            pageInfo { hasNextPage endCursor }
            edges {
              node {
                id
                legacyResourceId
                title
                handle
                vendor
                variants(first: 1) {
                  edges {
                    node {
                      id
                      price
                      inventoryItem { unitCost { amount } }
                    }
                  }
                }
              }
            }
          }
        }
        '''
        
        products = []
        cursor = None
        
        while True:
            try:
                resp = requests.post(self.graphql_url, headers=self.headers, json={
                    "query": query, 
                    "variables": {"cursor": cursor}
                }, timeout=30)
                
                if not resp.ok:
                    console.print(f"[red]GraphQL request failed: {resp.status_code}[/red]")
                    break
                
                data = resp.json()
                if "errors" in data:
                    console.print(f"[red]GraphQL errors: {data['errors']}[/red]")
                    break
                
                conn = data["data"]["products"]
                
                for edge in conn["edges"]:
                    node = edge["node"]
                    if node["vendor"].lower() == "venture design":
                        products.append(node)
                
                if not conn["pageInfo"]["hasNextPage"]:
                    break
                cursor = conn["pageInfo"]["endCursor"]
                
                time.sleep(0.5)  # Rate limiting
                
            except Exception as e:
                console.print(f"[red]Error fetching products: {e}[/red]")
                break
        
        return products
    
    def swap_images_rest(self, product_id: str, title: str) -> bool:
        """Swap images using REST API"""
        if self.dry_run:
            return True
            
        rest_url = f"https://{self.domain}/admin/api/2024-07/products/{product_id}.json"
        
        try:
            # Get current images
            resp = requests.get(rest_url, headers=self.headers, timeout=10)
            if not resp.ok:
                return False
            
            product_data = resp.json()["product"]
            images = product_data.get("images", [])
            
            if len(images) < 2:
                return True  # Nothing to swap
            
            first_img = images[0]
            second_img = images[1]
            
            # Update first image to position 2
            update_url1 = f"https://{self.domain}/admin/api/2024-07/products/{product_id}/images/{first_img['id']}.json"
            update_data1 = {"image": {"id": first_img['id'], "position": 2}}
            resp1 = requests.put(update_url1, headers=self.headers, json=update_data1, timeout=10)
            
            # Small delay
            time.sleep(0.1)
            
            # Update second image to position 1
            update_url2 = f"https://{self.domain}/admin/api/2024-07/products/{product_id}/images/{second_img['id']}.json"
            update_data2 = {"image": {"id": second_img['id'], "position": 1}}
            resp2 = requests.put(update_url2, headers=self.headers, json=update_data2, timeout=10)
            
            return resp1.ok and resp2.ok
            
        except Exception as e:
            console.print(f"[red]Image swap error for {title}: {e}[/red]")
            return False
    
    def update_price_rest(self, variant_id: str, product_title: str, cost: float) -> bool:
        """Update price using REST API (more reliable)"""
        if self.dry_run:
            return True
            
        expected_price = f"{cost * 1.75:.2f}"
        
        # Extract numeric variant ID from GraphQL ID
        numeric_variant_id = variant_id.split("/")[-1]
        variant_url = f"https://{self.domain}/admin/api/2024-07/variants/{numeric_variant_id}.json"
        
        try:
            update_data = {
                "variant": {
                    "id": int(numeric_variant_id),
                    "price": expected_price
                }
            }
            
            resp = requests.put(variant_url, headers=self.headers, json=update_data, timeout=10)
            
            if not resp.ok:
                console.print(f"[red]REST price update failed for {product_title}: {resp.status_code} - {resp.text[:100]}[/red]")
                return False
            
            return True
            
        except Exception as e:
            console.print(f"[red]Price update exception for {product_title}: {e}[/red]")
            return False
    
    def process_product(self, product: Dict) -> bool:
        """Process a single product"""
        product_id = product["legacyResourceId"]
        title = product["title"]
        
        # Skip if already processed
        if product_id in self.processed:
            return True
        
        console.print(f"[cyan]Processing: {title}[/cyan]")
        
        success = True
        
        # 1. Swap images
        if not self.swap_images_rest(product_id, title):
            console.print(f"[yellow]Image swap failed for {title}[/yellow]")
            success = False
        
        # Small delay between operations
        time.sleep(0.2)
        
        # 2. Update price
        if product["variants"]["edges"]:
            variant = product["variants"]["edges"][0]["node"]
            variant_id = variant["id"]
            cost = variant["inventoryItem"]["unitCost"]["amount"]
            
            if cost:
                if not self.update_price_rest(variant_id, title, float(cost)):
                    console.print(f"[yellow]Price update failed for {title}[/yellow]")
                    success = False
        
        # Save progress
        self.save_progress(product_id, success)
        
        # Rate limiting
        time.sleep(0.3)
        
        return success
    
    def run(self, limit: int = 0):
        """Main execution"""
        console.print(f"[bold]{'DRY RUN - ' if self.dry_run else ''}Improved Venture Design Updater[/bold]")
        
        # Fetch products
        products = self.fetch_venture_products()
        console.print(f"Found {len(products)} Venture Design products")
        
        if limit:
            products = products[:limit]
            console.print(f"Limited to {limit} products")
        
        # Filter unprocessed
        remaining = [p for p in products if p["legacyResourceId"] not in self.processed]
        console.print(f"Remaining to process: {len(remaining)}")
        
        if not remaining:
            console.print("[green]All products already processed![/green]")
            return
        
        # Process products
        success_count = 0
        error_count = 0
        
        for product in tqdm(remaining, desc="Processing"):
            try:
                if self.process_product(product):
                    success_count += 1
                else:
                    error_count += 1
                    
            except Exception as e:
                error_count += 1
                console.print(f"[red]Error processing {product['title']}: {e}[/red]")
                
            # Progress update
            if (success_count + error_count) % 10 == 0:
                console.print(f"Progress: {success_count} success, {error_count} errors")
        
        # Final results
        console.print(f"\n[green]Completed: {success_count} success, {error_count} errors[/green]")

def main():
    parser = argparse.ArgumentParser(description="Improved Venture Design updater")
    parser.add_argument("--apply", action="store_true", help="Apply changes")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    parser.add_argument("--limit", type=int, default=0, help="Limit products")
    parser.add_argument("--reset", action="store_true", help="Reset progress")
    args = parser.parse_args()

    dry_run = args.dry_run or not args.apply

    if args.reset:
        if os.path.exists("logs/improved_progress.json"):
            os.remove("logs/improved_progress.json")
            console.print("[yellow]Progress reset[/yellow]")

    try:
        domain = get_env("SHOPIFY_DOMAIN")
        token = get_env("SHOPIFY_TOKEN")
        
        updater = ImprovedVentureUpdater(domain, token, dry_run)
        updater.run(args.limit)
        
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise

if __name__ == "__main__":
    main()
