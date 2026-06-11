import streamlit as st
import os
from groq import Groq
import json
import re
from urllib.parse import urlparse
import smtplib
import imaplib
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from difflib import SequenceMatcher
from datetime import datetime
import math
import re
import requests
from typing import Dict, Optional
from dotenv import load_dotenv
SELENIUM_AVAILABLE = False

# Load environment variables from .env
load_dotenv()

# Global default: allow estimating missing prices from inventory base price
ESTIMATE_MISSING_PRICES = True

# Currency settings
CURRENCY_SYMBOL = "₹"

def format_inr(amount) -> str:
    try:
        return f"{CURRENCY_SYMBOL}{float(amount):,.2f}"
    except Exception:
        return f"{CURRENCY_SYMBOL}N/A"
try:
    # Preferred: renamed package
    from ddgs import DDGS  # pip install ddgs
    _DDG_AVAILABLE = True
    _DDG_SOURCE = "ddgs"
except Exception:
    try:
        # Legacy fallback
        from duckduckgo_search import DDGS  # pip install duckduckgo-search
        _DDG_AVAILABLE = True
        _DDG_SOURCE = "duckduckgo_search"
    except Exception:
        _DDG_AVAILABLE = False
        _DDG_SOURCE = None
# Optional LangChain integration flag
try:
    import langchain  # noqa: F401
    LANGCHAIN_AVAILABLE = True
except Exception:
    LANGCHAIN_AVAILABLE = False

# LangChain optional imports
if LANGCHAIN_AVAILABLE:
    try:
        from langchain_groq import ChatGroq
        from langchain_core.prompts import ChatPromptTemplate
    except Exception:
        LANGCHAIN_AVAILABLE = False

def _list_groq_models(client):
    """Return a list of available model ids from Groq client, with safe fallback."""
    try:
        models = client.models.list()
        ids = []
        for m in getattr(models, "data", []):
            mid = getattr(m, "id", None) or (m.get("id") if isinstance(m, dict) else None)
            if mid:
                ids.append(mid)
        return ids
    except Exception:
        return [
            "llama-3.3-70b-versatile",
            "llama-3.3-8b-instant",
            "llama-3.2-70b",
            "llama-3.1-8b-instant",
        ]

def _filter_chat_models(model_ids):
    """Filter out non-chat models from a list of model ids."""
    banned = (
        "whisper",
        "audio",
        "tts",
        "embedding",
        "embed",
        "vision",
        "clip",
        "mixtral",
    )
    return [m for m in model_ids if m and not any(b in m.lower() for b in banned)]

def _select_groq_model(client, requested: str) -> str:
    # Pick from API, validating requested against availability and banned list
    all_models = _list_groq_models(client)
    available = _filter_chat_models(all_models)
    # If user requested a model, only honor it if it's available and not banned
    if requested and requested not in ("PASTE_YOUR_GROQ_MODEL_HERE",):
        req_lower = requested.lower()
        banned = ("whisper", "audio", "tts", "embedding", "embed", "vision", "clip", "mixtral")
        if (requested in available) and not any(b in req_lower for b in banned):
            return requested
    # Prefer chat-capable, current Llama variants first
    priority = (
        "llama-3.3-70b-versatile",
        "llama-3.3-8b-instant",
        "llama-3.2-70b",
        "llama-3.2-11b",
        "llama-3.1-8b-instant",
        "gemma",  # generic gemma chat-capable variants
        "llama",  # any remaining llama
    )
    for p in priority:
        for name in available:
            if p in name:
                return name
    # Last resort fallback
    return available[0] if available else "llama-3.1-8b-instant"

# ===== INVENTORY & EMAIL TOOLS =====
def _load_inventory_data() -> list:
    """Load inventory from inventory.json."""
    try:
        with open("inventory.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "items" in data and isinstance(data["items"], list):
            return data["items"]
        if isinstance(data, list):
            return data
        return []
    except Exception:
        return []

def _find_similar_product(product_name: str, inventory: list, threshold: float = 0.6) -> dict | None:
    """Fuzzy match product name in inventory."""
    if not inventory:
        return None
    
    product_name = product_name.lower().strip()
    best_match = None
    best_score = 0
    
    for item in inventory:
        if not isinstance(item, dict):
            continue
        item_product = item.get("product", "").lower().strip()
        if not item_product:
            continue
        
        # Check for exact substring match first
        if product_name in item_product or item_product in product_name:
            return item
        
        # Fuzzy match
        score = SequenceMatcher(None, product_name, item_product).ratio()
        if score > best_score:
            best_score = score
            best_match = item
    
    return best_match if best_score >= threshold else None

def tool_lookup_inventory(product_name: str = None) -> dict:
    """Lookup product details from inventory by name (supports fuzzy matching)."""
    inventory = _load_inventory_data()
    
    if not inventory:
        return {"status": "error", "message": "Inventory not found"}
    
    if not product_name:
        # Return all items if no product specified
        return {
            "status": "success",
            "all_items": inventory,
            "count": len(inventory)
        }
    
    # Try to find the product
    item = _find_similar_product(product_name, inventory)
    
    if item:
        return {
            "status": "success",
            "product": item.get("product"),
            "stock": item.get("stock"),
            "location": item.get("location"),
            "price": item.get("price"),
            "found": True
        }
    else:
        return {
            "status": "error",
            "message": f"Product '{product_name}' not found in inventory",
            "available_products": [i.get("product") for i in inventory if isinstance(i, dict)]
        }

def tool_send_confirmation_email(product: str, quantity: int, supplier: str, unit_price: float, 
                                  total_cost: float, lead_time: str, manager_email: str, 
                                  order_id: str) -> dict:
    """Send email to inventory manager for order confirmation."""
    
    if not SENDER_EMAIL or not SENDER_PASSWORD or not manager_email:
        return {
            "status": "error",
            "message": "Email configuration incomplete. Set SENDER_EMAIL, SENDER_PASSWORD, MANAGER_EMAIL environment variables."
        }
    
    try:
        # Create email content
        subject = f"[Pending Approval] Restock Order - {product}"
        
        body = f"""
Hello Inventory Manager,

An autonomous restocking order has been generated and requires your approval.

ORDER DETAILS:
============
Order ID: {order_id}
Product: {product}
Quantity: {quantity} units
Supplier: {supplier}
Unit Price: ₹{unit_price:,.2f}
Total Cost: ₹{total_cost:,.2f}
Estimated Lead Time: {lead_time}

NEXT STEPS:
===========
1. Review the order details above
2. Reply to this email with "APPROVE" or "REJECT"
3. Once approved, the agent will automatically place the order

Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

---
This is an automated message from the Autonomous Supply Chain Agent.
"""
        
        # Send email
        msg = MIMEMultipart()
        msg["From"] = SENDER_EMAIL
        msg["To"] = manager_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))
        
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        
        # Store order in pending state (use session state if available)
        pending_orders_dict = get_pending_orders_dict()
        pending_orders_dict[order_id] = {
            "product": product,
            "quantity": quantity,
            "supplier": supplier,
            "unit_price": unit_price,
            "total_cost": total_cost,
            "lead_time": lead_time,
            "status": "pending_approval",
            "created_at": datetime.now().isoformat(),
            "manager_email": manager_email
        }
        
        # Explicitly save to session state if available
        try:
            st.session_state.pending_orders = pending_orders_dict
        except:
            pass
        
        # Also update global fallback
        global PENDING_ORDERS
        PENDING_ORDERS.update(pending_orders_dict)
        
        return {
            "status": "success",
            "message": f"Confirmation email sent to {manager_email}",
            "order_id": order_id
        }
    
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to send email: {str(e)}"
        }

# ===== REAL SUPPLIER API INTEGRATION =====
def call_amazon_api(product: str, quantity: int, unit_price: float) -> Dict[str, any]:
    """Place real order on Amazon using Amazon SP-API or public ordering."""
    try:
        # Option 1: Amazon SP-API (requires API credentials)
        amazon_api_key = os.environ.get("AMAZON_API_KEY", "")
        amazon_api_secret = os.environ.get("AMAZON_API_SECRET", "")
        
        if amazon_api_key and amazon_api_secret:
            # Real Amazon SP-API call for business accounts
            headers = {
                "Authorization": f"Bearer {amazon_api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "product_name": product,
                "quantity": quantity,
                "unit_price": unit_price,
                "marketplace": "in"  # India
            }
            
            response = requests.post(
                "https://sellingpartnerapi-fe.amazon.com/orders/v0/orders",
                headers=headers,
                json=payload,
                timeout=10
            )
            
            if response.status_code == 200:
                order_data = response.json()
                return {
                    "status": "success",
                    "supplier": "Amazon Business",
                    "order_id": order_data.get("order_id", f"AMZ-{datetime.now().strftime('%Y%m%d%H%M%S')}"),
                    "confirmation": f"Order placed with Amazon",
                    "details": order_data,
                    "real_api": True
                }
        
        # Option 2: Amazon B2B Business account webhook
        webhook_url = os.environ.get("AMAZON_WEBHOOK_URL", "")
        if webhook_url:
            payload = {
                "action": "place_order",
                "product": product,
                "quantity": quantity,
                "unit_price": unit_price,
                "timestamp": datetime.now().isoformat()
            }
            response = requests.post(webhook_url, json=payload, timeout=10)
            if response.status_code in [200, 201, 202]:
                return {
                    "status": "success",
                    "supplier": "Amazon Business",
                    "order_id": f"AMZ-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                    "confirmation": "Order placed with Amazon via webhook",
                    "real_api": True
                }
        
        # Fallback: Return success (will be sent as inquiry)
        return {
            "status": "pending",
            "supplier": "Amazon Business",
            "order_id": f"AMZ-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "confirmation": f"Order inquiry sent to Amazon for {product} ({quantity} units @ ₹{unit_price})",
            "details": {
                "product": product,
                "quantity": quantity,
                "unit_price": unit_price,
                "total_cost": quantity * unit_price
            },
            "real_api": False,
            "note": "Configure AMAZON_API_KEY or AMAZON_WEBHOOK_URL in .env for real orders"
        }
    
    except Exception as e:
        return {
            "status": "error",
            "supplier": "Amazon Business",
            "message": f"Failed to place order on Amazon: {str(e)}"
        }

def call_flipkart_api(product: str, quantity: int, unit_price: float) -> Dict[str, any]:
    """Place real order on Flipkart using API."""
    try:
        flipkart_api_key = os.environ.get("FLIPKART_API_KEY", "")
        flipkart_seller_id = os.environ.get("FLIPKART_SELLER_ID", "")
        
        if flipkart_api_key and flipkart_seller_id:
            # Real Flipkart API call
            headers = {
                "Authorization": f"Bearer {flipkart_api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "product_name": product,
                "quantity": quantity,
                "unit_price": unit_price,
                "seller_id": flipkart_seller_id
            }
            
            response = requests.post(
                "https://api.flipkart.com/v1/orders",
                headers=headers,
                json=payload,
                timeout=10
            )
            
            if response.status_code == 200:
                order_data = response.json()
                return {
                    "status": "success",
                    "supplier": "Flipkart",
                    "order_id": order_data.get("order_id", f"FK-{datetime.now().strftime('%Y%m%d%H%M%S')}"),
                    "confirmation": f"Order placed with Flipkart",
                    "real_api": True
                }
        
        # Fallback
        return {
            "status": "pending",
            "supplier": "Flipkart",
            "order_id": f"FK-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "confirmation": f"Order inquiry sent to Flipkart for {product} ({quantity} units @ ₹{unit_price})",
            "details": {
                "product": product,
                "quantity": quantity,
                "unit_price": unit_price,
                "total_cost": quantity * unit_price
            },
            "real_api": False,
            "note": "Configure FLIPKART_API_KEY and FLIPKART_SELLER_ID in .env for real orders"
        }
    
    except Exception as e:
        return {
            "status": "error",
            "supplier": "Flipkart",
            "message": f"Failed to place order on Flipkart: {str(e)}"
        }

def call_generic_supplier_api(supplier_name: str, supplier_api_url: str, 
                              product: str, quantity: int, unit_price: float) -> Dict[str, any]:
    """Call generic supplier API for order placement."""
    try:
        if not supplier_api_url:
            return {
                "status": "pending",
                "supplier": supplier_name,
                "order_id": f"{supplier_name[:3].upper()}-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                "confirmation": f"Order inquiry for {product} ({quantity} units)",
                "real_api": False
            }
        
        payload = {
            "action": "place_order",
            "product": product,
            "quantity": quantity,
            "unit_price": unit_price,
            "timestamp": datetime.now().isoformat()
        }
        
        response = requests.post(supplier_api_url, json=payload, timeout=10)
        
        if response.status_code in [200, 201, 202]:
            order_data = response.json()
            return {
                "status": "success",
                "supplier": supplier_name,
                "order_id": order_data.get("order_id", f"{supplier_name[:3].upper()}-{datetime.now().strftime('%Y%m%d%H%M%S')}"),
                "confirmation": order_data.get("message", "Order placed successfully"),
                "real_api": True,
                "details": order_data
            }
        else:
            # API error but not critical
            return {
                "status": "pending",
                "supplier": supplier_name,
                "order_id": f"{supplier_name[:3].upper()}-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                "confirmation": f"Order sent to {supplier_name}",
                "api_response": response.status_code,
                "real_api": True
            }
    
    except Exception as e:
        return {
            "status": "error",
            "supplier": supplier_name,
            "message": f"Failed to place order: {str(e)}"
        }

def send_order_email_to_seller(
    supplier_email: str,
    supplier_name: str,
    order_id: str,
    product: str,
    quantity: int,
    unit_price: float,
    total_cost: float,
    lead_time: str,
) -> Dict[str, any]:
    """Send a booking email to the supplier instead of calling an API."""
    supplier_display = (supplier_name or "").strip() or "Seller"
    if not supplier_email:
        return {
            "status": "error",
            "message": "Seller email not configured. Set SELLER_EMAIL in .env"
        }

    try:
        subject = f"Purchase Order {order_id} - {product}"
        body = f"""
    Hello {supplier_display},

Please confirm the following purchase order:

Order ID: {order_id}
Product: {product}
Quantity: {quantity} units
Unit Price: ₹{unit_price:,.2f}
Total Cost: ₹{total_cost:,.2f}
Requested Delivery: {lead_time}

Please reply with confirmation and expected delivery date.

Thanks,
Autonomous Inventory Agent
"""

        msg = MIMEMultipart()
        msg["From"] = SENDER_EMAIL
        msg["To"] = supplier_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()

        return {
            "status": "success",
            "real_api": False,
            "order_id": order_id,
            "confirmation": "Order email sent to seller",
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to email seller: {str(e)}"
        }


# WhatsApp automation removed per user request


def send_seller_confirmation_to_manager(order_id: str, product: str, confirmation_text: str) -> Dict[str, any]:
    """Send seller's response to inventory manager via email."""
    if not MANAGER_EMAIL:
        return {"status": "error", "message": "Manager email not configured"}
    
    try:
        subject = f"[SELLER CONFIRMED] Order {order_id} - {product}"
        body = f"""
Hello,

The seller has confirmed the following order:

Order ID: {order_id}
Product: {product}

Seller Response:
---
{confirmation_text}
---

The order has been booked successfully.

Autonomous Inventory Agent
"""
        
        msg = MIMEMultipart()
        msg["From"] = SENDER_EMAIL
        msg["To"] = MANAGER_EMAIL
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))
        
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        
        return {
            "status": "success",
            "message": f"Seller confirmation emailed to {MANAGER_EMAIL}"
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to email manager: {str(e)}"
        }


