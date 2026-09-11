"""Check if specific products are available in stock."""
import fastworkflow
from fastworkflow.train.generate_synthetic import generate_diverse_utterances
from pydantic import BaseModel, Field, validator
from typing import Optional
from ..application.shopify_store import ShopifyStore

class Signature:
    """Check if a specific product is available."""
    
    class Input(BaseModel):
        product_name: str = Field(
            description="The name or description of the product to check",
            examples=["blue t-shirt", "leather jacket in size M", "wireless headphones"],
            min_length=2
        )
        size: Optional[str] = Field(
            description="Specific size to check for",
            examples=["S", "M", "L", "XL", "7", "10"],
            default=None
        )
        color: Optional[str] = Field(
            description="Specific color to check for",
            examples=["blue", "black", "red"],
            default=None
        )
        
        @validator('product_name')
        def clean_query(cls, v):
            # Remove question marks and other unnecessary punctuation
            return v.replace('?', '').strip()
    
    plain_utterances = [
        "do you have blue t-shirts in stock?",
        "is the leather jacket available in medium?",
        "check if wireless headphones are in stock",
        "are black running shoes available?",
        "do you have size 8 sneakers?",
        "is the denim jacket available?",
        "check if you have red dresses in stock",
        "do you carry wireless earbuds?",
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
    """Check and display product availability."""
    
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
        
        # Build search query using product name and potentially color
        search_query = input.product_name
        if input.color and input.color.lower() not in input.product_name.lower():
            search_query = f"{input.color} {search_query}"
            
        # Search for products
        products = await store.client.search_products_with_inventory(
            query=search_query,
            limit=5
        )
        
        if not products:
            return f"I couldn't find any products matching '{input.product_name}'. Would you like to browse our other available products?"
        
        # Handle size filtering if provided
        if input.size:
            size = input.size.upper()  # Normalize size
            filtered_products = []
            
            for product in products:
                # Look for variants with this size
                variants = product.get("variants", [])
                matching_variants = [
                    v for v in variants 
                    if (size in v.get("title", "").upper() or 
                        any(size == opt.get("value", "").upper() for opt in v.get("options", [])))
                ]
                
                if matching_variants:
                    # If we have matching variants, replace the product's variants with only matching ones
                    filtered_product = product.copy()
                    filtered_product["variants"] = matching_variants
                    filtered_products.append(filtered_product)
            
            # Use the filtered products if any match, otherwise keep the original
            if filtered_products:
                products = filtered_products
        
        # Check color filtering if provided
        if input.color and input.color.lower() not in input.product_name.lower():
            color = input.color.lower()
            filtered_products = []
            
            for product in products:
                # Check product title and description
                title = product.get("title", "").lower()
                desc = product.get("body_html", "").lower()
                
                # Look for color in variants
                variants = product.get("variants", [])
                matching_variants = [
                    v for v in variants 
                    if (color in v.get("title", "").lower() or
                        any(color == opt.get("value", "").lower() for opt in v.get("options", [])))
                ]
                
                # Include product if color is in title, description or has matching variants
                if color in title or color in desc or matching_variants:
                    if matching_variants and input.color:
                        # If specific color variants match, only include those
                        filtered_product = product.copy()
                        filtered_product["variants"] = matching_variants
                        filtered_products.append(filtered_product)
                    else:
                        filtered_products.append(product)
            
            # Use the filtered products if any match, otherwise keep the original
            if filtered_products:
                products = filtered_products
        
        # Format detailed response
        response = ""
        
        # Check if any matching products were found
        if products:
            first_product = products[0]
            title = first_product.get("title", "")
            variants = first_product.get("variants", [])
            
            # Count available inventory
            total_inventory = sum([v.get("inventory_quantity", 0) for v in variants])
            
            if total_inventory > 0:
                response = f"Yes! We have {title} in stock. "
                
                # If we have multiple variants with inventory
                in_stock_variants = [v for v in variants if v.get("inventory_quantity", 0) > 0]
                if len(in_stock_variants) > 1:
                    # List available options
                    option_details = "\n\nAvailable options:\n"
                    for variant in in_stock_variants:
                        variant_title = variant.get("title", "Default")
                        inventory = variant.get("inventory_quantity", 0)
                        option_details += f"• {variant_title}: {inventory} available\n"
                    
                    response += f"There are {total_inventory} total items available across different options.{option_details}\n"
                else:
                    # Single variant or variant not specified
                    response += f"There are {total_inventory} available.\n\n"
                
                # Price information
                price = in_stock_variants[0].get("price", "0.00") if in_stock_variants else "0.00"
                response += f"Price: ${price}\n\n"
                
                # Add product description
                description = first_product.get("body_html", "").replace("<p>", "").replace("</p>", "")
                if description:
                    if len(description) > 150:
                        description = description[:147] + "..."
                    response += f"Description: {description}\n\n"
                
                # Add similar products if we have multiple matches
                if len(products) > 1:
                    response += "We also have these similar items:\n"
                    for i, product in enumerate(products[1:4], 1):  # List up to 3 more products
                        p_title = product.get("title", "")
                        p_inventory = sum([v.get("inventory_quantity", 0) for v in product.get("variants", [])])
                        response += f"{i}. {p_title} ({p_inventory} available)\n"
                        
            else:
                # Product exists but out of stock
                response = f"I'm sorry, {title} is currently out of stock."
                
                # Check if other similar products are available
                in_stock_alternatives = [p for p in products[1:] if sum([
                    v.get("inventory_quantity", 0) for v in p.get("variants", [])
                ]) > 0]
                
                if in_stock_alternatives:
                    response += " However, we do have these similar items in stock:\n\n"
                    for i, product in enumerate(in_stock_alternatives[:3], 1):  # Show up to 3 alternatives
                        p_title = product.get("title", "")
                        p_inventory = sum([v.get("inventory_quantity", 0) for v in product.get("variants", [])])
                        response += f"{i}. {p_title} ({p_inventory} available)\n"
                else:
                    # Try to get recommendations for similar products
                    recommendations = await store.client.get_product_recommendations(
                        str(first_product.get("id")), 
                        limit=3
                    )
                    
                    in_stock_recs = [r for r in recommendations if sum([
                        v.get("inventory_quantity", 0) for v in r.get("variants", [])
                    ]) > 0]
                    
                    if in_stock_recs:
                        response += " You might be interested in these related items that are in stock:\n\n"
                        for i, rec in enumerate(in_stock_recs, 1):
                            r_title = rec.get("title", "")
                            r_inventory = sum([v.get("inventory_quantity", 0) for v in rec.get("variants", [])])
                            response += f"{i}. {r_title} ({r_inventory} available)\n"
        else:
            response = f"I couldn't find any products matching '{input.product_name}' that are currently in stock."
            
            # Get some general recommendations of what's available
            general_products = await store.client.search_products_with_inventory(
                in_stock_only=True,
                limit=3
            )
            
            if general_products:
                response += " Here are some of our currently available products:\n\n"
                for i, product in enumerate(general_products, 1):
                    g_title = product.get("title", "")
                    g_inventory = sum([v.get("inventory_quantity", 0) for v in product.get("variants", [])])
                    response += f"{i}. {g_title} ({g_inventory} available)\n"
        
        return response.strip()
    
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
            ]
        )
