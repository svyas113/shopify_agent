"""Shopify API client wrapper for the customer support agent."""
import asyncio
import httpx
from typing import Dict, List, Optional, Any
from datetime import datetime


class ShopifyClient:
    """
    Handles all interactions with Shopify Admin API.
    Uses the access token obtained during OAuth flow.

    Key improvements over the naïve version:
      - Persistent httpx.AsyncClient for connection pooling (no per-request TCP handshake).
      - Retry logic that honours the ``Retry-After`` header returned on 429 responses,
        plus exponential back-off with jitter for other transient errors.
      - Batch inventory-level fetching to eliminate the N+1 call pattern that was the
        primary cause of rate-limit exhaustion.
      - A lightweight GraphQL helper for queries that cannot be expressed efficiently
        via the REST API (e.g. combined product + inventory in one round-trip).
    """

    # Maximum inventory_item_ids we can pass in a single REST batch call.
    # Shopify documents a practical limit of ~100 IDs per request.
    _INVENTORY_BATCH_SIZE = 100

    def __init__(self, shop_domain: str, access_token: str):
        self.shop = shop_domain
        self.token = access_token
        self.api_version = "2024-10"
        self.base_url = f"https://{shop_domain}/admin/api/{self.api_version}"
        self.graphql_url = f"https://{shop_domain}/admin/api/{self.api_version}/graphql.json"

        self._headers = {
            "X-Shopify-Access-Token": self.token,
            "Content-Type": "application/json",
        }
        # NOTE: We do NOT store a persistent AsyncClient here.
        #
        # fastworkflow commands call asyncio.run() or loop.run_until_complete()
        # from __call__, which means each invocation may run in a *different*
        # event loop.  An httpx.AsyncClient is bound to the event loop that was
        # current when it was created; reusing it across loops raises
        # "Event loop is closed" and can return stale data from previous
        # requests.
        #
        # Instead we create a fresh AsyncClient for each top-level request in
        # _make_client().  The overhead is one TCP handshake per command
        # invocation, which is acceptable.  Within a single command's
        # process_command() coroutine, the *same* client instance is reused
        # across all the await calls it makes, so we still get connection
        # reuse within a single command execution.
        self._client: Optional[httpx.AsyncClient] = None

    def _make_client(self) -> httpx.AsyncClient:
        """Return a fresh AsyncClient bound to the current event loop."""
        return httpx.AsyncClient(timeout=30.0, headers=self._headers)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _request(
        self,
        endpoint: str,
        method: str = "GET",
        data: Optional[Dict] = None,
        max_retries: int = 5,
        _client: Optional[httpx.AsyncClient] = None,
    ) -> Dict:
        """
        Make an authenticated request to the Shopify REST Admin API.

        Uses the provided *_client* if given (allows sharing one client
        across many calls within a single coroutine), otherwise creates a
        short-lived client just for this call.

        Retry strategy:
          - On 429: sleep for the ``Retry-After`` header value (default 2 s).
          - On 5xx / network error: exponential back-off, up to max_retries.
        """
        url = f"{self.base_url}/{endpoint}"
        own_client = _client is None
        client = _client or self._make_client()

        try:
            for attempt in range(max_retries):
                try:
                    if method == "GET":
                        response = await client.get(url)
                    elif method == "POST":
                        response = await client.post(url, json=data)
                    elif method == "PUT":
                        response = await client.put(url, json=data)
                    else:
                        raise ValueError(f"Unsupported HTTP method: {method}")

                    if response.status_code == 429:
                        retry_after = float(response.headers.get("Retry-After", 2))
                        await asyncio.sleep(retry_after)
                        continue

                    if response.status_code >= 500 and attempt < max_retries - 1:
                        backoff = (2 ** attempt) + 0.1 * attempt
                        await asyncio.sleep(backoff)
                        continue

                    response.raise_for_status()
                    return response.json()

                except httpx.TransportError:
                    if attempt < max_retries - 1:
                        backoff = (2 ** attempt) + 0.1 * attempt
                        await asyncio.sleep(backoff)
                        continue
                    raise

            raise RuntimeError(f"Failed to complete request to {url} after {max_retries} attempts.")
        finally:
            if own_client:
                await client.aclose()

    async def _graphql(
        self,
        query: str,
        variables: Optional[Dict] = None,
        _client: Optional[httpx.AsyncClient] = None,
    ) -> Dict:
        """
        Execute a GraphQL query against the Shopify Admin GraphQL API.

        Same access token, same domain — no extra credentials required.
        Uses the provided *_client* if given, otherwise creates a short-lived one.
        """
        payload: Dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        own_client = _client is None
        client = _client or self._make_client()

        try:
            for attempt in range(5):
                response = await client.post(self.graphql_url, json=payload)

                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", 2))
                    await asyncio.sleep(retry_after)
                    continue

                if response.status_code >= 500 and attempt < 4:
                    await asyncio.sleep(2 ** attempt)
                    continue

                response.raise_for_status()
                result = response.json()

                # Proactively back off if the GraphQL cost bucket is running low
                extensions = result.get("extensions", {})
                throttle = extensions.get("cost", {})
                if throttle:
                    actually_throttled = throttle.get("throttleStatus", {})
                    currently_available = actually_throttled.get("currentlyAvailable", 100)
                    restore_rate = actually_throttled.get("restoreRate", 50)
                    requested = throttle.get("requestedQueryCost", 0)
                    if currently_available < requested and restore_rate > 0:
                        wait_time = (requested - currently_available) / restore_rate
                        await asyncio.sleep(wait_time)

                return result

            raise RuntimeError("GraphQL request failed after multiple retries.")
        finally:
            if own_client:
                await client.aclose()

    # ------------------------------------------------------------------
    # Order operations
    # ------------------------------------------------------------------

    async def get_order(self, order_id: str) -> Optional[Dict]:
        """Fetch a specific order by ID."""
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
        limit: int = 50,
    ) -> List[Dict]:
        """
        Search orders by criteria.

        Args:
            customer_email: Filter by customer email.
            name: Filter by order name/number (e.g. "#1001").
            status: Order status (any, open, closed, cancelled).
            limit: Maximum number of results.
        """
        params = f"status={status}&limit={limit}"
        if customer_email:
            params += f"&email={customer_email}"
        if name:
            clean_name = name.replace("#", "").strip()
            params += f"&name={clean_name}"

        data = await self._request(f"orders.json?{params}")
        return data.get("orders", [])

    async def get_order_fulfillments(self, order_id: str) -> List[Dict]:
        """Get fulfillment/tracking info for an order."""
        data = await self._request(f"orders/{order_id}/fulfillments.json")
        return data.get("fulfillments", [])

    # ------------------------------------------------------------------
    # Product operations
    # ------------------------------------------------------------------

    async def search_products(
        self,
        query: Optional[str] = None,
        product_type: Optional[str] = None,
        vendor: Optional[str] = None,
        collection_id: Optional[str] = None,
        tag: Optional[str] = None,
        limit: int = 250,
    ) -> List[Dict]:
        """
        Search products with filtering.

        Args:
            query: Search by product title.
            product_type: Filter by product type.
            vendor: Filter by vendor name.
            collection_id: Filter by collection ID.
            tag: Filter by product tag.
            limit: Maximum number of results.
        """
        params = f"limit={limit}"
        if query:
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

        # Fallback: try tag-based search if title search returned nothing
        if not products and query:
            tag_data = await self._request(f"products.json?limit={limit}&tag={query}")
            products = tag_data.get("products", [])

        return products

    async def get_product(self, product_id: str) -> Optional[Dict]:
        """Get detailed product information."""
        try:
            data = await self._request(f"products/{product_id}.json")
            return data.get("product")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    async def get_inventory_level(
        self, inventory_item_id: str, location_id: Optional[str] = None
    ) -> Dict:
        """
        Get inventory level for a *single* inventory item.

        For bulk lookups prefer :meth:`get_inventory_levels_batch` which
        issues one HTTP request per ``_INVENTORY_BATCH_SIZE`` items.
        """
        params = f"inventory_item_ids={inventory_item_id}"
        if location_id:
            params += f"&location_ids={location_id}"

        try:
            data = await self._request(f"inventory_levels.json?{params}")
            levels = data.get("inventory_levels", [])

            if not levels:
                return {"available": 0, "policy": "deny"}

            total_available = sum(level.get("available", 0) for level in levels)
            return {
                "available": total_available,
                "locations": len(levels),
                "details": levels,
            }
        except httpx.HTTPStatusError:
            return {"available": 0, "policy": "unknown"}

    async def get_inventory_levels_batch(
        self, inventory_item_ids: List[str]
    ) -> Dict[str, Dict]:
        """
        Fetch inventory levels for *many* items in as few API calls as possible.

        Shopify allows a comma-separated list of up to ~100 inventory_item_ids
        in a single REST call.  This method splits the input into chunks and
        issues one call per chunk, then reassembles the results keyed by
        inventory_item_id.

        Returns:
            dict mapping inventory_item_id (str) → inventory summary dict
            with keys ``available``, ``locations``, ``details``.
        """
        result: Dict[str, Dict] = {}

        # Split into batches
        chunks = [
            inventory_item_ids[i: i + self._INVENTORY_BATCH_SIZE]
            for i in range(0, len(inventory_item_ids), self._INVENTORY_BATCH_SIZE)
        ]

        for chunk in chunks:
            ids_param = ",".join(str(i) for i in chunk)
            try:
                data = await self._request(
                    f"inventory_levels.json?inventory_item_ids={ids_param}"
                )
                levels = data.get("inventory_levels", [])
            except httpx.HTTPStatusError:
                levels = []

            # Group levels by inventory_item_id
            grouped: Dict[str, List[Dict]] = {}
            for level in levels:
                item_id = str(level.get("inventory_item_id", ""))
                grouped.setdefault(item_id, []).append(level)

            for item_id, item_levels in grouped.items():
                total = sum(l.get("available", 0) for l in item_levels)
                result[item_id] = {
                    "available": total,
                    "locations": len(item_levels),
                    "details": item_levels,
                }

        # Fill in zeros for any IDs we got no data back for
        for item_id in inventory_item_ids:
            if str(item_id) not in result:
                result[str(item_id)] = {"available": 0, "locations": 0, "details": []}

        return result

    async def check_inventory(self, variant_id: str) -> Dict:
        """
        Check inventory for a product variant.

        Uses the Inventory Levels API internally when possible,
        falling back to ``variant.inventory_quantity`` for compatibility.
        """
        try:
            data = await self._request(f"variants/{variant_id}.json")
            variant = data.get("variant", {})
            inventory_item_id = variant.get("inventory_item_id")

            if inventory_item_id:
                inventory = await self.get_inventory_level(str(inventory_item_id))
                inventory["policy"] = variant.get("inventory_policy")
                return inventory
            else:
                return {
                    "available": variant.get("inventory_quantity", 0),
                    "policy": variant.get("inventory_policy"),
                }
        except Exception:
            return {"available": 0, "policy": "deny"}

    async def get_product_recommendations(
        self, product_id: str, limit: int = 5
    ) -> List[Dict]:
        """
        Get recommended products based on a product ID.

        Runs the two sub-queries concurrently to halve wall-clock time.
        """
        product = await self.get_product(product_id)
        if not product:
            return []

        product_type = product.get("product_type")
        vendor = product.get("vendor")

        # Fire both searches in parallel instead of sequentially
        tasks = []
        if product_type:
            tasks.append(self.search_products(product_type=product_type, limit=limit))
        if vendor:
            tasks.append(self.search_products(vendor=vendor, limit=limit))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        related_products: List[Dict] = []
        for r in results:
            if isinstance(r, list):
                related_products.extend(r)

        # Deduplicate and remove the source product
        seen = set()
        unique = []
        for p in related_products:
            pid = str(p.get("id"))
            if pid != str(product_id) and pid not in seen:
                seen.add(pid)
                unique.append(p)

        return unique[:limit]

    async def get_product_by_handle(self, handle: str) -> Optional[Dict]:
        """Get product by its handle (slug)."""
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
        limit: int = 250,
    ) -> List[Dict]:
        """
        Search products and include enhanced inventory information.

        **Performance fix**: previously this issued one
        ``inventory_levels`` call *per variant*, meaning 250 products × N
        variants could saturate the rate-limit bucket in a single user
        request.  Now all inventory_item_ids are collected up-front and
        fetched in batches of up to 100, reducing hundreds of calls to a
        handful.

        Args:
            query: Search by product title.
            product_type: Filter by product type.
            vendor: Filter by vendor name.
            tag: Filter by product tag.
            in_stock_only: Only return products with available inventory.
            limit: Maximum number of results.
        """
        products = await self.search_products(
            query=query,
            product_type=product_type,
            vendor=vendor,
            tag=tag,
            limit=limit,
        )

        # Collect every inventory_item_id across all products/variants
        all_inventory_item_ids: List[str] = []
        for product in products:
            for variant in product.get("variants", []):
                iid = variant.get("inventory_item_id")
                if iid:
                    all_inventory_item_ids.append(str(iid))

        # Fetch all inventory data in batches — O(ceil(N/100)) calls
        inventory_map: Dict[str, Dict] = {}
        if all_inventory_item_ids:
            inventory_map = await self.get_inventory_levels_batch(all_inventory_item_ids)

        # Annotate each variant with its inventory data
        for product in products:
            for variant in product.get("variants", []):
                iid = str(variant.get("inventory_item_id", ""))
                inv = inventory_map.get(iid, {"available": 0})
                variant["inventory_details"] = inv
                # Keep inventory_quantity in sync for backward compatibility
                variant["inventory_quantity"] = inv.get("available", variant.get("inventory_quantity", 0))

        # Optionally filter to in-stock items only
        if in_stock_only:
            products = [
                p for p in products
                if any(
                    v.get("inventory_details", {}).get("available", 0) > 0
                    or v.get("inventory_quantity", 0) > 0
                    for v in p.get("variants", [])
                )
            ]

        return products

    # ------------------------------------------------------------------
    # GraphQL helper — products + inventory in a single round-trip
    # ------------------------------------------------------------------

    async def search_products_graphql(
        self,
        query: Optional[str] = None,
        first: int = 20,
    ) -> List[Dict]:
        """
        Fetch products together with their inventory quantities using the
        GraphQL Admin API — **one network round-trip regardless of the
        number of products or variants**.

        This is the most efficient way to get product + inventory data and
        should be preferred over ``search_products_with_inventory`` for
        large result sets.

        No new credentials are required — uses the same access token.

        Args:
            query: Shopify product search query string
                   (e.g. ``"title:shirt"`` or ``"product_type:shoes"``).
            first: Number of products to return (max 250).

        Returns:
            List of simplified product dicts compatible with the REST format.
        """
        gql_query = """
        query SearchProducts($query: String, $first: Int!) {
          products(query: $query, first: $first) {
            edges {
              node {
                id
                title
                handle
                descriptionHtml
                productType
                vendor
                status
                tags
                variants(first: 20) {
                  edges {
                    node {
                      id
                      title
                      sku
                      price
                      compareAtPrice
                      inventoryPolicy
                      inventoryQuantity
                      inventoryItem {
                        id
                        inventoryLevels(first: 3) {
                          edges {
                            node {
                              quantities(names: ["available"]) {
                                name
                                quantity
                              }
                              location {
                                name
                              }
                            }
                          }
                        }
                      }
                    }
                  }
                }
                images(first: 5) {
                  edges {
                    node {
                      src
                      altText
                    }
                  }
                }
              }
            }
          }
        }
        """

        variables = {"first": first}
        if query:
            variables["query"] = query

        result = await self._graphql(gql_query, variables)

        errors = result.get("errors")
        if errors:
            raise RuntimeError(f"GraphQL errors: {errors}")

        # Normalise GraphQL response into a REST-like structure
        products = []
        edges = result.get("data", {}).get("products", {}).get("edges", [])
        for edge in edges:
            node = edge["node"]
            # Extract numeric ID from GID (gid://shopify/Product/12345)
            gid = node.get("id", "")
            numeric_id = gid.split("/")[-1] if "/" in gid else gid

            variants = []
            for v_edge in node.get("variants", {}).get("edges", []):
                v = v_edge["node"]
                v_gid = v.get("id", "")
                v_numeric_id = v_gid.split("/")[-1] if "/" in v_gid else v_gid

                # Sum inventory across all locations.
                # In the 2024-10 GraphQL schema, inventory is exposed via
                # quantities(names: ["available"]) — NOT a bare `available` field.
                inv_levels = (
                    v.get("inventoryItem", {})
                    .get("inventoryLevels", {})
                    .get("edges", [])
                )
                total_available = 0
                for le in inv_levels:
                    for qty in le["node"].get("quantities", []):
                        if qty.get("name") == "available":
                            total_available += qty.get("quantity", 0)

                variants.append({
                    "id": v_numeric_id,
                    "title": v.get("title"),
                    "sku": v.get("sku"),
                    "price": v.get("price"),
                    "compare_at_price": v.get("compareAtPrice"),
                    "inventory_policy": v.get("inventoryPolicy", "").lower(),
                    "inventory_quantity": total_available,
                    "inventory_details": {
                        "available": total_available,
                        "locations": len(inv_levels),
                    },
                })

            images = [
                {"src": ie["node"]["src"], "alt": ie["node"].get("altText")}
                for ie in node.get("images", {}).get("edges", [])
            ]

            products.append({
                "id": numeric_id,
                "title": node.get("title"),
                "handle": node.get("handle"),
                # body_html alias so existing display code works unchanged
                "body_html": node.get("descriptionHtml", ""),
                "product_type": node.get("productType"),
                "vendor": node.get("vendor"),
                "status": node.get("status", "").lower(),
                "tags": node.get("tags", []),
                "variants": variants,
                "images": images,
            })

        return products

    # ------------------------------------------------------------------
    # Customer operations
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Product type / category discovery
    # ------------------------------------------------------------------

    async def get_all_product_types(self) -> List[str]:
        """
        Fetch every distinct product_type value used in the store.

        Uses a single lightweight REST call that requests only the
        ``product_type`` field for up to 250 products, then deduplicates.
        For stores with more than 250 products this still covers the full
        range of *types* in practice (types are far fewer than products).

        Returns:
            Sorted list of non-empty product type strings, e.g.
            ["ACCESSORIES", "JEANS", "SHOES", "T-SHIRTS"]
        """
        try:
            data = await self._request(
                "products.json?fields=product_type&limit=250"
            )
            types: List[str] = sorted(
                {
                    p["product_type"].strip().upper()
                    for p in data.get("products", [])
                    if p.get("product_type", "").strip()
                }
            )
            return types
        except Exception:
            return []

    async def search_customers(self, query: str) -> List[Dict]:
        """Search customers by email or name."""
        data = await self._request(f"customers/search.json?query={query}")
        return data.get("customers", [])

    async def get_customer(self, customer_id: str) -> Optional[Dict]:
        """Get customer details."""
        try:
            data = await self._request(f"customers/{customer_id}.json")
            return data.get("customer")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    # ------------------------------------------------------------------
    # Refund operations
    # ------------------------------------------------------------------

    async def calculate_refund(
        self,
        order_id: str,
        refund_line_items: List[Dict],
    ) -> Dict:
        """Calculate suggested transactions before creating a refund."""
        data = await self._request(
            f"orders/{order_id}/refunds/calculate.json",
            method="POST",
            data={"refund": {"refund_line_items": refund_line_items}},
        )
        return data.get("refund", {})

    async def create_refund(
        self,
        order_id: str,
        refund_line_items: List[Dict],
        notify_customer: bool = True,
        note: Optional[str] = None,
    ) -> Dict:
        """Create a refund for an order."""
        refund_data = {
            "refund": {
                "notify": notify_customer,
                "note": note,
                "refund_line_items": refund_line_items,
            }
        }
        data = await self._request(
            f"orders/{order_id}/refunds.json",
            method="POST",
            data=refund_data,
        )
        return data.get("refund", {})

    # ------------------------------------------------------------------
    # Draft Order / Purchase operations
    # ------------------------------------------------------------------

    async def create_draft_order(
        self,
        line_items: List[Dict],
        customer_email: Optional[str] = None,
        customer_id: Optional[str] = None,
        shipping_address: Optional[Dict] = None,
        note: Optional[str] = None,
        discount_code: Optional[str] = None,
    ) -> Dict:
        """
        Create a draft order and return its invoice/checkout URL.

        Args:
            line_items: List of dicts with 'variant_id' and 'quantity'.
            customer_email: Customer's email.
            customer_id: Shopify customer ID (takes precedence over email).
            shipping_address: Optional shipping address dict.
            note: Optional note attached to the draft order.
            discount_code: Optional discount/promo code to apply.

        Returns:
            The full draft_order dict, which includes 'invoice_url'.
        """
        draft: Dict[str, Any] = {"line_items": line_items}

        if customer_id:
            draft["customer"] = {"id": customer_id}
            draft["use_customer_default_address"] = True
        elif customer_email:
            draft["email"] = customer_email

        if shipping_address:
            draft["shipping_address"] = shipping_address

        if note:
            draft["note"] = note

        if discount_code:
            draft["applied_discount"] = {
                "value_type": "fixed_amount",
                "value": "0.00",
                "title": discount_code,
                "description": discount_code,
            }

        data = await self._request(
            "draft_orders.json",
            method="POST",
            data={"draft_order": draft},
        )
        return data.get("draft_order", {})

    async def get_draft_order(self, draft_order_id: str) -> Optional[Dict]:
        """Retrieve a draft order by its ID."""
        try:
            data = await self._request(f"draft_orders/{draft_order_id}.json")
            return data.get("draft_order")
        except Exception:
            return None

    async def send_draft_order_invoice(self, draft_order_id: str) -> bool:
        """Send the invoice email for a draft order. Returns True on success."""
        try:
            await self._request(
                f"draft_orders/{draft_order_id}/send_invoice.json",
                method="POST",
                data={"draft_order_invoice": {}},
            )
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def aclose(self) -> None:
        """No-op: clients are now short-lived and closed after each request."""
        pass

    async def __aenter__(self) -> "ShopifyClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass
