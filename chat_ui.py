import json
import time
from typing import List
import gradio as gr
from gradio import ChatMessage
from opentelemetry import trace

from azure.ai.projects.models import (
    AgentEventHandler,
    RunStep,
    RunStepDeltaChunk,
    ThreadMessage,
    ThreadRun,
    MessageDeltaChunk,
)


class EventHandler(AgentEventHandler):
    def __init__(self, tracer=None):
        super().__init__()
        self._current_message_id = None
        self._accumulated_text = ""
        self._current_tools = {}
        self.conversation = None
        self.create_tool_bubble_fn = None
        self.tracer = tracer

    def on_message_delta(self, delta: MessageDeltaChunk) -> None:
        if delta.id != self._current_message_id:
            # Start a new message
            if self._current_message_id is not None:
                print()
            self._current_message_id = delta.id
            self._accumulated_text = ""
            print("\nassistant> ", end="")

        partial_text = ""
        if delta.delta.content:
            for chunk in delta.delta.content:
                partial_text += chunk.text.get("value", "")
        self._accumulated_text += partial_text
        print(partial_text, end="", flush=True)

    def on_thread_message(self, message: ThreadMessage) -> None:
        if message.status == "completed" and message.role == "assistant":
            if self.tracer:
                span = trace.get_current_span()
                span.set_attribute("message_id", message.id)
                span.set_attribute("message_status", message.status)
                span.set_attribute("message_role", message.role)
            
            print()
            self._current_message_id = None
            self._accumulated_text = ""

    def on_thread_run(self, run: ThreadRun) -> None:
        print(f"thread_run status > {run.status}")
        
        if self.tracer:
            span = trace.get_current_span()
            span.set_attribute("run_id", run.id)
            span.set_attribute("run_status", run.status)
        
        if run.status == "failed":
            print(f"error > {run.last_error}")
            if self.tracer:
                span.set_attribute("error", str(run.last_error))

    def on_run_step(self, step: RunStep) -> None:
        print(f"step> {step.type} status={step.status}")
        
        if self.tracer:
            span = trace.get_current_span()
            span.set_attribute("step_id", step.id)
            span.set_attribute("step_type", step.type)
            span.set_attribute("step_status", step.status)
        
        # If we got a successful completion from a tool, we can do custom logging or UI updates here
        if step.status == "completed" and step.step_details and step.step_details.tool_calls:
            for tcall in step.step_details.tool_calls:
                if getattr(tcall, "function", None):
                    fn_name = tcall.function.name
                    try:
                        output = json.loads(tcall.function.output)
                        
                        # For Salesforce functions
                        if fn_name == "fetch_accounts":
                            if "error" in output:
                                message = f"Error fetching accounts: {output['error']}"
                            else:
                                account_count = output.get("totalSize", 0)
                                message = f"Found {account_count} account(s)."
                            
                            if self.create_tool_bubble_fn:
                                self.create_tool_bubble_fn(fn_name, message, tcall.id)
                        
                        elif fn_name == "fetch_contacts":
                            if "error" in output:
                                message = f"Error fetching contacts: {output['error']}"
                            else:
                                contact_count = output.get("totalSize", 0)
                                message = f"Found {contact_count} contact(s)."
                            
                            if self.create_tool_bubble_fn:
                                self.create_tool_bubble_fn(fn_name, message, tcall.id)

                    except json.JSONDecodeError:
                        print(f"Error parsing tool output: {tcall.function.output}")

    def on_run_step_delta(self, delta: RunStepDeltaChunk) -> None:
        if delta.delta.step_details and delta.delta.step_details.tool_calls:
            for tcall in delta.delta.step_details.tool_calls:
                if getattr(tcall, "function", None):
                    print(f"partial function call> {tcall.function}")


def convert_dict_to_chatmessage(msg: dict) -> ChatMessage:
    return ChatMessage(role=msg["role"], content=msg["content"], metadata=msg.get("metadata"))


