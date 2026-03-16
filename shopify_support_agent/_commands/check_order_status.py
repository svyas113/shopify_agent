"""Check the status of a customer's order."""
import fastworkflow
from fastworkflow.train.generate_synthetic import generate_diverse_utterances
from pydantic import BaseModel, Field
from ..application.shopify_store import ShopifyStore
from datetime import datetime

class Signature:
    """Check order status and details."""
    
    class Input(BaseModel):
        order_identifier: str = Field(
            description="Order number, order ID, or customer email",
            examples=["#1001", "5678901234", "customer@example.com"]
        )
    
    plain_utterances = [
        "check order status for #1001",
        "what's the status of order 5678901234",
        "find orders for customer@example.com",
        "track my order #1234",
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

class ResponseGenerator:
    """Fetch and format order information."""
    
    async def process_command(
        self,
        workflow: fastworkflow.Workflow,
        input: Signature.Input
    ) -> str:
        store: ShopifyStore = workflow.command_context_for_response_generation
        
        # Always use default instance if context is not set
        if store is None:
            from ..application.shopify_store import ShopifyStore
            store = ShopifyStore.get_default_instance()
        
        # Determine if identifier is email, order number, or order ID
        identifier = input.order_identifier.strip()
        
        if "@" in identifier:
            # Search by customer email
            orders = await store.client.search_orders(
                customer_email=identifier,
                limit=5
            )
            
            if not orders:
                return f"No orders found for customer {identifier}."
            
            # Format multiple orders
            response = f"Found {len(orders)} order(s) for {identifier}:\n\n"
            for order in orders:
                response += self._format_order_summary(order) + "\n\n"
            
            return response.strip()
        
        else:
            # First try searching by order name
            orders = await store.client.search_orders(name=identifier, limit=1)
            
            if orders:
                # Found by name search
                order = orders[0]
                return self._format_order_details(order)
            
            # If not found by name, try by ID
            try:
                order_id = identifier.replace("#", "").strip()
                order = await store.client.get_order(order_id)
                
                if not order:
                    return f"Order {identifier} not found."
                
                return self._format_order_details(order)
            
            except Exception as e:
                return f"Error fetching order {identifier}: {str(e)}"
    
    def _format_order_summary(self, order: dict) -> str:
        """Format a brief order summary."""
        order_number = order.get("name", "N/A")
        total = order.get("total_price", "0.00")
        status = order.get("fulfillment_status", "unfulfilled")
        financial = order.get("financial_status", "pending")
        
        return (
            f"Order {order_number}: ${total} - "
            f"Fulfillment: {status}, Payment: {financial}"
        )
    
    def _format_order_details(self, order: dict) -> str:
        """Format detailed order information."""
        order_number = order.get("name", "N/A")
        created_at = order.get("created_at", "")
        total = order.get("total_price", "0.00")
        currency = order.get("currency", "USD")
        
        fulfillment_status = order.get("fulfillment_status", "unfulfilled")
        financial_status = order.get("financial_status", "pending")
        
        # Line items
        items = order.get("line_items", [])
        items_text = "\n".join([
            f"  - {item.get('quantity')}x {item.get('title')} (${item.get('price')})"
            for item in items
        ])
        
        # Shipping address
        shipping = order.get("shipping_address", {})
        shipping_text = ""
        if shipping:
            shipping_text = (
                f"\n\nShipping Address:\n"
                f"  {shipping.get('name', 'N/A')}\n"
                f"  {shipping.get('address1', '')}\n"
                f"  {shipping.get('city', '')}, {shipping.get('province', '')} "
                f"{shipping.get('zip', '')}"
            )
        
        return (
            f"Order {order_number}\n"
            f"Created: {created_at}\n"
            f"Total: {currency} ${total}\n"
            f"Fulfillment Status: {fulfillment_status}\n"
            f"Payment Status: {financial_status}\n\n"
            f"Items:\n{items_text}"
            f"{shipping_text}"
        )
    
    def __call__(
        self,
        workflow: fastworkflow.Workflow,
        command: str,
        command_parameters: Signature.Input,
    ) -> fastworkflow.CommandOutput:
        # FastWorkflow automatically handles async methods
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
