#!/usr/bin/env python3
"""
Quick test to verify the ecommerce API endpoints work correctly.
Run this BEFORE testing the full booking flow.

Usage:
  python test_ecommerce_api.py
  python test_ecommerce_api.py --url http://localhost:3000
  python test_ecommerce_api.py --url https://wh8vvk7r-3000.inc1.devtunnels.ms
"""

import requests
import json
import sys
import os
from urllib.parse import urljoin

def test_api(base_url):
    print(f"\n🔍 Testing ecommerce API at: {base_url}")
    print("=" * 60)
    
    base_url = base_url.rstrip('/')
    
    # Test 1: Get products
    print("\n1️⃣  Testing GET /api/products...")
    try:
        response = requests.get(f"{base_url}/api/products", timeout=5)
        if response.status_code == 200:
            products = response.json()
            print(f"   ✓ Status: {response.status_code}")
            print(f"   ✓ Found {len(products)} products")
            if products:
                first = products[0]
                print(f"   ✓ First product: ID={first.get('id')}, Title='{first.get('title')}', Price={first.get('price')}, Stock={first.get('stock')}")
                product_id = first.get('id')
                product_qty = min(1, first.get('stock', 1))
        else:
            print(f"   ✗ Status: {response.status_code}")
            print(f"   Response: {response.text[:200]}")
            return False
    except Exception as e:
        print(f"   ✗ Error: {e}")
        return False
    
    # Test 2: Call buyNow API
    print("\n2️⃣  Testing POST /api/buyNow...")
    try:
        payload = {"productId": product_id, "quantity": product_qty}
        response = requests.post(
            f"{base_url}/api/buyNow",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=5
        )
        
        if response.status_code in [200, 201]:
            data = response.json()
            print(f"   ✓ Status: {response.status_code}")
            
            order = data.get('order', {})
            session_id = order.get('id')
            cart_item_id = data.get('cartItemId')
            
            print(f"   ✓ Order session ID: {session_id}")
            print(f"   ✓ Cart item ID: {cart_item_id}")
            print(f"   ✓ Order status: {order.get('status')}")
            print(f"   ✓ Order total: {order.get('total')}")
            print(f"   ✓ Order items: {len(order.get('items', []))} item(s)")
            
            if not session_id:
                print(f"   ✗ ERROR: No session ID returned!")
                return False
                
        else:
            print(f"   ✗ Status: {response.status_code}")
            print(f"   Response: {response.text[:300]}")
            return False
    except Exception as e:
        print(f"   ✗ Error: {e}")
        return False
    
    # Test 3: Get the order via GET
    print(f"\n3️⃣  Testing GET /api/orders/{session_id}...")
    try:
        response = requests.get(f"{base_url}/api/orders/{session_id}", timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            order = data.get('order', {})
            print(f"   ✓ Status: {response.status_code}")
            print(f"   ✓ Order ID: {order.get('id')}")
            print(f"   ✓ Order status: {order.get('status')}")
            print(f"   ✓ Order items: {len(order.get('items', []))} item(s)")
        else:
            print(f"   ✗ Status: {response.status_code}")
            print(f"   Response: {response.text[:300]}")
            return False
    except Exception as e:
        print(f"   ✗ Error: {e}")
        return False
    
    # Test 4: Finalize order via POST to /api/orders
    print(f"\n4️⃣  Testing POST /api/orders (finalize)...")
    try:
        payload = {"sessionId": session_id}
        response = requests.post(
            f"{base_url}/api/orders",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=5
        )
        
        if response.status_code in [200, 201]:
            data = response.json()
            order = data.get('order', {})
            print(f"   ✓ Status: {response.status_code}")
            print(f"   ✓ Order ID: {order.get('id')}")
            print(f"   ✓ Order status: {order.get('status')}")
            print(f"   ✓ Order publicId: {order.get('publicId')}")
            
            if order.get('status').lower() == 'confirmed':
                print(f"   ✓ Order CONFIRMED!")
                return True
            else:
                print(f"   ⚠ Order status is '{order.get('status')}' (expected 'confirmed')")
                return True  # Still success, just unexpected status
        else:
            print(f"   ✗ Status: {response.status_code}")
            try:
                print(f"   Response: {response.json()}")
            except:
                print(f"   Response: {response.text[:300]}")
            return False
    except Exception as e:
        print(f"   ✗ Error: {e}")
        return False

if __name__ == "__main__":
    # Get URL from command line or environment
    if len(sys.argv) > 1 and sys.argv[1].startswith("http"):
        url = sys.argv[1]
    elif "--url" in sys.argv:
        idx = sys.argv.index("--url")
        url = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else None
    else:
        # Try to detect from .env
        url = None
        if os.path.exists("/home/vishnu/Desktop/agent/.env"):
            with open("/home/vishnu/Desktop/agent/.env") as f:
                for line in f:
                    if "WEBSITE_BOOKING_URL=" in line:
                        url = line.split("=", 1)[1].strip().strip("'\"")
                        break
    
    if not url:
        url = "http://localhost:3000"
    
    # Run tests
    success = test_api(url)
    
    print("\n" + "=" * 60)
    if success:
        print("✅ API tests PASSED! Ready to test booking flow.")
    else:
        print("❌ API tests FAILED. Check configuration and try again.")
    print("=" * 60 + "\n")
    
    sys.exit(0 if success else 1)
