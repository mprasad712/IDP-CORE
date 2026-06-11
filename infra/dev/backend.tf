terraform {
  backend "azurerm" {
    resource_group_name  = "idpflow-service-rg"
    storage_account_name = "ipstatestoragecontainer"
    container_name       = "tfstate-storage-container"
    key                  = "dev.tfstate"
  }
}