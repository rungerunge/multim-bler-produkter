#!/usr/bin/env python3
"""
Robust Venture Design fix with resume capability
- Updates both image order (swap 1<->2) and prices (cost * 2.20)
- Saves progress to JSON file for resume functionality
- Uses REST API for reliable updates
- Comprehensive logging and error handling
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

class VentureProgressTracker:
    def __init__(self, progress_file: str = "logs/venture_progress.json"):
        self.progress_file = progress_file
        self.processed_products: Set[str] = set()
        self.updated_images: Set[str] = set()
        self.updated_prices: Set[str] = set()
        self.failed_products: Dict[str, str] = {}
        self.start_time = datetime.now().isoformat()
        
        # Create logs directory
        os.makedirs("logs", exist_ok=True)
        
        # Load existing progress
        self.load_progress()
    
    def load_progress(self):
        """Load previous progress if exists"""
        if os.path.exists(self.progress_file):
            with open(self.progress_file, 'r') as f:
                data = json.load(f)
                self.processed_products = set(data.get("processed_products", []))
                self.updated_images = set(data.get("updated_images", []))
                self.updated_prices = set(data.get("updated_prices", []))
                self.failed_products = data.get("failed_products", {})
                console.print(f"[blue]Loaded progress: {len(self.processed_products)} products processed[/blue]")
    
    def save_progress(self):
        """Save current progress"""
        data = {
            "last_update": datetime.now().isoformat(),
            "start_time": self.start_time,
            "processed_products": list(self.processed_products),
            "updated_images": list(self.updated_images),
            "updated_prices": list(self.updated_prices),
            "failed_products": self.failed_products,
            "stats": {
                "total_processed": len(self.processed_products),
                "images_updated": len(self.updated_images),
                "prices_updated": len(self.updated_prices),
                "failures": len(self.failed_products)
            }
        }
        
        with open(self.progress_file, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    def mark_processed(self, product_id: str):
        self.processed_products.add(product_id)
        self.save_progress()
    
    def mark_image_updated(self, product_id: str):
        self.updated_images.add(product_id)
        self.save_progress()
    
    def mark_price_updated(self, product_id: str):
        self.updated_prices.add(product_id)
        self.save_progress()
    
    def mark_failed(self, product_id: str, error: str):
        self.failed_products[product_id] = error
        self.save_progress()
    
    def is_processed(self, product_id: str) -> bool:
        return product_id in self.processed_products
    
    def get_stats(self) -> Dict:
        return {
            "processed": len(self.processed_products),
            "images_updated": len(self.updated_images),
            "prices_updated": len(self.updated_prices),
            "failed": len(self.failed_products)
        }

class VentureDesignUpdater:
    def __init__(self, domain: str, token: str, dry_run: bool = False):
        self.domain = domain
        self.token = token
        self.dry_run = dry_run
        self.headers = {
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json"
        }
        self.graphql_url = f"https://{domain}/admin/api/2024-07/graphql.json"
        self.tracker = VentureProgressTracker()
    
    def fetch_venture_products(self) -> List[Dict]:
        """Fetch all Venture Design products using GraphQL"""
        query = '''
        query VentureProducts($cursor: String) {
          products(first: 100, after: $cursor, query: "vendor:\\"Venture Design\\"") {
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
        
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}")) as progress:
            task = progress.add_task("Fetching products...", total=None)
            
            while True:
                resp = requests.post(self.graphql_url, headers=self.headers, json={
                    "query": query, 
                    "variables": {"cursor": cursor}
                }, timeout=30)
                
                if not resp.ok:
                    raise Exception(f"GraphQL request failed: {resp.status_code}")
                
                data = resp.json()
                if "errors" in data:
                    raise Exception(f"GraphQL errors: {data['errors']}")
                
                conn = data["data"]["products"]
                
                for edge in conn["edges"]:
                    node = edge["node"]
                    if node["vendor"].lower() == "venture design":
                        products.append(node)
                
                progress.update(task, description=f"Fetched {len(products)} products...")
                
                if not conn["pageInfo"]["hasNextPage"]:
                    break
                cursor = conn["pageInfo"]["endCursor"]
        
        return products
    
    def swap_product_images(self, product_id: str, title: str) -> bool:
        """Swap first two images using REST API"""
        if self.dry_run:
            console.print(f"[yellow]DRY RUN:[/yellow] Would swap images for {title}")
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
            
            # Update second image to position 1
            update_url2 = f"https://{self.domain}/admin/api/2024-07/products/{product_id}/images/{second_img['id']}.json"
            update_data2 = {"image": {"id": second_img['id'], "position": 1}}
            resp2 = requests.put(update_url2, headers=self.headers, json=update_data2, timeout=10)
            
            return resp1.ok and resp2.ok
            
        except Exception as e:
            console.print(f"[red]Image swap error for {title}: {e}[/red]")
            return False
    
    def update_variant_price(self, variant_id: str, product_title: str, cost: float) -> bool:
        """Update variant price using REST API"""
        expected_price = cost * 2.20  # +120%
        price_str = f"{expected_price:.2f}"
        
        if self.dry_run:
            console.print(f"[yellow]DRY RUN:[/yellow] Would set price to {price_str} DKK for {product_title}")
            return True
        
        # Extract numeric variant ID from GraphQL ID
        numeric_variant_id = variant_id.split("/")[-1]
        variant_url = f"https://{self.domain}/admin/api/2024-07/variants/{numeric_variant_id}.json"
        
        try:
            update_data = {
                "variant": {
                    "id": int(numeric_variant_id),
                    "price": price_str
                }
            }
            
            resp = requests.put(variant_url, headers=self.headers, json=update_data, timeout=10)
            return resp.ok
            
        except Exception as e:
            console.print(f"[red]Price update error for {product_title}: {e}[/red]")
            return False
    
    def process_product(self, product: Dict) -> Dict[str, bool]:
        """Process a single product - swap images and update price"""
        product_id = product["legacyResourceId"]
        title = product["title"]
        
        # Skip if already processed
        if self.tracker.is_processed(product_id):
            return {"skipped": True, "images": True, "price": True}
        
        results = {"skipped": False, "images": False, "price": False}
        
        # Swap images
        if product_id not in self.tracker.updated_images:
            success = self.swap_product_images(product_id, title)
            if success:
                self.tracker.mark_image_updated(product_id)
                results["images"] = True
            else:
                self.tracker.mark_failed(product_id, "Image swap failed")
        else:
            results["images"] = True
        
        # Update price
        if product["variants"]["edges"] and product_id not in self.tracker.updated_prices:
            variant = product["variants"]["edges"][0]["node"]
            variant_id = variant["id"]
            cost = variant["inventoryItem"]["unitCost"]["amount"]
            
            if cost:
                success = self.update_variant_price(variant_id, title, float(cost))
                if success:
                    self.tracker.mark_price_updated(product_id)
                    results["price"] = True
                else:
                    self.tracker.mark_failed(product_id, "Price update failed")
            else:
                results["price"] = True  # No cost to update
        else:
            results["price"] = True
        
        # Mark as processed if both operations succeeded
        if results["images"] and results["price"]:
            self.tracker.mark_processed(product_id)
        
        return results
    
    def run(self, limit: int = 0):
        """Main execution function"""
        console.print(f"[bold]{'DRY RUN - ' if self.dry_run else ''}Venture Design Updater[/bold]")
        
        # Fetch products
        products = self.fetch_venture_products()
        console.print(f"Found {len(products)} Venture Design products")
        
        if limit:
            products = products[:limit]
            console.print(f"Limited to {limit} products")
        
        # Filter out already processed
        remaining_products = [p for p in products if not self.tracker.is_processed(p["legacyResourceId"])]
        console.print(f"Remaining to process: {len(remaining_products)}")
        
        if not remaining_products:
            console.print("[green]All products already processed![/green]")
            return
        
        # Process products
        success_count = 0
        error_count = 0
        
        with tqdm(remaining_products, desc="Processing", unit="product") as pbar:
            for product in pbar:
                try:
                    results = self.process_product(product)
                    
                    if results["skipped"]:
                        continue
                    
                    if results["images"] and results["price"]:
                        success_count += 1
                    else:
                        error_count += 1
                    
                    pbar.set_postfix({
                        "Success": success_count,
                        "Errors": error_count
                    })
                    
                    # Brief pause to avoid rate limiting
                    time.sleep(0.1)
                    
                except Exception as e:
                    error_count += 1
                    product_id = product["legacyResourceId"]
                    self.tracker.mark_failed(product_id, str(e))
                    console.print(f"[red]Error processing {product['title']}: {e}[/red]")
        
        # Final stats
        stats = self.tracker.get_stats()
        
        results_table = Table(title="Final Results")
        results_table.add_column("Metric", style="bold")
        results_table.add_column("Count", justify="right")
        results_table.add_row("Total processed", str(stats["processed"]))
        results_table.add_row("Images updated", str(stats["images_updated"]))
        results_table.add_row("Prices updated", str(stats["prices_updated"]))
        results_table.add_row("Failed", str(stats["failed"]))
        
        console.print(results_table)
        
        if stats["failed"] > 0:
            console.print(f"\n[yellow]Check {self.tracker.progress_file} for failed products[/yellow]")
        
        if not self.dry_run and stats["processed"] > 0:
            console.print(f"\n[green]🎉 Successfully updated {stats['processed']} Venture Design products![/green]")

def main():
    parser = argparse.ArgumentParser(description="Robust Venture Design product updater")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default is dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes only")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of products to process")
    parser.add_argument("--reset", action="store_true", help="Reset progress and start from beginning")
    args = parser.parse_args()

    # Determine if this is a dry run
    dry_run = args.dry_run or not args.apply

    if args.reset:
        progress_file = "logs/venture_progress.json"
        if os.path.exists(progress_file):
            os.remove(progress_file)
            console.print("[yellow]Progress reset - starting from beginning[/yellow]")

    try:
        domain = get_env("SHOPIFY_DOMAIN")
        token = get_env("SHOPIFY_TOKEN")
        
        updater = VentureDesignUpdater(domain, token, dry_run)
        updater.run(args.limit)
        
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise

if __name__ == "__main__":
    main()
