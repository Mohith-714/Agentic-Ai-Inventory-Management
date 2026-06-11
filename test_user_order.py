#!/usr/bin/env python3
"""
Quick test for user order request parsing and placement.
"""
import json
import sys
import os

# Suppress Streamlit warnings
os.environ["STREAMLIT_SERVER_HEADLESS"] = "true"

sys.path.insert(0, '/home/vishnu/Desktop/agent')

# Load inventory directly from JSON
with open('/home/vishnu/Desktop/agent/inventory.json', 'r') as f:
    inventory_data = json.load(f)

# Import the parsing function
import re
from difflib import SequenceMatcher

def _find_similar_product(text: str, inventory_data: list, threshold: float = 0.5):
    """Find similar product using SequenceMatcher."""
    if not inventory_data:
        return None
    
    best_match = None
    best_score = 0.0
    
    for item in inventory_data:
        if not isinstance(item, dict):
            continue
        product_name = item.get("product", "").lower()
        if not product_name:
            continue
        score = SequenceMatcher(None, text.lower(), product_name).ratio()
        if score > best_score:
            best_score = score
            best_match = item
    
    return best_match if best_score >= threshold else None

def parse_user_order_request(user_input: str, inventory_data: list):
    """Parse user input for direct order placement requests."""
    text = (user_input or "").lower()
    order_keywords = ["order", "place order", "reorder", "buy", "purchase", "stock up", "stock"]
    is_order_request = any(k in text for k in order_keywords)
    
    if not is_order_request:
        return None
    
    # Extract quantity
    qty_match = re.search(r"(\d+)\s*(?:units?|of|x)?", text, re.IGNORECASE)
    if not qty_match:
        return None
    quantity = int(qty_match.group(1))
    
    # Extract product name
    remainder = text.split(qty_match.group(0))[1] if len(text.split(qty_match.group(0))) > 1 else text
    remainder = remainder.replace("of", "").replace("units", "").replace("for", "").replace("order", "").strip()
    
    # Try to find the product in inventory
    found_item = None
    for item in inventory_data:
        if not isinstance(item, dict):
            continue
        prod = item.get("product", "").lower()
        if prod and (prod in remainder or remainder in prod):
            found_item = item
            break
    
    # Fuzzy fallback
    if not found_item:
        found_item = _find_similar_product(remainder, inventory_data, threshold=0.5)
    
    if found_item:
        return {"product": found_item.get("product"), "quantity": quantity, "found_item": found_item}
    return None

print("=" * 70)
print("TESTING USER ORDER REQUEST PARSING")
print("=" * 70)

test_cases = [
    "order 50 units of Winter Jackets",
    "Place 100 Running Shoes",
    "I want to buy 25 Summer Hats please",
    "stock up with 30 Casual T-Shirts",
    "Can you place an order for 15 Denim Jeans",
    "Not an order request - just checking inventory",
    "order 200 items",
]

for test_input in test_cases:
    print(f"\nTest Input: \"{test_input}\"")
    result = parse_user_order_request(test_input, inventory_data)
    
    if result:
        print(f"  ✅ Order Request Detected!")
        print(f"     - Product: {result.get('product')}")
        print(f"     - Quantity: {result.get('quantity')} units")
    else:
        print(f"  ❌ Not an order request OR product not found")

print("\n" + "=" * 70)
print("Inventory Products Available:")
print("=" * 70)
for item in inventory_data:
    if isinstance(item, dict):
        print(f"  - {item.get('product')} (Stock: {item.get('stock')}, Price: ₹{item.get('price')})")
