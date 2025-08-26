#!/usr/bin/env python3
"""
Railway startup script for Venture Design updater.
This script will run continuously on Railway and process all Venture Design products.
"""

import os
import sys
import time
import signal
from robust_venture_fix import VentureDesignUpdater

def signal_handler(sig, frame):
    print('Gracefully shutting down...')
    sys.exit(0)

def main():
    print("🚀 Starting Venture Design updater on Railway...")
    
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
        sys.exit(1)
    
    print(f"📊 Domain: {domain}")
    print(f"🔑 Token: {token[:10]}..." if token else "❌ No token")
    
    try:
        # Create updater and run
        updater = VentureDesignUpdater(domain, token, dry_run=False)
        
        print("🔄 Starting full update of all Venture Design products...")
        updater.run()
        
        print("✅ Update completed successfully!")
        
    except KeyboardInterrupt:
        print("\n⚠️ Update interrupted by user")
    except Exception as e:
        print(f"❌ Error during update: {e}")
        raise

if __name__ == "__main__":
    main()