def send_seller_rejection_to_manager(order_id: str, product: str, rejection_text: str) -> Dict[str, any]:
    """Notify manager that seller rejected the order."""
    if not MANAGER_EMAIL:
        return {"status": "error", "message": "Manager email not configured"}

    try:
        subject = f"[SELLER REJECTED] Order {order_id} - {product}"
        body = f"""
Hello,

The seller has REJECTED the following order:

Order ID: {order_id}
Product: {product}

Seller Response:
---
{rejection_text}
---

Please review and take next steps (choose alternate supplier or adjust order).

Autonomous Inventory Agent
"""

        msg = MIMEMultipart()
        msg["From"] = SENDER_EMAIL
        msg["To"] = MANAGER_EMAIL
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()

        return {
            "status": "success",
            "message": f"Seller rejection emailed to {MANAGER_EMAIL}"
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to email manager: {str(e)}"
        }


def place_order_with_supplier(order_id: str, supplier_name: str, product: str, quantity: int, 
                             unit_price: float, total_cost: float, lead_time: str) -> Dict[str, any]:
    """Send booking email to configured seller address (WhatsApp removed)."""
    seller_email = SELLER_EMAIL or os.environ.get("SELLER_EMAIL", "")
    email_result = send_order_email_to_seller(
        supplier_email=seller_email,
        supplier_name=supplier_name,
        order_id=order_id,
        product=product,
        quantity=quantity,
        unit_price=unit_price,
        total_cost=total_cost,
        lead_time=lead_time or "2-5 days",
    )
    return {
        "status": email_result.get("status", "success"),
        "real_api": False,
        "order_id": order_id,
        "confirmation": "Order email sent to seller",
        "email_status": email_result.get("status")
    }


def tool_check_seller_confirmations() -> dict:
    """Check inbox for seller confirmations (email) and notify manager automatically."""
    results = {"confirmed": [], "errors": [], "debug": []}
    if not SENDER_EMAIL or not SENDER_PASSWORD:
        return {"status": "error", "message": "Email credentials not configured"}
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
        mail.login(SENDER_EMAIL, SENDER_PASSWORD)
        results["debug"].append(f"✓ Logged in as {SENDER_EMAIL}")
        mail.select('inbox')
        status, messages = mail.search(None, 'ALL')
        if status != 'OK':
            return {"status": "error", "message": "Failed to search emails"}
        email_ids = messages[0].split()[-30:]
        results["debug"].append(f"Checking {len(email_ids)} recent emails")
        
        pending_orders = get_pending_orders_dict()
        results["debug"].append(f"Pending orders in system: {list(pending_orders.keys())}")
        
        for email_id in email_ids:
            status, msg_data = mail.fetch(email_id, '(RFC822)')
            if status != 'OK':
                continue
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    from_email = (msg.get('From', '') or '').lower()
                    subject = msg.get('Subject', '') or ''
                    body = ''
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == 'text/plain':
                                try:
                                    body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                                    break
                                except:
                                    pass
                    else:
                        try:
                            body = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
                        except:
                            body = str(msg.get_payload())

                    combined = (subject + ' ' + body).upper()
                    
                    # Check if this is from seller
                    seller_check = SELLER_EMAIL.lower() in from_email if SELLER_EMAIL else True
                    if not seller_check:
                        continue
                    
                    results["debug"].append(f"Processing email from {from_email[:40]}... Subject: {subject[:50]}...")
                    
                    # Find order id - try multiple patterns
                    order_patterns = [
                        r'(USER-ORD-[0-9]+)',
                        r'(BULK-[A-Z0-9-]+)',
                        r'(ORD-[0-9]+)',
                        r'Order\s+ID[:\s]*([A-Z0-9-]+)',
                        r'order[:\s]*([A-Z0-9-]+)',
                    ]
                    oid = None
                    for pattern in order_patterns:
                        m = re.search(pattern, subject + ' ' + body, re.IGNORECASE)
                        if m:
                            oid = m.group(1).upper()
                            break
                    
                    if not oid:
                        results["debug"].append(f"  ✗ No order ID found in email")
                        continue
                    
                    results["debug"].append(f"  Found Order ID: {oid}")
                    
                    if oid not in pending_orders:
                        results["debug"].append(f"  ✗ Order {oid} NOT in pending orders. Available: {list(pending_orders.keys())}")
                        continue
                    
                    # Accept or reject keywords
                    acceptance_tokens = ('CONFIRM', 'ACCEPT', 'APPROVE', 'RECEIVED', 'BOOKED')
                    rejection_tokens = ('REJECT', 'DECLINE', 'CANCEL', 'OUT OF STOCK', 'UNABLE TO FULFILL', 'CANNOT FULFILL')
                    has_acceptance = any(token in combined for token in acceptance_tokens)
                    has_rejection = any(token in combined for token in rejection_tokens)

                    if has_acceptance and not has_rejection:
                        results["debug"].append(f"  ✓ Acceptance detected in email")
                        # Notify manager
                        product = pending_orders[oid].get('product', 'Unknown')
                        _ = send_seller_confirmation_to_manager(oid, product, subject + '\n' + body)
                        # Mark order as seller_confirmed
                        pending_orders[oid]['status'] = 'seller_confirmed'
                        pending_orders[oid]['seller_confirmation'] = {
                            'at': datetime.now().isoformat(),
                            'subject': subject,
                            'from': from_email,
                            'body_preview': body[:200] if body else 'N/A',
                        }
                        # Update BOTH session and global pending orders
                        PENDING_ORDERS[oid] = pending_orders[oid]
                        try:
                            st.session_state.pending_orders = pending_orders
                        except:
                            pass
                        results['confirmed'].append({'order_id': oid, 'product': product})
                        results["debug"].append(f"  ✓ Order {oid} marked as seller_confirmed and manager notified")
                    elif has_rejection:
                        results["debug"].append(f"  ✗ Rejection detected in email")
                        product = pending_orders[oid].get('product', 'Unknown')
                        _ = send_seller_rejection_to_manager(oid, product, subject + '\n' + body)
                        pending_orders[oid]['status'] = 'seller_rejected'
                        pending_orders[oid]['seller_confirmation'] = {
                            'at': datetime.now().isoformat(),
                            'subject': subject,
                            'from': from_email,
                            'body_preview': body[:200] if body else 'N/A',
                            'rejected': True,
                        }
                        PENDING_ORDERS[oid] = pending_orders[oid]
                        try:
                            st.session_state.pending_orders = pending_orders
                        except:
                            pass
                        results.setdefault('rejected', []).append({'order_id': oid, 'product': product})
                        results["debug"].append(f"  ✗ Order {oid} marked as seller_rejected and manager notified")
                    else:
                        results["debug"].append(f"  ✗ No acceptance/rejection keywords found (accept: {acceptance_tokens}, reject: {rejection_tokens})")
        
        mail.close()
        mail.logout()
        results['status'] = 'success'
        return results
    except Exception as e:
        results["debug"].append(f"Exception: {str(e)}")
        return {"status": "error", "message": str(e), "debug": results["debug"]}

def tool_place_order(order_id: str, supplier: str, product: str, quantity: int, 
                     unit_price: float, total_cost: float) -> dict:
    """Place REAL order with supplier via API."""
    
    pending_orders_dict = get_pending_orders_dict()
    
    # Debug: Check what orders exist
    all_order_ids = list(pending_orders_dict.keys())
    
    if order_id not in pending_orders_dict:
        return {
            "status": "error",
            "message": f"Order {order_id} not found. Available orders: {all_order_ids}"
        }
    
    order = pending_orders_dict[order_id]
    if order.get("status") != "pending_approval":
        return {
            "status": "error",
            "message": f"Order {order_id} has already been processed or rejected (current status: {order.get('status')})"
        }
    
    try:
        # Send booking email to seller instead of API
        supplier_response = place_order_with_supplier(
            order_id=order_id,
            supplier_name=supplier,
            product=product,
            quantity=quantity,
            unit_price=unit_price,
            total_cost=total_cost,
            lead_time=order.get("lead_time", "2-5 days"),
        )

        if supplier_response.get("status") in ["success", "pending"]:
            # Order email sent to seller
            confirmation = {
                "order_number": supplier_response.get("order_id", f"ORD-{order_id[-8:].upper()}"),
                "supplier": supplier,
                "product": product,
                "quantity": quantity,
                "unit_price": unit_price,
                "total_cost": total_cost,
                "status": "confirmed",
                "placed_at": datetime.now().isoformat(),
                "expected_delivery": order.get("lead_time", "2-5 days"),
                "api_response": supplier_response.get("confirmation", "Order email sent"),
                "real_order": False,
            }
            
            # Update order status to confirmed
            pending_orders_dict[order_id]["status"] = "confirmed"
            pending_orders_dict[order_id]["confirmation"] = confirmation
            pending_orders_dict[order_id]["supplier_response"] = supplier_response
            
            # Also update global fallback
            PENDING_ORDERS[order_id] = pending_orders_dict[order_id]
            
            # Explicitly save pending orders back to session state
            try:
                st.session_state.pending_orders = pending_orders_dict
            except:
                pass
            
            return {
                "status": "success",
                "message": f"✅ Order successfully placed with {supplier}",
                "order_confirmation": confirmation
            }
        else:
            # Email send failed; mark as error so UI reflects state
            error_msg = supplier_response.get("message", f"Failed to place order with {supplier}")
            pending_orders_dict[order_id]["status"] = "error"
            pending_orders_dict[order_id]["error"] = error_msg
            try:
                st.session_state.pending_orders = pending_orders_dict
            except:
                pass
            return {
                "status": "error",
                "message": error_msg
            }
    
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to place order: {str(e)}"
        }

def tool_get_pending_orders() -> dict:
    """Retrieve all pending orders."""
    pending_orders_dict = get_pending_orders_dict()
    return {
        "status": "success",
        "pending_orders": pending_orders_dict,
        "count": len(pending_orders_dict)
    }

def tool_track_order(order_id: str) -> dict:
    """Track order location and status (mock tracking for testing purposes)."""
    pending_orders_dict = get_pending_orders_dict()
    
    if order_id not in pending_orders_dict:
        return {
            "status": "error",
            "message": f"Order {order_id} not found in system"
        }
    
    order = pending_orders_dict[order_id]
    order_status = order.get("status", "pending_approval")
    placed_at = order.get("confirmation", {}).get("placed_at")
    seller_confirmed = order.get("seller_confirmation", {}).get("at")
    
    # Mock tracking data based on order status and time elapsed
    tracking_data = {
        "order_id": order_id,
        "product": order.get("product", "Unknown"),
        "quantity": order.get("quantity", 0),
        "supplier": order.get("supplier", "Unknown"),
        "current_status": order_status,
        "tracking_history": []
    }
    
    # Generate mock tracking events based on status
    if order_status == "pending_approval":
        tracking_data["current_location"] = "Order Pending Manager Approval"
        tracking_data["tracking_history"] = [
            {
                "timestamp": order.get("created_at", datetime.now().isoformat()),
                "status": "Order Created",
                "location": "Inventory Management System",
                "description": "Order request submitted, awaiting manager approval"
            }
        ]
    
    elif order_status == "confirmed" and not seller_confirmed:
        tracking_data["current_location"] = "Order Sent to Supplier"
        tracking_data["tracking_history"] = [
            {
                "timestamp": order.get("created_at", datetime.now().isoformat()),
                "status": "Order Created",
                "location": "Inventory Management System",
                "description": "Order approved by manager"
            },
            {
                "timestamp": placed_at or datetime.now().isoformat(),
                "status": "Sent to Supplier",
                "location": order.get("supplier", "Supplier"),
                "description": f"Order request sent to {order.get('supplier', 'supplier')}"
            }
        ]
    
    elif order_status == "seller_confirmed" or seller_confirmed:
        # Calculate time since seller confirmation
        if seller_confirmed:
            confirmed_time = datetime.fromisoformat(seller_confirmed)
            elapsed_hours = (datetime.now() - confirmed_time).total_seconds() / 3600
        else:
            elapsed_hours = 0
        
        # Mock tracking progression based on time
        tracking_data["tracking_history"] = [
            {
                "timestamp": order.get("created_at", datetime.now().isoformat()),
                "status": "Order Created",
                "location": "Inventory Management System",
                "description": "Order approved by manager"
            },
            {
                "timestamp": placed_at or datetime.now().isoformat(),
                "status": "Sent to Supplier",
                "location": order.get("supplier", "Supplier"),
                "description": f"Order request sent to {order.get('supplier', 'supplier')}"
            },
            {
                "timestamp": seller_confirmed or datetime.now().isoformat(),
                "status": "Confirmed by Supplier",
                "location": f"{order.get('supplier', 'Supplier')} Warehouse",
                "description": "Supplier confirmed order availability"
            }
        ]
        
        # Add progressive tracking based on elapsed time
        if elapsed_hours >= 1:
            tracking_data["tracking_history"].append({
                "timestamp": (datetime.fromisoformat(seller_confirmed) + timedelta(hours=1)).isoformat() if seller_confirmed else datetime.now().isoformat(),
                "status": "Order Packed",
                "location": f"{order.get('supplier', 'Supplier')} Warehouse",
                "description": "Order has been packed and ready for dispatch"
            })
        
        if elapsed_hours >= 2:
            tracking_data["tracking_history"].append({
                "timestamp": (datetime.fromisoformat(seller_confirmed) + timedelta(hours=2)).isoformat() if seller_confirmed else datetime.now().isoformat(),
                "status": "In Transit",
                "location": "Distribution Hub - Mumbai",
                "description": "Package picked up by logistics partner"
            })
        
        if elapsed_hours >= 4:
            tracking_data["tracking_history"].append({
                "timestamp": (datetime.fromisoformat(seller_confirmed) + timedelta(hours=4)).isoformat() if seller_confirmed else datetime.now().isoformat(),
                "status": "In Transit",
                "location": "Regional Sorting Center - Delhi",
                "description": "Package in transit to destination"
            })
        
        if elapsed_hours >= 6:
            tracking_data["tracking_history"].append({
                "timestamp": (datetime.fromisoformat(seller_confirmed) + timedelta(hours=6)).isoformat() if seller_confirmed else datetime.now().isoformat(),
                "status": "Out for Delivery",
                "location": f"Local Hub - {order.get('location', 'Destination')}",
                "description": "Package out for delivery"
            })
        
        if elapsed_hours >= 8:
            tracking_data["tracking_history"].append({
                "timestamp": (datetime.fromisoformat(seller_confirmed) + timedelta(hours=8)).isoformat() if seller_confirmed else datetime.now().isoformat(),
                "status": "Delivered",
                "location": f"Warehouse - {order.get('location', 'Destination')}",
                "description": "Package delivered successfully"
            })
            tracking_data["current_status"] = "delivered"
        
        # Set current location to latest tracking event
        if tracking_data["tracking_history"]:
            latest = tracking_data["tracking_history"][-1]
            tracking_data["current_location"] = latest["location"]
            tracking_data["latest_status"] = latest["status"]
    
    elif order_status == "rejected":
        tracking_data["current_location"] = "Order Rejected"
        tracking_data["tracking_history"] = [
            {
                "timestamp": order.get("created_at", datetime.now().isoformat()),
                "status": "Order Created",
                "location": "Inventory Management System",
                "description": "Order request submitted"
            },
            {
                "timestamp": datetime.now().isoformat(),
                "status": "Rejected",
                "location": "Inventory Management System",
                "description": "Order rejected by manager"
            }
        ]
    
    elif order_status == "seller_rejected":
        tracking_data["current_location"] = "Order Cancelled"
        tracking_data["tracking_history"] = [
            {
                "timestamp": order.get("created_at", datetime.now().isoformat()),
                "status": "Order Created",
                "location": "Inventory Management System",
                "description": "Order approved by manager"
            },
            {
                "timestamp": placed_at or datetime.now().isoformat(),
                "status": "Sent to Supplier",
                "location": order.get("supplier", "Supplier"),
                "description": f"Order request sent to {order.get('supplier', 'supplier')}"
            },
            {
                "timestamp": seller_confirmed or datetime.now().isoformat(),
                "status": "Rejected by Supplier",
                "location": order.get("supplier", "Supplier"),
                "description": "Supplier unable to fulfill order"
            }
        ]
    
    # Store tracking data in order
    order["tracking"] = tracking_data
    pending_orders_dict[order_id] = order
    try:
        st.session_state.pending_orders = pending_orders_dict
    except:
        pass
    
    return {
        "status": "success",
        "tracking": tracking_data
    }

