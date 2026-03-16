"""Process a refund for a customer order using the Calculate-then-Create flow."""
import fastworkflow
from fastworkflow.train.generate_synthetic import generate_diverse_utterances
from pydantic import BaseModel, Field
from typing import Optional
from ..application.shopify_store import ShopifyStore

class Signature:
    """
    Process a refund for an order.
    
    STRICT RULES:
    1. Respond in plain text ONLY. 
    2. NEVER use markdown tables or '|' characters.
    3. Use '•' for bullet points.
    4. Keep the response friendly and concise for WhatsApp.
    """
    
    class Input(BaseModel):
        order_identifier: str = Field(
            description="Order ID, order number (e.g. #1001), or customer email",
            examples=["#1001", "5678901234", "customer@example.com"]
        )
        reason: str = Field(
            description="Reason for the refund",
            examples=["Customer not satisfied", "Damaged item"],
            min_length=5
        )
        notify_customer: bool = Field(
            description="Whether to send refund notification to customer",
            default=True
        )

    plain_utterances = [
        "process refund for order #1001 due to damaged item",
        "refund customer order 5678901234",
        "I want a refund for my order #1002",
        "cancel and refund order egnition_sample_1525@egnition.com"
    ]
    
    @staticmethod
    def generate_utterances(workflow: fastworkflow.Workflow, command_name: str) -> list[str]:
        return [
            command_name.split("/")[-1].lower().replace("_", " ")
        ] + generate_diverse_utterances(Signature.plain_utterances, command_name)

class ResponseGenerator:
    """Resolve IDs, calculate monetary transactions, and execute refund."""
    
    async def process_command(
        self,
        workflow: fastworkflow.Workflow,
        input: Signature.Input
    ) -> str:
        store: ShopifyStore = workflow.command_context_for_response_generation
        if store is None:
            from ..application.shopify_store import ShopifyStore
            store = ShopifyStore.get_default_instance()

        raw_id = input.order_identifier.strip()
        numeric_id = None
        order_data = None

        # --- STEP 1: RESOLVE IDENTIFIER TO NUMERIC ID ---
        if raw_id.isdigit() and len(raw_id) > 10:
            numeric_id = raw_id
            order_data = await store.client.get_order(numeric_id)
        
        if not order_data:
            search_params = {"email": raw_id} if "@" in raw_id else {"name": raw_id}
            search_results = await store.client.search_orders(limit=1, **search_params)
            
            if search_results:
                order_data = search_results[0]
                numeric_id = str(order_data.get("id"))
            else:
                return f"I couldn't find an order for '{raw_id}'. Please check the order number and try again."

        # --- STEP 2: BUILD LINE ITEMS ---
        line_items = order_data.get("line_items", [])
        if not line_items:
            return f"Order {order_data.get('name')} has no items available for a refund."

        refund_line_items = [
            {
                "line_item_id": item.get("id"),
                "quantity": item.get("quantity"),
                "restock_type": "no_restock"
            }
            for item in line_items
        ]

        # --- STEP 3: CALCULATE REFUND (The Mandatory Step) ---
        # This tells Shopify to find the 'parent_id' for the payment gateway transaction
        try:
            calculation = await store.client.calculate_refund(numeric_id, refund_line_items)
            suggested_transactions = calculation.get("transactions", [])
        except Exception as e:
            return f"Could not calculate refund: {str(e)}"

        # --- STEP 4: EXECUTE REFUND ---
        try:
            refund = await store.client.create_refund(
                order_id=numeric_id,
                refund_line_items=refund_line_items,
                notify_customer=input.notify_customer,
                note=input.reason,
                transactions=suggested_transactions
            )
            
            # Safe parsing of the response
            transactions = refund.get("transactions", [])
            if transactions:
                total_refunded = transactions[0].get("amount", "0.00")
                currency = transactions[0].get("currency", "USD")
            else:
                total_refunded = "0.00 (Restock Only)"
                currency = ""

            return (
                f"Refund successfully processed for order {order_data.get('name')}.\n\n"
                f"• Amount: {currency} ${total_refunded}\n"
                f"• Reason: {input.reason}\n"
                f"• Customer Notified: {'Yes' if input.notify_customer else 'No'}\n\n"
                f"The funds should appear in the customer's account within 5-10 business days."
            )
        
        except Exception as e:
            return f"Shopify API Error: {str(e)}"

    def __call__(
        self,
        workflow: fastworkflow.Workflow,
        command: str,
        command_parameters: Signature.Input,
    ) -> fastworkflow.CommandOutput:
        import asyncio
        # We wrap in asyncio.run to maintain parity with your existing commands
        response = asyncio.run(
            self.process_command(workflow, command_parameters)
        )
        
        return fastworkflow.CommandOutput(
            workflow_id=workflow.id,
            command_responses=[
                fastworkflow.CommandResponse(response=response)
            ]
        )