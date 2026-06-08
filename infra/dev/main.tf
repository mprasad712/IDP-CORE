provider "azurerm" {
  features {}
}

# =========================================
# RESOURCE GROUP
# =========================================
resource "azurerm_resource_group" "rg" {
  name     = "idpflow-rg"
  location = "Central India"
}

# =========================================
# AZURE CONTAINER REGISTRY
# =========================================
resource "azurerm_container_registry" "acr" {
  name                = "idpflowacr1"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  sku                 = "Basic"
  admin_enabled       = true
}

# =========================================
# STORAGE ACCOUNT (APPLICATION STORAGE)
# =========================================
resource "azurerm_storage_account" "storage" {
  name                     = "idpflowappstore1"
  resource_group_name      = azurerm_resource_group.rg.name
  location                 = azurerm_resource_group.rg.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
}

# =========================================
# STORAGE CONTAINER
# =========================================
resource "azurerm_storage_container" "container" {
  name                  = "app-storage-container"
  storage_account_id    = azurerm_storage_account.storage.id
  container_access_type = "private"
}

# =========================================
# KEY VAULT
# =========================================
data "azurerm_client_config" "current" {}

resource "azurerm_key_vault" "kv" {
  name                       = "idpflow-kv-dev-001"
  location                   = azurerm_resource_group.rg.location
  resource_group_name        = azurerm_resource_group.rg.name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  purge_protection_enabled   = false
  soft_delete_retention_days = 7
}

# =========================================
# POSTGRESQL FLEXIBLE SERVER
# =========================================
resource "azurerm_postgresql_flexible_server" "postgres" {
  name                = "idpflow-postgres"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location

  administrator_login    = "pgadmin"
  administrator_password = "Pass@1234"

  sku_name   = "B_Standard_B1ms"
  version    = "15"
  storage_mb = 32768

  zone = "1"
}

# =========================================
# AZURE MANAGED REDIS (OSS CLUSTER)
# =========================================
resource "azurerm_redis_cache" "redis" {
  name                = "idpflow-redis"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name

  capacity = 1
  family   = "C"
  sku_name = "Basic"

  minimum_tls_version = "1.2"

  redis_configuration {
  }
}
