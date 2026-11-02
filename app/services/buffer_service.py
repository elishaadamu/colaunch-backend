"""
Buffer GraphQL API Client for Backend Services
"""
import os
import requests
from typing import Optional, Dict, Any, List

BUFFER_API_URL = "https://api.buffer.com"
DEFAULT_BUFFER_TOKEN = "bvPvXtI6gbWAaml79XPEK51TkZEIrjjPnKODFQahUK4"

def get_buffer_token() -> str:
    return os.environ.get("BUFFER_ACCESS_TOKEN", DEFAULT_BUFFER_TOKEN)

def execute_buffer_graphql(query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    token = get_buffer_token()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }
    payload = {"query": query}
    if variables:
        payload["variables"] = variables

    response = requests.post(BUFFER_API_URL, headers=headers, json=payload, timeout=15)
    response.raise_for_status()
    res_json = response.json()
    if "errors" in res_json and res_json["errors"]:
        error_msg = " | ".join([e.get("message", "Unknown error") for e in res_json["errors"]])
        raise RuntimeError(f"Buffer GraphQL error: {error_msg}")
    return res_json.get("data", {})

def get_buffer_account() -> Dict[str, Any]:
    query = """
    query GetAccount {
      account {
        id
        email
        organizations {
          id
          name
        }
      }
    }
    """
    data = execute_buffer_graphql(query)
    account = data.get("account", {})
    orgs = account.get("organizations", [])
    primary_org = orgs[0] if orgs else {}
    return {
        "accountId": account.get("id"),
        "email": account.get("email"),
        "organizationId": primary_org.get("id"),
        "organizationName": primary_org.get("name")
    }

def get_buffer_channels(organization_id: Optional[str] = None) -> List[Dict[str, Any]]:
    if not organization_id:
        acct = get_buffer_account()
        organization_id = acct.get("organizationId")
    if not organization_id:
        return []

    query = """
    query GetChannels($input: ChannelsInput!) {
      channels(input: $input) {
        id
        name
        displayName
        service
        avatar
        isQueuePaused
      }
    }
    """
    data = execute_buffer_graphql(query, {"input": {"organizationId": organization_id}})
    return data.get("channels", [])

def create_buffer_post(
    channel_id: str,
    text: str,
    mode: str = "addToQueue",
    scheduling_type: str = "automatic",
    due_at: Optional[str] = None,
    save_to_draft: bool = False,
    assets: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    mutation = """
    mutation CreatePost($input: CreatePostInput!) {
      createPost(input: $input) {
        ... on PostActionSuccess {
          post {
            id
            text
            dueAt
            channelId
            status
          }
        }
        ... on MutationError {
          message
        }
      }
    }
    """
    input_payload: Dict[str, Any] = {
        "channelId": channel_id,
        "text": text,
        "mode": mode,
        "schedulingType": scheduling_type,
        "needsApproval": False
    }
    if save_to_draft:
        input_payload["saveToDraft"] = True
    if mode == "customScheduled" and due_at:
        input_payload["dueAt"] = due_at
    if assets:
        input_payload["assets"] = [
            {"image": {"url": a["url"]}} if "url" in a else a for a in assets
        ]

    data = execute_buffer_graphql(mutation, {"input": input_payload})
    result = data.get("createPost", {})
    if "message" in result and not result.get("post"):
        raise RuntimeError(f"Buffer post failed: {result['message']}")
    return result.get("post", {})