def create_chat_interface(project_client, agent, thread, tracer=None):
    last_message = None
    last_message_timestamp = 0
    
    def azure_sf_chat(user_message: str, history: List[dict]):
        nonlocal last_message, last_message_timestamp
        
        # Start a span for the entire chat interaction
        chat_span = None
        if tracer:
            chat_span = tracer.start_span("chat_interaction")
            chat_span.set_attribute("user_message", user_message)
            chat_span.set_attribute("thread_id", thread.id)
            chat_span.set_attribute("agent_id", agent.id)
        
        try:
            if last_message == user_message and time.time() - last_message_timestamp < 5:
                # To prevent double sending if user quickly hits Enter
                if chat_span:
                    chat_span.set_attribute("duplicate_message", True)
                    chat_span.end()
                return history, ""
                
            last_message_timestamp = time.time()

            conversation = [convert_dict_to_chatmessage(m) for m in history]
            conversation.append(ChatMessage(role="user", content=user_message))
            yield conversation, ""

            # Send user message to the thread
            with tracer.start_as_current_span("create_message") if tracer else nullcontext() as span:
                if span:
                    span.set_attribute("message_role", "user")
                    span.set_attribute("message_content_length", len(user_message))
                
                project_client.agents.create_message(thread_id=thread.id, role="user", content=user_message)

            # Define how to display tool calls
            tool_titles = {
                "fetch_accounts": "🏢 Fetching Salesforce Accounts",
                "fetch_contacts": "👤 Fetching Salesforce Contacts",
                "bing_grounding": "🌐 Searching Web Sources"
            }

            def create_tool_bubble(tool_name: str, content: str = "", call_id: str = None):
                if tool_name is None:
                    return
                
                title = tool_titles.get(tool_name, f"🛠️ {tool_name}")
                
                msg = ChatMessage(
                    role="assistant",
                    content=content,
                    metadata={
                        "title": title,
                        "id": f"tool-{call_id}" if call_id else "tool-noid"
                    }
                )
                conversation.append(msg)
                return msg

            # Prepare event handler
            event_handler = EventHandler(tracer)
            event_handler.conversation = conversation
            event_handler.create_tool_bubble_fn = create_tool_bubble

            # Create streaming session for the agent's response
            with project_client.agents.create_stream(
                thread_id=thread.id,
                assistant_id=agent.id,
                event_handler=event_handler
            ) as stream:
                for item in stream:
                    event_type, event_data, *_ = item
                    
                    if event_type == "thread.run.step.delta":
                        # We can detect partial tool call usage here
                        step_delta = event_data.get("delta", {}).get("step_details", {})
                        if step_delta.get("type") == "tool_calls":
                            for tcall in step_delta.get("tool_calls", []):
                                call_id = tcall.get("id")
                                if tcall.get("type") == "bing_grounding":
                                    search_query = tcall.get("bing_grounding", {}).get("requesturl", "").split("?q=")[-1]
                                    if search_query:
                                        create_tool_bubble("bing_grounding", f"Searching for '{search_query}'...", call_id)
                            yield conversation, ""

                    elif event_type == "run_step":
                        # Completed tool usage
                        if event_data["type"] == "tool_calls" and event_data["status"] == "completed":
                            for msg in conversation:
                                if msg.metadata and msg.metadata.get("status") == "pending":
                                    msg.metadata["status"] = "done"
                            yield conversation, ""

                    elif event_type == "thread.message.delta":
                        # This is partial text from the assistant
                        content = ""
                        citations = []
                        for chunk in event_data["delta"]["content"]:
                            chunk_value = chunk["text"].get("value", "")
                            content += chunk_value
                            # If the chunk includes citations
                            if "annotations" in chunk["text"]:
                                for annotation in chunk["text"]["annotations"]:
                                    if annotation.get("type") == "url_citation":
                                        url_citation = annotation.get("url_citation", {})
                                        citation_text = f"{annotation.get('text', '')} [{url_citation.get('title', '')}]({url_citation.get('url', '')})"
                                        citations.append(citation_text)
                        citations_str = "\n" + "\n".join(citations) if citations else ""
                        
                        # If we don't have an "assistant" message or last message has metadata, create a new one
                        if not conversation or conversation[-1].role != "assistant" or conversation[-1].metadata:
                            conversation.append(ChatMessage(role="assistant", content=content + citations_str))
                        else:
                            # Append to the existing last assistant message
                            conversation[-1].content += content + citations_str
                        yield conversation, ""

            if chat_span:
                chat_span.set_attribute("conversation_length", len(conversation))
                chat_span.end()
                
            return conversation, ""
            
        except Exception as ex:
            if chat_span:
                chat_span.record_exception(ex)
                chat_span.set_attribute("error", str(ex))
                chat_span.end()
            raise

    return azure_sf_chat


# Helper context manager for when no tracer is provided
class nullcontext:
    def __init__(self, enter_result=None):
        self.enter_result = enter_result

    def __enter__(self):
        return self.enter_result

    def __exit__(self, *excinfo):
        pass