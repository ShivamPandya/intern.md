"""
Fetch JIRA ticket details via the JIRA REST API.

Supports both JIRA Cloud (Atlassian-hosted) and JIRA Server/Data Center.
Set "server_type" in config.json to "cloud" or "server".

Usage:
    python fetch_jira_ticket.py TICKET-123
    python fetch_jira_ticket.py TICKET-123 --config config.json
"""

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urljoin

import requests


def load_config(config_path: str) -> dict:
    path = Path(config_path)
    if not path.is_file():
        print(f"Error: config file not found at {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_credentials(config: dict) -> dict:
    """Return auth credentials based on server_type."""
    server_type = config.get("server_type", "cloud")
    auth_config = config["auth"]
    token_var = auth_config["api_token_env_var"]
    token = os.environ.get(token_var)

    if not token:
        print(
            f"Error: Set environment variable {token_var} with your JIRA API token/PAT.",
            file=sys.stderr,
        )
        sys.exit(1)

    if server_type == "cloud":
        email_var = auth_config.get("email_env_var", "JIRA_EMAIL")
        email = os.environ.get(email_var)
        if not email:
            print(
                f"Error: Set environment variable {email_var} with your Atlassian email "
                "(required for JIRA Cloud).",
                file=sys.stderr,
            )
            sys.exit(1)
        return {"type": "basic", "email": email, "token": token}

    return {"type": "bearer", "token": token}


def fetch_ticket(base_url: str, ticket_key: str, credentials: dict, config: dict) -> dict:
    server_type = config.get("server_type", "cloud")
    api_version = "3" if server_type == "cloud" else "2"
    verify_ssl = config.get("verify_ssl", server_type == "cloud")

    api_url = urljoin(base_url.rstrip("/") + "/", f"rest/api/{api_version}/issue/{ticket_key}")
    params = {
        "expand": "renderedFields",
        "fields": "summary,description,issuetype,priority,assignee,labels,"
        "components,customfield_10016,subtasks,comment,status,created,updated",
    }

    headers = {"Accept": "application/json"}
    auth = None
    if credentials["type"] == "basic":
        auth = (credentials["email"], credentials["token"])
    else:
        headers["Authorization"] = f"Bearer {credentials['token']}"

    if not verify_ssl:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    response = requests.get(
        api_url,
        params=params,
        auth=auth,
        headers=headers,
        timeout=30,
        verify=verify_ssl,
    )

    if response.status_code == 401:
        print("Error: Authentication failed. Check your JIRA credentials.", file=sys.stderr)
        sys.exit(1)
    if response.status_code == 404:
        print(f"Error: Ticket {ticket_key} not found.", file=sys.stderr)
        sys.exit(1)

    response.raise_for_status()
    return response.json()


def extract_text_from_adf(adf_node: dict | list | None) -> str:
    """Extract plain text from Atlassian Document Format (ADF) JSON."""
    if adf_node is None:
        return ""
    if isinstance(adf_node, str):
        return adf_node
    if isinstance(adf_node, list):
        return "".join(extract_text_from_adf(item) for item in adf_node)
    if isinstance(adf_node, dict):
        node_type = adf_node.get("type", "")
        text = adf_node.get("text", "")
        children = adf_node.get("content", [])
        child_text = "".join(extract_text_from_adf(c) for c in children)

        if node_type in ("paragraph", "heading", "blockquote", "rule"):
            return child_text + "\n"
        if node_type == "listItem":
            return "- " + child_text + "\n"
        if node_type == "hardBreak":
            return "\n"
        return text + child_text
    return ""


def parse_ticket(raw: dict) -> dict:
    fields = raw.get("fields", {})

    description_raw = fields.get("description")
    if isinstance(description_raw, str):
        description = description_raw.strip()
    else:
        description = extract_text_from_adf(description_raw).strip()

    assignee = fields.get("assignee")
    comments_raw = fields.get("comment", {}).get("comments", [])
    comments = []
    for c in comments_raw:
        body = extract_text_from_adf(c.get("body"))
        comments.append({
            "author": c.get("author", {}).get("displayName", "Unknown"),
            "created": c.get("created", ""),
            "body": body.strip(),
        })

    subtasks = []
    for st in fields.get("subtasks", []):
        subtasks.append({
            "key": st.get("key"),
            "summary": st.get("fields", {}).get("summary", ""),
            "status": st.get("fields", {}).get("status", {}).get("name", ""),
        })

    return {
        "key": raw.get("key"),
        "url": raw.get("self", "").split("/rest/")[0] + "/browse/" + raw.get("key", ""),
        "summary": fields.get("summary", ""),
        "description": description,
        "issue_type": fields.get("issuetype", {}).get("name", ""),
        "priority": fields.get("priority", {}).get("name", ""),
        "status": fields.get("status", {}).get("name", ""),
        "assignee": assignee.get("displayName", "") if assignee else "Unassigned",
        "labels": fields.get("labels", []),
        "components": [c.get("name", "") for c in fields.get("components", [])],
        "story_points": fields.get("customfield_10016"),
        "created": fields.get("created", ""),
        "updated": fields.get("updated", ""),
        "subtasks": subtasks,
        "comments": comments,
    }


def main():
    default_config = str(Path(__file__).resolve().parent / "config.json")

    parser = argparse.ArgumentParser(description="Fetch JIRA ticket details")
    parser.add_argument("ticket", help="JIRA ticket key (e.g. PROJ-123)")
    parser.add_argument("--config", default=default_config, help="Path to config.json")
    parser.add_argument("--raw", action="store_true", help="Output raw API response instead of parsed")
    args = parser.parse_args()

    config = load_config(args.config)
    credentials = get_credentials(config)

    raw_ticket = fetch_ticket(config["jira_base_url"], args.ticket, credentials, config)

    if args.raw:
        output = raw_ticket
    else:
        output = parse_ticket(raw_ticket)

    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