def tool_check_email_approvals() -> dict:
    """Check inbox for APPROVE replies and automatically place orders."""
    if not SENDER_EMAIL or not SENDER_PASSWORD:
        return {
            "status": "error",
            "message": "Email credentials not configured"
        }
    
    approved_orders = []
    rejected_orders = []
    errors = []
    debug_info = []
    
    try:
        # Get pending orders ONCE at the start
        pending_orders_dict = get_pending_orders_dict()
        debug_info.append(f"Starting with pending orders: {list(pending_orders_dict.keys())}")
        
        # Connect to IMAP server
        debug_info.append(f"Connecting to {IMAP_SERVER}:{IMAP_PORT}")
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
        mail.login(SENDER_EMAIL, SENDER_PASSWORD)
        debug_info.append("Successfully logged in")
        
        mail.select('inbox')
        
        # Search for ALL recent emails (not just unread) to catch already-read replies
        status, messages = mail.search(None, 'ALL')
        
        if status != 'OK':
            return {
                "status": "error",
                "message": "Failed to search emails",
                "debug": debug_info
            }
        
        email_ids = messages[0].split()
        debug_info.append(f"Found {len(email_ids)} total emails in inbox")
        
        # Get last 20 emails to check for recent replies
        recent_email_ids = email_ids[-20:] if len(email_ids) > 20 else email_ids
        
        processed_count = 0
        for email_id in recent_email_ids:
            try:
                # Fetch email
                status, msg_data = mail.fetch(email_id, '(RFC822)')
                
                if status != 'OK':
                    continue
                
                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        msg = email.message_from_bytes(response_part[1])
                        
                        # Get sender and subject
                        from_email = msg.get('From', '')
                        subject = msg.get('Subject', '')
                        date = msg.get('Date', '')
                        
                        processed_count += 1
                        
                        # Extract email body
                        body = ""
                        if msg.is_multipart():
                            for part in msg.walk():
                                if part.get_content_type() == "text/plain":
                                    try:
                                        body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                                        break
                                    except:
                                        pass
                        else:
                            try:
                                body = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
                            except:
                                body = str(msg.get_payload())
                        
                        # Check if this is a reply to our confirmation email
                        if 'Pending Approval' not in subject and 'Inventory Summary' not in subject and 'Restock Order' not in subject:
                            continue
                        
                        debug_info.append(f"Found relevant email: {subject[:50]}... from {from_email[:30]}")
                        
                        # Check for APPROVE or REJECT first
                        body_upper = body.upper()
                        subject_upper = subject.upper()
                        combined = body_upper + ' ' + subject_upper
                        
                        is_inventory_summary = 'Inventory Summary' in subject
                        
                        # Extract order IDs from subject or body (all occurrences)
                        combined_text = subject + ' ' + body
                        order_ids_found = re.findall(r'(ORD-[0-9]+|USER-ORD-[0-9]+|BULK-[A-Z0-9-]+)', combined_text)
                        order_id_match = re.search(r'Order ID:\s*([A-Z0-9-]+)', combined_text)
                        
                        # Handle APPROVE
                        if 'APPROVE' in combined:
                            # If multiple order IDs are present, approve each; otherwise if none, approve all
                            target_ids = order_ids_found if order_ids_found else []
                            if not target_ids and order_id_match:
                                target_ids = [order_id_match.group(1)]
                            if not target_ids:
                                debug_info.append("Found APPROVE without Order ID - approving ALL pending orders")
                                target_ids = [oid for oid, o in pending_orders_dict.items() if o.get('status') == 'pending_approval']

                            for oid in target_ids:
                                if oid not in pending_orders_dict:
                                    debug_info.append(f"Order {oid} not in pending orders")
                                    continue
                                order = pending_orders_dict[oid]
                                if order.get('status') != 'pending_approval':
                                    debug_info.append(f"Order {oid} status is {order.get('status')}, skipping")
                                    continue
                                result = tool_place_order(
                                    order_id=oid,
                                    supplier=order.get('supplier'),
                                    product=order.get('product'),
                                    quantity=order.get('quantity'),
                                    unit_price=order.get('unit_price'),
                                    total_cost=order.get('total_cost')
                                )
                                if result.get('status') == 'success':
                                    approved_orders.append({
                                        'order_id': oid,
                                        'product': order.get('product'),
                                        'confirmation': result.get('order_confirmation')
                                    })
                                    debug_info.append(f"Successfully placed order {oid}")
                                    
                                    # Send confirmation to manager
                                    manager_email = order.get('manager_email') or MANAGER_EMAIL
                                    if manager_email:
                                        try:
                                            send_simple_email(
                                                to_email=manager_email,
                                                subject=f"✅ Order {oid} Confirmed & Placed",
                                                body=f"""Your approval has been processed successfully.

Order ID: {oid}
Product: {order.get('product')}
Quantity: {order.get('quantity')}
Supplier: {order.get('supplier')}
Total Cost: ${order.get('total_cost', 0):.2f}

The order has been sent to the supplier.
Expected Delivery: {order.get('lead_time', '2-5 days')}

Thank you!"""
                                            )
                                            debug_info.append(f"Sent confirmation email to manager for {oid}")
                                        except Exception as e:
                                            debug_info.append(f"Failed to send manager confirmation: {str(e)}")
                                else:
                                    errors.append(f"Failed to place order {oid}: {result.get('message')}")
                            continue
                        
                        # Handle REJECT
                        elif 'REJECT' in combined:
                            target_ids = order_ids_found if order_ids_found else []
                            if not target_ids and order_id_match:
                                target_ids = [order_id_match.group(1)]
                            if not target_ids:
                                debug_info.append(f"Found REJECT without Order ID - rejecting ALL pending orders")
                                target_ids = [oid for oid, o in pending_orders_dict.items() if o.get('status') == 'pending_approval']

                            for oid in target_ids:
                                if oid not in pending_orders_dict:
                                    debug_info.append(f"Order {oid} not in pending orders")
                                    continue
                                pending_orders_dict[oid]['status'] = 'rejected'
                                order = pending_orders_dict[oid]
                                rejected_orders.append({
                                    'order_id': oid,
                                    'product': order.get('product')
                                })
                                
                                # Send rejection confirmation to manager
                                manager_email = order.get('manager_email') or MANAGER_EMAIL
                                if manager_email:
                                    try:
                                        send_simple_email(
                                            to_email=manager_email,
                                            subject=f"❌ Order {oid} Rejected",
                                            body=f"""Your rejection has been processed.

Order ID: {oid}
Product: {order.get('product')}
Quantity: {order.get('quantity')}
Supplier: {order.get('supplier')}
Total Cost: ${order.get('total_cost', 0):.2f}

The order has been cancelled and will not be placed with the supplier.

Thank you!"""
                                        )
                                        debug_info.append(f"Sent rejection confirmation email to manager for {oid}")
                                    except Exception as e:
                                        debug_info.append(f"Failed to send rejection confirmation: {str(e)}")
                            continue
                        else:
                            debug_info.append(f"No APPROVE or REJECT found in email body/subject")
                
            except Exception as e:
                errors.append(f"Error processing email: {str(e)}")
        
        mail.close()
        mail.logout()
        
        debug_info.append(f"Processed {processed_count} emails")
        debug_info.append(f"Processed {processed_count} emails")
        pending_orders_dict = get_pending_orders_dict()
        debug_info.append(f"Current pending orders: {list(pending_orders_dict.keys())}")
        
        # Explicitly save pending orders back to session state
        try:
            st.session_state.pending_orders = pending_orders_dict
        except:
            pass  # If not in Streamlit context, just use the dict as-is
        
        return {
            "status": "success",
            "approved": approved_orders,
            "rejected": rejected_orders,
            "approved_count": len(approved_orders),
            "rejected_count": len(rejected_orders),
            "errors": errors,
            "debug": debug_info
        }
    
    except Exception as e:
        debug_info.append(f"Exception: {str(e)}")
        return {
            "status": "error",
            "message": f"Failed to check emails: {str(e)}",
            "debug": debug_info
        }

# ===== INVENTORY SUMMARY & BULK CONFIRMATION =====
def tool_inventory_overview(threshold: int = 15) -> dict:
    """Aggregate inventory totals and flag items needing restock."""
    inventory = _load_inventory_data()
    history = load_purchase_history()
    hist_summary = summarize_history_by_product(history, lookback_days=90)
    total_stock = 0
    low_stock = []
    total_items = 0
    total_restock_cost = 0.0

    for item in inventory:
        if not isinstance(item, dict):
            continue
        total_items += 1
        stock = item.get("stock", 0) or 0
        total_stock += stock
        price = item.get("price", 0) or 0

        # Demand signal from history (avg daily sales). High if >=1/day, medium if >=0.25/day, else low.
        hist = hist_summary.get(item.get("product"), {}) if hist_summary else {}
        avg_daily = float(hist.get("avg_daily_sales", 0) or 0)
        demand_signal = "high" if avg_daily >= 1.0 else "medium" if avg_daily >= 0.25 else "low"

        if stock < threshold:
            restock_qty = estimate_restock_quantity(stock, demand_signal=demand_signal, lead_time=item.get("lead_time"))
            est_cost = float(price) * restock_qty if isinstance(price, (int, float)) else None
            if est_cost:
                total_restock_cost += est_cost
            low_stock.append({
                "product": item.get("product"),
                "stock": stock,
                "location": item.get("location"),
                "price": price,
                "restock_quantity": restock_qty,
                "estimated_cost": est_cost,
                "demand_level": demand_signal,
                "avg_daily_sales": avg_daily,
            })

    return {
        "status": "success",
        "total_stock": total_stock,
        "items_count": total_items,
        "low_stock": low_stock,
        "low_count": len(low_stock),
        "estimated_restock_cost": round(total_restock_cost, 2),
        "threshold": threshold,
    }


def tool_send_inventory_summary_email(summary: dict, manager_email: str) -> dict:
    """Send a consolidated inventory summary and restock recommendation email with Order IDs."""
    if not SENDER_EMAIL or not SENDER_PASSWORD or not manager_email:
        return {
            "status": "error",
            "message": "Email configuration incomplete. Set SENDER_EMAIL, SENDER_PASSWORD, MANAGER_EMAIL."
        }

    try:
        subject = "[Inventory Summary] Stock & Restock Recommendations"
        lines = []
        lines.append(f"Total stock units: {summary.get('total_stock')}")
        lines.append(f"Items tracked: {summary.get('items_count')}")
        lines.append(f"Items below threshold ({summary.get('threshold')}): {summary.get('low_count')}")
        lines.append("")
        lines.append("LOW STOCK ITEMS & RESTOCK ORDERS:")
        lines.append("=" * 80)
        
        pending_orders_dict = get_pending_orders_dict()
        created_order_ids = []
        
        if summary.get("low_stock"):
            for item in summary["low_stock"]:
                product = item['product']
                stock = item['stock']
                restock_qty = item['restock_quantity']
                est_cost = item.get('estimated_cost', 0)
                location = item.get('location', 'Unknown')
                
                # Create individual order for this item
                order_id = f"BULK-{datetime.now().strftime('%Y%m%d%H%M%S')}-{abs(hash(product))%10000:04d}"
                
                # Find supplier
                suppliers = tool_search_suppliers(product, location, base_price=item.get('price'), estimate_missing=ESTIMATE_MISSING_PRICES)
                best_supplier = suppliers[0] if suppliers else {"name": "Market Price", "price": item.get('price'), "lead_time": "3-6 days"}
                unit_price = best_supplier.get('price', item.get('price', 0))
                
                # Store order in pending
                pending_orders_dict[order_id] = {
                    "product": product,
                    "quantity": restock_qty,
                    "supplier": best_supplier.get('name'),
                    "unit_price": unit_price if isinstance(unit_price, (int, float)) else item.get('price', 0),
                    "total_cost": (unit_price if isinstance(unit_price, (int, float)) else item.get('price', 0)) * restock_qty,
                    "lead_time": best_supplier.get('lead_time', '3-6 days'),
                    "status": "pending_approval",
                    "created_at": datetime.now().isoformat(),
                    "manager_email": manager_email
                }
                created_order_ids.append(order_id)
                
                unit_price_display = format_inr(unit_price) if isinstance(unit_price, (int, float)) else "Market Price"
                lines.append("")
                lines.append(f"Order ID: {order_id}")
                lines.append(f"  Product: {product}")
                lines.append(f"  Current Stock: {stock} units")
                lines.append(f"  Restock Quantity: {restock_qty} units")
                lines.append(f"  Supplier: {best_supplier.get('name')}")
                lines.append(f"  Unit Price: {unit_price_display}")
                lines.append(f"  Total Cost: ₹{est_cost:,.2f}")
                lines.append(f"  Lead Time: {best_supplier.get('lead_time', '3-6 days')}")
        else:
            lines.append("- None")
        
        # Explicitly save pending orders back to session state
        try:
            st.session_state.pending_orders = pending_orders_dict
        except:
            pass  # If not in Streamlit context, just use the dict as-is
        
        lines.append("")
        lines.append("=" * 80)
        lines.append(f"Estimated total restock cost: ₹{summary.get('estimated_restock_cost', 0):,.2f}")
        lines.append("")
        lines.append("NEXT STEPS:")
        lines.append("1. Review all orders above")
        lines.append("2. Reply to this email with 'APPROVE' to place all orders")
        lines.append("3. System will automatically book orders with suppliers")
        lines.append("")
        lines.append(f"Order IDs for reference: {', '.join(created_order_ids)}")

        body = "\n".join(lines)

        msg = MIMEMultipart()
        msg["From"] = SENDER_EMAIL
        msg["To"] = manager_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()

        return {
            "status": "success",
            "message": f"Summary email sent to {manager_email}",
        }
    except Exception as e:
        return {"status": "error", "message": f"Failed to send summary email: {e}"}

# --- CONFIGURATION ---
# 🔑 Supply your Groq API key via env var
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "PASTE_YOUR_GROQ_API_KEY_HERE")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "PASTE_YOUR_GROQ_MODEL_HERE")

# Email Configuration
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD", "")
MANAGER_EMAIL = os.environ.get("MANAGER_EMAIL", "")
SELLER_EMAIL = os.environ.get("SELLER_EMAIL", "")

# IMAP Configuration (for checking email replies)
IMAP_SERVER = os.environ.get("IMAP_SERVER", "imap.gmail.com")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))

client = None
if GROQ_API_KEY and GROQ_API_KEY != "PASTE_YOUR_GROQ_API_KEY_HERE":
    client = Groq(api_key=GROQ_API_KEY)

# In-memory pending orders (in production, use a database)
PENDING_ORDERS = {}

# Initialize Streamlit session state for persistent pending orders
def init_session_state():
    """Initialize session state for persistent pending orders across reruns."""
    if "pending_orders" not in st.session_state:
        st.session_state.pending_orders = {}
    if "manager_email" not in st.session_state:
        st.session_state.manager_email = MANAGER_EMAIL  # Use .env config as default

def get_pending_orders_dict():
    """Get the pending orders dictionary (from session state if in Streamlit context)."""
    try:
        return st.session_state.pending_orders
    except:
        return PENDING_ORDERS

