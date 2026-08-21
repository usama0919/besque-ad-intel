$project = "besque-martech"
$region = "europe-west2"
$secretName = "shared-access-password"

# Create the secret only if it doesn't already exist - `gcloud secrets describe`
# exits non-zero exactly when it's missing, which is the "doesn't exist" signal
# this checks for. The value itself never appears in this script, in git history,
# or on the command line - it's piped into `gcloud secrets create` over stdin.
gcloud secrets describe $secretName --project $project *> $null
if ($LASTEXITCODE -eq 0) {
    Write-Output "Secret '$secretName' already exists in $project - skipping creation."
} else {
    $sharedPassword = $env:SHARED_ACCESS_PASSWORD
    if (-not $sharedPassword) {
        # Not masked - this runs in the operator's own terminal, not a shared one.
        $sharedPassword = Read-Host -Prompt "Secret '$secretName' doesn't exist yet - enter its value"
    }
    if (-not $sharedPassword) {
        Write-Error "No value provided for '$secretName' (checked `$env:SHARED_ACCESS_PASSWORD and the prompt) - aborting deploy."
        exit 1
    }
    $sharedPassword | gcloud secrets create $secretName --project $project --data-file=-
}

# Grant the Cloud Run service's own runtime service account read access to the
# secret. besque-dashboard has no --service-account flag, so it runs as the
# project's default compute SA; ask the already-deployed revision for its real one
# (authoritative) and fall back to the constructed default-compute-SA name only if
# no revision exists yet to ask (a first-ever deploy of this service).
$serviceAccount = gcloud run services describe besque-dashboard --region $region --project $project --format="value(spec.template.spec.serviceAccountName)" 2>$null
if (-not $serviceAccount) {
    $projectNumber = gcloud projects describe $project --format="value(projectNumber)"
    $serviceAccount = "$projectNumber-compute@developer.gserviceaccount.com"
}
gcloud secrets add-iam-policy-binding $secretName --project $project --member="serviceAccount:$serviceAccount" --role="roles/secretmanager.secretAccessor"

# --allow-unauthenticated (2026-08-21): changed from --no-allow-unauthenticated -
# the team has no Google accounts on this project, so Cloud Run's own IAM layer was
# never a usable second door, only an obstacle between testers and the app-level
# shared-password gate (dashboard.py's own middleware) that IS meant to be the door.
# --set-secrets replaces the old plain --update-env-vars SHARED_ACCESS_PASSWORD=...
# (never used - this is the first time this var reaches deploy) with a Secret
# Manager reference, so the value is never visible in `gcloud run services describe`
# output or deploy logs the way a plain env var would be. --max-instances lowered
# 5 -> 3 (see the pool-size math recorded alongside this change) - --min-instances 1
# is unchanged, kept explicit here rather than left to a flag default.
gcloud run deploy besque-dashboard --source . --region $region --project $project --allow-unauthenticated --add-cloudsql-instances besque-martech:europe-west2:besque-db --update-env-vars STORAGE_BACKEND=gcs --update-env-vars GCS_BUCKET=besque-ad-intel-assets --set-secrets "SHARED_ACCESS_PASSWORD=${secretName}:latest" --no-cpu-throttling --min-instances 1 --max-instances 3
$img = gcloud run services describe besque-dashboard --region $region --project $project --format="value(spec.template.spec.containers[0].image)"
gcloud run jobs update besque-pipeline --image $img --region $region --project $project
Write-Output "SHIPPED: $img"
