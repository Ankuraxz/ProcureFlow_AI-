# ==============================================================================
# ProcureFlow AI — Cloud Run v2 Microservices, Artifact Registry & Secret Manager
# ==============================================================================

locals {
  required_services = toset([
    "artifactregistry.googleapis.com",
    "cloudtrace.googleapis.com",
    "iam.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
  ])

  registry_prefix = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.procureflow.repository_id}"
  agent_image     = "${local.registry_prefix}/procureflow-agent:${var.image_tag}"
  ui_image        = "${local.registry_prefix}/procureflow-ui:${var.image_tag}"

  common_labels = {
    app         = "procureflow-ai"
    environment = var.environment
    managed_by  = "terraform"
  }
}

resource "google_project_service" "required_apis" {
  for_each = local.required_services

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# ------------------------------------------------------------------------------
# Artifact Registry Docker Repository
# ------------------------------------------------------------------------------
resource "google_artifact_registry_repository" "procureflow" {
  project       = var.project_id
  location      = var.region
  repository_id = var.artifact_repo_id
  description   = "Container images for ProcureFlow AI ADK agents and Declarative A2UI client"
  format        = "DOCKER"
  labels        = local.common_labels

  depends_on = [google_project_service.required_apis]
}

# ------------------------------------------------------------------------------
# Secret Manager Secrets (Populated out-of-band via gcloud secrets versions add)
# ------------------------------------------------------------------------------
resource "google_secret_manager_secret" "google_api_key" {
  project   = var.project_id
  secret_id = "procureflow-google-api-key"
  labels    = local.common_labels

  replication {
    auto {}
  }

  depends_on = [google_project_service.required_apis]
}

resource "google_secret_manager_secret" "ap2_ed25519_seed" {
  project   = var.project_id
  secret_id = "procureflow-ap2-ed25519-seed"
  labels    = local.common_labels

  replication {
    auto {}
  }

  depends_on = [google_project_service.required_apis]
}

# ------------------------------------------------------------------------------
# 1. Internal A2A Service: Catalog & Sourcing SLM (Port 8001)
# ------------------------------------------------------------------------------
resource "google_cloud_run_v2_service" "a2a_catalog" {
  name                = "procureflow-a2a-catalog"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  deletion_protection = false
  labels              = local.common_labels

  template {
    service_account = google_service_account.a2a_catalog.email

    scaling {
      min_instance_count = var.min_instance_count
      max_instance_count = var.max_instance_count
    }

    containers {
      image = local.agent_image

      ports {
        container_port = 8001
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
      }

      env {
        name  = "SERVICE_ROLE"
        value = "catalog"
      }

      env {
        name  = "SLM_MODEL"
        value = var.slm_model
      }

      env {
        name  = "ORCHESTRATOR_MODEL"
        value = var.orchestrator_model
      }

      env {
        name  = "ENABLE_CLOUD_TRACE"
        value = "true"
      }

      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }

      env {
        name  = "DATA_DIR"
        value = "/tmp/data"
      }

      env {
        name = "GOOGLE_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.google_api_key.secret_id
            version = "latest"
          }
        }
      }

      startup_probe {
        http_get {
          path = "/.well-known/agent.json"
          port = 8001
        }
        initial_delay_seconds = 5
        period_seconds        = 5
        failure_threshold     = 6
      }
    }
  }

  depends_on = [
    google_secret_manager_secret_iam_member.google_api_key_accessor,
  ]
}

# ------------------------------------------------------------------------------
# 2. Internal A2A Service: Policy & FinOps Risk SLM (Port 8002)
# ------------------------------------------------------------------------------
resource "google_cloud_run_v2_service" "a2a_policy" {
  name                = "procureflow-a2a-policy"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  deletion_protection = false
  labels              = local.common_labels

  template {
    service_account = google_service_account.a2a_policy.email

    scaling {
      min_instance_count = var.min_instance_count
      max_instance_count = var.max_instance_count
    }

    containers {
      image = local.agent_image

      ports {
        container_port = 8002
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
      }

      env {
        name  = "SERVICE_ROLE"
        value = "policy"
      }

      env {
        name  = "SLM_MODEL"
        value = var.slm_model
      }

      env {
        name  = "ORCHESTRATOR_MODEL"
        value = var.orchestrator_model
      }

      env {
        name  = "ENABLE_CLOUD_TRACE"
        value = "true"
      }

      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }

      env {
        name  = "DATA_DIR"
        value = "/tmp/data"
      }

      env {
        name = "GOOGLE_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.google_api_key.secret_id
            version = "latest"
          }
        }
      }

      startup_probe {
        http_get {
          path = "/.well-known/agent.json"
          port = 8002
        }
        initial_delay_seconds = 5
        period_seconds        = 5
        failure_threshold     = 6
      }
    }
  }

  depends_on = [
    google_secret_manager_secret_iam_member.google_api_key_accessor,
  ]
}

