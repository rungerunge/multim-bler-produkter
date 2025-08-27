#!/usr/bin/env python3
"""
Persistent Venture Design updater - NEVER GIVES UP!
Designed to work alongside other apps using the same API with aggressive retries.
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

console = Console()

def get_env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise Exception(f"Missing environment variable: {name}")
    return v

class PersistentVentureUpdater:
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
        self.progress_file = "logs/persistent_progress.json"
        self.processed = set()
        self.load_progress()
        
        # Statistics
        self.total_requests = 0
        self.rate_limited_count = 0
        self.retry_count = 0
    
    def load_progress(self):
        """Load previous progress"""
        os.makedirs("logs", exist_ok=True)
        if os.path.exists(self.progress_file):
            with open(self.progress_file, 'r') as f:
                data = json.load(f)
                self.processed = set(data.get("processed", []))
                console.print(f"[blue]Resuming: {len(self.processed)} products already completed[/blue]")
    
    def save_progress(self, product_id: str):
        """Save progress immediately"""
        self.processed.add(product_id)
        data = {
            "last_update": datetime.now().isoformat(),
            "processed": list(self.processed),
            "total_completed": len(self.processed),
            "stats": {
                "total_requests": self.total_requests,
                "rate_limited_count": self.rate_limited_count,
                "retry_count": self.retry_count
            }
        }
        
        with open(self.progress_file, 'w') as f:
            json.dump(data, f, indent=2)
    
    def wait_with_backoff(self, attempt: int, base_delay: float = 2.0):
        """Exponential backoff with jitter"""
        delay = base_delay * (2 ** min(attempt, 6)) + random.uniform(0, 2)
        console.print(f"[yellow]Waiting {delay:.1f}s before retry (attempt {attempt + 1})[/yellow]")
        time.sleep(delay)
    
    def make_request_persistent(self, method: str, url: str, json_data: dict = None, max_retries: int = 10) -> requests.Response:
        """Make a request that NEVER gives up"""
        attempt = 0
        
        while attempt < max_retries:
            try:
                self.total_requests += 1
                
                if method.upper() == "GET":
                    response = requests.get(url, headers=self.headers, timeout=30)
                elif method.upper() == "PUT":
                    response = requests.put(url, headers=self.headers, json=json_data, timeout=30)
                elif method.upper() == "POST":
                    response = requests.post(url, headers=self.headers, json=json_data, timeout=30)
                else:
                    raise ValueError(f"Unsupported method: {method}")
                
                if response.status_code == 429:
                    self.rate_limited_count += 1
                    console.print(f"[red]Rate limited (#{self.rate_limited_count}) - backing off[/red]")
                    time.sleep(30 + random.uniform(0, 10))  # Wait 30-40 seconds
                    attempt += 1
                    continue
                
                if response.ok:
                    if attempt > 0:
                        console.print(f"[green]Request succeeded after {attempt + 1} attempts[/green]")
                    return response
                
                # Other HTTP errors
                console.print(f"[red]HTTP {response.status_code}: {response.text[:100]}[/red]")
                self.wait_with_backoff(attempt)
                attempt += 1
                continue
                
            except requests.exceptions.Timeout:
                console.print(f"[red]Request timeout - attempt {attempt + 1}[/red]")
                self.wait_with_backoff(attempt)
                attempt += 1
                continue
                
            except requests.exceptions.ConnectionError:
                console.print(f"[red]Connection error - attempt {attempt + 1}[/red]")
                self.wait_with_backoff(attempt, 5.0)  # Longer delay for connection issues
                attempt += 1
                continue
                
            except Exception as e:
                console.print(f"[red]Request exception: {e} - attempt {attempt + 1}[/red]")
                self.wait_with_backoff(attempt)
                attempt += 1
                continue
        
        # If we get here, we've exhausted retries
        self.retry_count += 1
        console.print(f"[red]Request failed after {max_retries} attempts - will retry later[/red]")
        return None
    
    def fetch_all_products(self) -> List[Dict]:
        """Fetch ALL products with persistent retries"""
        console.print("[blue]Fetching all Venture Design products...[/blue]")
        
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
        page = 1
        
        while True:
            console.print(f"Fetching page {page}...")
            
            response = self.make_request_persistent("POST", self.graphql_url, {
                "query": query,
                "variables": {"cursor": cursor}
            })
            
            if not response:
                console.print("[red]Failed to fetch products - will retry in 60 seconds[/red]")
                time.sleep(60)
                continue
            
            try:
                data = response.json()
                if "errors" in data:
                    console.print(f"[red]GraphQL errors: {data['errors']}[/red]")
                    time.sleep(10)
                    continue
                
                conn = data["data"]["products"]
                
                for edge in conn["edges"]:
                    node = edge["node"]
                    if node["vendor"].lower() == "venture design":
                        products.append(node)
                
                console.print(f"Page {page}: {len(products)} total products found")
                
                if not conn["pageInfo"]["hasNextPage"]:
                    break
                    
                cursor = conn["pageInfo"]["endCursor"]
                page += 1
                
                # Small delay between pages
                time.sleep(random.uniform(1, 3))
                
            except Exception as e:
                console.print(f"[red]Error parsing response: {e}[/red]")
                time.sleep(10)
                continue
        
        console.print(f"[green]Successfully fetched {len(products)} Venture Design products[/green]")
        return products
    
    def swap_images_persistent(self, product_id: str, title: str) -> bool:
        """Swap images with infinite retries"""
        if self.dry_run:
            return True
        
        console.print(f"[cyan]Swapping images for: {title}[/cyan]")
        
        # Get product images
        while True:
            rest_url = f"https://{self.domain}/admin/api/2024-07/products/{product_id}.json"
            response = self.make_request_persistent("GET", rest_url)
            
            if response:
                try:
                    product_data = response.json()["product"]
                    images = product_data.get("images", [])
                    
                    if len(images) < 2:
                        console.print(f"[yellow]Not enough images to swap for {title}[/yellow]")
                        return True
                    
                    break
                except Exception as e:
                    console.print(f"[red]Error parsing product data: {e}[/red]")
                    time.sleep(5)
                    continue
            else:
                console.print("[red]Failed to get product data - retrying in 30s[/red]")
                time.sleep(30)
                continue
        
        first_img = images[0]
        second_img = images[1]
        
        # Update first image to position 2
        while True:
            update_url1 = f"https://{self.domain}/admin/api/2024-07/products/{product_id}/images/{first_img['id']}.json"
            update_data1 = {"image": {"id": first_img['id'], "position": 2}}
            response1 = self.make_request_persistent("PUT", update_url1, update_data1)
            
            if response1:
                break
            console.print("[red]Image 1 update failed - retrying in 10s[/red]")
            time.sleep(10)
        
        # Small delay between updates
        time.sleep(2)
        
        # Update second image to position 1
        while True:
            update_url2 = f"https://{self.domain}/admin/api/2024-07/products/{product_id}/images/{second_img['id']}.json"
            update_data2 = {"image": {"id": second_img['id'], "position": 1}}
            response2 = self.make_request_persistent("PUT", update_url2, update_data2)
            
            if response2:
                break
            console.print("[red]Image 2 update failed - retrying in 10s[/red]")
            time.sleep(10)
        
        console.print(f"[green]✓ Images swapped for {title}[/green]")
        return True
    
    def update_price_persistent(self, variant_id: str, title: str, cost: float) -> bool:
        """Update price with infinite retries"""
        if self.dry_run:
            return True
        
        expected_price = f"{cost * 1.75:.2f}"
        numeric_variant_id = variant_id.split("/")[-1]
        
        console.print(f"[cyan]Updating price for: {title} -> {expected_price} DKK[/cyan]")
        
        while True:
            variant_url = f"https://{self.domain}/admin/api/2024-07/variants/{numeric_variant_id}.json"
            update_data = {
                "variant": {
                    "id": int(numeric_variant_id),
                    "price": expected_price
                }
            }
            
            response = self.make_request_persistent("PUT", variant_url, update_data)
            
            if response:
                console.print(f"[green]✓ Price updated for {title}[/green]")
                return True
            
            console.print("[red]Price update failed - retrying in 15s[/red]")
            time.sleep(15)
    
    def process_product_persistent(self, product: Dict) -> bool:
        """Process a product with infinite persistence"""
        product_id = product["legacyResourceId"]
        title = product["title"]
        
        if product_id in self.processed:
            return True
        
        console.print(f"\n[bold blue]Processing: {title}[/bold blue]")
        
        # Swap images
        self.swap_images_persistent(product_id, title)
        
        # Update price
        if product["variants"]["edges"]:
            variant = product["variants"]["edges"][0]["node"]
            variant_id = variant["id"]
            cost = variant["inventoryItem"]["unitCost"]["amount"]
            
            if cost:
                self.update_price_persistent(variant_id, title, float(cost))
        
        # Mark as completed
        self.save_progress(product_id)
        console.print(f"[green]✓ Completed: {title}[/green]")
        
        # Delay between products to be nice to the API
        time.sleep(random.uniform(3, 6))
        
        return True
    
    def run(self, limit: int = 0):
        """Main execution - NEVER GIVES UP!"""
        console.print(f"[bold]{'DRY RUN - ' if self.dry_run else ''}Persistent Venture Design Updater[/bold]")
        console.print("[yellow]This script will NEVER give up - it retries until everything succeeds![/yellow]")
        
        # Fetch all products
        products = self.fetch_all_products()
        
        if limit:
            products = products[:limit]
            console.print(f"Limited to first {limit} products")
        
        # Filter unprocessed
        remaining = [p for p in products if p["legacyResourceId"] not in self.processed]
        
        console.print(f"\n[bold]Status:[/bold]")
        console.print(f"Total products: {len(products)}")
        console.print(f"Already completed: {len(self.processed)}")
        console.print(f"Remaining: {len(remaining)}")
        
        if not remaining:
            console.print("[green]🎉 ALL PRODUCTS COMPLETED! 🎉[/green]")
            return
        
        # Process remaining products
        for i, product in enumerate(remaining):
            console.print(f"\n[bold]Progress: {i+1}/{len(remaining)}[/bold]")
            
            try:
                self.process_product_persistent(product)
            except KeyboardInterrupt:
                console.print("\n[yellow]Interrupted by user - progress saved[/yellow]")
                break
            except Exception as e:
                console.print(f"[red]Unexpected error: {e} - continuing anyway[/red]")
                time.sleep(10)
                continue
        
        # Final status
        final_completed = len(self.processed)
        console.print(f"\n[green]Session complete![/green]")
        console.print(f"Total completed: {final_completed}")
        console.print(f"API requests made: {self.total_requests}")
        console.print(f"Rate limited: {self.rate_limited_count} times")
        console.print(f"Retries needed: {self.retry_count}")

def main():
    parser = argparse.ArgumentParser(description="Persistent Venture Design updater")
    parser.add_argument("--apply", action="store_true", help="Apply changes")
    parser.add_argument("--dry-run", action="store_true", help="Preview only") 
    parser.add_argument("--limit", type=int, default=0, help="Limit products")
    parser.add_argument("--reset", action="store_true", help="Reset progress")
    args = parser.parse_args()

    dry_run = args.dry_run or not args.apply

    if args.reset:
        if os.path.exists("logs/persistent_progress.json"):
            os.remove("logs/persistent_progress.json")
            console.print("[yellow]Progress reset[/yellow]")

    try:
        domain = get_env("SHOPIFY_DOMAIN")
        token = get_env("SHOPIFY_TOKEN")
        
        updater = PersistentVentureUpdater(domain, token, dry_run)
        updater.run(args.limit)
        
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped by user[/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        console.print("[yellow]Script will restart and continue from where it left off[/yellow]")

if __name__ == "__main__":
    main()
