variable "project_id" {
  description = "Google Cloud Project ID hosting ProcureFlow AI Cloud Run v2 resources."
  type        = string
}

variable "region" {
  description = "Primary Google Cloud region for Artifact Registry and Cloud Run v2 services."
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Deployment environment label (dev, staging, prod)."
  type        = string
  default     = "prod"
}

variable "artifact_repo_id" {
  description = "Artifact Registry Docker repository ID for ProcureFlow container images."
  type        = string
  default     = "procureflow-repo"
}

variable "image_tag" {
  description = "Immutable container image tag (commit SHA or release version) deployed to Cloud Run."
  type        = string
  default     = "latest"
}

variable "orchestrator_model" {
  description = "Gemini model tier for Concierge Orchestrator and AP2 Settlement agents."
  type        = string
  default     = "gemini-3.5-flash"
}

variable "slm_model" {
  description = "Cost-optimized Gemini SLM tier for Catalog and Policy Risk A2A sub-agents."
  type        = string
  default     = "gemini-3.5-flash-lite"
}

variable "judge_model" {
  description = "Gemini model tier used for LLM-as-a-Judge rubric evaluation."
  type        = string
  default     = "gemini-3.5-flash"
}

variable "min_instance_count" {
  description = "Minimum warm Cloud Run v2 instances per service (0 for scale-to-zero cost efficiency)."
  type        = number
  default     = 0
}

variable "max_instance_count" {
  description = "Maximum autoscaled Cloud Run v2 instances per service."
  type        = number
  default     = 10
}

variable "allow_public_gateway" {
  description = "Whether to grant allUsers roles/run.invoker on the public Gateway and UI (internal A2A services always remain private)."
  type        = bool
  default     = true
}
