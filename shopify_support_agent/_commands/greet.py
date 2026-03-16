import fastworkflow
from pydantic import BaseModel, Field
from fastworkflow.train.generate_synthetic import generate_diverse_utterances

class Signature:
    """Greet the user and introduce the store.
    
    STRICT RULE: ONLY return the greeting message. 
    DO NOT list available commands. DO NOT explain what you can do.
    Simply say hello and ask how you can help."""
    
    class Input(BaseModel):
        pass
    
    plain_utterances = [
        "hi",
        "hello",
        "hey there",
        "good morning",
        "is anyone there",
        "help",
        "what can you do",
        "start"
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
    """Generate a friendly, non-technical introduction."""
    
    def _process_command(
        self,
        workflow: fastworkflow.Workflow,
        input: Signature.Input
    ) -> str:
        return (
            "Hello! I am your Shopify store assistant. I'm here to help you with your shopping needs.\n\n"
            "You can ask me to:\n"
            "- Check your order status\n"
            "- Search for products and check availability\n"
            "- Track your shipments\n"
            "- Process a refund\n\n"
            "How can I help you today?"
        )
    
    def __call__(
        self,
        workflow: fastworkflow.Workflow,
        command: str,
        command_parameters: Signature.Input,
    ) -> fastworkflow.CommandOutput:
        # Use the internal _process_command logic
        response_text = self._process_command(workflow, command_parameters)
        
        # Ensure the response is wrapped correctly for the runtime to trace
        return fastworkflow.CommandOutput(
            workflow_id=workflow.id,
            command_responses=[
                fastworkflow.CommandResponse(
                    response=response_text,
                    # Adding a status can help the tracer identify successful completion
                    status="success" 
                )
            ]
        )