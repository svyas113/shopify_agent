"""Search for products in the store with enhanced details and recommendations."""
import fastworkflow
from fastworkflow.train.generate_synthetic import generate_diverse_utterances
from pydantic import BaseModel, Field, validator
from typing import Optional
import textwrap
from babel.numbers import format_currency

from ..application.shopify_store import ShopifyStore


# ---------------------------
# Presentation helpers ONLY
# ---------------------------

LABEL_WIDTH = 12
LINE_WIDTH = 60


def label(key: str, value: str = "") -> str:
    return f"{key + ':':<{LABEL_WIDTH}} {value}"


def wrap(text: str) -> str:
    return textwrap.fill(text, width=LINE_WIDTH)


def format_price_range(min_price: float, max_price: float) -> str:
    if min_price == max_price:
        return format_currency(min_price, "USD", locale="en_US")
    return (
        f"{format_currency(min_price, 'USD', locale='en_US')} – "
        f"{format_currency(max_price, 'USD', locale='en_US')}"
    )


# ---------------------------
# Signature
# ---------------------------

class Signature:
    """Search for products in the store.
    
    STRICT FORMATTING RULES:
    1. NEVER use markdown tables or the '|' character.
    2. Respond with a simple list of products.
    3. Use '•' for bullets and plain text for labels (e.g., Price: $10).
    4. NO bolding (**) or italics (_)."""

    class Input(BaseModel):
        query: str = Field(
            description="Product search query (name, keyword, or description)",
            examples=["blue jeans", "leather jacket", "running shoes"],
            min_length=2
        )
        product_type: Optional[str] = Field(
            description="Filter by product type/category",
            examples=["t-shirt", "jeans", "shoes"],
            default=None
        )
        in_stock_only: bool = Field(
            description="Only show products that are in stock",
            default=False
        )

        @validator("query")
        def clean_query(cls, v):
            return v.replace("?", "").strip()

    plain_utterances = [
        "search for blue jeans",
        "find leather jacket products",
        "do we have running shoes in stock?",
        "show me products matching winter coat",
        "do you have blue t-shirts available?",
        "what black dresses do you have?",
        "are there any hoodies in stock?",
        "show me available sneakers",
    ]

    @staticmethod
    def generate_utterances(
        workflow: fastworkflow.Workflow,
        command_name: str
    ) -> list[str]:
        return [
            command_name.split("/")[-1].lower().replace("_", " ")
        ] + generate_diverse_utterances(
            Signature.plain_utterances,
            command_name
        )


# ---------------------------
# Response Generator
# ---------------------------

class ResponseGenerator:
    """Search and display product information with beautified text output."""

    async def process_command(
        self,
        workflow: fastworkflow.Workflow,
        input: Signature.Input
    ) -> str:
        # Get store from context or fallback
        store: ShopifyStore = workflow.command_context_for_response_generation
        if store is None:
            store = ShopifyStore.get_default_instance()

        products = await store.client.search_products_with_inventory(
            query=input.query,
            product_type=input.product_type,
            in_stock_only=input.in_stock_only,
            limit=250
        )

        if not products:
            return f"No products found matching '{input.query}'."

        response_lines: list[str] = []
        response_lines.append(
            f"Found {len(products)} product(s) matching '{input.query}':\n"
        )

        # ---------------------------
        # Products
        # ---------------------------
        for product in products:
            title = product.get("title", "N/A")
            vendor = product.get("vendor", "")
            product_type = product.get("product_type", "")
            variants = product.get("variants", [])

            prices = [float(v.get("price", 0)) for v in variants]
            min_price = min(prices) if prices else 0
            max_price = max(prices) if prices else 0
            price_text = format_price_range(min_price, max_price)

            total_inventory = sum(
                v.get("inventory_quantity", 0) for v in variants
            )
            stock_status = "In Stock" if total_inventory > 0 else "Out of Stock"

            response_lines.append(f"• {title}")
            response_lines.append(label("Vendor", vendor))
            response_lines.append(label("Type", product_type))
            response_lines.append(label("Price", price_text))
            status_line = stock_status
            if total_inventory < 5:
                status_line += f" ({total_inventory} left)"
            response_lines.append(label("Status", status_line))

            # Variants (unchanged logic)
            if len(variants) > 1:
                response_lines.append(label("Options"))
                for variant in variants:
                    option_title = variant.get("title", "Default")
                    if option_title == "Default":
                        continue

                    inventory = variant.get("inventory_quantity", 0)
                    variant_status = (
                        "In Stock" if inventory > 0 else "Out of Stock"
                    )

                    line = f"  - {option_title:<20} {variant_status}"
                    if inventory < 5:
                        line += f" ({inventory} left)"
                    response_lines.append(line)

            # Description
            description = (
                product.get("body_html", "")
                .replace("<p>", "")
                .replace("</p>", "")
                .strip()
            )

            if description:
                wrapped = wrap(description)
                if len(wrapped) > 120:
                    wrapped = wrapped[:117] + "..."
                response_lines.append(label("Description"))
                response_lines.append(f"  {wrapped}")

            response_lines.append("")  # spacing

        # ---------------------------
        # Recommendations (unchanged logic)
        # ---------------------------
        if products:
            first_product = products[0]
            recommendations = await store.client.get_product_recommendations(
                str(first_product.get("id")),
                limit=3
            )

            if recommendations:
                response_lines.append("You might also like:")
                for rec in recommendations:
                    rec_title = rec.get("title", "")
                    rec_type = rec.get("product_type", "")
                    rec_inventory = sum(
                        v.get("inventory_quantity", 0)
                        for v in rec.get("variants", [])
                    )
                    rec_status = (
                        "In Stock" if rec_inventory > 0 else "Out of Stock"
                    )
                    response_lines.append(
                        f"  - {rec_title} ({rec_type}) — {rec_status}"
                    )

        return "\n".join(response_lines).strip()

    def __call__(
        self,
        workflow: fastworkflow.Workflow,
        command: str,
        command_parameters: Signature.Input,
    ) -> fastworkflow.CommandOutput:
        import asyncio

        response = asyncio.run(
            self.process_command(workflow, command_parameters)
        )

        return fastworkflow.CommandOutput(
            workflow_id=workflow.id,
            command_responses=[
                fastworkflow.CommandResponse(response=response)
            ]
        )