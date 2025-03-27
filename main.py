# main.py

import os
from dotenv import load_dotenv

load_dotenv(override=True)

# Azure identity and AI Project
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    BingGroundingTool,
    FunctionTool,
    ToolSet
)

# Our custom Salesforce functions
from sf_functions import fetch_accounts, fetch_contacts

# Import the Gradio chat interface creator
import gradio as gr
from chat_ui import create_chat_interface

# Import tracing setup
from tracing import setup_tracing

# --------------------------------------------------
# 1) Initialize the Azure AI Project Client
# --------------------------------------------------
credential = DefaultAzureCredential()
project_client = AIProjectClient.from_connection_string(
    credential=credential,
    conn_str=os.environ["PROJECT_CONNECTION_STRING"]  # Set in your .env
)

# --------------------------------------------------
# 1.1) Setup OpenTelemetry Tracing
# --------------------------------------------------
tracer = setup_tracing(project_client)

# --------------------------------------------------
# 2) Setup the Bing Grounding Tool if desired
# --------------------------------------------------
bing_tool = None
bing_connection_name = os.environ.get("BING_CONNECTION_NAME")
if bing_connection_name:
    try:
        with tracer.start_as_current_span("setup_bing_tool") as span:
            span.set_attribute("bing_connection_name", bing_connection_name)
            bing_connection = project_client.connections.get(connection_name=bing_connection_name)
            conn_id = bing_connection.id
            bing_tool = BingGroundingTool(connection_id=conn_id)
            print("bing > connected")
    except Exception as ex:
        print(f"bing > not connected: {ex}")

# --------------------------------------------------
# 3) Create/Update an Agent with Tools
# --------------------------------------------------
AGENT_NAME = "salesforce-assistant"

with tracer.start_as_current_span("setup_agent") as span:
    span.set_attribute("agent_name", AGENT_NAME)
    span.set_attribute("model", os.environ.get("MODEL_DEPLOYMENT_NAME", "gpt-4"))
    
    # Find existing agent
    found_agent = next(
        (a for a in project_client.agents.list_agents().data if a.name == AGENT_NAME),
        None
    )

    # Build toolset
    toolset = ToolSet()

    # Add Bing if connected
    if bing_tool:
        toolset.add(bing_tool)

    # Add our Salesforce functions
    toolset.add(FunctionTool({fetch_accounts, fetch_contacts}))

    # Define the new instructions for the agent
    instructions = """
    You are a helpful Salesforce assistant that can retrieve information from a Salesforce instance. Follow these rules:

    1. If the user asks general questions, use the Bing grounding tool.
    2. If the user wants to look up Salesforce accounts, call the `fetch_accounts` function.
       - They might specify a search filter for account name, and optionally a limit.
       - For example: "Show me the top 5 accounts with 'Tech' in their name"
    3. If the user wants to look up Salesforce contacts, call the `fetch_contacts` function.
       - They might specify an account ID, name filter, or limit.
       - For example: "Show me contacts for account 001xxxxxxxxxxx" or "Find contacts with 'Smith' in their name"
    4. Provide relevant answers to the user in a concise yet complete manner.
    5. Always ensure the user's request is properly addressed.
    6. Format the results in a readable way when displaying account or contact information.
    7. Never share Salesforce credentials or sensitive information.
    """

    if found_agent:
        # Update existing
        span.set_attribute("agent_action", "update")
        agent = project_client.agents.update_agent(
            assistant_id=found_agent.id,
            model=found_agent.model,
            instructions=instructions,
            toolset=toolset
        )
    else:
        # Create new
        span.set_attribute("agent_action", "create")
        agent = project_client.agents.create_agent(
            model=os.environ.get("MODEL_DEPLOYMENT_NAME", "gpt-4"),
            name=AGENT_NAME,
            instructions=instructions,
            toolset=toolset
        )

# --------------------------------------------------
# 4) Create a Thread for conversation
# --------------------------------------------------
with tracer.start_as_current_span("create_thread") as span:
    thread = project_client.agents.create_thread()
    span.set_attribute("thread_id", thread.id)

# --------------------------------------------------
# 5) Build a Gradio interface
# --------------------------------------------------
salesforce_chat = create_chat_interface(project_client, agent, thread, tracer)

with gr.Blocks(title="Azure AI - Salesforce Assistant") as demo:
    gr.Markdown("## Azure AI - Salesforce CRM Assistant")

    chatbot = gr.Chatbot(type="messages")
    input_box = gr.Textbox(label="Ask your Salesforce assistant...")

    def clear_history():
        with tracer.start_as_current_span("clear_chat_history") as span:
            global thread
            thread = project_client.agents.create_thread()
            span.set_attribute("new_thread_id", thread.id)
            return []

    # Buttons
    with gr.Row():
        clear_button = gr.Button("Clear Chat")

    # Example questions
    gr.Markdown("### Example Questions")
    with gr.Row():
        q1 = gr.Button("What is the address of Microsoft?")
        q2 = gr.Button("Find contacts with 'Furter' in their name")

    # Handle clearing chat
    clear_button.click(fn=clear_history, outputs=chatbot)

    # Helper function to set example question
    def set_example_question(question):
        with tracer.start_as_current_span("select_example_question") as span:
            span.set_attribute("example_question", question)
            return question

    # Wire example question buttons
    for btn in [q1, q2]:
        btn.click(fn=set_example_question, inputs=btn, outputs=input_box) \
           .then(salesforce_chat, inputs=[input_box, chatbot], outputs=[chatbot, input_box]) \
           .then(lambda: "", outputs=input_box)

    # Submit the user input
    input_box.submit(salesforce_chat, inputs=[input_box, chatbot], outputs=[chatbot, input_box]) \
             .then(lambda: "", outputs=input_box)

demo.queue().launch(debug=True)