# ------------------------------------------------------------------------------
# 3. Internal A2A Service: AP2 Mandate Settlement SLM (Port 8003)
# ------------------------------------------------------------------------------
resource "google_cloud_run_v2_service" "a2a_settlement" {
  name                = "procureflow-a2a-settlement"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  deletion_protection = false
  labels              = local.common_labels

  template {
    service_account = google_service_account.a2a_settlement.email

    scaling {
      min_instance_count = var.min_instance_count
      max_instance_count = var.max_instance_count
    }

    containers {
      image = local.agent_image

      ports {
        container_port = 8003
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
      }

      env {
        name  = "SERVICE_ROLE"
        value = "settlement"
      }

      env {
        name  = "ORCHESTRATOR_MODEL"
        value = var.orchestrator_model
      }

      env {
        name  = "SLM_MODEL"
        value = var.slm_model
      }

      env {
        name  = "ENABLE_CLOUD_TRACE"
        value = "true"
      }

      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }

      env {
        name  = "DATA_DIR"
        value = "/tmp/data"
      }

      env {
        name = "GOOGLE_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.google_api_key.secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "AP2_ED25519_SEED"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.ap2_ed25519_seed.secret_id
            version = "latest"
          }
        }
      }

      startup_probe {
        http_get {
          path = "/.well-known/agent.json"
          port = 8003
        }
        initial_delay_seconds = 5
        period_seconds        = 5
        failure_threshold     = 6
      }
    }
  }

  depends_on = [
    google_secret_manager_secret_iam_member.google_api_key_accessor,
    google_secret_manager_secret_iam_member.ap2_ed25519_seed_accessor,
  ]
}

# ------------------------------------------------------------------------------
# 4. Edge Gateway Service: Concierge Orchestrator + FastAPI SSE/A2UI (Port 8000)
# ------------------------------------------------------------------------------
resource "google_cloud_run_v2_service" "gateway" {
  name                = "procureflow-gateway"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false
  labels              = local.common_labels

  template {
    service_account = google_service_account.concierge_gateway.email
    timeout         = "300s"

    scaling {
      min_instance_count = var.min_instance_count
      max_instance_count = var.max_instance_count
    }

    containers {
      image = local.agent_image

      ports {
        container_port = 8000
      }

      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
      }

      env {
        name  = "SERVICE_ROLE"
        value = "gateway"
      }

      env {
        name  = "ORCHESTRATOR_MODEL"
        value = var.orchestrator_model
      }

      env {
        name  = "SLM_MODEL"
        value = var.slm_model
      }

      env {
        name  = "JUDGE_MODEL"
        value = var.judge_model
      }

      env {
        name  = "CATALOG_A2A_URL"
        value = google_cloud_run_v2_service.a2a_catalog.uri
      }

      env {
        name  = "POLICY_A2A_URL"
        value = google_cloud_run_v2_service.a2a_policy.uri
      }

      env {
        name  = "SETTLEMENT_A2A_URL"
        value = google_cloud_run_v2_service.a2a_settlement.uri
      }

      env {
        name  = "ENABLE_CLOUD_TRACE"
        value = "true"
      }

      env {
        name  = "CLOUD_RUN_IAM_AUTH"
        value = "true"
      }

      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }

      env {
        name  = "DATA_DIR"
        value = "/tmp/data"
      }

      env {
        name = "GOOGLE_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.google_api_key.secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "AP2_ED25519_SEED"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.ap2_ed25519_seed.secret_id
            version = "latest"
          }
        }
      }

      startup_probe {
        http_get {
          path = "/health"
          port = 8000
        }
        initial_delay_seconds = 5
        period_seconds        = 5
        failure_threshold     = 6
      }
    }
  }

  depends_on = [
    google_cloud_run_v2_service_iam_member.gateway_invokes_catalog,
    google_cloud_run_v2_service_iam_member.gateway_invokes_policy,
    google_cloud_run_v2_service_iam_member.gateway_invokes_settlement,
    google_secret_manager_secret_iam_member.google_api_key_accessor,
    google_secret_manager_secret_iam_member.ap2_ed25519_seed_accessor,
  ]
}

# ------------------------------------------------------------------------------
# 5. Edge Frontend Service: Declarative A2UI & AP2 Signer SPA (Port 8080)
# ------------------------------------------------------------------------------
resource "google_cloud_run_v2_service" "ui" {
  name                = "procureflow-ui"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false
  labels              = local.common_labels

  template {
    service_account = google_service_account.procureflow_ui.email
    timeout         = "300s"

    scaling {
      min_instance_count = var.min_instance_count
      max_instance_count = var.max_instance_count
    }

    containers {
      image = local.ui_image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }

      env {
        name  = "GATEWAY_URL"
        value = google_cloud_run_v2_service.gateway.uri
      }

      startup_probe {
        http_get {
          path = "/healthz"
          port = 8080
        }
        initial_delay_seconds = 3
        period_seconds        = 5
        failure_threshold     = 3
      }
    }
  }
}