# ===== BACKEND TOOLS (Simulated) =====
def _extract_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""

def _guess_store_name(domain: str) -> str:
    mapping = {
        "amazon": "Amazon",
        "walmart": "Walmart",
        "target": "Target",
        "bestbuy": "Best Buy",
        "costco": "Costco",
        "homedepot": "Home Depot",
        "lowes": "Lowe's",
        "flipkart": "Flipkart",
        "myntra": "Myntra",
        "ajio": "Ajio",
        "reliancedigital": "Reliance Digital",
        "croma": "Croma",
        "mediamarkt": "MediaMarkt",
        "carrefour": "Carrefour",
        "argos": "Argos",
        "aliexpress": "AliExpress",
        "ebay": "eBay",
        "macys": "Macy's",
        "bhphotovideo": "B&H",
    }
    for key, name in mapping.items():
        if key in domain:
            return name
    # Fallback to cleaned domain
    return domain.split(":")[0].split("/")[0]

def _extract_price_from_text(text: str):
    """Extract numeric price and a display string from arbitrary text.
    Supports $, £, €, ₹, Rs, INR, USD and common formats like 1,299.99 or 1.299,99.
    Returns (price_value: float|None, price_text: str|None)
    """
    if not text:
        return None, None
    # Normalize whitespace
    t = " ".join(str(text).split())
    # Common currency-prefixed or suffixed amounts
    pattern = r"((?:US\$|\$|£|€|₹|Rs\.?|INR)\s*[0-9]{1,3}(?:[.,\s][0-9]{3})*(?:[.,][0-9]{2})?|[0-9]{1,3}(?:[.,\s][0-9]{3})*(?:[.,][0-9]{2})?\s*(?:USD|EUR|GBP|INR|Rs\.?))"
    m = re.search(pattern, t, flags=re.IGNORECASE)
    if not m:
        return None, None
    raw = m.group(1).strip()
    # Try to convert to float: prefer dot-decimal. If comma used as decimal, convert.
    cleaned = raw
    # Remove currency words/symbols for numeric parse
    cleaned = re.sub(r"(US\$|\$|£|€|₹|Rs\.?|INR|USD|EUR|GBP|INR)", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip()
    # Replace thousand separators
    # If both comma and dot present, assume comma thousands, dot decimals (1,234.56)
    if "," in cleaned and "." in cleaned:
        cleaned_num = cleaned.replace(",", "")
    else:
        # If only comma present, assume comma decimals (1.234,56 or 1,23)
        if "," in cleaned and "." not in cleaned:
            cleaned_num = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned_num = cleaned
    # Remove stray spaces
    cleaned_num = cleaned_num.replace(" ", "")
    try:
        val = float(cleaned_num)
    except Exception:
        val = None
    return val, raw


def tool_llm_check_order_status(order_id: str) -> dict:
    """Use LLM to summarize the current status of an order by ID.
    Falls back to deterministic summary if LLM key is missing.
    """
    orders = get_pending_orders_dict()
    if order_id not in orders:
        # try fuzzy search
        best_id = None
        best_ratio = 0.0
        for oid in orders.keys():
            r = SequenceMatcher(None, order_id, oid).ratio()
            if r > best_ratio:
                best_ratio, best_id = r, oid
        if best_id and best_ratio > 0.6:
            order_id = best_id
        else:
            return {"status": "error", "message": f"Order {order_id} not found"}

    o = orders[order_id]
    summary = {
        "order_id": order_id,
        "status": o.get("status"),
        "product": o.get("product"),
        "supplier": o.get("supplier"),
        "quantity": o.get("quantity"),
        "unit_price": o.get("unit_price"),
        "total_cost": o.get("total_cost"),
        "created_at": o.get("created_at"),
        "lead_time": o.get("lead_time"),
        "confirmation": o.get("confirmation"),
        "seller_confirmation": o.get("seller_confirmation"),
        "error": o.get("error"),
    }

    if client is None:
        return {"status": "success", "summary": summary, "explanation": "LLM not configured; returning structured status only."}

    try:
        prompt = (
            "You are an operations assistant. Given this order record, provide a clear one-paragraph status "
            "update for a manager. Include current state, last known action, and next step if any. Be precise.\n\n"
            f"Order Record (JSON):\n{json.dumps(summary, default=str)}\n"
        )
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=200,
        )
        explanation = resp.choices[0].message.content.strip()
        return {"status": "success", "summary": summary, "explanation": explanation}
    except Exception as e:
        return {"status": "success", "summary": summary, "explanation": f"LLM failed: {e}"}

def tool_search_suppliers(product: str, location: str, max_results: int = 6, base_price: float | None = None, estimate_missing: bool | None = None) -> list:
    """Backend: Searches nearby/regional and e-commerce sources for the product.
    Uses DuckDuckGo web search if available; otherwise returns a curated fallback list.
    """
    aggregated = []
    seen_domains = set()
    if estimate_missing is None:
        estimate_missing = ESTIMATE_MISSING_PRICES

    def _add_source(name, price_value=None, price_text=None, lead_time="2-5 days (est)", reliability="High", url=None, is_est: bool=False):
        display = price_text if price_text else (f"₹{price_value:,.2f}" if isinstance(price_value, (int, float)) else "Unknown")
        if is_est and display != "Unknown":
            display = f"Est. {display}"
        aggregated.append({
            "name": name,
            "price": price_value,
            "price_text": display,
            "lead_time": lead_time,
            "reliability": reliability,
            "url": url,
        })

    store_keywords = [
        "amazon", "walmart", "target", "bestbuy", "costco", "homedepot", "lowes",
        "flipkart", "myntra", "ajio", "reliancedigital", "croma", "mediamarkt", "carrefour",
        "argos", "aliexpress", "ebay", "macys", "bhphotovideo"
    ]

    queries = [
        f"buy {product} in {location}",
        f"{product} near {location}",
        f"{product} price {location}",
        f"order {product} online {location}",
        # Store-specific price queries help surface price-bearing snippets
        f"site:amazon.com {product} price",
        f"site:walmart.com {product} price",
        f"site:target.com {product} price",
        f"site:bestbuy.com {product} price",
    ]

    if _DDG_AVAILABLE:
        try:
            with DDGS() as ddgs:
                # Prefer shopping results for real price data
                for r in ddgs.shopping(product, region="wt-wt", safesearch="moderate", max_results=20):
                    url = r.get("link") or r.get("url") or ""
                    title = r.get("title") or ""
                    price_text = r.get("price") or r.get("price_from") or r.get("price_to") or r.get("price_str")
                    currency = r.get("currency") or ""
                    domain = _extract_domain(url)
                    if not domain:
                        continue
                    if not any(k in domain for k in store_keywords):
                        continue
                    if domain in seen_domains:
                        continue
                    seen_domains.add(domain)
                    name = _guess_store_name(domain)
                    # Prefer the shopping-provided currency + amount when present
                    price_val, price_txt = _extract_price_from_text((price_text or "").strip() or f"{currency} {price_text}" if currency and price_text else title)
                    reliability = "Very High" if name in {"Amazon", "Walmart", "Target", "Best Buy"} else "High"
                    # If no price found but estimation enabled, estimate from base_price
                    if (price_val is None and not price_txt) and estimate_missing and isinstance(base_price, (int, float)):
                        price_val = float(base_price)
                        price_txt = None
                        _add_source(name, price_val, price_txt, lead_time="2-5 days (est)", reliability=reliability, url=url, is_est=True)
                    else:
                        _add_source(name, price_val, price_txt, lead_time="2-5 days (est)", reliability=reliability, url=url)
                    if len(aggregated) >= max_results:
                        break

                # If still not enough, use text results as a secondary source
                if len(aggregated) < max_results:
                    for q in queries:
                        for r in ddgs.text(q, max_results=10):
                            url = r.get("href") or r.get("url") or ""
                            title = r.get("title") or ""
                            body = r.get("body") or ""
                            domain = _extract_domain(url)
                            if not domain:
                                continue
                            if any(k in domain for k in store_keywords):
                                if domain in seen_domains:
                                    continue
                                seen_domains.add(domain)
                                name = _guess_store_name(domain)
                                # Extract price from title or body, preferring explicit price-bearing snippets
                                pval, ptxt = _extract_price_from_text(title)
                                if not pval and not ptxt:
                                    pval, ptxt = _extract_price_from_text(body)
                                reliability = "Very High" if name in {"Amazon", "Walmart", "Target", "Best Buy"} else "High"
                                if (pval is None and not ptxt) and estimate_missing and isinstance(base_price, (int, float)):
                                    pval = float(base_price)
                                    ptxt = None
                                    _add_source(name, pval, ptxt, lead_time="2-5 days (est)", reliability=reliability, url=url, is_est=True)
                                else:
                                    _add_source(name, pval, ptxt, lead_time="2-5 days (est)", reliability=reliability, url=url)
                                if len(aggregated) >= max_results:
                                    break
                        if len(aggregated) >= max_results:
                            break
        except Exception:
            pass

    # Product- and region-aware curated fallbacks to avoid generic repeats
    if len(aggregated) < max_results:
        p = (product or "").lower()
        regional_extra = []
        if "new york" in (location or "").lower():
            regional_extra.extend(["Macy's", "B&H", "REI", "Uniqlo"])
        if "los angeles" in (location or "").lower():
            regional_extra.extend(["REI", "Target", "Foot Locker"])
        if "chicago" in (location or "").lower():
            regional_extra.extend(["Dick's Sporting Goods", "Foot Locker", "REI"])
        if "miami" in (location or "").lower():
            regional_extra.extend(["Sunglass Hut", "Macy's", "Target"])
        if "seattle" in (location or "").lower():
            regional_extra.extend(["REI", "Costco", "Target"])
        if "portland" in (location or "").lower():
            regional_extra.extend(["Columbia Store", "REI", "Cabela's"])

        by_product = []
        if any(k in p for k in ["jacket", "coat", "winter"]):
            by_product = ["REI", "Columbia", "The North Face", "Uniqlo", "Macy's"]
        elif any(k in p for k in ["hat", "cap"]):
            by_product = ["Target", "H&M", "Zara", "Amazon", "Walmart"]
        elif any(k in p for k in ["shoe", "sneaker", "running"]):
            by_product = ["Nike", "Adidas", "Foot Locker", "Finish Line", "Dick's Sporting Goods"]
        elif any(k in p for k in ["sunglass", "glasses"]):
            by_product = ["Sunglass Hut", "Ray-Ban", "Macy's", "Amazon"]
        elif any(k in p for k in ["backpack", "bag"]):
            by_product = ["REI", "Decathlon", "Amazon", "Walmart"]
        elif any(k in p for k in ["raincoat", "rain jacket", "poncho"]):
            by_product = ["Columbia", "Decathlon", "REI", "Amazon"]

        fallback_pool = [
            (name, None) for name in (
                ["Amazon", "Walmart", "Target"] + regional_extra + by_product
            )
        ]

        for name, price in fallback_pool:
            if len(aggregated) >= max_results:
                break
            if not any(name == x["name"] for x in aggregated):
                # Estimate from inventory base price if allowed
                est_price = None
                est_text = None
                is_est = False
                if estimate_missing and isinstance(base_price, (int, float)):
                    # Store-specific multipliers
                    factors = {
                        "amazon": 1.00, "walmart": 0.98, "target": 1.02, "best buy": 1.05,
                        "rei": 1.10, "macy": 1.08, "b&h": 1.03, "home depot": 1.04,
                        "costco": 0.97, "nike": 1.05, "adidas": 1.05, "foot locker": 1.06,
                        "uniqlo": 0.95, "columbia": 1.07, "the north face": 1.10,
                    }
                    key = name.lower()
                    factor = next((v for k, v in factors.items() if k in key), 1.03)
                    est_price = round(float(base_price) * factor, 2)
                    is_est = True
                _add_source(name, est_price, est_text, lead_time="3-6 days (est)", reliability="High", is_est=is_est)

    return aggregated

def tool_calculate_pricing(product: str, stock: int, demand_level: str, base_price: float | None = None) -> dict:
    """Backend: Suggests pricing based on demand and stock levels.
    If base_price is provided (e.g., from inventory), use it; otherwise fall back to defaults.
    """
    default_base_prices = {"Winter Jackets": 50, "Summer Hats": 15, "Running Shoes": 55}
    base = base_price if (isinstance(base_price, (int, float)) and base_price > 0) else default_base_prices.get(product, 25)

    adjustments = {
        "high": 1.2,   # raise price
        "medium": 1.0, # keep same
        "low": 0.85,   # lower price
    }
    multiplier = adjustments.get(str(demand_level).lower(), 1.0)

    low_stock_boost = 1.15 if (isinstance(stock, (int, float)) and stock < 10) else 1.0
    suggested = round(float(base) * multiplier * low_stock_boost, 2)
    return {
        "suggested_price": suggested,
        "reasoning": f"Base {base} × demand-factor {multiplier} × stock-factor {low_stock_boost}"
    }

def tool_analyze_restock_decision(stock: int, lead_time_days: int, threshold: int = 15) -> dict:
    """Backend: Analyzes whether to restock based on stock level and lead time."""
    decision = "RESTOCK" if stock < threshold else "HOLD"
    urgency = "URGENT" if stock < 5 else "STANDARD"
    return {
        "decision": decision,
        "urgency": urgency,
        "reasoning": f"Stock={stock}, Lead Time={lead_time_days}d, Threshold={threshold}"
    }

# ===== DEMAND SIGNAL DETECTOR =====
def detect_demand_signal(user_input: str) -> str:
    """Analyzes user input to infer demand level: high, medium, or low."""
    text = (user_input or "").lower()
    high_keywords = ["urgent", "high demand", "shortage", "selling fast", "increased demand", 
                     "peak season", "hot item", "trending", "popular", "rush"]
    low_keywords = ["slow", "low demand", "oversupply", "excess", "clearance", 
                    "off-season", "declining", "weak sales", "low interest"]
    
    if any(k in text for k in high_keywords):
        return "high"
    elif any(k in text for k in low_keywords):
        return "low"
    return "medium"


def parse_user_order_request(user_input: str, inventory_data: list) -> dict | None:
    """Parse user input for direct order placement requests.
    E.g., 'place order for 50 units of Winter Jackets', 'order 100 Running Shoes', etc.
    Returns {product, quantity, found_item} or None if not a valid order request.
    """
    text = (user_input or "").lower()
    order_keywords = ["order", "place order", "reorder", "buy", "purchase", "stock up", "stock"]
    is_order_request = any(k in text for k in order_keywords)
    
    if not is_order_request:
        return None
    
    # Extract quantity: look for patterns like "50 units", "50 of", etc.
    qty_match = re.search(r"(\d+)\s*(?:units?|of|x)?", text, re.IGNORECASE)
    if not qty_match:
        return None
    quantity = int(qty_match.group(1))
    
    # Extract product name by finding text after quantity
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
    
    # Fuzzy fallback if exact match failed
    if not found_item:
        found_item = _find_similar_product(remainder, inventory_data, threshold=0.5)
    
    if found_item:
        return {"product": found_item.get("product"), "quantity": quantity, "found_item": found_item}
    return None


