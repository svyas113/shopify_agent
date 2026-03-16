"""Shopify API client wrapper for the customer support agent."""
import httpx
from typing import Dict, List, Optional, Any
from datetime import datetime

class ShopifyClient:
    """
    Handles all interactions with Shopify Admin API.
    Uses the access token obtained during OAuth flow.
    """
    
    def __init__(self, shop_domain: str, access_token: str):
        self.shop = shop_domain
        self.token = access_token
        self.api_version = "2024-10"  # Using current Shopify API version
        self.base_url = f"https://{shop_domain}/admin/api/{self.api_version}"
    
    async def _request(
        self, 
        endpoint: str, 
        method: str = "GET",
        data: Optional[Dict] = None
    ) -> Dict:
        """Make authenticated request to Shopify API"""
        headers = {
            "X-Shopify-Access-Token": self.token,
            "Content-Type": "application/json"
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            if method == "GET":
                response = await client.get(
                    f"{self.base_url}/{endpoint}",
                    headers=headers
                )
            elif method == "POST":
                response = await client.post(
                    f"{self.base_url}/{endpoint}",
                    headers=headers,
                    json=data
                )
            elif method == "PUT":
                response = await client.put(
                    f"{self.base_url}/{endpoint}",
                    headers=headers,
                    json=data
                )
            
            response.raise_for_status()
            return response.json()
    
    # Order operations
    async def get_order(self, order_id: str) -> Optional[Dict]:
        """Fetch a specific order by ID"""
        try:
            data = await self._request(f"orders/{order_id}.json")
            return data.get("order")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
    
    async def search_orders(
        self, 
        customer_email: Optional[str] = None,
        name: Optional[str] = None,
        status: str = "any",
        limit: int = 50
    ) -> List[Dict]:
        """
        Search orders by criteria
        
        Args:
            customer_email: Filter by customer email
            name: Filter by order name/number (e.g. "#1001")
            status: Order status (any, open, closed, cancelled)
            limit: Maximum number of results
        """
        params = f"status={status}&limit={limit}"
        if customer_email:
            params += f"&email={customer_email}"
        if name:
            # Remove # prefix if present as Shopify API expects just the number
            clean_name = name.replace("#", "").strip()
            params += f"&name={clean_name}"
        
        data = await self._request(f"orders.json?{params}")
        return data.get("orders", [])
    
    async def get_order_fulfillments(self, order_id: str) -> List[Dict]:
        """Get fulfillment/tracking info for an order"""
        data = await self._request(f"orders/{order_id}/fulfillments.json")
        return data.get("fulfillments", [])
    
    # Product operations
    async def search_products(
        self, 
        query: Optional[str] = None,
        product_type: Optional[str] = None,
        vendor: Optional[str] = None,
        collection_id: Optional[str] = None,
        tag: Optional[str] = None,
        limit: int = 250
    ) -> List[Dict]:
        """
        Search products with enhanced filtering
        
        Args:
            query: Search by product title/description
            product_type: Filter by product type
            vendor: Filter by vendor name
            collection_id: Filter by collection ID
            tag: Filter by product tag
            limit: Maximum number of results
        """
        params = f"limit={limit}"
        if query:
            # Use product title for precise searches
            params += f"&title={query}"
        if product_type:
            params += f"&product_type={product_type}"
        if vendor:
            params += f"&vendor={vendor}"
        if collection_id:
            params += f"&collection_id={collection_id}"
        if tag:
            params += f"&tag={tag}"
        
        data = await self._request(f"products.json?{params}")
        products = data.get("products", [])
        
        # If no products found with title search and query provided, try fallback search
        if not products and query:
            # Try searching by tags which might contain keywords
            tag_params = f"limit={limit}&tag={query}"
            tag_data = await self._request(f"products.json?{tag_params}")
            products = tag_data.get("products", [])
            
            # If still no products, perform a broader search (future enhancement)
            # This would ideally use Shopify's search endpoint, but for now
            # we're working with the basic REST API capabilities
        
        return products
    
    async def get_product(self, product_id: str) -> Optional[Dict]:
        """Get detailed product information"""
        try:
            data = await self._request(f"products/{product_id}.json")
            return data.get("product")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
    
    async def get_inventory_level(self, inventory_item_id: str, location_id: Optional[str] = None) -> Dict:
        """
        Get inventory level using the modern Inventory Levels API
        
        Args:
            inventory_item_id: The ID of the inventory item
            location_id: Optional location ID to filter by
        
        Returns:
            Dict with inventory level information
        """
        params = f"inventory_item_ids={inventory_item_id}"
        if location_id:
            params += f"&location_ids={location_id}"
            
        try:
            data = await self._request(f"inventory_levels.json?{params}")
            levels = data.get("inventory_levels", [])
            
            if not levels:
                return {"available": 0, "policy": "deny"}
            
            # Sum up inventory across all locations if multiple locations exist
            total_available = sum(level.get("available", 0) for level in levels)
            
            return {
                "available": total_available,
                "locations": len(levels),
                "details": levels
            }
        except httpx.HTTPStatusError:
            # Fallback to 0 if API call fails
            return {"available": 0, "policy": "unknown"}
    
    async def check_inventory(self, variant_id: str) -> Dict:
        """
        Check inventory for a product variant
        
        Legacy method that now uses the Inventory Levels API internally
        when possible, but falls back to the variant.inventory_quantity 
        for backward compatibility
        """
        try:
            # First get the variant to get its inventory_item_id
            data = await self._request(f"variants/{variant_id}.json")
            variant = data.get("variant", {})
            
            inventory_item_id = variant.get("inventory_item_id")
            
            # If we have an inventory_item_id, use the modern API
            if inventory_item_id:
                inventory = await self.get_inventory_level(inventory_item_id)
                # Add policy from variant for backwards compatibility
                inventory["policy"] = variant.get("inventory_policy")
                return inventory
            else:
                # Fall back to the old way for backward compatibility
                return {
                    "available": variant.get("inventory_quantity", 0),
                    "policy": variant.get("inventory_policy"),
                }
        except Exception:
            # If anything fails, return the simplest format
            return {
                "available": 0,
                "policy": "deny"
            }
    
    async def get_product_recommendations(self, product_id: str, limit: int = 5) -> List[Dict]:
        """
        Get recommended products based on a product ID
        
        Args:
            product_id: The ID of the product to get recommendations for
            limit: Maximum number of recommendations to return
        """
        # Shopify doesn't have a direct recommendations API, so we'll simulate this
        # by getting products in the same product_type or from the same vendor
        product = await self.get_product(product_id)
        if not product:
            return []
        
        product_type = product.get("product_type")
        vendor = product.get("vendor")
        
        # Try to find related products by type or vendor
        related_products = []
        if product_type:
            related_by_type = await self.search_products(product_type=product_type, limit=limit)
            related_products.extend(related_by_type)
        
        if vendor and len(related_products) < limit:
            remaining = limit - len(related_products)
            related_by_vendor = await self.search_products(vendor=vendor, limit=remaining)
            related_products.extend(related_by_vendor)
            
        # Remove the original product from recommendations if present
        related_products = [p for p in related_products if str(p.get("id")) != str(product_id)]
        
        # Limit to the requested number
        return related_products[:limit]
    
    async def get_product_by_handle(self, handle: str) -> Optional[Dict]:
        """
        Get product by its handle (slug)
        
        Args:
            handle: The product handle/slug
        """
        # Shopify API doesn't have a direct endpoint for handle lookup,
        # so we use a workaround with a handle filter
        products = await self._request(f"products.json?handle={handle}&limit=1")
        products_list = products.get("products", [])
        return products_list[0] if products_list else None
    
    async def search_products_with_inventory(
        self,
        query: Optional[str] = None,
        product_type: Optional[str] = None,
        vendor: Optional[str] = None,
        tag: Optional[str] = None,
        in_stock_only: bool = False,
        limit: int = 250
    ) -> List[Dict]:
        """
        Search products and include enhanced inventory information
        
        Args:
            query: Search by product title/description
            product_type: Filter by product type
            vendor: Filter by vendor name
            tag: Filter by product tag
            in_stock_only: Only return products with available inventory
            limit: Maximum number of results
        """
        products = await self.search_products(
            query=query,
            product_type=product_type,
            vendor=vendor,
            tag=tag,
            limit=limit
        )
        
        # Use modern inventory approach to get accurate inventory data
        for product in products:
            variants = product.get("variants", [])
            
            # Add detailed inventory information to each variant
            for variant in variants:
                variant_id = variant.get("id")
                inventory_item_id = variant.get("inventory_item_id")
                
                if inventory_item_id:
                    # Get modern inventory data
                    inventory_data = await self.get_inventory_level(inventory_item_id)
                    
                    # Add updated inventory data to variant
                    variant["inventory_details"] = inventory_data
                    
                    # Ensure inventory_quantity is set for backward compatibility
                    if "available" in inventory_data and variant.get("inventory_quantity", 0) != inventory_data["available"]:
                        variant["inventory_quantity"] = inventory_data["available"]
        
        # Filter for in-stock items if requested
        if in_stock_only:
            products = [p for p in products if any(
                v.get("inventory_details", {}).get("available", 0) > 0 or
                v.get("inventory_quantity", 0) > 0
                for v in p.get("variants", [])
            )]
        
        return products
    
    # Customer operations
    async def search_customers(self, query: str) -> List[Dict]:
        """Search customers by email or name"""
        data = await self._request(f"customers/search.json?query={query}")
        return data.get("customers", [])
    
    async def get_customer(self, customer_id: str) -> Optional[Dict]:
        """Get customer details"""
        try:
            data = await self._request(f"customers/{customer_id}.json")
            return data.get("customer")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    # Add this to shopify_client.py
    async def calculate_refund(
        self, 
        order_id: str, 
        refund_line_items: List[Dict]
    ) -> Dict:
        """Calculate suggested transactions before creating a refund."""
        data = await self._request(
            f"orders/{order_id}/refunds/calculate.json",
            method="POST",
            data={"refund": {"refund_line_items": refund_line_items}}
        )
        return data.get("refund", {})
    
    # Refund operations
    async def create_refund(
        self,
        order_id: str,
        refund_line_items: List[Dict],
        notify_customer: bool = True,
        note: Optional[str] = None  # Add this parameter
    ) -> Dict:
        """Create a refund for an order"""
        refund_data = {
            "refund": {
                "notify": notify_customer,
                "note": note,  # Include the note in the payload
                "refund_line_items": refund_line_items
            }
        }
        
        data = await self._request(
            f"orders/{order_id}/refunds.json",
            method="POST",
            data=refund_data
        )
        return data.get("refund", {})