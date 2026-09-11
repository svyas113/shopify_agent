"""Add a product to the customer's session cart (no checkout link yet)."""
import fastworkflow
from fastworkflow.train.generate_synthetic import generate_diverse_utterances
from pydantic import BaseModel, Field, validator
from typing import Optional

from ..application.shopify_store import ShopifyStore

# Keys used in workflow.context
CART_KEY = "cart"
EMAIL_KEY = "cart_customer_email"


# ---------------------------------------------------------------------------
# Signature
# ---------------------------------------------------------------------------

class Signature:
    """
    Add a product to the customer's cart.
    Does NOT create a checkout link yet — that happens when the customer says
    'checkout' or 'I'm done'. The email is asked once and remembered for the
    whole session.

    STRICT FORMATTING RULES:
    1. Plain text only. No markdown tables or '|' characters.
    2. Use '•' for bullet points.
    3. After adding, always tell the customer their current cart total and
       ask if they want to add more or checkout.
    """

    class Input(BaseModel):
        product_name: str = Field(
            description="Name or description of the product to add to the cart",
            examples=["blue t-shirt", "leather jacket", "wireless headphones"],
            min_length=2,
        )
        quantity: int = Field(
            description="How many units to add",
            default=1,
            ge=1,
            le=100,
        )
        size: Optional[str] = Field(
            description="Size variant (e.g. S, M, L, XL, 7, 10)",
            examples=["S", "M", "L", "XL"],
            default=None,
        )
        color: Optional[str] = Field(
            description="Color variant",
            examples=["black", "blue", "red"],
            default=None,
        )
        customer_email: str = Field(
            description=(
                "Customer's email address — needed once to send the final checkout link. "
                "If the customer already provided it earlier in the conversation, reuse it."
            ),
            examples=["alice@example.com"],
        )

        @validator("product_name")
        def clean_product_name(cls, v: str) -> str:
            return v.replace("?", "").strip()

        @validator("size")
        def normalise_size(cls, v: Optional[str]) -> Optional[str]:
            if not v:
                return None
            cleaned = v.strip()
            if cleaned.upper() in ("NOT_FOUND", "N/A", "NA", "NONE", "NULL", "UNKNOWN", "N/A"):
                return None
            return cleaned.upper()

        @validator("color")
        def normalise_color(cls, v: Optional[str]) -> Optional[str]:
            if not v:
                return None
            cleaned = v.strip()
            if cleaned.upper() in ("NOT_FOUND", "N/A", "NA", "NONE", "NULL", "UNKNOWN"):
                return None
            return cleaned.lower()

    plain_utterances = [
        "add blue t-shirt size M to my cart",
        "put the leather jacket in my cart",
        "add 2 pairs of running shoes to the cart",
        "I want the black hoodie in large added to cart",
        "can you add wireless headphones to my basket?",
        "add the adidas classic backpack to cart",
        "put adidas superstar 80s size 9 in cart",
        "add this to my shopping cart",
        "I'd like to add the red dress to cart",
        "add a yoga mat to my cart please",
    ]

    @staticmethod
    def generate_utterances(
        workflow: fastworkflow.Workflow,
        command_name: str,
    ) -> list[str]:
        return [
            command_name.split("/")[-1].lower().replace("_", " ")
        ] + generate_diverse_utterances(
            Signature.plain_utterances,
            command_name,
        )


# ---------------------------------------------------------------------------
# Response Generator
# ---------------------------------------------------------------------------

