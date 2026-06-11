# Real-World Usage Scenarios

## Scenario 1: Order from Your Testing Ecommerce Site


from mohan import book_order_from_website
import json
from datetime import datetime

def test_development_ecommerce():
    """Test booking on dev site"""
    
    test_cases = [
        {
            "product": "Winter Jacket",
            "quantity": 5,
            "email": "inventory@company.local",
            "phone": "9876543210",
            "address": "Warehouse A, Mumbai"
        },
        {
            "product": "Running Shoes",
            "quantity": 10,
            "email": "inventory@company.local",
            "phone": "9876543210",
            "address": "Warehouse A, Mumbai"
        },
        {
            "product": "Summer Hat",
            "quantity": 20,
            "email": "inventory@company.local",
            "phone": "9876543210",
            "address": "Warehouse A, Mumbai"
        }
    ]
    
    results = []
    dev_site_url = "http://localhost:8000"
    
    for i, test_case in enumerate(test_cases, 1):
        print(f"\n[Test {i}/{len(test_cases)}] Booking: {test_case['product']}...")
        
        result = book_order_from_website(
            product=test_case['product'],
            quantity=test_case['quantity'],
            website_url=dev_site_url,
            customer_email=test_case['email'],
            customer_phone=test_case['phone'],
            customer_address=test_case['address']
        )
        
        test_result = {
            "test_id": i,
            "product": test_case['product'],
            "quantity": test_case['quantity'],
            "status": result['status'],
            "order_id": result['order_id'],
            "confirmation": result['confirmation'],
            "timestamp": datetime.now().isoformat()
        }
        
        results.append(test_result)
        
        # Print result
        status_icon = "✅" if result['status'] == "success" else "⏳" if result['status'] == "pending" else "❌"
        print(f"{status_icon} {result['status'].upper()}")
        print(f"   Order ID: {result['order_id']}")
        if result['confirmation'] != 'N/A':
            print(f"   Confirmation: {result['confirmation']}")
        
        # Print last 3 debug logs
        print("   Last steps:")
        for log in result['debug'][-3:]:
            print(f"     - {log}")
    
    # Summary
    success_count = len([r for r in results if r['status'] == 'success'])
    pending_count = len([r for r in results if r['status'] == 'pending'])
    error_count = len([r for r in results if r['status'] == 'error'])
    
    print(f"\n{'='*60}")
    print(f"SUMMARY: {success_count} Success | {pending_count} Pending | {error_count} Errors")
    print(f"{'='*60}")
    
    # Save results
    with open('booking_results.json', 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f"\nResults saved to: booking_results.json")
    
    return results

if __name__ == "__main__":
    test_development_ecommerce()
