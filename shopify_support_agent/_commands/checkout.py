"""Checkout: create one draft order for everything in the session cart."""
import fastworkflow
from fastworkflow.train.generate_synthetic import generate_diverse_utterances
from pydantic import BaseModel

from ..application.shopify_store import ShopifyStore

# Shared keys (must match add_to_cart.py and view_cart.py)
CART_KEY = "cart"
EMAIL_KEY = "cart_customer_email"


# ---------------------------------------------------------------------------
# Signature
# ---------------------------------------------------------------------------

class Signature:
    """
    Finalise the customer's cart by creating a single Shopify draft order
    containing all items, then return ONE secure checkout link and send
    the invoice email.

    STRICT FORMATTING RULES:
    1. Plain text only. No markdown tables or '|' characters.
    2. Use '•' for line items.
    3. Show the full order summary before the checkout link.
    4. Keep the tone warm and brief — optimised for WhatsApp / chat.
    """

    class Input(BaseModel):
        pass  # All data comes from workflow.context (cart + email)

    plain_utterances = [
        "checkout",
        "I'm ready to pay",
        "proceed to checkout",
        "complete my order",
        "I'm done adding items",
        "place my order",
        "finish my purchase",
        "ready to checkout",
        "that's everything, checkout please",
        "go to payment",
        "pay now",
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
    """Read the session cart, create one draft order, send invoice email."""

    async def process_command(
        self,
        workflow: fastworkflow.Workflow,
        input: Signature.Input,
    ) -> str:
        cart: list = workflow.context.get(CART_KEY, [])
        customer_email: str = workflow.context.get(EMAIL_KEY, "")

        # ------------------------------------------------------------------
        # Guard: empty cart
        # ------------------------------------------------------------------
        if not cart:
            return (
                "Your cart is empty — there's nothing to checkout yet.\n\n"
                "Tell me what you'd like to buy, for example:\n"
                "  'Add adidas superstar 80s size 9 to my cart'"
            )

        # ------------------------------------------------------------------
        # Guard: no email on file (shouldn't normally happen, but be safe)
        # ------------------------------------------------------------------
        if not customer_email:
            return (
                "I need your email address to send you the checkout link. "
                "Please tell me your email and I'll complete the order right away."
            )

        # ------------------------------------------------------------------
        # Resolve store
        # ------------------------------------------------------------------
        store: ShopifyStore = workflow.command_context_for_response_generation
        if store is None:
            store = ShopifyStore.get_default_instance()

        # ------------------------------------------------------------------
        # Build line_items list for the draft order
        # ------------------------------------------------------------------
        line_items = [
            {"variant_id": item["variant_id"], "quantity": item["quantity"]}
            for item in cart
        ]

        # ------------------------------------------------------------------
        # Create the single draft order
        # ------------------------------------------------------------------
        cart_total = sum(float(item["price"]) * item["quantity"] for item in cart)

        # Build a note listing all items for the merchant
        item_descriptions = ", ".join(
            f"{item['product_title']}"
            + (f" ({item['variant_title']})" if item.get("variant_title") and item["variant_title"].lower() not in ("default title", "") else "")
            + f" x{item['quantity']}"
            for item in cart
        )

        try:
            draft_order = await store.client.create_draft_order(
                line_items=line_items,
                customer_email=customer_email,
                note=f"Cart checkout via support agent. Items: {item_descriptions}",
            )
        except Exception as e:
            return (
                f"Sorry, I couldn't create your order right now. "
                f"Error: {str(e)}. Please try again or contact support."
            )

        if not draft_order:
            return (
                "Something went wrong while creating your order. "
                "Please try again or contact our support team."
            )

        # ------------------------------------------------------------------
        # Fire the invoice email immediately
        # ------------------------------------------------------------------
        draft_order_id = str(draft_order.get("id", ""))
        if draft_order_id:
            await store.client.send_draft_order_invoice(draft_order_id)

        # ------------------------------------------------------------------
        # Clear the cart from session so a new one can start fresh
        # ------------------------------------------------------------------
        workflow.context[CART_KEY] = []

        # ------------------------------------------------------------------
        # Build the response
        # ------------------------------------------------------------------
        invoice_url = draft_order.get("invoice_url", "")
        draft_order_name = draft_order.get("name", "")

        lines = ["Here's your order summary:\n"]

        for item in cart:
            title = item.get("product_title", "")
            variant = item.get("variant_title", "")
            qty = item.get("quantity", 1)
            price = float(item.get("price", "0.00"))
            line_total = price * qty

            display = f"• {title}"
            if variant and variant.lower() not in ("default title", ""):
                display += f" ({variant})"
            display += f" × {qty} — ${line_total:.2f}"
            lines.append(display)

        lines += [
            "",
            f"Total: ${cart_total:.2f}",
        ]

        if draft_order_name:
            lines.append(f"Order Ref: {draft_order_name}")

        lines += [
            "",
            "To complete your purchase, use the secure checkout link below:",
            f"{invoice_url}",
            "",
            "Enter your shipping address and pay securely. The link is valid for 3 days.",
            f"An invoice has also been sent to {customer_email}.",
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
