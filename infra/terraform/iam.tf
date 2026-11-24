# ==============================================================================
# ProcureFlow AI — Dedicated Least-Privilege Service Accounts & IAM Mesh
# ==============================================================================

resource "google_service_account" "concierge_gateway" {
  account_id   = "sa-concierge-gateway"
  display_name = "ProcureFlow Concierge Gateway & Orchestrator SA"
  description  = "Invokes internal A2A Cloud Run services, exports Cloud Trace spans, and reads API/AP2 secrets."
  depends_on   = [google_project_service.required_apis]
}

resource "google_service_account" "a2a_catalog" {
  account_id   = "sa-a2a-catalog"
  display_name = "ProcureFlow Catalog & Sourcing A2A Agent SA"
  description  = "Isolated identity for Catalog SLM with Cloud Trace export and Gemini API key access only."
  depends_on   = [google_project_service.required_apis]
}

resource "google_service_account" "a2a_policy" {
  account_id   = "sa-a2a-policy"
  display_name = "ProcureFlow Policy & FinOps Risk A2A Agent SA"
  description  = "Isolated identity for Policy SLM with Cloud Trace export and Gemini API key access only."
  depends_on   = [google_project_service.required_apis]
}

resource "google_service_account" "a2a_settlement" {
  account_id   = "sa-a2a-settlement"
  display_name = "ProcureFlow AP2 Settlement A2A Agent SA"
  description  = "Isolated identity for Settlement SLM with Cloud Trace export, Gemini API key, and AP2 Ed25519 seed access."
  depends_on   = [google_project_service.required_apis]
}

resource "google_service_account" "procureflow_ui" {
  account_id   = "sa-procureflow-ui"
  display_name = "ProcureFlow Frontend UI SA"
  description  = "Zero-secret identity for the stateless Nginx SPA and reverse-proxy container."
  depends_on   = [google_project_service.required_apis]
}

# ------------------------------------------------------------------------------
# Cloud Trace Telemetry Permissions (All 4 Backend ADK Agents)
# ------------------------------------------------------------------------------
locals {
  backend_agent_service_accounts = {
    gateway    = google_service_account.concierge_gateway.email
    catalog    = google_service_account.a2a_catalog.email
    policy     = google_service_account.a2a_policy.email
    settlement = google_service_account.a2a_settlement.email
  }

  ap2_signer_service_accounts = {
    gateway    = google_service_account.concierge_gateway.email
    settlement = google_service_account.a2a_settlement.email
  }
}

resource "google_project_iam_member" "cloud_trace_agent" {
  for_each = local.backend_agent_service_accounts

  project = var.project_id
  role    = "roles/cloudtrace.agent"
  member  = "serviceAccount:${each.value}"
}

# ------------------------------------------------------------------------------
# Secret Manager Least-Privilege Access
# ------------------------------------------------------------------------------
resource "google_secret_manager_secret_iam_member" "google_api_key_accessor" {
  for_each = local.backend_agent_service_accounts

  project   = var.project_id
  secret_id = google_secret_manager_secret.google_api_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${each.value}"
}

# Only Gateway and AP2 Settlement Agent can read the cryptographic Ed25519 seed
resource "google_secret_manager_secret_iam_member" "ap2_ed25519_seed_accessor" {
  for_each = local.ap2_signer_service_accounts

  project   = var.project_id
  secret_id = google_secret_manager_secret.ap2_ed25519_seed.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${each.value}"
}

# ------------------------------------------------------------------------------
# Zero-Trust Internal A2A Mesh: ONLY sa-concierge-gateway can invoke A2A agents
# ------------------------------------------------------------------------------
resource "google_cloud_run_v2_service_iam_member" "gateway_invokes_catalog" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.a2a_catalog.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.concierge_gateway.email}"
}

resource "google_cloud_run_v2_service_iam_member" "gateway_invokes_policy" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.a2a_policy.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.concierge_gateway.email}"
}

resource "google_cloud_run_v2_service_iam_member" "gateway_invokes_settlement" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.a2a_settlement.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.concierge_gateway.email}"
}

# ------------------------------------------------------------------------------
# Edge Ingress IAM: Public Invoker ONLY on Gateway & UI (When Enabled)
# ------------------------------------------------------------------------------
resource "google_cloud_run_v2_service_iam_member" "public_gateway_invoker" {
  count = var.allow_public_gateway ? 1 : 0

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.gateway.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "public_ui_invoker" {
  count = var.allow_public_gateway ? 1 : 0

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.ui.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
