# Shopify Customer Support Agent

A FastWorkflow-powered agent that provides intelligent customer support for Shopify stores. This agent can handle customer inquiries about orders, products, shipping, and refunds.

## Features

- **Order Management**: Check order status and track shipments
- **Product Search**: Find products and check inventory
- **Customer Information**: Look up customer details and order history
- **Refund Processing**: Handle refund requests seamlessly
- **Natural Language Understanding**: Interpret user queries in everyday language
- **Conversation Persistence**: Maintain context across conversations

## Prerequisites

- Python 3.11 or higher
- A Shopify store with Admin API access
- [Mistral AI](https://mistral.ai/) API key

## Installation

1. Clone this repository:

```bash
git clone <repository-url>
cd shopify-support-agent
```

2. Set up a virtual environment (recommended):

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install FastWorkflow:

```bash
pip install fastworkflow
```

4. Add your Mistral API key to `fastworkflow.passwords.env`:

```
# Open the file
nano fastworkflow.passwords.env

# Replace 'your-mistral-key' with your actual API key
```

## Configuration

### Shopify API Credentials

To use this agent, you need to obtain API credentials from your Shopify store:

1. Log in to your Shopify admin
2. Go to Apps > Develop apps
3. Click "Create an app"
4. Set appropriate permissions (read_orders, read_products, read_customers, write_orders)
5. Generate an access token

When starting the agent, you'll need to provide:
- Your shop domain (e.g., `mystore.myshopify.com`)
- The access token
- A friendly name for your store

## Training the Workflow

Before first use, train the FastWorkflow with:

```bash
fastworkflow train ./shopify-support-agent ./shopify-support-agent/fastworkflow.env ./shopify-support-agent/fastworkflow.passwords.env
```

## Running the Agent

Start the agent with:

```bash
fastworkflow run ./shopify-support-agent ./shopify-support-agent/fastworkflow.env ./shopify-support-agent/fastworkflow.passwords.env
```

When prompted, initialize the Shopify connection:

```
User > connect to shopify store mystore.myshopify.com
```

You'll be asked to provide your access token and store name.

## Available Commands

Once connected, the agent can handle queries like:

- "Check order status for #1001"
- "Track shipment for order #1234"
- "Search for blue jeans"
- "Find customer information for john@example.com"
- "Process refund for order #5678 due to damaged item"

## FastAPI Integration

This agent can be exposed as a web service using FastWorkflow's FastAPI service:

```bash
uvicorn services.run_fastapi.main:app \
  --workflow_path ./shopify-support-agent \
  --env_file_path ./shopify-support-agent/fastworkflow.env \
  --passwords_file_path ./shopify-support-agent/fastworkflow.passwords.env \
  --host 0.0.0.0 \
  --port 8000
```

You can then interact with the agent through HTTP endpoints as documented in the FastWorkflow FastAPI specification.

## OAuth Integration

For production use, implement the `shopify_oauth_integration.py` module to handle the Shopify OAuth flow and user authentication.

## Customization

To add new commands or modify existing ones:

1. Add new Python files in `_commands/ShopifyStore/`
2. Follow the FastWorkflow command pattern with Signature and ResponseGenerator classes
3. Retrain the workflow after making changes

## License

[Insert License Information]

## Acknowledgments

- Built with [FastWorkflow](https://github.com/radiantlogicinc/fastworkflow)
- Uses the [Shopify Admin API](https://shopify.dev/docs/api/admin-rest)
- Powered by [Mistral AI](https://mistral.ai/)
