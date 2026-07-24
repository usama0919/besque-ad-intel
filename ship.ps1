param([string]$msg = "update")
git add -A src templates dashboard.py job_runner.py requirements.txt
git commit -m $msg
git push origin main
gcloud run deploy besque-dashboard --source . --region europe-west2 --project besque-martech --no-allow-unauthenticated --add-cloudsql-instances besque-martech:europe-west2:besque-db --update-env-vars STORAGE_BACKEND=gcs --update-env-vars GCS_BUCKET=besque-ad-intel-assets --no-cpu-throttling --min-instances 1 --max-instances 5
$img = gcloud run services describe besque-dashboard --region europe-west2 --project besque-martech --format="value(spec.template.spec.containers[0].image)"
gcloud run jobs update besque-pipeline --image $img --region europe-west2 --project besque-martech
Write-Output "SHIPPED: $img"
