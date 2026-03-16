"""Get customer information."""
import fastworkflow
from fastworkflow.train.generate_synthetic import generate_diverse_utterances
from pydantic import BaseModel, Field
from ..application.shopify_store import ShopifyStore

class Signature:
    """Get information about a customer."""
    
    class Input(BaseModel):
        customer_identifier: str = Field(
            description="Customer ID, email, or name",
            examples=["1234567890", "customer@example.com", "John Smith"],
            min_length=2
        )
    
    plain_utterances = [
        "find customer with email customer@example.com",
        "get customer information for John Smith",
        "look up customer 1234567890",
        "show customer details for jane.doe@gmail.com",
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
    """Look up and display customer information."""
    
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
        identifier = input.customer_identifier.strip()
        
        # Try to determine if the identifier is an ID or search term
        if identifier.isdigit():
            # Treat as customer ID
            customer = await store.client.get_customer(identifier)
            if customer:
                return self._format_customer_details(customer)
            else:
                return f"No customer found with ID {identifier}."
        else:
            # Treat as a search term (email or name)
            customers = await store.client.search_customers(identifier)
            
            if not customers:
                return f"No customers found matching '{identifier}'."
            
            if len(customers) == 1:
                # Just one match, show details
                return self._format_customer_details(customers[0])
            else:
                # Multiple matches, show summary
                response = f"Found {len(customers)} customers matching '{identifier}':\n\n"
                for customer in customers:
                    response += self._format_customer_summary(customer) + "\n\n"
                return response.strip()
    
    def _format_customer_summary(self, customer: dict) -> str:
        """Format a brief customer summary."""
        customer_id = customer.get("id", "N/A")
        name = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()
        email = customer.get("email", "N/A")
        orders_count = customer.get("orders_count", 0)
        
        return (
            f"Customer: {name}\n"
            f"ID: {customer_id}\n"
            f"Email: {email}\n"
            f"Orders: {orders_count}"
        )
    
    def _format_customer_details(self, customer: dict) -> str:
        """Format detailed customer information."""
        customer_id = customer.get("id", "N/A")
        name = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()
        email = customer.get("email", "N/A")
        phone = customer.get("phone", "Not provided")
        orders_count = customer.get("orders_count", 0)
        total_spent = customer.get("total_spent", "0.00")
        tags = customer.get("tags", "")
        state = customer.get("state", "")
        created_at = customer.get("created_at", "")
        
        # Format address if available
        default_address = customer.get("default_address", {})
        address_text = ""
        if default_address:
            address1 = default_address.get("address1", "")
            address2 = default_address.get("address2", "")
            city = default_address.get("city", "")
            province = default_address.get("province", "")
            country = default_address.get("country", "")
            zip_code = default_address.get("zip", "")
            
            address_text = (
                f"\n\nDefault Address:\n"
                f"  {address1}"
            )
            if address2:
                address_text += f"\n  {address2}"
            address_text += f"\n  {city}, {province} {zip_code}\n  {country}"
        
        return (
            f"Customer: {name}\n"
            f"ID: {customer_id}\n"
            f"Email: {email}\n"
            f"Phone: {phone}\n"
            f"Created: {created_at}\n"
            f"Status: {state}\n"
            f"Orders: {orders_count}\n"
            f"Total Spent: ${total_spent}\n"
            f"Tags: {tags}"
            f"{address_text}"
        )
    
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
