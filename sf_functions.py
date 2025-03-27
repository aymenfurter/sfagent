import os
import json
from typing import Any, Dict, Optional, List
from opentelemetry import trace
from simple_salesforce import Salesforce

# Get the tracer
tracer = trace.get_tracer(__name__)

def connect_to_salesforce():
    """
    Creates a connection to Salesforce using environment variables
    """
    try:
        return Salesforce(
            username=os.environ.get("SF_USERNAME"),
            password=os.environ.get("SF_PASSWORD"),
            security_token=os.environ.get("SF_SECURITY_TOKEN"),
            domain=os.environ.get("SF_DOMAIN", "login")
        )
    except Exception as e:
        print(f"Error connecting to Salesforce: {str(e)}")
        return None

def fetch_accounts(limit: int = 10, name_filter: Optional[str] = None) -> str:
    """
    Fetches accounts from Salesforce with optional name filtering.
    
    Args:
        limit: Maximum number of accounts to return
        name_filter: Optional filter for account name (uses LIKE in SOQL)
        
    Returns:
        JSON string with account records
    """
    with tracer.start_as_current_span("fetch_accounts") as span:
        span.set_attribute("limit", limit)
        if name_filter:
            span.set_attribute("name_filter", name_filter)
        
        try:
            sf = connect_to_salesforce()
            if not sf:
                error_msg = "Failed to connect to Salesforce"
                span.set_attribute("error", error_msg)
                return json.dumps({"error": error_msg})
            
            # Construct the query
            query = f"SELECT Id, Name, Industry, Type, BillingCity, BillingState, BillingCountry, Phone, Website FROM Account"
            if name_filter:
                query += f" WHERE Name LIKE '%{name_filter}%'"
            query += f" ORDER BY CreatedDate DESC LIMIT {limit}"
            
            span.set_attribute("soql_query", query)
            span.add_event("salesforce_query_start")
            
            # Execute the query
            result = sf.query(query)
            
            span.add_event("salesforce_query_end")
            span.set_attribute("record_count", len(result["records"]))
            
            # Clean up the records to make them more readable
            records = []
            for record in result["records"]:
                # Remove attributes and null values
                clean_record = {k: v for k, v in record.items() 
                               if k != "attributes" and v is not None}
                records.append(clean_record)
            
            return json.dumps({"accounts": records, "totalSize": result["totalSize"]})
        
        except Exception as e:
            span.record_exception(e)
            span.set_attribute("error", str(e))
            return json.dumps({"error": str(e)})

def fetch_contacts(account_id: Optional[str] = None, 
                   limit: int = 10, 
                   name_filter: Optional[str] = None) -> str:
    """
    Fetches contacts from Salesforce with optional account filtering.
    
    Args:
        account_id: Optional Salesforce Account ID to filter contacts
        limit: Maximum number of contacts to return
        name_filter: Optional filter for contact name (uses LIKE in SOQL)
        
    Returns:
        JSON string with contact records
    """
    with tracer.start_as_current_span("fetch_contacts") as span:
        span.set_attribute("limit", limit)
        if account_id:
            span.set_attribute("account_id", account_id)
        if name_filter:
            span.set_attribute("name_filter", name_filter)
        
        try:
            sf = connect_to_salesforce()
            if not sf:
                error_msg = "Failed to connect to Salesforce"
                span.set_attribute("error", error_msg)
                return json.dumps({"error": error_msg})
            
            # Construct the query
            query = "SELECT Id, FirstName, LastName, Email, Phone, Title, AccountId, Account.Name FROM Contact"
            
            where_clauses = []
            if account_id:
                where_clauses.append(f"AccountId = '{account_id}'")
            if name_filter:
                where_clauses.append(f"(FirstName LIKE '%{name_filter}%' OR LastName LIKE '%{name_filter}%')")
            
            if where_clauses:
                query += " WHERE " + " AND ".join(where_clauses)
            
            query += f" ORDER BY CreatedDate DESC LIMIT {limit}"
            
            span.set_attribute("soql_query", query)
            span.add_event("salesforce_query_start")
            
            # Execute the query
            result = sf.query(query)
            
            span.add_event("salesforce_query_end")
            span.set_attribute("record_count", len(result["records"]))
            
            # Clean up the records to make them more readable
            records = []
            for record in result["records"]:
                # Handle the Account.Name relationship field
                if "Account" in record and record["Account"]:
                    record["AccountName"] = record["Account"]["Name"]
                    del record["Account"]
                
                # Remove attributes and null values
                clean_record = {k: v for k, v in record.items() 
                               if k != "attributes" and v is not None}
                records.append(clean_record)
            
            return json.dumps({"contacts": records, "totalSize": result["totalSize"]})
        
        except Exception as e:
            span.record_exception(e)
            span.set_attribute("error", str(e))
            return json.dumps({"error": str(e)})