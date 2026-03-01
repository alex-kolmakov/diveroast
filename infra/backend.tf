resource "google_cloud_run_v2_service" "backend" {
  name     = "diveroast-backend"
  location = var.region

  template {
    service_account = google_service_account.backend.email

    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }

    timeout = "300s"

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/diveroast/backend:${var.backend_image_tag}"

      ports {
        container_port = 8000
      }

      env {
        name  = "GEMINI_MODEL"
        value = var.gemini_model
      }

      env {
        name  = "PROMPT_VERSION"
        value = tostring(var.prompt_version)
      }

      env {
        name  = "LANCEDB_URI"
        value = ".lancedb"
      }

      env {
        name  = "DESTINATION__LANCEDB__EMBEDDING_MODEL_PROVIDER"
        value = "sentence-transformers"
      }

      env {
        name  = "DESTINATION__LANCEDB__EMBEDDING_MODEL"
        value = "all-MiniLM-L6-v2"
      }

      env {
        name  = "DESTINATION__LANCEDB__CREDENTIALS__URI"
        value = ".lancedb"
      }

      env {
        name  = "RAG_TOP_K"
        value = "10"
      }

      env {
        name  = "ENABLE_RERANKING"
        value = "true"
      }

      env {
        name  = "CROSS_ENCODER_MODEL"
        value = "cross-encoder/ms-marco-MiniLM-L-6-v2"
      }

      env {
        # Informational — index is pre-baked at build time, but documents
        # what chunk size the running LanceDB index was built with.
        name  = "CHUNK_SIZE"
        value = "900"
      }

      env {
        name  = "CHUNK_OVERLAP"
        value = "50"
      }

      env {
        name  = "PHOENIX_COLLECTOR_ENDPOINT"
        value = "${google_cloud_run_v2_service.phoenix.uri}/v1/traces"
      }

      env {
        name  = "PHOENIX_PROJECT_NAME"
        value = "diveroast"
      }

      env {
        name  = "ALLOWED_ORIGINS"
        value = google_cloud_run_v2_service.frontend.uri
      }

      env {
        name = "GEMINI_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.gemini_api_key.secret_id
            version = "latest"
          }
        }
      }

      resources {
        limits = {
          cpu    = "2"
          memory = "4Gi"
        }
      }

      startup_probe {
        http_get {
          path = "/health"
          port = 8000
        }
        initial_delay_seconds = 10
        period_seconds        = 5
        failure_threshold     = 12
      }

      liveness_probe {
        http_get {
          path = "/health"
          port = 8000
        }
        period_seconds = 30
      }
    }
  }

  depends_on = [
    google_project_service.apis["run.googleapis.com"],
    google_artifact_registry_repository.diveroast,
    google_secret_manager_secret_version.gemini_api_key,
  ]
}

# Allow public access (frontend calls API from browser)
resource "google_cloud_run_v2_service_iam_member" "backend_public" {
  name     = google_cloud_run_v2_service.backend.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}
