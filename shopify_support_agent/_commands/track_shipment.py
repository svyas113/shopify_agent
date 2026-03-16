"""Get tracking information for an order."""
import fastworkflow
from fastworkflow.train.generate_synthetic import generate_diverse_utterances
from pydantic import BaseModel, Field
from ..application.shopify_store import ShopifyStore

class Signature:
    """Get shipment tracking information for an order."""
    
    class Input(BaseModel):
        order_identifier: str = Field(
            description="Order number or order ID",
            examples=["#1001", "5678901234"]
        )
    
    plain_utterances = [
        "track shipment for order #1001",
        "where is my order #1234",
        "get tracking info for order 5678901234",
        "check shipping status of order #5678",
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
    """Fetch and display tracking information."""
    
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
        
        # Clean order ID
        order_id = input.order_identifier.replace("#", "").strip()
        
        # Fetch order
        order = await store.client.get_order(order_id)
        
        if not order:
            return f"Order {input.order_identifier} not found."
        
        order_number = order.get("name", order_id)
        fulfillment_status = order.get("fulfillment_status", "unfulfilled")
        
        if fulfillment_status == "unfulfilled":
            return (
                f"Order {order_number} has not been fulfilled yet.\n"
                f"Status: Pending shipment"
            )
        
        # Get fulfillments
        fulfillments = await store.client.get_order_fulfillments(order_id)
        
        if not fulfillments:
            return f"No tracking information available for order {order_number}."
        
        response = f"Tracking Information for Order {order_number}:\n\n"
        
        for idx, fulfillment in enumerate(fulfillments, 1):
            status = fulfillment.get("status", "unknown")
            tracking_number = fulfillment.get("tracking_number", "N/A")
            tracking_url = fulfillment.get("tracking_url", "")
            carrier = fulfillment.get("tracking_company", "Unknown Carrier")
            
            response += f"Shipment #{idx}:\n"
            response += f"  Carrier: {carrier}\n"
            response += f"  Tracking Number: {tracking_number}\n"
            response += f"  Status: {status}\n"
            
            if tracking_url:
                response += f"  Track: {tracking_url}\n"
            
            response += "\n"
        
        return response.strip()
    
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