class ResponseGenerator:
    """Find the product variant and add it to the session cart."""

    async def process_command(
        self,
        workflow: fastworkflow.Workflow,
        input: Signature.Input,
    ) -> str:
        # Resolve store
        store: ShopifyStore = workflow.command_context_for_response_generation
        if store is None:
            store = ShopifyStore.get_default_instance()

        # ------------------------------------------------------------------
        # Remember the email for the whole session (asked only once)
        # ------------------------------------------------------------------
        workflow.context[EMAIL_KEY] = input.customer_email

        # ------------------------------------------------------------------
        # STEP 1 — Search with progressively broader queries until we get hits
        # ------------------------------------------------------------------
        query_words = input.product_name.upper().split()
        search_attempts = []

        # Build search attempts from most-specific to least-specific
        search_attempts.append(input.product_name)
        if len(query_words) > 2:
            # Try last 3 words (usually most descriptive, skip brand prefix)
            search_attempts.append(" ".join(query_words[-3:]))
        if len(query_words) > 1:
            # Try last 2 words
            search_attempts.append(" ".join(query_words[-2:]))
        # Try the longest single word (not "CAP", "THE", etc.)
        long_words = sorted([w for w in query_words if len(w) > 3], key=len, reverse=True)
        if long_words:
            search_attempts.append(long_words[0])

        products = []
        for attempt in search_attempts:
            products = await store.client.search_products_graphql(
                query=attempt,
                first=10,
            )
            if products:
                break

        if not products:
            return (
                f"Sorry, I couldn't find any products matching '{input.product_name}'. "
                "Try a different name or ask me to search available products."
            )

        # ------------------------------------------------------------------
        # STEP 2 — Score products by title similarity, pick best match
        # ------------------------------------------------------------------
        def title_score(product: dict) -> int:
            """Count how many words from the user query appear in the product title."""
            title_upper = product.get("title", "").upper()
            # Also check vendor field
            vendor_upper = product.get("vendor", "").upper()
            combined = title_upper + " " + vendor_upper
            return sum(1 for word in query_words if word in combined)

        # Sort products by match score descending
        products_scored = sorted(products, key=title_score, reverse=True)

        # Only consider products whose score is >= half the query words
        # (prevents completely unrelated fallback)
        min_score = max(1, len(query_words) // 2)
        best_score = title_score(products_scored[0])
        if best_score < min_score:
            return (
                f"Sorry, I couldn't find '{input.product_name}' in the store. "
                "Could you try a different name? You can also ask me to search available products."
            )

        # Filter to products with the best score (ties allowed)
        top_products = [p for p in products_scored if title_score(p) == best_score]

        chosen_product = None
        chosen_variant = None

        for product in top_products:
            for variant in product.get("variants", []):
                variant_title = variant.get("title", "").upper()
                option_values = [
                    str(v).upper()
                    for v in [
                        variant.get("option1"),
                        variant.get("option2"),
                        variant.get("option3"),
                    ]
                    if v
                ]

                size_ok = (
                    not input.size
                    or input.size in variant_title
                    or input.size in option_values
                )
                color_ok = (
                    not input.color
                    or input.color.upper() in variant_title
                    or input.color.upper() in option_values
                )

                if size_ok and color_ok:
                    inventory = variant.get("inventory_quantity", 0)
                    policy = variant.get("inventory_policy", "deny")
                    if inventory > 0 or policy == "continue":
                        chosen_product = product
                        chosen_variant = variant
                        break

            if chosen_variant:
                break

        # Fallback within top_products: first in-stock variant (ignoring size/color)
        if not chosen_variant:
            for product in top_products:
                for variant in product.get("variants", []):
                    if variant.get("inventory_quantity", 0) > 0 or variant.get("inventory_policy") == "continue":
                        chosen_product = product
                        chosen_variant = variant
                        break
                if chosen_variant:
                    break

        if not chosen_variant:
            product_title = top_products[0].get("title", input.product_name)
            msg = f"Sorry, '{product_title}' is currently out of stock"
            if input.size:
                msg += f" in size {input.size}"
            if input.color:
                msg += f" in {input.color}"
            msg += ". Would you like to check similar products?"
            return msg

        # ------------------------------------------------------------------
        # STEP 3 — Check stock vs requested quantity
        # ------------------------------------------------------------------
        available_qty = chosen_variant.get("inventory_quantity", 0)
        policy = chosen_variant.get("inventory_policy", "deny")

        if policy != "continue" and available_qty < input.quantity:
            if available_qty == 0:
                return (
                    f"Sorry, '{chosen_product.get('title')}' just went out of stock. "
                    "Please choose a different option."
                )
            return (
                f"Only {available_qty} unit(s) of '{chosen_product.get('title')}' "
                f"({chosen_variant.get('title', 'Default')}) are available. "
                f"Would you like to add {available_qty} instead?"
            )

        # ------------------------------------------------------------------
        # STEP 4 — Add to session cart
        # ------------------------------------------------------------------
        cart: list = workflow.context.get(CART_KEY, [])

        # If the same variant is already in the cart, increment quantity
        existing = next(
            (item for item in cart if item["variant_id"] == chosen_variant["id"]),
            None,
        )
        if existing:
            existing["quantity"] += input.quantity
        else:
            cart.append({
                "variant_id": chosen_variant["id"],
                "quantity": input.quantity,
                "product_title": chosen_product.get("title", input.product_name),
                "variant_title": chosen_variant.get("title", ""),
                "price": chosen_variant.get("price", "0.00"),
            })

        workflow.context[CART_KEY] = cart

        # ------------------------------------------------------------------
        # STEP 5 — Build response
        # ------------------------------------------------------------------
        product_title = chosen_product.get("title", input.product_name)
        variant_title = chosen_variant.get("title", "")
        price = float(chosen_variant.get("price", "0.00"))

        # Cart summary
        cart_total = sum(float(item["price"]) * item["quantity"] for item in cart)
        cart_count = sum(item["quantity"] for item in cart)

        lines = [
            f"Added to your cart!",
            f"• {product_title}"
            + (f" ({variant_title})" if variant_title and variant_title.lower() != "default title" else "")
            + f" × {input.quantity} — ${price * input.quantity:.2f}",
            "",
            f"Your cart: {cart_count} item(s), ${cart_total:.2f} total.",
            "",
            "Want to add more items, or are you ready to checkout?",
        ]

        return "\n".join(lines)

    def __call__(
        self,
        workflow: fastworkflow.Workflow,
        command: str,
        command_parameters: Signature.Input,
    ) -> fastworkflow.CommandOutput:
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                raise RuntimeError("closed")
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        response = loop.run_until_complete(
            self.process_command(workflow, command_parameters)
        )

        return fastworkflow.CommandOutput(
            workflow_id=workflow.id,
            command_responses=[
                fastworkflow.CommandResponse(response=response)
            ],
        )
