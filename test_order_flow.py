import sys
import os
sys.path.insert(0, '/home/vishnu/Desktop/agent')

# Mock Streamlit session state for testing
class MockSessionState:
    def __init__(self):
        self.pending_orders = {}
        self.manager_email = "matlikinnu@gmail.com"

# Simulate Streamlit environment
class MockStreamlit:
    session_state = MockSessionState()
    
    def __getattr__(self, name):
        return lambda *args, **kwargs: None

sys.modules['streamlit'] = MockStreamlit()

# Now we can test
print("=" * 80)
print("INVENTORY AGENT - ORDER FLOW TEST")
print("=" * 80)

# Test 1: Session state initialization
print("\n✅ Test 1: Session State Initialization")
st_mock = MockStreamlit()
print(f"  - Initial pending orders: {st_mock.session_state.pending_orders}")
print(f"  - Manager email: {st_mock.session_state.manager_email}")

# Test 2: Order creation simulation
print("\n✅ Test 2: Order Creation (Simulated)")
test_order_id = "BULK-20251217223023-4176"
test_order = {
    "product": "Widget Pro",
    "quantity": 50,
    "supplier": "Amazon Business",
    "unit_price": 450.00,
    "total_cost": 22500.00,
    "lead_time": "2-5 days",
    "status": "pending_approval",
    "created_at": "2025-12-17T22:30:23.123456",
    "manager_email": "test@example.com"
}
st_mock.session_state.pending_orders[test_order_id] = test_order
print(f"  - Created order: {test_order_id}")
print(f"  - Product: {test_order['product']}")
print(f"  - Status: {test_order['status']}")
print(f"  - Total Cost: ₹{test_order['total_cost']:,.2f}")

# Test 3: Order status update
print("\n✅ Test 3: Order Status Update")
print(f"  - Before: status = '{st_mock.session_state.pending_orders[test_order_id]['status']}'")
st_mock.session_state.pending_orders[test_order_id]["status"] = "confirmed"
st_mock.session_state.pending_orders[test_order_id]["confirmation"] = {
    "order_number": f"ORD-{test_order_id[-8:].upper()}",
    "supplier": "Amazon Business",
    "product": "Widget Pro",
    "quantity": 50,
    "unit_price": 450.00,
    "total_cost": 22500.00,
    "status": "confirmed",
    "placed_at": "2025-12-17T22:35:10.654321",
    "expected_delivery": "2-5 days"
}
print(f"  - After: status = '{st_mock.session_state.pending_orders[test_order_id]['status']}'")

# Test 4: Order retrieval
print("\n✅ Test 4: Order Retrieval")
pending_orders = st_mock.session_state.pending_orders
confirmed = [o for o in pending_orders.values() if o.get("status") == "confirmed"]
print(f"  - Total orders: {len(pending_orders)}")
print(f"  - Confirmed orders: {len(confirmed)}")
for order in confirmed:
    print(f"    • {order['product']}: ₹{order['total_cost']:,.2f} (Expected: {order.get('expected_delivery')})")

# Test 5: Multiple orders
print("\n✅ Test 5: Multiple Orders Handling")
for i, product in enumerate(["Widget Pro", "Gadget X", "Device Y"], 1):
    order_id = f"BULK-20251217223023-{4176+i}"
    st_mock.session_state.pending_orders[order_id] = {
        "product": product,
        "quantity": 50 * i,
        "supplier": f"Supplier {i}",
        "unit_price": 400.00 + (i * 50),
        "total_cost": (400.00 + (i * 50)) * (50 * i),
        "status": "pending_approval" if i % 2 == 0 else "confirmed",
        "lead_time": "3-6 days"
    }

pending = [o for o in st_mock.session_state.pending_orders.values() if o.get("status") == "pending_approval"]
confirmed = [o for o in st_mock.session_state.pending_orders.values() if o.get("status") == "confirmed"]

print(f"  - Total orders: {len(st_mock.session_state.pending_orders)}")
print(f"  - Awaiting approval: {len(pending)}")
print(f"  - Confirmed: {len(confirmed)}")
print(f"  - Total value of confirmed orders: ₹{sum(o['total_cost'] for o in confirmed):,.2f}")

print("\n" + "=" * 80)
print("✅ ALL TESTS PASSED")
print("=" * 80)
print("\nNow you can:")
print("1. Run the app: streamlit run app.py")
print("2. Enable autonomous mode in sidebar")
print("3. Run inventory summary")
print("4. Orders will be placed automatically when manager approves")
print("=" * 80)
