import os
import json
import time
from datetime import datetime
from typing import Dict, List
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import FunctionTool, ToolSet

# Import our Salesforce functions
from sf_functions import fetch_accounts, fetch_contacts

def load_test_queries() -> List[Dict]:
    """Load test queries from JSONL file"""
    queries = []
    with open('test_queries.jsonl', 'r') as f:
        for line in f:
            if line.strip():  # Skip empty lines
                queries.append(json.loads(line))
    return queries

def save_test_results(results: List[Dict]):
    """Save test results to a timestamped JSONL file"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = "test_results"
    os.makedirs(output_dir, exist_ok=True)
    
    output_file = os.path.join(output_dir, f'test_results_{timestamp}.jsonl')
    
    # Write each result as a separate JSON line
    with open(output_file, 'w') as f:
        f.write(json.dumps({"timestamp": datetime.now().isoformat()}) + '\n')
        for result in results:
            f.write(json.dumps(result) + '\n')
    
    print(f"Results saved to {output_file}")

def get_or_create_agent(project_client: AIProjectClient) -> str:
    """Gets existing agent or creates a new one"""
    AGENT_NAME = "salesforce-assistant"
    
    # Find existing agent
    found_agent = next(
        (a for a in project_client.agents.list_agents().data if a.name == AGENT_NAME),
        None
    )

    # Build toolset - this is critical to prevent "Toolset is not available" error
    toolset = ToolSet()
    toolset.add(FunctionTool({fetch_accounts, fetch_contacts}))

    # Define the agent instructions
    instructions = """
    You are a helpful Salesforce assistant that can retrieve information from a Salesforce instance. Follow these rules:
    1. If the user wants to look up Salesforce accounts, call the `fetch_accounts` function.
    2. If the user wants to look up Salesforce contacts, call the `fetch_contacts` function.
    3. Provide relevant answers to the user in a concise yet complete manner.
    4. Format the results in a readable way when displaying account or contact information.
    """

    if found_agent:
        # Update existing agent with toolset
        agent = project_client.agents.update_agent(
            assistant_id=found_agent.id,
            model=found_agent.model,
            instructions=instructions,
            toolset=toolset
        )
        print(f"Updated existing agent: {agent.id}")
    else:
        # Create new agent with toolset
        agent = project_client.agents.create_agent(
            model=os.environ.get("MODEL_DEPLOYMENT_NAME", "gpt-4"),
            name=AGENT_NAME,
            instructions=instructions,
            toolset=toolset
        )
        print(f"Created new agent: {agent.id}")
    
    return agent.id

def run_automated_tests(project_client: AIProjectClient):
    """Execute automated tests using the Salesforce agent"""
    results = []
    queries = load_test_queries()
    
    # Get or create agent
    agent_id = get_or_create_agent(project_client)
    
    for query in queries:
        time.sleep(10)
        print(f"\nProcessing Query {query['id']}: {query['question']}")
        result = {
            "query_id": query["id"],
            "question": query["question"],
            "ground_truth": query["ground_truth"],
            "timestamp": datetime.now().isoformat()
        }
        
        # Create a new thread for each query to avoid contention
        thread = project_client.agents.create_thread()
        print(f"Created thread for query {query['id']}: {thread.id}")
        
        try:
            # Send the question
            project_client.agents.create_message(
                thread_id=thread.id,
                role="user",
                content=query["question"]
            )
            
            # Process the run
            run = project_client.agents.create_and_process_run(
                thread_id=thread.id,
                assistant_id=agent_id
            )
            
            # Check for failure
            if run.status == "failed":
                result["status"] = "failed"
                result["error"] = str(run.last_error)
                print(f"Run failed: {run.last_error}")
                results.append(result)
                continue
            
            # Add a slight delay to ensure run completion
            time.sleep(1)
            
            # Get run steps to find tool outputs
            run_steps = project_client.agents.list_run_steps(
                run_id=run.id,
                thread_id=thread.id
            )
            
            # Collect tool outputs (Salesforce context) - store as plain strings
            context_entries = []
            for step in run_steps.data:
                if step.type == "tool_calls" and step.step_details and step.step_details.tool_calls:
                    for tool_call in step.step_details.tool_calls:
                        if getattr(tool_call, "function", None):
                            try:
                                # Store raw output as is - no processing or JSON parsing
                                raw_output = tool_call.function.output
                                
                                # Remove any surrounding quotes if present
                                if raw_output and raw_output.startswith('"') and raw_output.endswith('"'):
                                    raw_output = raw_output[1:-1]
                                    # Unescape interior quotes if needed
                                    raw_output = raw_output.replace('\\"', '"')
                                
                                context_entries.append({
                                    "function": tool_call.function.name,
                                    "context": raw_output
                                })
                            except Exception as e:
                                print(f"Warning: Error processing tool output: {str(e)}")
            
            # Get the assistant's response
            messages = project_client.agents.list_messages(thread_id=thread.id)
            latest_message = next((msg for msg in messages.data if msg.role == "assistant"), None)
            
            result.update({
                "status": "completed",
                "context": context_entries,
                "response": latest_message.content[0].text.value if latest_message else None
            })
            
            print("Query completed successfully")
            
        except Exception as e:
            result.update({
                "status": "error",
                "error": str(e)
            })
            print(f"Error processing query: {str(e)}")
        
        results.append(result)
    
    return results

def main():
    load_dotenv(override=True)
    
    # Initialize Azure AI client
    credential = DefaultAzureCredential()
    project_client = AIProjectClient.from_connection_string(
        credential=credential,
        conn_str=os.environ["PROJECT_CONNECTION_STRING"]
    )
    
    # Run tests
    print("Starting automated tests...")
    results = run_automated_tests(project_client)
    
    # Save results
    save_test_results(results)
    print("Testing completed.")

if __name__ == "__main__":
    main()