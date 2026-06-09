output "acr_login_server" {
  value = azurerm_container_registry.acr.login_server
}

output "storage_account_name" {
  value = azurerm_storage_account.storage.name
}

output "keyvault_name" {
  value = azurerm_key_vault.kv.name
}

output "postgres_fqdn" {
  value = azurerm_postgresql_flexible_server.postgres.fqdn
}

output "redis_connection_info" {
  value = {
    host = azurerm_redis_enterprise_cluster.redis.hostname
    port = 10000
    db   = azurerm_redis_enterprise_database.db.name
  }
}

output "aks_cluster_name" {
  value = azurerm_kubernetes_cluster.aks.name
}

output "aks_kube_config" {
  value     = azurerm_kubernetes_cluster.aks.kube_config_raw
  sensitive = true
}

