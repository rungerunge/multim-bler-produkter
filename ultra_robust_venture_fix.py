#!/usr/bin/env python3
"""
Ultra robust Venture Design updater with aggressive rate limiting and retry logic
"""

import os
import json
import requests
import argparse
import time
import random
from datetime import datetime
from typing import Dict, List, Set, Tuple
from rich.console import Console
from rich.table import Table
from tqdm import tqdm

console = Console()

def get_env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise Exception(f"Missing environment variable: {name}")
    return v

class UltraRobustVentureUpdater:
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
        self.image_updated = set()
        self.price_updated = set()
        self.failed = {}
        self.progress_file = "logs/ultra_progress.json"
        self.load_progress()
        
        # Rate limiting counters
        self.requests_this_minute = 0
        self.last_minute_reset = time.time()
        self.consecutive_failures = 0
    
    def load_progress(self):
        """Load previous progress"""
        os.makedirs("logs", exist_ok=True)
        if os.path.exists(self.progress_file):
            with open(self.progress_file, 'r') as f:
                data = json.load(f)
                self.processed = set(data.get("processed", []))
                self.image_updated = set(data.get("image_updated", []))
                self.price_updated = set(data.get("price_updated", []))
                self.failed = data.get("failed", {})
                console.print(f"[blue]Loaded progress: {len(self.processed)} processed, {len(self.failed)} failed[/blue]")
    
    def save_progress(self):
        """Save progress"""
        data = {
            "last_update": datetime.now().isoformat(),
            "processed": list(self.processed),
            "image_updated": list(self.image_updated),
            "price_updated": list(self.price_updated),
            "failed": self.failed,
            "stats": {
                "total_processed": len(self.processed),
                "images_updated": len(self.image_updated),
                "prices_updated": len(self.price_updated),
                "failures": len(self.failed)
            }
        }
        
        with open(self.progress_file, 'w') as f:
            json.dump(data, f, indent=2)
    
    def rate_limit_check(self):
        """Aggressive rate limiting"""
        current_time = time.time()
        
        # Reset counter every minute
        if current_time - self.last_minute_reset > 60:
            self.requests_this_minute = 0
            self.last_minute_reset = current_time
        
        # Shopify allows 40 requests per app per store per minute
        # We'll use max 20 to be safe
        if self.requests_this_minute >= 20:
            sleep_time = 60 - (current_time - self.last_minute_reset)
            if sleep_time > 0:
                console.print(f"[yellow]Rate limiting: sleeping {sleep_time:.1f}s[/yellow]")
                time.sleep(sleep_time)
                self.requests_this_minute = 0
                self.last_minute_reset = time.time()
        
        # Extra delay based on consecutive failures
        if self.consecutive_failures > 0:
            delay = min(5, self.consecutive_failures * 0.5)
            time.sleep(delay + random.uniform(0.1, 0.5))
        else:
            # Base delay between requests
            time.sleep(random.uniform(1.5, 3.0))
        
        self.requests_this_minute += 1
    
    def fetch_venture_products_batch(self, cursor: str = None) -> Tuple[List[Dict], str]:
        """Fetch a single batch of products"""
        query = '''
        query VentureProducts($cursor: String) {
          products(first: 25, after: $cursor, query: "vendor:\\"Venture Design\\"") {
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
        
        self.rate_limit_check()
        
        try:
            resp = requests.post(self.graphql_url, headers=self.headers, json={
                "query": query, 
                "variables": {"cursor": cursor}
            }, timeout=30)
            
            if resp.status_code == 429:
                console.print("[red]Rate limited by Shopify - backing off[/red]")
                time.sleep(10)
                return [], cursor
            
            if not resp.ok:
                console.print(f"[red]GraphQL request failed: {resp.status_code}[/red]")
                return [], cursor
            
            data = resp.json()
            if "errors" in data:
                console.print(f"[red]GraphQL errors: {data['errors']}[/red]")
                return [], cursor
            
            conn = data["data"]["products"]
            products = []
            
            for edge in conn["edges"]:
                node = edge["node"]
                if node["vendor"].lower() == "venture design":
                    products.append(node)
            
            next_cursor = conn["pageInfo"]["endCursor"] if conn["pageInfo"]["hasNextPage"] else None
            self.consecutive_failures = 0
            return products, next_cursor
            
        except Exception as e:
            self.consecutive_failures += 1
            console.print(f"[red]Error fetching batch: {e}[/red]")
            return [], cursor
    
    def swap_images_robust(self, product_id: str, title: str) -> bool:
        """Robust image swapping with retries"""
        if self.dry_run:
            return True
            
        if product_id in self.image_updated:
            return True
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                self.rate_limit_check()
                
                # Get current images
                rest_url = f"https://{self.domain}/admin/api/2024-07/products/{product_id}.json"
                resp = requests.get(rest_url, headers=self.headers, timeout=15)
                
                if resp.status_code == 429:
                    console.print(f"[yellow]Rate limited getting images for {title} - attempt {attempt+1}[/yellow]")
                    time.sleep(10 + attempt * 5)
                    continue
                
                if not resp.ok:
                    console.print(f"[red]Failed to get product {title}: {resp.status_code}[/red]")
                    if attempt < max_retries - 1:
                        time.sleep(5)
                        continue
                    return False
                
                product_data = resp.json()["product"]
                images = product_data.get("images", [])
                
                if len(images) < 2:
                    self.image_updated.add(product_id)
                    return True
                
                first_img = images[0]
                second_img = images[1]
                
                # Update first image to position 2
                self.rate_limit_check()
                update_url1 = f"https://{self.domain}/admin/api/2024-07/products/{product_id}/images/{first_img['id']}.json"
                update_data1 = {"image": {"id": first_img['id'], "position": 2}}
                resp1 = requests.put(update_url1, headers=self.headers, json=update_data1, timeout=15)
                
                if resp1.status_code == 429:
                    console.print(f"[yellow]Rate limited updating image 1 for {title} - attempt {attempt+1}[/yellow]")
                    time.sleep(10 + attempt * 5)
                    continue
                
                # Delay between image updates
                time.sleep(1)
                
                # Update second image to position 1
                self.rate_limit_check()
                update_url2 = f"https://{self.domain}/admin/api/2024-07/products/{product_id}/images/{second_img['id']}.json"
                update_data2 = {"image": {"id": second_img['id'], "position": 1}}
                resp2 = requests.put(update_url2, headers=self.headers, json=update_data2, timeout=15)
                
                if resp2.status_code == 429:
                    console.print(f"[yellow]Rate limited updating image 2 for {title} - attempt {attempt+1}[/yellow]")
                    time.sleep(10 + attempt * 5)
                    continue
                
                if resp1.ok and resp2.ok:
                    self.image_updated.add(product_id)
                    self.consecutive_failures = 0
                    return True
                else:
                    console.print(f"[red]Image swap failed for {title}: {resp1.status_code}/{resp2.status_code}[/red]")
                    if attempt < max_retries - 1:
                        time.sleep(3 + attempt * 2)
                        continue
                    return False
                
            except Exception as e:
                self.consecutive_failures += 1
                console.print(f"[red]Image swap exception for {title} (attempt {attempt+1}): {e}[/red]")
                if attempt < max_retries - 1:
                    time.sleep(5 + attempt * 3)
                    continue
                return False
        
        return False
    
    def update_price_robust(self, variant_id: str, product_title: str, cost: float) -> bool:
        """Robust price updating with retries"""
        if self.dry_run:
            return True
            
        product_id = variant_id.split("/")[-2]  # Extract product ID from variant ID
        if product_id in self.price_updated:
            return True
            
        expected_price = f"{cost * 2.20:.2f}"
        numeric_variant_id = variant_id.split("/")[-1]
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                self.rate_limit_check()
                
                variant_url = f"https://{self.domain}/admin/api/2024-07/variants/{numeric_variant_id}.json"
                
                update_data = {
                    "variant": {
                        "id": int(numeric_variant_id),
                        "price": expected_price
                    }
                }
                
                resp = requests.put(variant_url, headers=self.headers, json=update_data, timeout=15)
                
                if resp.status_code == 429:
                    console.print(f"[yellow]Rate limited updating price for {product_title} - attempt {attempt+1}[/yellow]")
                    time.sleep(15 + attempt * 5)
                    continue
                
                if resp.ok:
                    self.price_updated.add(product_id)
                    self.consecutive_failures = 0
                    return True
                else:
                    console.print(f"[red]Price update failed for {product_title}: {resp.status_code}[/red]")
                    if attempt < max_retries - 1:
                        time.sleep(3 + attempt * 2)
                        continue
                    return False
                
            except Exception as e:
                self.consecutive_failures += 1
                console.print(f"[red]Price update exception for {product_title} (attempt {attempt+1}): {e}[/red]")
                if attempt < max_retries - 1:
                    time.sleep(5 + attempt * 3)
                    continue
                return False
        
        return False
    
    def process_product(self, product: Dict) -> bool:
        """Process a single product with full error handling"""
        product_id = product["legacyResourceId"]
        title = product["title"]
        
        if product_id in self.processed:
            return True
        
        console.print(f"[cyan]Processing: {title}[/cyan]")
        
        image_success = True
        price_success = True
        
        # Try image swap
        if not self.swap_images_robust(product_id, title):
            image_success = False
            self.failed[product_id] = "Image swap failed"
        
        # Try price update
        if product["variants"]["edges"]:
            variant = product["variants"]["edges"][0]["node"]
            variant_id = variant["id"]
            cost = variant["inventoryItem"]["unitCost"]["amount"]
            
            if cost:
                if not self.update_price_robust(variant_id, title, float(cost)):
                    price_success = False
                    self.failed[product_id] = "Price update failed"
        
        # Mark as processed if at least one operation succeeded
        overall_success = image_success or price_success
        if overall_success:
            self.processed.add(product_id)
        
        # Save progress every 5 products
        if len(self.processed) % 5 == 0:
            self.save_progress()
        
        return overall_success
    
    def run(self, limit: int = 0):
        """Main execution with batch processing"""
        console.print(f"[bold]{'DRY RUN - ' if self.dry_run else ''}Ultra Robust Venture Design Updater[/bold]")
        
        # Fetch products in batches
        all_products = []
        cursor = None
        
        console.print("[blue]Fetching products in small batches...[/blue]")
        
        while True:
            products, cursor = self.fetch_venture_products_batch(cursor)
            if not products and cursor is None:
                break
            
            all_products.extend(products)
            console.print(f"Fetched {len(all_products)} products so far...")
            
            if limit and len(all_products) >= limit:
                all_products = all_products[:limit]
                break
            
            if cursor is None:
                break
            
            # Small delay between batches
            time.sleep(2)
        
        console.print(f"Total products found: {len(all_products)}")
        
        # Filter unprocessed
        remaining = [p for p in all_products if p["legacyResourceId"] not in self.processed]
        console.print(f"Remaining to process: {len(remaining)}")
        
        if not remaining:
            console.print("[green]All products already processed![/green]")
            return
        
        # Process products one by one
        success_count = 0
        error_count = 0
        
        for i, product in enumerate(remaining):
            try:
                if self.process_product(product):
                    success_count += 1
                else:
                    error_count += 1
                    
                # Progress report every 10 products
                if (i + 1) % 10 == 0:
                    console.print(f"Progress: {i+1}/{len(remaining)} - Success: {success_count}, Errors: {error_count}")
                    
            except Exception as e:
                error_count += 1
                console.print(f"[red]Critical error processing {product['title']}: {e}[/red]")
        
        # Final save and results
        self.save_progress()
        
        console.print(f"\n[green]Final results: {success_count} success, {error_count} errors[/green]")
        console.print(f"Total processed: {len(self.processed)}")
        console.print(f"Images updated: {len(self.image_updated)}")
        console.print(f"Prices updated: {len(self.price_updated)}")

def main():
    parser = argparse.ArgumentParser(description="Ultra robust Venture Design updater")
    parser.add_argument("--apply", action="store_true", help="Apply changes")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    parser.add_argument("--limit", type=int, default=0, help="Limit products")
    parser.add_argument("--reset", action="store_true", help="Reset progress")
    args = parser.parse_args()

    dry_run = args.dry_run or not args.apply

    if args.reset:
        if os.path.exists("logs/ultra_progress.json"):
            os.remove("logs/ultra_progress.json")
            console.print("[yellow]Progress reset[/yellow]")

    try:
        domain = get_env("SHOPIFY_DOMAIN")
        token = get_env("SHOPIFY_TOKEN")
        
        updater = UltraRobustVentureUpdater(domain, token, dry_run)
        updater.run(args.limit)
        
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise

if __name__ == "__main__":
    main()
