"""Create a draft order (purchase) and return a Shopify checkout link to the customer."""
import fastworkflow
from fastworkflow.train.generate_synthetic import generate_diverse_utterances
from pydantic import BaseModel, Field, validator
from typing import Optional

from ..application.shopify_store import ShopifyStore


# ---------------------------------------------------------------------------
# Signature
# ---------------------------------------------------------------------------

class Signature:
    """
    Help a customer purchase a product by creating a Shopify draft order and
    returning a secure checkout/payment link.

    STRICT FORMATTING RULES:
    1. Respond in plain text ONLY. No markdown tables or '|' characters.
    2. Use '•' for bullet points.
    3. Always present the checkout URL clearly so the customer can tap/click it.
    4. Keep the tone friendly and brief — optimised for WhatsApp / chat.
    """

    class Input(BaseModel):
        product_name: str = Field(
            description="Name or description of the product the customer wants to buy",
            examples=["blue t-shirt", "leather jacket", "wireless headphones"],
            min_length=2,
        )
        quantity: int = Field(
            description="How many units the customer wants to buy",
            default=1,
            ge=1,
            le=100,
        )
        size: Optional[str] = Field(
            description="Size variant requested (e.g. S, M, L, XL, 7, 10)",
            examples=["S", "M", "L", "XL"],
            default=None,
        )
        color: Optional[str] = Field(
            description="Color variant requested",
            examples=["black", "blue", "red"],
            default=None,
        )
        customer_email: str = Field(
            description="Customer's email address — required to create the order and send the secure checkout link",
            examples=["alice@example.com", "john.doe@gmail.com"],
        )
        discount_code: Optional[str] = Field(
            description="Discount or promo code to apply to the order",
            examples=["SAVE10", "SUMMER20"],
            default=None,
        )

        @validator("product_name")
        def clean_product_name(cls, v: str) -> str:
            return v.replace("?", "").strip()

        @validator("size")
        def normalise_size(cls, v: Optional[str]) -> Optional[str]:
            if not v:
                return None
            cleaned = v.strip()
            if cleaned.upper() in ("NOT_FOUND", "N/A", "NA", "NONE", "NULL", "UNKNOWN"):
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
        "I want to buy a blue t-shirt in size M",
        "I'd like to purchase the leather jacket",
        "can I order 2 pairs of running shoes?",
        "buy the wireless headphones for me",
        "I want to get the black hoodie in large",
        "help me purchase a red dress",
        "order the denim jacket size L for customer@example.com",
        "I want to buy some sneakers, size 10",
        "place an order for the summer dress",
        "can you create an order for me for the yoga mat?",
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
    """
    1. Search for the requested product.
    2. Find the best matching variant (size / color).
    3. Create a Shopify Draft Order via the Admin API.
    4. Return the invoice_url so the customer can pay securely.
    """

    async def process_command(
        self,
        workflow: fastworkflow.Workflow,
        input: Signature.Input,
    ) -> str:
        # Resolve store context
        store: ShopifyStore = workflow.command_context_for_response_generation
        if store is None:
            store = ShopifyStore.get_default_instance()

        # ------------------------------------------------------------------
        # STEP 1 — Find the product
        # ------------------------------------------------------------------
        search_query = input.product_name
        if input.color and input.color not in input.product_name.lower():
            search_query = f"{input.color} {search_query}"

        products = await store.client.search_products_graphql(
            query=search_query,
            first=10,
        )

        if not products:
            return (
                f"Sorry, I couldn't find any products matching '{input.product_name}'. "
                "Try a different name, or ask me to search for available products."
            )

        # ------------------------------------------------------------------
        # STEP 2 — Pick the best variant
        # ------------------------------------------------------------------
        chosen_product = None
        chosen_variant = None

        for product in products:
            variants = product.get("variants", [])

            # Try to match size and/or color
            for variant in variants:
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
                    # Prefer in-stock variants
                    inventory = variant.get("inventory_quantity", 0)
                    inventory_policy = variant.get("inventory_policy", "deny")

                    if inventory > 0 or inventory_policy == "continue":
                        chosen_product = product
                        chosen_variant = variant
                        break

            if chosen_variant:
                break

        # Fallback: just take the first in-stock variant of the first product
        if not chosen_variant:
            for product in products:
                for variant in product.get("variants", []):
                    inventory = variant.get("inventory_quantity", 0)
                    policy = variant.get("inventory_policy", "deny")
                    if inventory > 0 or policy == "continue":
                        chosen_product = product
                        chosen_variant = variant
                        break
                if chosen_variant:
                    break

        if not chosen_variant:
            # Product found but everything is out of stock
            product_title = products[0].get("title", input.product_name)
            msg = f"Sorry, '{product_title}' is currently out of stock"
            if input.size:
                msg += f" in size {input.size}"
            if input.color:
                msg += f" in {input.color}"
            msg += ". Would you like to check for similar products?"
            return msg

        # ------------------------------------------------------------------
        # STEP 3 — Validate quantity against stock
        # ------------------------------------------------------------------
        available_qty = chosen_variant.get("inventory_quantity", 0)
        inventory_policy = chosen_variant.get("inventory_policy", "deny")

        if inventory_policy != "continue" and available_qty < input.quantity:
            if available_qty == 0:
                return (
                    f"Sorry, the selected variant of '{chosen_product.get('title')}' "
                    "just went out of stock. Please choose a different option."
                )
            return (
                f"Only {available_qty} unit(s) of '{chosen_product.get('title')}' "
                f"({chosen_variant.get('title', 'Default')}) are available. "
                f"Would you like to order {available_qty} instead?"
            )

        # ------------------------------------------------------------------
        # STEP 4 — Create the Draft Order
        # ------------------------------------------------------------------
        line_items = [
            {
                "variant_id": chosen_variant["id"],
                "quantity": input.quantity,
            }
        ]

        try:
            draft_order = await store.client.create_draft_order(
                line_items=line_items,
                customer_email=input.customer_email,
                discount_code=input.discount_code,
                note=(
                    f"Order placed via customer support agent. "
                    f"Customer requested: {input.product_name}"
                    + (f", size {input.size}" if input.size else "")
                    + (f", color {input.color}" if input.color else "")
                ),
            )
        except Exception as e:
            return (
                f"I found the product but couldn't create the order right now. "
                f"Error: {str(e)}. Please try again or contact support."
            )

        if not draft_order:
            return (
                "Something went wrong while creating your order. "
                "Please try again or contact our support team."
            )

        # ------------------------------------------------------------------
        # STEP 5 — Fire the invoice email to the customer immediately
        # ------------------------------------------------------------------
        draft_order_id = str(draft_order.get("id", ""))
        if draft_order_id:
            # This sends Shopify's built-in invoice email with the payment link.
            # We don't block on failure — the checkout URL is still usable.
            await store.client.send_draft_order_invoice(draft_order_id)

        # ------------------------------------------------------------------
        # STEP 6 — Build a friendly response with the checkout link
        # ------------------------------------------------------------------
        product_title = chosen_product.get("title", input.product_name)
        variant_title = chosen_variant.get("title", "")
        price = chosen_variant.get("price", "0.00")
        total = float(price) * input.quantity
        invoice_url = draft_order.get("invoice_url", "")
        draft_order_name = draft_order.get("name", "")

        lines = [
            f"Great! I've created your order for {product_title}.",
            "",
        ]

        if variant_title and variant_title.lower() != "default title":
            lines.append(f"• Variant:    {variant_title}")

        lines += [
            f"• Quantity:   {input.quantity}",
            f"• Unit Price: ${price}",
            f"• Total:      ${total:.2f}",
        ]

        if input.discount_code:
            lines.append(f"• Promo Code: {input.discount_code} (applied at checkout)")

        if draft_order_name:
            lines.append(f"• Order Ref:  {draft_order_name}")

        lines += [
            "",
            "To complete your purchase, please use the secure checkout link below:",
            f"{invoice_url}",
            "",
            "The link will take you to Shopify's checkout where you can enter your "
            "shipping address and pay securely. The link is valid for 3 days.",
        ]

        if input.customer_email:
            lines.append(
                f"\nA copy of this order summary has also been sent to {input.customer_email}."
            )

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