def agent_user_order_placement(order_request: dict, manager_email: str = None) -> dict:
    """Handle user-requested order (direct placement request with custom quantity)."""
    found_item = order_request.get("found_item")
    requested_qty = order_request.get("quantity", 0)
    product = found_item.get("product")
    location = found_item.get("location", "Unknown")
    
    if requested_qty <= 0:
        return {"status": "error", "message": "Invalid quantity requested"}
    
    # Use supplier from inventory if available, otherwise search
    supplier_name = found_item.get("supplier", None)
    unit_price = found_item.get("price", 0)
    lead_time = "3-6 days"  # Default lead time
    
    if not supplier_name:
        # Fallback to supplier search if not in inventory
        suppliers = tool_search_suppliers(product, location, base_price=unit_price, estimate_missing=ESTIMATE_MISSING_PRICES)
        if not suppliers:
            return {"status": "error", "message": f"No suppliers found for {product}"}
        best_supplier = suppliers[0]
        supplier_name = best_supplier.get("name")
        unit_price = best_supplier.get("price", unit_price)
        lead_time = best_supplier.get("lead_time", "2-5 days")
    
    total_cost = float(unit_price) * requested_qty if isinstance(unit_price, (int, float)) else None
    
    if not manager_email:
        return {"status": "error", "message": "Manager email not configured. Cannot send approval request."}
    
    # Generate order ID
    order_id = f"USER-ORD-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    
    # Send confirmation email
    email_result = tool_send_confirmation_email(
        product=product,
        quantity=requested_qty,
        supplier=supplier_name,
        unit_price=unit_price if isinstance(unit_price, (int, float)) else 0,
        total_cost=total_cost if total_cost else 0,
        lead_time=lead_time,
        manager_email=manager_email,
        order_id=order_id
    )
    
    return {
        "status": "pending_approval",
        "order_type": "user_requested",
        "order_id": order_id,
        "product": product,
        "requested_quantity": requested_qty,
        "supplier": supplier_name,
        "unit_price": unit_price,
        "total_cost": total_cost,
        "lead_time": lead_time,
        "email_sent": email_result.get("status") == "success",
        "email_message": email_result.get("message")
    }


def _parse_lead_time_days(lead_time: str | None) -> int:
    """Extract a conservative day-count from a lead-time string (e.g., "2-5 days")."""
    if lead_time is None:
        return 5
    try:
        nums = [int(n) for n in re.findall(r"\d+", str(lead_time))]
        if not nums:
            return 5
        return max(nums)
    except Exception:
        return 5


def estimate_restock_quantity(stock: int, demand_signal: str = "medium", lead_time: str | int | None = None,
                              min_batch: int = 10, max_batch: int = 250) -> int:
    """Estimate restock quantity using demand, current stock, and lead time (no static cap)."""
    demand_factor_map = {"high": 1.5, "medium": 1.0, "low": 0.7}
    demand_factor = demand_factor_map.get(str(demand_signal).lower(), 1.0)

    lead_days = lead_time if isinstance(lead_time, (int, float)) else _parse_lead_time_days(lead_time)
    lead_factor = 1 + max(0, lead_days - 5) * 0.05  # +5% buffer per extra day beyond 5

    # Dynamic base target by demand level (smaller, demand-led)
    # high: plan roughly ~45 units, medium: ~30, low: ~18 before lead-time multiplier
    base_target_map = {"high": 45, "medium": 30, "low": 18}
    base_target = base_target_map.get(str(demand_signal).lower(), 30)

    target_stock = base_target * demand_factor * lead_factor

    needed = target_stock - (stock or 0)
    if needed <= 0:
        return 0

    qty = int(math.ceil(needed))
    qty = max(min_batch, qty)
    qty = min(max_batch, qty)
    return qty

# ===== SPECIALIZED AGENTS =====
def agent_decision_maker(inventory_item: dict, user_context: str) -> dict:
    """Agent 1: Makes restock/hold decisions."""
    stock = inventory_item.get('stock')
    lead_time_days = 3  # Default lead time estimate
    result = tool_analyze_restock_decision(stock, lead_time_days)
    return result

def agent_pricing_strategist(inventory_item: dict, user_context: str, demand_signal: str = "medium") -> dict:
    """Agent 2: Recommends pricing strategy."""
    product = inventory_item.get('product')
    stock = inventory_item.get('stock')
    base_price = inventory_item.get('price')
    result = tool_calculate_pricing(product, stock, demand_signal, base_price)
    return result

def agent_sourcing_specialist(inventory_item: dict, user_context: str) -> dict:
    """Agent 3: Identifies low-cost sourcing options."""
    product = inventory_item.get('product')
    location = inventory_item.get('location')
    suppliers = tool_search_suppliers(product, location, base_price=inventory_item.get('price'), estimate_missing=ESTIMATE_MISSING_PRICES)
    
    if suppliers:
        # Sort by numeric price when available; unknowns go last
        def _price_key(x):
            v = x.get('price')
            return float(v) if isinstance(v, (int, float)) else float('inf')
        sorted_suppliers = sorted(suppliers, key=_price_key)
        return {
            "sources": sorted_suppliers,
            "recommended": sorted_suppliers[0],
            "alternatives": sorted_suppliers[1:],
        }
    return {"sources": [], "recommended": None}

def agent_order_handler(inventory_item: dict, user_context: str, manager_email: str = None) -> dict:
    """Agent 4: Handles order placement and approval workflow."""
    product = inventory_item.get('product')
    stock = inventory_item.get('stock', 0)
    location = inventory_item.get('location')
    base_price = inventory_item.get('price')
    demand_signal = detect_demand_signal(user_context)
    
    # Determine if restocking is needed
    if stock > 15:
        return {
            "status": "no_action",
            "reason": "Current stock levels are sufficient",
            "current_stock": stock
        }
    
    # Get supplier recommendations
    suppliers = tool_search_suppliers(product, location, base_price=base_price, estimate_missing=ESTIMATE_MISSING_PRICES)
    
    if not suppliers:
        return {
            "status": "error",
            "reason": "No suppliers found"
        }
    
    best_supplier = suppliers[0]
    supplier_name = best_supplier.get('name')
    unit_price = best_supplier.get('price', base_price)
    lead_time = best_supplier.get('lead_time', '2-5 days')
    
    # Estimate restock quantity using demand and supplier lead time
    lead_time_days = _parse_lead_time_days(lead_time)
    restock_quantity = estimate_restock_quantity(stock, demand_signal, lead_time_days)
    
    if unit_price is None:
        unit_price = base_price
    
    total_cost = float(unit_price) * restock_quantity if isinstance(unit_price, (int, float)) else None
    
    if not manager_email or manager_email == "PASTE_YOUR_MANAGER_EMAIL_HERE":
        return {
            "status": "pending_config",
            "message": "Manager email not configured",
            "action": "Set MANAGER_EMAIL environment variable to enable order approval"
        }
    
    # Generate order ID
    order_id = f"ORD-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    
    # Send confirmation email
    email_result = tool_send_confirmation_email(
        product=product,
        quantity=restock_quantity,
        supplier=supplier_name,
        unit_price=unit_price if isinstance(unit_price, (int, float)) else 0,
        total_cost=total_cost if total_cost else 0,
        lead_time=lead_time,
        manager_email=manager_email,
        order_id=order_id
    )
    
    return {
        "status": "pending_approval",
        "order_id": order_id,
        "product": product,
        "restock_quantity": restock_quantity,
        "current_stock": stock,
        "supplier": supplier_name,
        "unit_price": unit_price,
        "total_cost": total_cost,
        "lead_time": lead_time,
        "email_sent": email_result.get("status") == "success",
        "email_message": email_result.get("message")
    }

# ===== ORCHESTRATOR/ROUTER =====
def orchestrator_agent(inventory_item: dict, user_input: str, manager_email: str = None) -> dict:
    """
    Router/Orchestrator: Routes user query to appropriate specialized agents.
    Calls each agent, collects results, coordinates tools.
    Supports product lookup from user input.
    """
    status_log = []
    
    user_lower = (user_input or "").lower()
    inventory_data = _load_inventory_data()

    # PRIORITY: inventory listing queries
    inventory_query_keywords = [
        "what items", "what products", "available items", "available products",
        "list items", "list products", "show inventory", "what's in inventory",
        "items in inventory", "products in inventory", "all items", "all products",
        "inventory list", "what do you have", "what items are available",
        "show all", "all available", "complete inventory", "full inventory",
        "items avaliable", "avaliable in inventory",  # typos
        "items available in inventory", "products available in inventory",
        "what are the items available", "what are the items available in inventory",
        "show items available", "show products available", "all inventory items"
    ]
    heuristic_inventory_list = ("inventory" in user_lower) and (
        "item" in user_lower or "product" in user_lower or "available" in user_lower
    )
    is_inventory_list_query = any(k in user_lower for k in inventory_query_keywords) or heuristic_inventory_list

    if is_inventory_list_query:
        status_log.append("🔍 **Query Type:** Inventory listing request detected")
        if not inventory_data:
            return {"logs": status_log + ["❌ No inventory data found"], "response": "No inventory items found in the system."}

        response = "### AVAILABLE INVENTORY ITEMS\n\n" + f"**Total Items:** {len(inventory_data)}\n\n"
        for idx, item in enumerate(inventory_data, 1):
            if isinstance(item, dict):
                product_name = item.get("product", "Unknown")
                stock = item.get("stock", 0)
                price = item.get("price", 0)
                location = item.get("location", "Unknown")
                response += f"{idx}. **{product_name}**\n"
                response += f"   - Stock: {stock} units\n"
                response += f"   - Price: {format_inr(price)}\n"
                response += f"   - Location: {location}\n"
                if stock < 5:
                    response += "   - Status: 🔴 CRITICAL - Immediate restock needed\n"
                elif stock < 15:
                    response += "   - Status: 🟡 LOW - Restock recommended\n"
                else:
                    response += "   - Status: 🟢 GOOD - Sufficient stock\n"
                response += "\n"
        response += "\n**Need to check a specific item?** Just mention the product name in your query.\n"
        response += "**Need to restock?** Ask about restocking any low-stock item."
        status_log.append(f"✅ Listed {len(inventory_data)} inventory items")
        return {"logs": status_log, "response": response}

    # CHECK FOR USER ORDER REQUEST (e.g., "order 50 Winter Jackets")
    order_request = parse_user_order_request(user_input, inventory_data)
    if order_request:
        status_log.append("🛒 **Query Type:** User-initiated order request detected")
        status_log.append(f"📋 **Order Details:** {order_request['quantity']} units of {order_request['product']}")
        
        # Process the order
        order_result = agent_user_order_placement(order_request, manager_email=manager_email)
        
        if order_result.get("status") == "error":
            status_log.append(f"❌ **Order Error:** {order_result.get('message')}")
            response = f"**Order Placement Failed**\n\n{order_result.get('message')}"
        else:
            status_log.append(f"✅ **Order Pending Approval:** Order ID {order_result.get('order_id')}")
            response = f"""### 🛒 Order Request Submitted

**Product:** {order_result.get('product')}  
**Quantity Requested:** {order_result.get('requested_quantity')} units  
**Supplier:** {order_result.get('supplier')}  
**Unit Price:** {format_inr(order_result.get('unit_price'))}  
**Total Cost:** {format_inr(order_result.get('total_cost', 0))}  
**Lead Time:** {order_result.get('lead_time')}  
**Order ID:** `{order_result.get('order_id')}`

**Status:** ⏳ Pending Manager Approval

An approval request has been sent to your manager at {manager_email}. Once approved, the order will be automatically placed with the supplier.
"""
        
        return {"logs": status_log, "response": response}

    # CHECK FOR ORDER TRACKING REQUEST (e.g., "track USER-ORD-20251220130734" or "where is my order")
    tracking_keywords = ["track", "tracking", "location", "where is", "status of order", "order status", "shipment"]
    is_tracking_query = any(keyword in user_lower for keyword in tracking_keywords)
    
    if is_tracking_query:
        # Try to extract order ID
        order_id_match = re.search(r'(USER-ORD-[0-9]+|BULK-[A-Z0-9-]+|ORD-[0-9]+)', user_input, re.IGNORECASE)
        
        if order_id_match:
            order_id = order_id_match.group(1).upper()
            status_log.append(f"📍 **Query Type:** Order tracking request for {order_id}")
            
            tracking_result = tool_track_order(order_id)
            
            if tracking_result.get("status") == "success":
                tracking = tracking_result.get("tracking", {})
                status_log.append(f"✅ **Tracking Found:** {len(tracking.get('tracking_history', []))} events")
                
                response = f"""### 📦 Order Tracking - {order_id}

**Product:** {tracking.get('product')}  
**Quantity:** {tracking.get('quantity')} units  
**Supplier:** {tracking.get('supplier')}  
**Current Status:** {tracking.get('current_status', 'Unknown')}  
**Current Location:** 📍 {tracking.get('current_location', 'Unknown')}

---

### 📍 Tracking History

"""
                for event in tracking.get('tracking_history', []):
                    timestamp = event.get('timestamp', '')
                    if timestamp:
                        try:
                            dt = datetime.fromisoformat(timestamp)
                            formatted_time = dt.strftime('%b %d, %Y %I:%M %p')
                        except:
                            formatted_time = timestamp
                    else:
                        formatted_time = 'N/A'
                    
                    response += f"""
**{event.get('status', 'Unknown')}**  
📍 Location: {event.get('location', 'Unknown')}  
🕐 Time: {formatted_time}  
_{event.get('description', '')}_

---
"""
                
                return {"logs": status_log, "response": response}
            else:
                status_log.append(f"❌ **Tracking Error:** {tracking_result.get('message')}")
                response = f"**Order Not Found**\n\n{tracking_result.get('message')}"
                return {"logs": status_log, "response": response}
        else:
            # No order ID provided, show all trackable orders
            pending_orders_dict = get_pending_orders_dict()
            trackable = {oid: o for oid, o in pending_orders_dict.items() 
                        if o.get("status") in ["confirmed", "seller_confirmed"]}
            
            if trackable:
                status_log.append(f"📋 **Query Type:** Showing all trackable orders ({len(trackable)} found)")
                response = "### 📦 Trackable Orders\n\n"
                response += f"Found **{len(trackable)}** orders that can be tracked:\n\n"
                for oid, order in trackable.items():
                    response += f"- **{oid}** - {order.get('product')} ({order.get('status')})\n"
                response += "\n💡 **Tip:** To track a specific order, say 'track {order_id}'"
                return {"logs": status_log, "response": response}
            else:
                status_log.append("❌ **No trackable orders found**")
                response = "No orders are currently in transit. Orders must be confirmed before tracking is available."
                return {"logs": status_log, "response": response}

    # Product resolution (when not an inventory list query)
    product_lookup = None

    # 1) Exact/substring match
    for item in inventory_data:
        item_name = item.get("product", "").lower()
        if item_name and (item_name in user_lower or user_lower in item_name):
            product_lookup = item
            status_log.append(f"🔍 **Product Lookup:** Found '{item.get('product')}' in inventory via direct match")
            break

    # 2) Fuzzy match on entire query if direct match failed
    if not product_lookup and inventory_data:
        best_match = None
        best_score = 0.0
        for item in inventory_data:
            item_name = item.get("product", "").lower()
            if not item_name:
                continue
            score = SequenceMatcher(None, user_lower, item_name).ratio()
            if score > best_score:
                best_score = score
                best_match = item
        if best_match and best_score >= 0.45:  # tolerant threshold for short names
            product_lookup = best_match
            status_log.append(f"🔍 **Product Lookup:** Fuzzy-matched '{best_match.get('product')}' (score {best_score:.2f})")

    # 3) Word-level fuzzy fallback (for short noisy inputs)
    if not product_lookup:
        words = [w for w in user_lower.split() if len(w) > 3]
        for word in words:
            match = _find_similar_product(word, inventory_data)
            if match:
                product_lookup = match
                status_log.append(f"🔍 **Product Lookup:** Matched '{match.get('product')}' from token '{word}'")
                break
    
    # Use product lookup if found, otherwise use provided inventory_item
    active_item = product_lookup if product_lookup else inventory_item
    
    if not active_item or not active_item.get('product'):
        available = [i.get("product") for i in inventory_data if isinstance(i, dict)]
        return {
            "logs": ["❌ No product selected and no product found in query"],
            "response": "Please mention a product name. Available inventory: " + ", ".join(available)
        }
    
        # Check if user is asking about available items
        inventory_check_triggers = ("available", "show items", "what items", "list", "all items", "inventory", "what's available", "what products", "show inventory", "display items")
        is_inventory_query = any(t in user_lower for t in inventory_check_triggers)
    
        if is_inventory_query and not product_lookup:
            # User is asking about available items - show full inventory
            status_log.append("📋 **Query Type:** Inventory Overview")
            if not inventory_data:
                return {
                    "logs": status_log + ["❌ No items in inventory"],
                    "response": "No items found in inventory."
                }
        
            # Build detailed inventory display
            response_lines = ["## 📦 Available Inventory\n"]
            response_lines.append(f"**Total Products:** {len(inventory_data)}\n")
            response_lines.append("---\n")
        
            total_stock = 0
            for idx, item in enumerate(inventory_data, 1):
                if not isinstance(item, dict):
                    continue
                product = item.get("product", "Unknown")
                stock = item.get("stock", 0) or 0
                location = item.get("location", "N/A")
                price = item.get("price", "N/A")
                price_display = format_inr(price) if isinstance(price, (int, float)) else str(price)
            
                total_stock += stock
            
                # Color code stock status
                status_indicator = "🟢" if stock > 20 else "🟡" if stock > 10 else "🔴"
            
                response_lines.append(f"{idx}. **{product}** {status_indicator}")
                response_lines.append(f"   - Stock: {stock} units")
                response_lines.append(f"   - Location: {location}")
                response_lines.append(f"   - Price: {price_display}")
                response_lines.append("")
        
            response_lines.append("---\n")
            response_lines.append(f"**Total Stock Across All Items:** {total_stock} units\n")
            response_lines.append("\n_Legend: 🟢 Good (>20) | 🟡 Medium (11-20) | 🔴 Low (<11)_")
        
            status_log.append(f"✅ Displayed {len(inventory_data)} products from inventory")
        
            return {
                "logs": status_log,
                "response": "\n".join(response_lines)
            }
    
        product = active_item.get('product')
    stock = active_item.get('stock')
    location = active_item.get('location')
    
    status_log.append(f"🔍 **User Query:** {user_input}")
    status_log.append(f"📦 **Context:** Product={product}, Stock={stock}, Location={location}")
    
    # Step 1: Router decides which agents to activate
    status_log.append("🤖 **Router Decision:** Activating Decision Maker, Pricing Strategist, Sourcing Specialist, Order Handler")

    # Optional: If user asks global inventory question, compute auto plan (LLM-based)
    global_check_triggers = ("check inventory", "total inventory", "what to restock", "inventory summary", "restock plan")
    run_global_plan = any(t in user_lower for t in global_check_triggers)
    
    # Step 2: Detect demand from user input
    demand_level = detect_demand_signal(user_input)
    status_log.append(f"📊 **Detected Demand Level:** {demand_level.upper()}")
    
    # Step 3: Call specialized agents (they internally call tools)
    status_log.append("⚙️ **Agents are reasoning and calling tools...**")
    
    decision_result = agent_decision_maker(active_item, user_input)
    pricing_result = agent_pricing_strategist(active_item, user_input, demand_signal=demand_level)
    sourcing_result = agent_sourcing_specialist(active_item, user_input)
    order_result = agent_order_handler(active_item, user_input, manager_email=manager_email)
    
    # Global inventory planning if requested
    global_plan_block = ""
    if run_global_plan:
        try:
            plan_log, plan_md = run_inventory_planner_llm(user_input=user_input, manager_email=manager_email)
            for l in plan_log:
                status_log.append(l)
            global_plan_block = "\n" + plan_md + "\n"
        except Exception as e:
            status_log.append(f"⚠️ Inventory planner error: {e}")

    status_log.append("✅ **Tools executed. Formatting final response...**")
    
    # Step 3: Form final response
    final_response = f"""
### PRODUCT INFORMATION
**Product:** {product}
**Current Stock:** {stock} units
**Location:** {location}
**Base Price:** {format_inr(active_item.get('price', 'N/A'))}

### DECISION
{decision_result['decision']} ({decision_result['urgency']})
Reasoning: {decision_result['reasoning']}

### STRATEGY
Demand Level: {demand_level.upper()}
Suggested Price: {format_inr(pricing_result['suggested_price'])}
Cost Breakdown: {pricing_result['reasoning']}
Note: Suggested price reflects current stock levels and detected demand signals.

### SOURCING OPTIONS
"""
    
    if sourcing_result.get('sources'):
        for i, source in enumerate(sourcing_result['sources'], 1):
            price_display = source.get('price_text')
            if not price_display and isinstance(source.get('price'), (int, float)):
                price_display = format_inr(source['price'])
            if not price_display:
                price_display = "Unknown"
            final_response += f"""
- **Option {i}: {source['name']}**
  Unit Price: {price_display}
  Lead Time: {source.get('lead_time', 'N/A')}
  Reliability: {source.get('reliability', 'N/A')}
"""
    
    if sourcing_result.get('recommended'):
        rec = sourcing_result['recommended']
        price_display = rec.get('price_text')
        if not price_display and isinstance(rec.get('price'), (int, float)):
            price_display = format_inr(rec['price'])
        if not price_display:
            price_display = "Unknown"
        final_response += f"""
### RECOMMENDATION
**Chosen Source:** {rec['name']}
**Unit Cost:** {price_display}
**Lead Time:** {rec['lead_time']}
**Reliability:** {rec['reliability']}
**Next Steps:** Place order immediately to meet demand within {rec['lead_time']}.
"""
    
    # Add order status
    final_response += "\n### ORDER STATUS\n"
    if order_result.get('status') == 'pending_approval':
        final_response += f"""
✉️ **PENDING MANAGER APPROVAL**
- Order ID: {order_result['order_id']}
- Restock Quantity: {order_result['restock_quantity']} units
- Supplier: {order_result['supplier']}
- Unit Price: {format_inr(order_result['unit_price'])}
- Total Cost: {format_inr(order_result['total_cost'])}
- Lead Time: {order_result['lead_time']}

**Status:** Confirmation email sent to inventory manager at {manager_email}
**Action Required:** Await manager approval before order placement
"""
        status_log.append(f"📧 Order confirmation email sent to {manager_email}")
    elif order_result.get('status') == 'no_action':
        final_response += f"""
✅ **NO RESTOCKING NEEDED**
Current stock ({stock} units) is above restock threshold.
"""
    elif order_result.get('status') == 'pending_config':
        final_response += f"""
⚠️ **CONFIGURATION REQUIRED**
{order_result['message']}
{order_result['action']}
"""
    else:
        final_response += f"""
ℹ️ {order_result.get('reason', 'Order processing status unavailable')}
"""
    
    # Append global plan block if present
    if global_plan_block:
        final_response += "\n---\n" + global_plan_block

    return {"logs": status_log, "response": final_response}

