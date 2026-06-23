"""Azure credential helpers — workload identity on AKS, DefaultAzureCredential locally."""

from __future__ import annotations

import os


def get_azure_credential():
    """Return sync Azure credential. Uses WorkloadIdentityCredential on AKS (token file present),
    DefaultAzureCredential elsewhere (Azure CLI, VS Code, etc.)."""
    if os.getenv("AZURE_FEDERATED_TOKEN_FILE"):
        from azure.identity import WorkloadIdentityCredential
        return WorkloadIdentityCredential()
    from azure.identity import DefaultAzureCredential
    return DefaultAzureCredential(exclude_interactive_browser_credential=True)


def get_azure_credential_async():
    """Return async Azure credential. Uses WorkloadIdentityCredential on AKS (token file present),
    DefaultAzureCredential elsewhere (Azure CLI, VS Code, etc.)."""
    if os.getenv("AZURE_FEDERATED_TOKEN_FILE"):
        from azure.identity.aio import WorkloadIdentityCredential
        return WorkloadIdentityCredential()
    from azure.identity.aio import DefaultAzureCredential
    return DefaultAzureCredential(exclude_interactive_browser_credential=True)

