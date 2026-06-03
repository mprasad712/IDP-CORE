terraform {
  backend "azurerm" {
    resource_group_name  = "agentflow-service-rg"
    storage_account_name = "afstatestoragecontainer"
    container_name       = "tfstate-storage-container"
    key                  = "dev.tfstate"
  }
}