def run_agent_logic(inventory_item, user_input: str = "", manager_email: str = None):
    """Main entry point: calls the orchestrator."""
    result = orchestrator_agent(inventory_item, user_input, manager_email=manager_email)
    return result["logs"], result["response"]

# ===== LangChain Orchestrator (Optional) =====
def run_agent_logic_langchain(inventory_item: dict, user_input: str = "", manager_email: str = None):
    """Uses LangChain + ChatGroq to format final output using tool results (no RAG)."""
    if not LANGCHAIN_AVAILABLE:
        raise RuntimeError("LangChain not installed. Run: pip install langchain-core langchain-groq")

    # --- Resolve product from user text (mirrors non-LC orchestrator) ---
    user_lower = (user_input or "").lower()
    inventory_data = _load_inventory_data()

    # Inventory list detection (LangChain path)
    inventory_query_keywords = [
        "what items", "what products", "available items", "available products",
        "list items", "list products", "show inventory", "what's in inventory",
        "items in inventory", "products in inventory", "all items", "all products",
        "inventory list", "what do you have", "what items are available",
        "show all", "all available", "complete inventory", "full inventory",
        "items avaliable", "avaliable in inventory",  # typos
        "items available in inventory", "products available in inventory",
        "what are the items available", "what are the items available in inventory",
        "show items available", "show products available", "all inventory items"
    ]
    heuristic_inventory_list = ("inventory" in user_lower) and (
        "item" in user_lower or "product" in user_lower or "available" in user_lower
    )
    is_inventory_list_query = any(k in user_lower for k in inventory_query_keywords) or heuristic_inventory_list

    if is_inventory_list_query:
        logs = ["🔍 **Query Type:** Inventory listing request detected (LangChain path)"]
        if not inventory_data:
            return logs, "No inventory items found in the system."

        response = "### AVAILABLE INVENTORY ITEMS\n\n" + f"**Total Items:** {len(inventory_data)}\n\n"
        for idx, item in enumerate(inventory_data, 1):
            if isinstance(item, dict):
                product_name = item.get("product", "Unknown")
                stock = item.get("stock", 0)
                price = item.get("price", 0)
                location = item.get("location", "Unknown")
                response += f"{idx}. **{product_name}**\n"
                response += f"   - Stock: {stock} units\n"
                response += f"   - Price: {format_inr(price)}\n"
                response += f"   - Location: {location}\n"
                if stock < 5:
                    response += "   - Status: 🔴 CRITICAL - Immediate restock needed\n"
                elif stock < 15:
                    response += "   - Status: 🟡 LOW - Restock recommended\n"
                else:
                    response += "   - Status: 🟢 GOOD - Sufficient stock\n"
                response += "\n"

        response += "\n**Need to check a specific item?** Just mention the product name in your query.\n"
        response += "**Need to restock?** Ask about restocking any low-stock item."

        logs.append(f"✅ Listed {len(inventory_data)} inventory items")
        return logs, response

    # CHECK FOR USER ORDER REQUEST (LangChain path) - e.g., "order 50 Winter Jackets"
    order_request = parse_user_order_request(user_input, inventory_data)
    if order_request:
        status_log = []
        status_log.append("🛒 **Query Type:** User-initiated order request detected (LangChain path)")
        status_log.append(f"📋 **Order Details:** {order_request['quantity']} units of {order_request['product']}")
        
        # Process the order
        order_result = agent_user_order_placement(order_request, manager_email=manager_email)
        
        if order_result.get("status") == "error":
            status_log.append(f"❌ **Order Error:** {order_result.get('message')}")
            response = f"**Order Placement Failed**\n\n{order_result.get('message')}"
        else:
            status_log.append(f"✅ **Order Pending Approval:** Order ID {order_result.get('order_id')}")
            response = f"""### 🛒 Order Request Submitted

**Product:** {order_result.get('product')}  
**Quantity Requested:** {order_result.get('requested_quantity')} units  
**Supplier:** {order_result.get('supplier')}  
**Unit Price:** {format_inr(order_result.get('unit_price'))}  
**Total Cost:** {format_inr(order_result.get('total_cost', 0))}  
**Lead Time:** {order_result.get('lead_time')}  
**Order ID:** `{order_result.get('order_id')}`

**Status:** ⏳ Pending Manager Approval

An approval request has been sent to your manager at {manager_email}. Once approved, the order will be automatically placed with the supplier.
"""
        
        return status_log, response

    product_lookup = None

    # If the user is asking to list/show available items, short-circuit and render inventory
    inventory_check_triggers = (
        "available", "show items", "what items", "list", "all items", "inventory",
        "what's available", "what products", "show inventory", "display items"
    )
    is_inventory_query = any(t in user_lower for t in inventory_check_triggers)
    status_log = []

    if is_inventory_query:
        status_log.append("📋 **Query Type:** Inventory Overview (LangChain bypass)")
        if not inventory_data:
            return status_log + ["❌ No items in inventory"], "No items found in inventory."

        response_lines = ["## 📦 Available Inventory\n"]
        response_lines.append(f"**Total Products:** {len(inventory_data)}\n")
        response_lines.append("---\n")

        total_stock = 0
        for idx, item in enumerate(inventory_data, 1):
            if not isinstance(item, dict):
                continue
            product = item.get("product", "Unknown")
            stock = item.get("stock", 0) or 0
            location = item.get("location", "N/A")
            price = item.get("price", "N/A")
            price_display = format_inr(price) if isinstance(price, (int, float)) else str(price)

            total_stock += stock

            status_indicator = "🟢" if stock > 20 else "🟡" if stock > 10 else "🔴"

            response_lines.append(f"{idx}. **{product}** {status_indicator}")
            response_lines.append(f"   - Stock: {stock} units")
            response_lines.append(f"   - Location: {location}")
            response_lines.append(f"   - Price: {price_display}")
            response_lines.append("")

        response_lines.append("---\n")
        response_lines.append(f"**Total Stock Across All Items:** {total_stock} units\n")
        response_lines.append("\n_Legend: 🟢 Good (>20) | 🟡 Medium (11-20) | 🔴 Low (<11)_")

        status_log.append(f"✅ Displayed {len(inventory_data)} products from inventory")
        return status_log, "\n".join(response_lines)

    # 1) Direct/substring match
    for item in inventory_data:
        item_name = item.get("product", "").lower()
        if item_name and (item_name in user_lower or user_lower in item_name):
            product_lookup = item
            break

    # 2) Fuzzy match on full query
    if not product_lookup and inventory_data:
        best_match = None
        best_score = 0.0
        for item in inventory_data:
            item_name = item.get("product", "").lower()
            if not item_name:
                continue
            score = SequenceMatcher(None, user_lower, item_name).ratio()
            if score > best_score:
                best_score = score
                best_match = item
        if best_match and best_score >= 0.45:
            product_lookup = best_match

    # 3) Token-level fuzzy fallback
    if not product_lookup:
        words = [w for w in user_lower.split() if len(w) > 3]
        for word in words:
            match = _find_similar_product(word, inventory_data)
            if match:
                product_lookup = match
                break

    active_item = product_lookup if product_lookup else inventory_item

    product = active_item.get('product')
    stock = active_item.get('stock')
    location = active_item.get('location')
    supplier = active_item.get('supplier')

    # Detect demand from user input
    demand_level = detect_demand_signal(user_input)
    
    # Use our deterministic tools first, then let LLM format/justify
    decision = tool_analyze_restock_decision(stock, 3)
    pricing = tool_calculate_pricing(product, stock, demand_level, active_item.get('price'))
    sources = tool_search_suppliers(product, location, base_price=active_item.get('price'), estimate_missing=ESTIMATE_MISSING_PRICES)

    status_log.append(f"🔍 **User Query:** {user_input}")
    if product_lookup:
        status_log.append(f"📦 **Resolved Product:** {product} (from query)")
    status_log.append(f"📊 **Detected Demand Level:** {demand_level.upper()}")
    status_log.append("🤖 **Router:** LangChain chain with ChatGroq")
    status_log.append("⚙️ **Tools:** decision, pricing, sourcing computed")

    template = """
You are an Autonomous Supply Chain Agent.

USER INPUT:
{user_input}

CONTEXT:
Product: {product}
Current Stock: {stock}
Location: {location}
Supplier: {supplier}

TOOL RESULTS (Ground Truth):
- demand_level: {demand_level}
- decision: {decision}
- pricing: {pricing}
- suppliers: {sources}

Note: Prices marked 'Est.' are approximations based on inventory base price with store-specific factors.

TASK:
Based on the TOOL RESULTS, produce the final output strictly in this format:

### DECISION
[RESTOCK/HOLD] ([URGENT/STANDARD])
Reasoning: [short]

### STRATEGY
Suggested Price (INR): ₹[number]
Calculation: [brief]

### SOURCING OPTIONS
- Option 1: [name]\n  Unit Price (INR): ₹[price]\n  Lead Time: [eta]\n  Reliability: [text]
- Option 2: [...]

### RECOMMENDATION
Chosen Source: [name]
Unit Cost (INR): ₹[price]
Lead Time: [eta]
Reliability: [text]
Next Steps: [concise actionable steps]
"""

    prompt = ChatPromptTemplate.from_template(template)
    model_name = _select_groq_model(client, GROQ_MODEL)
    llm = ChatGroq(model=model_name, temperature=0.2)
    chain = prompt | llm

    try:
        result = chain.invoke({
            "user_input": user_input or "N/A",
            "product": product,
            "stock": stock,
            "location": location,
            "supplier": supplier,
            "demand_level": demand_level,
            "decision": decision,
            "pricing": pricing,
            "sources": sources,
        })
    except Exception as e:
        msg = str(e).lower()
        if "decommissioned" in msg or "not supported" in msg or "invalid_request_error" in msg:
            # Try a safer fallback model selection and re-run
            status_log.append("⚠️ Primary model unavailable. Selecting a fallback model…")
            fallback_model = _select_groq_model(client, None)
            llm = ChatGroq(model=fallback_model, temperature=0.2)
            chain = prompt | llm
            result = chain.invoke({
                "user_input": user_input or "N/A",
                "product": product,
                "stock": stock,
                "location": location,
                "supplier": supplier,
                "demand_level": demand_level,
                "decision": decision,
                "pricing": pricing,
                "sources": sources,
            })
        else:
            raise

    content = getattr(result, "content", str(result))
    status_log.append("✅ **LangChain completed.**")
    return status_log, content

