#!/usr/bin/env python3
"""
Test script for CSV price loader
"""

from robust_venture_fix import CSVPriceLoader

def test_csv_loader():
    print("Testing CSV Price Loader...")
    
    try:
        loader = CSVPriceLoader()
        print(f"✓ CSV loader initialized successfully!")
        print(f"✓ Loaded {len(loader.price_data)} price entries")
        
        # Test a few SKUs
        test_skus = ['1001-400', '1001-408', '1002-408', '1003-004', '1005-195']
        print("\nTesting specific SKUs:")
        
        for sku in test_skus:
            data = loader.get_price_for_sku(sku)
            if data:
                print(f"✓ SKU {sku}: Cost={data['cost']:.2f} kr, Sale Price={data['sale_price']:.0f} kr")
            else:
                print(f"✗ SKU {sku}: No data found")
        
        print("\n✓ CSV loading test completed successfully!")
        
    except Exception as e:
        print(f"✗ Error testing CSV loader: {e}")
        return False
    
    return True

if __name__ == "__main__":
    test_csv_loader()
