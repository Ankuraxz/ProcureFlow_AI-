output "artifact_registry_repository_url" {
  description = "Artifact Registry Docker repository prefix for pushing ProcureFlow images."
  value       = local.registry_prefix
}

output "gateway_url" {
  description = "Public HTTPS URL of the Concierge Orchestrator & FastAPI SSE Gateway."
  value       = google_cloud_run_v2_service.gateway.uri
}

output "ui_url" {
  description = "Public HTTPS URL of the ProcureFlow AI React + A2UI Web Workspace."
  value       = google_cloud_run_v2_service.ui.uri
}

output "catalog_a2a_internal_url" {
  description = "Internal-only Cloud Run URL for the Catalog & Sourcing A2A SLM."
  value       = google_cloud_run_v2_service.a2a_catalog.uri
}

output "policy_a2a_internal_url" {
  description = "Internal-only Cloud Run URL for the Policy & FinOps Risk A2A SLM."
  value       = google_cloud_run_v2_service.a2a_policy.uri
}

output "settlement_a2a_internal_url" {
  description = "Internal-only Cloud Run URL for the AP2 Mandate Settlement A2A SLM."
  value       = google_cloud_run_v2_service.a2a_settlement.uri
}

output "service_account_emails" {
  description = "Map of dedicated least-privilege Google Cloud Service Account emails."
  value = {
    gateway    = google_service_account.concierge_gateway.email
    catalog    = google_service_account.a2a_catalog.email
    policy     = google_service_account.a2a_policy.email
    settlement = google_service_account.a2a_settlement.email
    ui         = google_service_account.procureflow_ui.email
  }
}