# ===== INVENTORY LLM PLANNER =====
def summarize_history_by_product(history: list, lookback_days: int = 90) -> dict:
    """Summarize history into avg daily sales and avg lead time by product."""
    from datetime import datetime, timedelta
    cutoff = datetime.now() - timedelta(days=lookback_days)
    per = {}
    for rec in history:
        try:
            d = datetime.fromisoformat(rec.get("date"))
        except Exception:
            continue
        if d < cutoff:
            continue
        p = rec.get("product")
        qty = float(rec.get("quantity", 0) or 0)
        lt = float(rec.get("lead_time_days", 0) or 0)
        if p not in per:
            per[p] = {"units": 0.0, "days": lookback_days, "lead_times": []}
        per[p]["units"] += qty
        if lt:
            per[p]["lead_times"].append(lt)
    # finalize
    out = {}
    for p, data in per.items():
        avg_daily = data["units"] / max(1, data["days"])
        avg_lead = sum(data["lead_times"]) / len(data["lead_times"]) if data["lead_times"] else 3.0
        out[p] = {"avg_daily_sales": round(avg_daily, 2), "avg_lead_time_days": round(avg_lead, 1)}
    return out

def run_inventory_planner_llm(user_input: str, manager_email: str = None):
    """Analyze all inventory using LLM + history to propose restock plan and send confirmations (pending)."""
    inventory = load_inventory()
    history = load_purchase_history()
    hist_summary = summarize_history_by_product(history, lookback_days=90)

    # Prepare compact context
    inv_min = [
        {
            "product": i.get("product"),
            "stock": i.get("stock"),
            "price_inr": i.get("price"),
            "location": i.get("location"),
            "avg_daily_sales": hist_summary.get(i.get("product"), {}).get("avg_daily_sales", 0),
            "avg_lead_time_days": hist_summary.get(i.get("product"), {}).get("avg_lead_time_days", 3),
        }
        for i in inventory if isinstance(i, dict)
    ]

    demand_hint = detect_demand_signal(user_input)

    # LLM prompt to compute restock quantities across products
    sys_prompt = (
        "You are an expert supply planner. Given inventory, avg daily sales, and lead times, "
        "compute restock quantities needed to avoid stockouts. Consider demand level hints, "
        "prefer covering at least 2× lead-time days of sales. Output STRICT JSON only."
    )
    user_prompt = {
        "demand_level": demand_hint,
        "inventory": inv_min,
        "instructions": "Return a JSON object with 'plan': [ {product, restock_qty, rationale}... ] and 'notes' string."
    }

    plan = {"plan": [], "notes": "fallback"}

    try:
        if LANGCHAIN_AVAILABLE:
            from langchain_core.prompts import ChatPromptTemplate
            from langchain_groq import ChatGroq
            model_name = _select_groq_model(client, GROQ_MODEL)
            llm = ChatGroq(model=model_name, temperature=0.1)
            tmpl = ChatPromptTemplate.from_messages([
                ("system", sys_prompt),
                ("human", "Demand level: {demand}\nInventory: {inv}\n{instr}")
            ])
            chain = tmpl | llm
            resp = chain.invoke({
                "demand": demand_hint,
                "inv": json.dumps(inv_min),
                "instr": json.dumps(user_prompt)
            })
            content = getattr(resp, "content", str(resp))
        else:
            # Direct Groq API fallback
            comp = client.chat.completions.create(
                model=_select_groq_model(client, GROQ_MODEL),
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": json.dumps(user_prompt)}
                ],
                temperature=0.1
            )
            content = comp.choices[0].message.content

        # Parse JSON from LLM
        try:
            # Find JSON substring if extra text
            start = content.find("{")
            end = content.rfind("}")
            if start != -1 and end != -1:
                plan = json.loads(content[start:end+1])
        except Exception:
            pass
    except Exception as e:
        plan = {"plan": [], "notes": f"llm-error: {e}"}

    # Fallback heuristic if empty plan
    if not plan.get("plan"):
        for i in inv_min:
            velocity = float(i.get("avg_daily_sales", 0) or 0)
            lead = float(i.get("avg_lead_time_days", 3) or 3)
            cover_days = max(2*lead, 5)
            required = max(0, int(round(velocity*cover_days - float(i.get("stock", 0) or 0))))
            if required > 0:
                plan["plan"].append({
                    "product": i["product"],
                    "restock_qty": required,
                    "rationale": f"cover {cover_days} days × {velocity}/day - current stock"
                })
        plan["notes"] = plan.get("notes", "") + " | heuristic-fallback"

    # Build markdown and create pending approvals per item
    logs = ["📊 **Inventory Planner (LLM)**", f"Demand hint: {demand_hint.upper()}"]
    md = ["## Inventory Summary (LLM-driven)"]
    total_units = sum(i.get("stock", 0) or 0 for i in inv_min)
    md.append(f"Total Units In Stock: {total_units}")

    if plan.get("plan"):
        md.append("\n### Restock Recommendations")
        for rec in plan["plan"]:
            prod = rec.get("product")
            qty = int(rec.get("restock_qty", 0) or 0)
            rationale = rec.get("rationale", "")

            # Find inventory item to price and location
            inv_item = next((x for x in inv_min if x["product"] == prod), None)
            base_price = inv_item.get("price_inr") if inv_item else None
            location = inv_item.get("location") if inv_item else None

            # Find supplier and create pending order email
            supplier_info = tool_search_suppliers(prod, location, base_price=base_price, estimate_missing=ESTIMATE_MISSING_PRICES)
            best_supplier = supplier_info[0] if supplier_info else {"name": "Preferred Supplier", "price": base_price, "lead_time": "3-6 days", "reliability": "High"}
            unit_price = best_supplier.get("price", base_price)
            if unit_price is None:
                unit_price = base_price
            total_cost = (float(unit_price) if isinstance(unit_price, (int, float)) else 0) * qty

            # Email confirmation (pending approval)
            order_id = f"BULK-{datetime.now().strftime('%Y%m%d%H%M%S')}-{abs(hash(prod))%1000}"
            if manager_email:
                _ = tool_send_confirmation_email(
                    product=prod,
                    quantity=qty,
                    supplier=best_supplier.get("name"),
                    unit_price=float(unit_price) if isinstance(unit_price, (int, float)) else 0,
                    total_cost=float(total_cost),
                    lead_time=best_supplier.get("lead_time", "3-6 days"),
                    manager_email=manager_email,
                    order_id=order_id
                )
                logs.append(f"📧 Sent pending approval for {prod} x{qty} to {manager_email}")

            md.append(f"- {prod}: restock {qty} units • est. unit {format_inr(unit_price)} • total {format_inr(total_cost)}")
            if rationale:
                md.append(f"  - Reason: {rationale}")

    if plan.get("notes"):
        md.append("\nNotes: " + str(plan["notes"]))

    return logs, "\n".join(md)

# --- STREAMLIT UI ---
st.set_page_config(page_title="Agentic Inventory", layout="wide")
st.title("⚡ Productive Agent: Inventory & Logistics")

def load_inventory(path: str = "inventory.json"):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "items" in data and isinstance(data["items"], list):
            return data["items"]
        if isinstance(data, list):
            return data
        return []
    except Exception:
        return []

def load_purchase_history(path: str = "purchase_history.json"):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except Exception:
        return []

inventory_list = load_inventory()
product_names = [i.get("product", "") for i in inventory_list if isinstance(i, dict)]
selected_product_name = product_names[0] if product_names else None
selected_item = next((i for i in inventory_list if i.get("product") == selected_product_name), None)

# Initialize session state for persistent data across reruns
init_session_state()

