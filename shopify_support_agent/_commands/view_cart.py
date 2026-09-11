"""Show the customer what's currently in their session cart."""
import fastworkflow
from fastworkflow.train.generate_synthetic import generate_diverse_utterances
from pydantic import BaseModel

# Shared keys (must match add_to_cart.py and checkout.py)
CART_KEY = "cart"
EMAIL_KEY = "cart_customer_email"


# ---------------------------------------------------------------------------
# Signature
# ---------------------------------------------------------------------------

class Signature:
    """
    Display the contents of the customer's current shopping cart.

    STRICT FORMATTING RULES:
    1. Plain text only. No markdown tables or '|' characters.
    2. Use '•' for each line item.
    3. Always show the cart total at the end.
    4. If the cart is empty, say so and invite them to start adding items.
    """

    class Input(BaseModel):
        pass  # No parameters needed — cart lives in session_data

    plain_utterances = [
        "show me my cart",
        "what's in my cart?",
        "view my shopping cart",
        "what have I added so far?",
        "show cart",
        "display my basket",
        "what items do I have?",
        "cart summary",
        "how many items in my cart?",
        "review my cart",
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
    """Read the session cart and format it as a readable summary."""

    def _process_command(
        self,
        workflow: fastworkflow.Workflow,
        input: Signature.Input,
    ) -> str:
        cart: list = workflow.context.get(CART_KEY, [])
        email: str = workflow.context.get(EMAIL_KEY, "")

        if not cart:
            return (
                "Your cart is currently empty.\n\n"
                "You can ask me to add products — for example:\n"
                "  'Add adidas superstar 80s size 9 to my cart'"
            )

        lines = ["Here's what's in your cart:\n"]

        cart_total = 0.0
        for item in cart:
            title = item.get("product_title", "Unknown product")
            variant = item.get("variant_title", "")
            qty = item.get("quantity", 1)
            price = float(item.get("price", "0.00"))
            line_total = price * qty
            cart_total += line_total

            display = f"• {title}"
            if variant and variant.lower() not in ("default title", ""):
                display += f" ({variant})"
            display += f" × {qty} — ${line_total:.2f}"
            lines.append(display)

        lines += [
            "",
            f"Total: ${cart_total:.2f}",
        ]

        if email:
            lines.append(f"Checkout link will be sent to: {email}")

        lines += [
            "",
            "Ready to pay? Just say 'checkout' and I'll create your secure payment link.",
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
