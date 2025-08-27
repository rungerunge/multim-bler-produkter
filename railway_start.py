#!/usr/bin/env python3
"""
Railway startup script for Venture Design updater.
This script will run continuously on Railway and process all Venture Design products.
"""

import os
import sys
import time
import signal

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from improved_venture_fix import ImprovedVentureUpdater
except ImportError as e:
    print(f"❌ Import error: {e}")
    print("📁 Current directory:", os.getcwd())
    print("📄 Files in directory:", os.listdir('.'))
    sys.exit(1)

def signal_handler(sig, frame):
    print('Gracefully shutting down...')
    sys.exit(0)

def main():
    print("🚀 Starting Venture Design updater on Railway...")
    print(f"⏰ Start time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Register signal handler for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Check environment variables
    domain = os.getenv("SHOPIFY_DOMAIN")
    token = os.getenv("SHOPIFY_TOKEN")
    
    if not domain or not token:
        print("❌ Missing required environment variables:")
        print("   SHOPIFY_DOMAIN")
        print("   SHOPIFY_TOKEN")
        print("🔧 Set these in Railway dashboard under 'Variables' tab")
        sys.exit(1)
    
    print(f"📊 Domain: {domain}")
    print(f"🔑 Token: {token[:10]}..." if token else "❌ No token")
    
    # Keep script running and auto-restart on completion
    max_retries = 3
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            # Create updater and run
            updater = ImprovedVentureUpdater(domain, token, dry_run=False)
            
            print(f"🔄 Starting full update of all Venture Design products... (Attempt {retry_count + 1}/{max_retries})")
            updater.run()
            
            print("✅ Update completed successfully!")
            
            # Sleep for a while before checking again (prevent constant running)
            print("😴 Sleeping for 1 hour before next check...")
            time.sleep(3600)  # 1 hour
            
            # Reset retry count on successful completion
            retry_count = 0
            
        except KeyboardInterrupt:
            print("\n⚠️ Update interrupted by user")
            break
        except Exception as e:
            retry_count += 1
            print(f"❌ Error during update (attempt {retry_count}/{max_retries}): {e}")
            
            if retry_count < max_retries:
                wait_time = 60 * retry_count  # Progressive backoff
                print(f"🔄 Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                print("❌ Max retries reached. Exiting.")
                raise

if __name__ == "__main__":
    main()