# Sidebar for controls
with st.sidebar:
    st.header("⚙️ Agent Settings")
    
    # Email Configuration Section
    st.subheader("📧 Email Configuration")
    st.session_state.manager_email = st.text_input(
        "Inventory Manager Email",
        value=st.session_state.manager_email,
        type="password",
        help="Email address to send order confirmations"
    )
    
    with st.expander("SMTP Configuration", expanded=False):
        st.info("These are read from environment variables:\n- SMTP_SERVER\n- SMTP_PORT\n- SENDER_EMAIL\n- SENDER_PASSWORD")
        if SENDER_EMAIL:
            st.success(f"✅ Sender configured: {SENDER_EMAIL}")
        else:
            st.warning("⚠️ SENDER_EMAIL not configured in environment")
    
    st.divider()
    
    # Agent Settings
    use_langchain = False
    if LANGCHAIN_AVAILABLE:
        use_langchain = st.toggle("Use LangChain Orchestrator", value=True, help="Requires langchain-core and langchain-groq")
    else:
        st.info("LangChain not installed. Fallback to built-in orchestrator.")
    
    # Toggle for estimating missing prices from inventory base price
    estimate_prices = st.toggle("Estimate missing prices", value=True, help="Use inventory base price to estimate when online price not found")
    
    # Auto-check emails toggle
    st.subheader("🤖 Autonomous Mode")
    auto_check = st.toggle("Auto-check emails & place orders", value=False, help="Automatically check for approvals without manual clicking")
    auto_poll = st.toggle("Background polling (every 20s)", value=True, help="Continuously checks approvals and seller confirmations")
    
    if auto_check:
        st.info("✅ **Autonomous mode enabled**: System will check for manager approval emails every page load and place orders automatically")
        # Auto-check in autonomous mode
        approval_result = tool_check_email_approvals()
        if approval_result.get("approved_count", 0) > 0:
            st.success(f"✅ {approval_result['approved_count']} order(s) placed automatically!")
        # Also check seller confirmations
        seller_result = tool_check_seller_confirmations()
        if seller_result.get("status") == "success" and seller_result.get("confirmed"):
            st.success(f"📣 Seller confirmed {len(seller_result['confirmed'])} order(s). Manager notified.")

    if auto_poll:
        # Refresh the page every 20 seconds
        try:
            st.experimental_rerun  # attribute exists in streamlit
            st_autorefresh = st.experimental_rerun  # placeholder to avoid linter
        except Exception:
            pass
        try:
            from streamlit_autorefresh import st_autorefresh
        except Exception:
            st_autorefresh = None
        if 'st_autorefresh' in globals() or st_autorefresh:
            try:
                st_autorefresh(interval=20_000, key="auto_poll_refresh")
            except Exception:
                pass
    
    if st.button("🔴 Reset Agent"):
        st.rerun()
    
    st.divider()
    
    # Show pending orders
    st.subheader("📋 Order Status")
    
    # Display current order status from session state
    pending_orders_dict = get_pending_orders_dict()
    if pending_orders_dict:
        status_counts = {"pending_approval": 0, "confirmed": 0, "rejected": 0, "error": 0}
        for order_id, order in pending_orders_dict.items():
            status = order.get("status", "pending_approval")
            status_counts[status] = status_counts.get(status, 0) + 1
        
        # Show metrics
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("⏳ Pending", status_counts.get("pending_approval", 0))
        with col2:
            st.metric("✅ Confirmed", status_counts.get("confirmed", 0))
        with col3:
            st.metric("❌ Rejected", status_counts.get("rejected", 0))
        with col4:
            st.metric("⚠️ Error", status_counts.get("error", 0))
        
        # Show order details in expander
        with st.expander("📦 View All Orders", expanded=False):
            for order_id, order in pending_orders_dict.items():
                status = order.get("status", "pending_approval")
                product = order.get("product", "Unknown")
                quantity = order.get("quantity", 0)
                total_cost = order.get("total_cost", 0)
                
                # Status icon
                status_icon = {
                    "pending_approval": "⏳",
                    "confirmed": "✅",
                    "rejected": "❌",
                    "seller_confirmed": "📦",
                    "seller_rejected": "❌",
                    "error": "⚠️"
                }.get(status, "❓")
                
                st.markdown(f"""
**{status_icon} {order_id}**  
Product: {product} | Qty: {quantity} | Cost: ${total_cost:.2f}  
Status: `{status}`
""")
                
                # Add tracking button
                if status in ["confirmed", "seller_confirmed"]:
                    if st.button(f"📍 Track {order_id}", key=f"track_{order_id}"):
                        tracking_result = tool_track_order(order_id)
                        if tracking_result.get("status") == "success":
                            tracking = tracking_result.get("tracking", {})
                            st.info(f"📍 Current Location: **{tracking.get('current_location', 'Unknown')}**")
                            
                            with st.expander("📦 Full Tracking History", expanded=True):
                                for event in tracking.get("tracking_history", []):
                                    timestamp = event.get("timestamp", "")
                                    if timestamp:
                                        try:
                                            dt = datetime.fromisoformat(timestamp)
                                            formatted_time = dt.strftime("%b %d, %Y %I:%M %p")
                                        except:
                                            formatted_time = timestamp
                                    else:
                                        formatted_time = "N/A"
                                    
                                    st.markdown(f"""
**{event.get('status', 'Unknown')}**  
📍 {event.get('location', 'Unknown')}  
🕐 {formatted_time}  
_{event.get('description', '')}_
""")
                                    st.divider()
                
                st.divider()
    else:
        st.info("No orders in the system")
    
    # Email approval checker button (manual mode)
    col1, col2 = st.columns(2)
    with col1:
        check_approvals = st.button("📧 Check Approvals", help="Check inbox for APPROVE/REJECT replies", use_container_width=True)
    with col2:
        check_seller = st.button("📨 Check Seller", help="Check inbox for seller confirmations", use_container_width=True)
    
    if check_approvals:
        with st.spinner("Checking emails and placing orders..."):
            approval_result = tool_check_email_approvals()
            
            st.divider()
            st.subheader("📧 Email Approval & Order Placement")
            
            if approval_result.get("status") == "success":
                approved_count = approval_result.get("approved_count", 0)
                rejected_count = approval_result.get("rejected_count", 0)
                error_count = len(approval_result.get("errors", []))
                
                if approved_count > 0:
                    st.success(f"✅ Successfully placed {approved_count} order(s)!")
                    for approved in approval_result.get("approved", []):
                        order_id = approved['order_id']
                        product = approved['product']
                        conf = approved.get('confirmation', {})
                        supplier = conf.get('supplier', 'Unknown')
                        quantity = conf.get('quantity', 'N/A')
                        unit_price = conf.get('unit_price', 0)
                        total_cost = conf.get('total_cost', 0)
                        lead_time = conf.get('expected_delivery', 'N/A')
                        is_real = conf.get('real_order', False)
                        
                        # Real order vs inquiry
                        if is_real:
                            status_badge = "🌐 REAL ORDER"
                            api_note = "Placed with actual supplier"
                        else:
                            status_badge = "📧 ORDER INQUIRY"
                            api_note = "Sent to supplier (awaiting confirmation)"
                        
                        st.markdown(f"""
**✅ Order Booked Successfully** - {status_badge}

- **Product:** {product}
- **Supplier:** {supplier}
- **Quantity:** {quantity} units
- **Unit Price:** ₹{unit_price:,.2f}
- **Total Cost:** ₹{total_cost:,.2f}
- **Expected Delivery:** {lead_time}
- **Order ID:** `{order_id}`
- **Confirmation #:** `{conf.get('order_number', 'N/A')}`

**Status:** {api_note}
                        """)
                
                if rejected_count > 0:
                    st.warning(f"❌ {rejected_count} order(s) rejected")
                    for rejected in approval_result.get("rejected", []):
                        st.write(f"- {rejected['product']} (Order: `{rejected['order_id']}`)")
                
                if error_count > 0:
                    st.error("⚠️ Errors encountered:")
                    for error in approval_result["errors"]:
                        st.error(f"  {error}")
                
                if approved_count == 0 and rejected_count == 0 and error_count == 0:
                    st.info("ℹ️ No new approvals or rejections found in your emails")
                
                # Show debug info if requested
                if st.checkbox("Show debug details", value=False):
                    with st.expander("🔍 Debug Info", expanded=False):
                        for info in approval_result.get("debug", []):
                            st.text(info)
                
                # Rerun to refresh order status display
                st.rerun()
            else:
                st.error(f"Error: {approval_result.get('message')}")
    
    if check_seller:
        with st.spinner("Checking for seller confirmation emails..."):
            seller_result = tool_check_seller_confirmations()
            
            st.divider()
            st.subheader("📨 Seller Confirmations")
            
            if seller_result.get("status") == "success":
                confirmed_count = len(seller_result.get("confirmed", []))
                if confirmed_count > 0:
                    st.success(f"📣 **{confirmed_count} seller confirmation(s) found!**")
                    for conf in seller_result.get("confirmed", []):
                        order_id = conf['order_id']
                        product = conf['product']
                        st.markdown(f"✅ **{product}** — Order `{order_id}` confirmed by seller. Manager has been notified.")
                    st.rerun()
                else:
                    confirmed_count = len(seller_result.get('confirmed', []))
                    rejected_count = len(seller_result.get('rejected', []))
                    if confirmed_count or rejected_count:
                        if confirmed_count:
                            st.success(f"📣 Seller confirmed {confirmed_count} order(s). Manager notified.")
                        if rejected_count:
                            st.error(f"🚫 Seller rejected {rejected_count} order(s). Manager notified.")
                    else:
                        st.info("No new seller confirmations found.")
            else:
                st.error(f"Error checking confirmations: {seller_result.get('message')}")
            
            # Show debug logs
            with st.expander("📋 Debug Details", expanded=False):
                for debug_line in seller_result.get("debug", []):
                    st.text(debug_line)
    
    # Show all orders with their current status
    pending = tool_get_pending_orders()
    pending_orders = {oid: o for oid, o in pending["pending_orders"].items() if o.get("status") == "pending_approval"}
    confirmed_orders = {oid: o for oid, o in pending["pending_orders"].items() if o.get("status") == "confirmed"}
    rejected_orders = {oid: o for oid, o in pending["pending_orders"].items() if o.get("status") == "rejected"}
    seller_confirmed_orders = {oid: o for oid, o in pending["pending_orders"].items() if o.get("status") == "seller_confirmed"}
    seller_rejected_orders = {oid: o for oid, o in pending["pending_orders"].items() if o.get("status") == "seller_rejected"}
    error_orders = {oid: o for oid, o in pending["pending_orders"].items() if o.get("status") == "error"}
    
    # Show confirmed orders
    if confirmed_orders:
        st.success(f"✅ **{len(confirmed_orders)} Order(s) Confirmed**")
        for order_id, order in confirmed_orders.items():
            conf = order.get("confirmation", {})
            product = order.get('product', 'Unknown')
            supplier = order.get('supplier', 'Unknown')
            quantity = order.get('quantity', 'N/A')
            unit_price = order.get('unit_price', 0)
            total_cost = order.get('total_cost', 0)
            lead_time = order.get('lead_time', 'N/A')
            
            with st.expander(f"✅ {product} - Booked with {supplier}", expanded=False):
                col1, col2 = st.columns(2)
                with col1:
                    st.metric("Quantity", f"{quantity} units")
                    st.metric("Unit Price", f"₹{unit_price:,.2f}")
                with col2:
                    st.metric("Total Cost", f"₹{total_cost:,.2f}")
                    st.metric("Expected Delivery", lead_time)
                st.text(f"Order ID: {order_id}")
                if conf.get('order_number'):
                    st.text(f"Confirmation: {conf.get('order_number')}")
    
    # Show pending orders
    if pending_orders:
        st.info(f"⏳ **{len(pending_orders)} Order(s) Awaiting Approval**")
        st.write("_Waiting for manager approval via email reply with 'APPROVE'_")
        for order_id, order in pending_orders.items():
            product = order.get('product', 'Unknown')
            supplier = order.get('supplier', 'Unknown')
            quantity = order.get('quantity', 'N/A')
            total_cost = order.get('total_cost', 0)
            
            with st.expander(f"⏳ {product} - {supplier}", expanded=False):
                st.write(f"**Quantity:** {quantity} units")
                st.write(f"**Estimated Cost:** ₹{total_cost:,.2f}")
                st.write(f"**Order ID:** {order_id}")
                st.caption("_Reply to inventory summary email with 'APPROVE' to place this order_")
    
    # Show rejected orders
    if rejected_orders:
        st.error(f"❌ **{len(rejected_orders)} Order(s) Rejected**")
        for order_id, order in rejected_orders.items():
            product = order.get('product', 'Unknown')
            st.write(f"- {product} (Order: `{order_id}`)")

    # Show seller-confirmed orders
    if seller_confirmed_orders:
        st.success(f"📣 **{len(seller_confirmed_orders)} Order(s) Confirmed by Seller**")
        for order_id, order in seller_confirmed_orders.items():
            product = order.get('product', 'Unknown')
            supplier = order.get('supplier', 'Unknown')
            conf = order.get('seller_confirmation', {})
            when = conf.get('at', 'recently')
            st.write(f"- {product} (Order: `{order_id}`) — {supplier} confirmed at {when}")

    # Show seller-rejected orders
    if seller_rejected_orders:
        st.error(f"🚫 **{len(seller_rejected_orders)} Order(s) Rejected by Seller**")
        for order_id, order in seller_rejected_orders.items():
            product = order.get('product', 'Unknown')
            supplier = order.get('supplier', 'Unknown')
            conf = order.get('seller_confirmation', {})
            when = conf.get('at', 'recently')
            reason = conf.get('body_preview', 'Seller rejected') if isinstance(conf, dict) else 'Seller rejected'
            st.write(f"- {product} (Order: `{order_id}`) — {supplier} rejected at {when}")
            with st.expander(f"Details for {order_id}", expanded=False):
                st.write(reason)

    # Show failed/error orders (e.g., missing API credentials)
    if error_orders:
        st.warning(f"⚠️ **{len(error_orders)} Order(s) Failed**")
        for order_id, order in error_orders.items():
            product = order.get('product', 'Unknown')
            supplier = order.get('supplier', 'Unknown')
            err = order.get('error', 'Unknown error')
            with st.expander(f"⚠️ {product} - {supplier}", expanded=False):
                st.write(f"**Order ID:** {order_id}")
                st.write(f"**Error:** {err}")
                st.caption("Add supplier API credentials in .env and rerun approvals to place real orders.")
    
    if not pending_orders and not confirmed_orders and not rejected_orders:
        st.info("No orders yet. Run Inventory Summary to generate orders.")

    st.divider()
    st.subheader("📈 Inventory Summary & Auto-Restock")
    st.write("_This feature automatically analyzes your inventory and creates restock orders based on demand and stock levels. No manual threshold needed._")
    
    col_threshold, col_email = st.columns([1, 1])
    with col_threshold:
        threshold_val = st.number_input("Restock sensitivity (lower = more frequent restocking)", min_value=5, max_value=50, value=15, step=1, help="Items below this stock level will be restocked")
    with col_email:
        send_email_summary = st.checkbox("Email summary to manager", value=True, help="Send order list to manager for approval")
    
    if st.button("🚀 Run Inventory Summary", type="primary", use_container_width=True):
        with st.spinner("Analyzing inventory and creating restock orders..."):
            summary = tool_inventory_overview(threshold=threshold_val)
            
            if summary.get("status") == "success":
                st.success("✅ Inventory analysis complete!")
                
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("Total Stock", summary['total_stock'], "units")
                with col2:
                    st.metric("Items Tracked", summary['items_count'])
                with col3:
                    st.metric("Below Threshold", summary['low_count'])
                with col4:
                    st.metric("Restock Cost", f"₹{summary.get('estimated_restock_cost', 0):,.0f}")
                
                if summary.get("low_stock"):
                    st.subheader("📦 Items to Restock")
                    for item in summary["low_stock"]:
                        col1, col2, col3 = st.columns(3)
                        with col1:
                            st.write(f"**{item['product']}**")
                            st.caption(f"Current: {item['stock']} units")
                        with col2:
                            st.write(f"Restock: {item['restock_quantity']} units")
                            st.caption(f"Location: {item.get('location', 'N/A')}")
                        with col3:
                            st.write(f"Estimated Cost")
                            st.metric(label="Cost", value=f"₹{item.get('estimated_cost', 0):,.2f}")
                else:
                    st.info("✅ All items are well-stocked. No restocking needed.")

                if send_email_summary and summary.get("low_stock"):
                    manager_email = st.session_state.manager_email if st.session_state.manager_email else MANAGER_EMAIL
                    if manager_email:
                        with st.spinner("Creating orders and sending email to manager..."):
                            email_res = tool_send_inventory_summary_email(summary, manager_email)
                            if email_res.get("status") == "success":
                                st.success(f"✅ {email_res.get('message')}")
                                st.info("📧 **Next Steps:** Manager will review the email and reply with 'APPROVE' to place all orders automatically.")
                                st.info("Click '📧 Check Email for Approvals' button above to check for manager approval and place orders.")
                            else:
                                st.error(f"❌ {email_res.get('message')}")
                    else:
                        st.warning("⚠️ Manager email not configured. Set it in the sidebar to send summary.")
            else:
                st.error("❌ Failed to analyze inventory.")

# Set selected_item to None by default - everything handled through LLM
selected_item = None

st.subheader("🧠 Agent Workspace")

if not client:
    st.error("⚠️ Set GROQ_API_KEY via environment or .env before running.")
else:
    resolved = _select_groq_model(client, GROQ_MODEL)
    st.caption(f"Model: {resolved}")

    # Chat-like input
    prompt = st.chat_input("Type your instructions/context (e.g., 'How much stock of Winter Jackets?' or 'order 50 winter jackets' or 'status BULK-2025…') and press Enter…")
    if prompt is not None:
        # Quick path: if prompt contains an Order ID, show status and skip the full agent chain
        order_match = re.search(r"(BULK-[A-Z0-9-]+|ORD-[0-9]+)", prompt, flags=re.IGNORECASE)
        if order_match:
            order_id = order_match.group(1).upper()
            with st.spinner(f"Checking status for {order_id}…"):
                res = tool_llm_check_order_status(order_id)
            if res.get("status") == "success":
                s = res.get("summary", {})
                st.write(f"**Order:** {s.get('order_id')}  •  **Status:** {s.get('status')}")
                with st.expander("Structured Details", expanded=False):
                    st.json(s)
                expl = res.get("explanation")
                if expl:
                    st.info(expl)
            else:
                st.error(res.get("message"))
        else:
            with st.spinner("Agent is analyzing and drafting outputs…"):
                try:
                    # Use manager email from session state, fallback to .env config
                    manager_email = st.session_state.manager_email if st.session_state.manager_email else MANAGER_EMAIL
                    
                    inventory_query_keywords = [
                        "what items", "what products", "available items", "available products",
                        "list items", "list products", "show inventory", "what's in inventory",
                        "items in inventory", "products in inventory", "all items", "all products",
                        "inventory list", "what do you have", "what items are available",
                        "show all", "all available", "complete inventory", "full inventory",
                        "items avaliable", "avaliable in inventory",  # typos
                        "items available in inventory", "products available in inventory",
                        "what are the items available", "what are the items available in inventory",
                        "show items available", "show products available", "all inventory items"
                    ]
                    prompt_lower = prompt.lower()
                    heuristic_inventory_list = ("inventory" in prompt_lower) and (
                        "item" in prompt_lower or "product" in prompt_lower or "available" in prompt_lower
                    )
                    is_inventory_list = any(keyword in prompt_lower for keyword in inventory_query_keywords) or heuristic_inventory_list

                    item_to_pass = {} if is_inventory_list else selected_item

                    if 'use_langchain' in locals() and use_langchain:
                        logs, output = run_agent_logic_langchain(item_to_pass, prompt, manager_email=manager_email)
                    else:
                        logs, output = run_agent_logic(item_to_pass, prompt, manager_email=manager_email)

                    # 1. Show Thought Process
                    with st.expander("Show Agent Thoughts", expanded=True):
                        for log in logs:
                            st.write(log)

                    # 2. Display the response
                    st.divider()
                    st.markdown(output)

                    # 3. Actions
                    st.success("✅ Workflow Complete. Orders can be managed in the sidebar.")
                    
                    # Export Report Feature - Generate data
                    report_content = f"""INVENTORY MANAGEMENT AGENT - REPORT
Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
==========================================

QUERY
-----
{prompt}

AGENT ANALYSIS
--------------
"""
                    for log in logs:
                        report_content += f"• {log}\n"
                    
                    report_content += f"""

OUTPUT
------
{output}

PENDING ORDERS
--------------
"""
                    pending_orders_dict = get_pending_orders_dict()
                    if pending_orders_dict:
                        for oid, order in pending_orders_dict.items():
                            report_content += f"""
Order ID: {oid}
Product: {order.get('product')}
Quantity: {order.get('quantity')}
Supplier: {order.get('supplier')}
Total Cost: ${order.get('total_cost', 0):.2f}
Status: {order.get('status')}
---
"""
                    else:
                        report_content += "No pending orders.\n"
                    
                    # Generate JSON summary
                    summary = {
                        "generated_at": datetime.now().isoformat(),
                        "query": prompt,
                        "agent_logs": logs,
                        "output": output,
                        "pending_orders": pending_orders_dict,
                        "order_count": len(pending_orders_dict)
                    }
                    
                    import json
                    json_data = json.dumps(summary, indent=2)
                    
                    # Display export buttons without nesting in st.button()
                    col1, col2 = st.columns(2)
                    with col1:
                        st.download_button(
                            label="💾 Download Report (TXT)",
                            data=report_content,
                            file_name=f"inventory_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
                            mime="text/plain",
                            key="download_txt"
                        )
                    
                    with col2:
                        st.download_button(
                            label="📊 Download Summary (JSON)",
                            data=json_data,
                            file_name=f"inventory_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                            mime="application/json",
                            key="download_json"
                        )
                except Exception as e:
                    if "model" in str(e) and ("decommissioned" in str(e) or "not supported" in str(e)):
                        st.error(f"Model error: {e}. Try setting a supported model via GROQ_MODEL.")
                    else:
                        st.error(f"Error: {e}")

# Global setting update
if 'estimate_prices' in locals():
    ESTIMATE_MISSING_PRICES = estimate_prices