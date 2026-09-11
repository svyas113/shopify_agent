"""Remove a product from the customer's session cart."""
import fastworkflow
from fastworkflow.train.generate_synthetic import generate_diverse_utterances
from pydantic import BaseModel, Field, validator
from typing import Optional

# Shared keys (must match add_to_cart.py, view_cart.py, checkout.py)
CART_KEY = "cart"
EMAIL_KEY = "cart_customer_email"


# ---------------------------------------------------------------------------
# Signature
# ---------------------------------------------------------------------------

class Signature:
    """
    Remove a specific item from the customer's cart by matching the product
    name they mention. Does NOT touch any other items.

    STRICT FORMATTING RULES:
    1. Plain text only. No markdown tables or '|' characters.
    2. Use '•' for bullet points.
    3. After removing, show the updated cart total (or say cart is empty).
    4. If the item is not found in the cart, say so clearly and list what IS
       in the cart so the customer knows what to ask to remove.
    """

    class Input(BaseModel):
        product_name: str = Field(
            description="Name or partial name of the product to remove from the cart",
            examples=["backpack", "nike cap", "superstar 80s"],
            min_length=2,
        )

        @validator("product_name")
        def clean(cls, v: str) -> str:
            return v.replace("?", "").strip()

    plain_utterances = [
        "remove the backpack from my cart",
        "take the nike cap out of my cart",
        "delete adidas superstar 80s from cart",
        "I don't want the jacket anymore, remove it",
        "remove item from cart",
        "take out the shoes from my basket",
        "cancel the t-shirt from my order",
        "remove the cap please",
        "I changed my mind about the boots",
        "delete the hoodie from my cart",
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
    """Find the matching cart item by name similarity and remove it."""

    def _process_command(
        self,
        workflow: fastworkflow.Workflow,
        input: Signature.Input,
    ) -> str:
        cart: list = workflow.context.get(CART_KEY, [])

        if not cart:
            return (
                "Your cart is already empty — nothing to remove.\n\n"
                "Would you like to add something?"
            )

        # ------------------------------------------------------------------
        # Score each cart item by how many query words appear in its title
        # ------------------------------------------------------------------
        query_words = input.product_name.upper().split()

        def item_score(item: dict) -> int:
            combined = (
                item.get("product_title", "").upper()
                + " "
                + item.get("variant_title", "").upper()
            )
            return sum(1 for word in query_words if word in combined)

        scored = [(item, item_score(item)) for item in cart]
        scored.sort(key=lambda x: x[1], reverse=True)

        best_item, best_score = scored[0]

        if best_score == 0:
            # Nothing matched — list the cart so the customer knows what's there
            cart_lines = []
            for item in cart:
                title = item.get("product_title", "unknown")
                variant = item.get("variant_title", "")
                display = title
                if variant and variant.lower() not in ("default title", ""):
                    display += f" ({variant})"
                cart_lines.append(f"• {display}")

            return (
                f"I couldn't find '{input.product_name}' in your cart.\n\n"
                "Your cart currently contains:\n"
                + "\n".join(cart_lines)
                + "\n\nTell me the name of the item you'd like to remove."
            )

        # ------------------------------------------------------------------
        # Remove the matched item
        # ------------------------------------------------------------------
        removed_title = best_item.get("product_title", "item")
        removed_variant = best_item.get("variant_title", "")
        removed_qty = best_item.get("quantity", 1)
        removed_price = float(best_item.get("price", "0.00"))

        new_cart = [item for item in cart if item is not best_item]
        workflow.context[CART_KEY] = new_cart

        # ------------------------------------------------------------------
        # Build response
        # ------------------------------------------------------------------
        display = removed_title
        if removed_variant and removed_variant.lower() not in ("default title", ""):
            display += f" ({removed_variant})"

        lines = [
            f"Removed from your cart:",
            f"• {display} × {removed_qty} — ${removed_price * removed_qty:.2f}",
        ]

        if not new_cart:
            lines += ["", "Your cart is now empty. Add items whenever you're ready!"]
        else:
            cart_total = sum(float(i["price"]) * i["quantity"] for i in new_cart)
            cart_count = sum(i["quantity"] for i in new_cart)
            lines += [
                "",
                f"Updated cart: {cart_count} item(s), ${cart_total:.2f} total.",
                "",
                "Want to keep shopping or checkout?",
            ]

        return "\n".join(lines)

    def __call__(
        self,
        workflow: fastworkflow.Workflow,
        command: str,
        command_parameters: Signature.Input,
    ) -> fastworkflow.CommandOutput:
        response = self._process_command(workflow, command_parameters)

        return fastworkflow.CommandOutput(
            workflow_id=workflow.id,
            command_responses=[
                fastworkflow.CommandResponse(response=response)
            ],
        )
