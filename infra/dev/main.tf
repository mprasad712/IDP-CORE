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
# AZURE MANAGED REDIS
# =========================================
resource "azurerm_managed_redis" "redis" {
  name                = "idpflow-redis-dev"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  sku_name            = "Balanced_B1"

  default_database {
    clustering_policy = "OSSCluster"
    eviction_policy   = "NoEviction"
  }

  tags = {
    environment = "dev"
  }
}

# =========================================
# AKS CLUSTER
# =========================================
resource "azurerm_kubernetes_cluster" "aks" {
  name                = "idpflow-aks"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  dns_prefix          = "idpflow"

  default_node_pool {
    name       = "system"
    node_count = 2
    vm_size    = "Standard_B2s_v2"
  }

  identity {
    type = "SystemAssigned"
  }

  oidc_issuer_enabled       = true
  workload_identity_enabled = true

  tags = {
    environment = "dev"
  }
}

# =========================================
# REDIS ENTRA ACCESS POLICY
# =========================================
# Grant the backend workload identity (UAMI) Data Owner access on Redis.
# The object_id must match the Object ID of the UAMI whose client-id is
# annotated on the backend-sa service account (d82aece2-f81b-42fc-a53e-15d6cba3b613).
resource "azurerm_managed_redis_access_policy_assignment" "redis_backend" {
  managed_redis_id = azurerm_managed_redis.redis.id
  object_id        = "3d2780f7-b7f9-40c7-bf11-e53c940064bc"
}

# Allow AKS to pull images from ACR
resource "azurerm_role_assignment" "aks_acr_pull" {
  principal_id                     = azurerm_kubernetes_cluster.aks.kubelet_identity[0].object_id
  role_definition_name             = "AcrPull"
  scope                            = azurerm_container_registry.acr.id
  skip_service_principal_aad_check = true
}
